import time
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    import backend.main as m
    (tmp_path / "ma vidéo test.mp4").write_bytes(b"x")
    monkeypatch.setattr(m, "VIDEO_DIR", tmp_path)
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "data" / "test.db")
    m.init_db()
    m.job.update(status="idle", video=None, project_id=None, error=None, files=None)
    return m, TestClient(m.app), tmp_path


def fake(m, tmp_path, body):
    s = tmp_path / "fake.py"
    s.write_text("import sys,time\nfrom pathlib import Path\nv=Path(sys.argv[1])\n"
                 "o=Path(sys.argv[2]) if len(sys.argv)>2 else v.with_name(v.stem+'_whisper')\n" + body, encoding="utf-8")
    m.SCRIPT = s


def wait(c):
    for _ in range(400):
        j = c.get("/api/transcriptions/current").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("timeout")


OK = ("print('go é')\no.with_name(o.name+'.srt').write_text('a')\n"
      "o.with_name(o.name+'.json').write_text('{\"duration\": 12.5}')\n")
VIDEO = "ma vidéo test.mp4"


# ---------- phase 1 ----------

def test_ok_with_spaces(env):
    m, c, t = env
    fake(m, t, OK)
    assert c.post("/api/transcriptions", json={"video": VIDEO}).status_code == 200
    j = wait(c)
    assert j["status"] == "done" and j["files"]["srt"].endswith("ma vidéo test_whisper.srt")
    assert (t / "ma vidéo test_whisper.srt").read_text() == "a"
    assert "go é" in j["logs"]


def test_missing_and_unauthorized_path(env):
    m, c, t = env
    assert c.post("/api/transcriptions", json={"video": "nope.mp4"}).status_code == 404
    assert c.post("/api/transcriptions", json={"video": "../x.mp4"}).status_code == 404


def test_script_fails(env):
    m, c, t = env
    fake(m, t, "raise SystemExit('boom')\n")
    c.post("/api/transcriptions", json={"video": VIDEO})
    j = wait(c)
    assert j["status"] == "error" and "boom" in "\n".join(j["logs"])


def test_no_output_files(env):
    m, c, t = env
    fake(m, t, "print('rien')\n")
    c.post("/api/transcriptions", json={"video": VIDEO})
    assert "pas été générés" in wait(c)["error"]


def test_double_launch(env):
    m, c, t = env
    fake(m, t, "time.sleep(1)\n" + OK)
    assert c.post("/api/transcriptions", json={"video": VIDEO}).status_code == 200
    assert c.post("/api/transcriptions", json={"video": VIDEO}).status_code == 409
    assert wait(c)["status"] == "done"


# ---------- phase 2 : projets ----------

def new(c, video=VIDEO, name=None):
    return c.post("/api/projects", json={"video": video, "name": name})


def test_create_list_rename_delete(env):
    m, c, t = env
    (t / "b.mp4").write_bytes(b"x")
    p = new(c, name="  Mon projet ").json()
    assert p["name"] == "Mon projet" and p["video_name"] == VIDEO and p["status"] == "idle" and p["srt"] is None
    assert new(c, "b.mp4").json()["name"] == "b"  # nom par défaut = nom de la vidéo
    assert len(c.get("/api/projects").json()) == 2
    assert c.patch(f"/api/projects/{p['id']}", json={"name": "Autre"}).json()["name"] == "Autre"
    assert (t / VIDEO).exists()  # renommer ne touche pas la vidéo
    assert c.patch(f"/api/projects/{p['id']}", json={"name": " "}).status_code == 422
    assert c.delete(f"/api/projects/{p['id']}").status_code == 200
    assert (t / VIDEO).exists()  # supprimer ne touche pas la vidéo
    assert c.get(f"/api/projects/{p['id']}").status_code == 404
    assert c.delete(f"/api/projects/{p['id']}").status_code == 404


def test_create_rejects_unknown_or_duplicate_video(env):
    m, c, t = env
    assert new(c, "nope.mp4").status_code == 404
    assert new(c, "../x.mp4").status_code == 404
    assert new(c).status_code == 200
    (t / "ma vidéo test.mkv").write_bytes(b"x")  # mêmes fichiers de sortie que le .mp4
    assert new(c, "ma vidéo test.mkv").status_code == 409
    assert new(c).status_code == 409


def test_persistence_across_restart(env):
    m, c, t = env
    pid = new(c).json()["id"]
    m.init_db()  # simule un redémarrage : même fichier de base
    assert c.get(f"/api/projects/{pid}").json()["video_name"] == VIDEO


def test_project_transcription_links_results_and_keeps_them_on_failure(env):
    m, c, t = env
    (t / "autre.mp4").write_bytes(b"x")
    p, other = new(c).json(), new(c, "autre.mp4").json()
    fake(m, t, OK)
    assert c.post(f"/api/projects/{p['id']}/transcription").status_code == 200
    assert wait(c)["status"] == "done"
    d = c.get(f"/api/projects/{p['id']}").json()
    assert d["status"] == "done" and d["duration"] == 12.5
    assert d["srt"] == {"path": str(t / "ma vidéo test_whisper.srt"), "exists": True}
    assert d["json"]["exists"] and d["runs"][0]["status"] == "done"
    assert c.get(f"/api/projects/{other['id']}").json()["srt"] is None  # pas de fuite vers un autre projet

    fake(m, t, "o.with_name(o.name+'.srt').write_text('PARTIEL')\nraise SystemExit('boom')\n")
    c.post(f"/api/projects/{p['id']}/transcription")
    wait(c)
    d = c.get(f"/api/projects/{p['id']}").json()
    assert d["status"] == "error" and "code" in d["error"]
    assert d["srt"]["exists"] and (t / "ma vidéo test_whisper.srt").read_text() == "a"  # ancien SRT intact
    assert not list(t.glob("*.new.*"))  # temporaires nettoyés
    assert [r["status"] for r in d["runs"]] == ["error", "done"]


def test_missing_files_are_reported_and_launch_refused(env):
    m, c, t = env
    p = new(c).json()
    fake(m, t, OK)
    c.post(f"/api/projects/{p['id']}/transcription")
    wait(c)
    (t / "ma vidéo test_whisper.srt").unlink()
    (t / VIDEO).unlink()
    d = c.get(f"/api/projects/{p['id']}").json()
    assert d["srt"]["exists"] is False and d["json"]["exists"] is True and d["video_exists"] is False
    assert len(c.get("/api/projects").json()) == 1  # la bibliothèque reste utilisable
    assert c.post(f"/api/projects/{p['id']}/transcription").status_code == 404


def test_running_project_cannot_be_deleted_and_restart_marks_interrupted(env):
    m, c, t = env
    pid = new(c).json()["id"]
    fake(m, t, "time.sleep(1)\n" + OK)
    c.post(f"/api/projects/{pid}/transcription")
    assert c.delete(f"/api/projects/{pid}").status_code == 409
    wait(c)
    m.q("UPDATE projects SET status='running' WHERE id=?", (pid,))
    m.init_db()
    assert c.get(f"/api/projects/{pid}").json()["status"] == "error"


# ---------- phase 3 ----------

def project(m, c):
    return c.post("/api/projects", json={"video": VIDEO}).json()["id"]


def test_video_range_and_security(env):
    m, c, tmp = env
    (tmp / VIDEO).write_bytes(bytes(range(256)) * 4)
    pid = project(m, c)
    r = c.get(f"/api/projects/{pid}/video", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == (bytes(range(256)) * 4)[10:20]
    assert r.headers["content-range"] == "bytes 10-19/1024"
    assert c.get("/api/projects/inconnu/video").status_code == 404
    (tmp / VIDEO).unlink()
    assert c.get(f"/api/projects/{pid}/video").status_code == 404


def test_parse_srt_real_format():
    from backend.transcript import parse_srt
    raw = "﻿1\r00:00:01,900 --> 00:00:04,120\rOn camble en 2020\r\r\n2\r00:01:00,000 --> 01:00:00,5\rdeux\rlignes\r\r\n"
    assert parse_srt(raw) == [
        {"start": 1.9, "end": 4.12, "text": "On camble en 2020"},
        {"start": 60.0, "end": 3600.5, "text": "deux lignes"}]


def test_parse_json_cleaning():
    from backend.transcript import parse_json
    segs, words = parse_json({"duration": 10, "segments": [{"start": 0, "end": 2, "text": "a", "words": [
        {"word": "b", "start": 1, "end": 3}, {"word": "a", "start": 0, "end": 1},
        {"word": "x", "start": None, "end": 1}, {"word": " ", "start": 5, "end": 6}, {"word": "z", "start": 2, "end": 1}]}]})
    assert words == [[0, 1, "a"], [1, 2, "b"], [2, 2, "z"]]  # trié, invalides retirés, chevauchement coupé
    _, ms = parse_json({"duration": 10, "words": [{"word": "a", "start": 1000, "end": 2000}]})
    assert ms == [[1.0, 2.0, "a"]]  # millisecondes détectées


def test_transcript_fallbacks(env):
    m, c, tmp = env
    pid = project(m, c)
    t = c.get(f"/api/projects/{pid}/transcript").json()
    assert t["source"] is None and t["segments"] == []
    js = tmp / "j.json"
    js.write_text('{"segments":[{"start":1,"end":2,"text":"salut","words":[{"word":"salut","start":1,"end":2}]}]}', encoding="utf-8")
    m.q("UPDATE projects SET json_path=? WHERE id=?", (str(js), pid))
    t = c.get(f"/api/projects/{pid}/transcript").json()
    assert t["source"] == "json" and t["words"] == [[1, 2, "salut"]]
    srt = tmp / "s.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nsalut\n", encoding="utf-8")
    m.q("UPDATE projects SET srt_path=? WHERE id=?", (str(srt), pid))
    assert c.get(f"/api/projects/{pid}/transcript").json()["source"] == "srt"
    js.write_text("pas du json")
    srt.unlink()
    t = c.get(f"/api/projects/{pid}/transcript").json()
    assert t["segments"] == [] and len(t["warnings"]) == 2
