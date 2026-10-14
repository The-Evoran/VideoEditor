import json
import time
import urllib.error

import pytest
from fastapi.testclient import TestClient

from backend import analysis as A


# ---------- parsing de la réponse du modèle ----------

def test_parse_response_valid_and_cleaning():
    raw = json.dumps({"moments": [
        {"title": "T", "summary": "s", "category": "punchline", "justification": "j", "passage": "bonjour", "score": 8.4,
         "confidence": "high", "needs_review": False},
        {"title": "X", "passage": "ok", "score": 5, "category": "inconnue"},  # catégorie inconnue -> review
        {"title": "sans passage", "passage": " ", "score": 5},
        {"title": "score hors bornes", "passage": "a", "score": 11},
        "n'importe quoi",
    ]})
    m = A.parse_response(raw)
    assert [x["title"] for x in m] == ["T", "X"]
    assert m[0]["score"] == 8 and m[0]["category"] == "punchline"
    assert m[1]["category"] == "review" and m[1]["needs_review"] and m[1]["confidence"] == "low"


def test_parse_response_fences_and_garbage():
    inner = '{"moments": [{"title": "T", "passage": "p", "score": 3}]}'
    assert len(A.parse_response("```json\n" + inner + "\n```")) == 1
    assert len(A.parse_response("Voici : " + inner + " voilà")) == 1
    assert A.parse_response('{"moments": []}') == []
    for bad in ("pas du json", '{"autre": 1}', ""):
        with pytest.raises(ValueError):
            A.parse_response(bad)


# ---------- alignement des timecodes ----------

def words(text, t0=10.0, step=0.5):
    return [[t0 + i * step, t0 + i * step + 0.4, w] for i, w in enumerate(text.split())]


def toks(ws):
    return A.build_tokens([], ws, 0, 1e9)


def test_align_exact_with_punctuation_and_accents():
    ws = words("alors je disais que le café était froid hier soir et personne ne répondait")
    a = A.align("Le café, était froid : hier soir !", toks(ws))
    assert (a["start"], a["end"]) == (ws[4][0], ws[9][1]) and a["cover"] == 1


def test_align_tolerates_transcription_errors():
    ws = words("on est partis au marché acheter des pommes rouges et des poires vertes pour le dessert de dimanche")
    a = A.align("acheter des pommes rouge et des poires vert pour le dessert", toks(ws))
    assert a and ws[5][0] <= a["start"] <= ws[6][0] and a["end"] >= ws[13][0]


def test_align_picks_right_occurrence_among_repetitions():
    ws = words("ça va ça va ça va très bien merci beaucoup ça va ça va")
    a = A.align("très bien merci beaucoup", toks(ws))
    assert a["start"] == ws[6][0] and a["end"] == ws[9][1]


def test_align_refuses_invented_text():
    ws = words("on parle de cuisine et de recettes pour la famille")
    assert A.align("le président a annoncé une réforme fiscale majeure", toks(ws)) is None
    assert A.align("", toks(ws)) is None


def test_locate_unaligned_has_no_timecode_and_needs_review():
    ws = words("bonjour tout le monde bienvenue sur le stream")
    segs = [{"start": 10.0, "end": 14.0, "text": "bonjour tout le monde bienvenue sur le stream"}]
    ch = {"start": 10.0, "end": 14.0}
    det = {"title": "t", "passage": "texte totalement absent du transcript", "score": 5, "needs_review": False,
           "category": "topic", "summary": "", "justification": "", "confidence": "high"}
    r = A.locate(det, segs, ws, ch)
    assert r["start"] is None and r["end"] is None and r["needs_review"] and not r["aligned"]
    ok = A.locate({**det, "passage": "tout le monde bienvenue"}, segs, ws, ch)
    assert ok["aligned"] and ok["start"] == ws[1][0] and ok["passage"] == "tout le monde bienvenue"


def test_align_uses_srt_segments_without_words():
    segs = [{"start": 0, "end": 5, "text": "intro du stream"}, {"start": 5, "end": 9, "text": "on parle de jeux vidéo"}]
    a = A.align("on parle de jeux", A.build_tokens(segs, [], 0, 100))
    assert (a["start"], a["end"]) == (5, 9)


# ---------- découpage ----------

def make_segs(n, dur=10, text="une phrase de test"):
    return [{"start": i * dur, "end": (i + 1) * dur, "text": text + f" {i}"} for i in range(n)]


def test_chunks_overlap_progress_and_coverage():
    segs = make_segs(60)  # 600 s
    ch = A.make_chunks(segs, 120, 30, 10**6)
    assert ch[0]["i0"] == 0 and ch[-1]["i1"] == 60
    for a, b in zip(ch, ch[1:]):
        assert b["i0"] > a["i0"] and b["i0"] < a["i1"]  # progression + chevauchement
        assert a["end"] - b["start"] <= 30 + 10
    assert max(c["end"] - c["start"] for c in ch) <= 120 * 1.2 + 10


def test_chunks_extend_to_sentence_end_and_respect_max_chars():
    segs = make_segs(30, text="mot")
    segs[10]["text"] = "fin de phrase."  # fenêtre de 100 s atteinte à i=9 -> prolongée jusqu'à la fin de phrase
    assert A.make_chunks(segs, 100, 0, 10**6)[0]["i1"] == 11
    small = A.make_chunks(segs, 10_000, 0, 40)  # le contexte limite la taille avant la durée
    assert len(small) > 3 and all(c["i1"] > c["i0"] for c in small)


# ---------- fusion des doublons ----------

def det(title, start, end, score=5, cat="topic", passage="p"):
    return {"title": title, "summary": "", "category": cat, "justification": "", "passage": passage, "model_passage": passage,
            "score": score, "confidence": "high", "needs_review": False, "start": start, "end": end,
            "aligned": start is not None, "hint": [0, 1]}


def test_merge_duplicates_keeps_union_bounds_and_sources():
    m = A.merge([det("Le débat", 100, 120, 6), det("Débat sur les prix", 105, 130, 8), det("Autre moment", 400, 420)])
    assert len(m) == 2
    assert (m[0]["start"], m[0]["end"], m[0]["score"], len(m[0]["sources"])) == (100, 130, 8, 2)


def test_merge_close_same_topic_only_and_distinct_kept():
    near_same = A.merge([det("Annonce du tournoi", 100, 110), det("Annonce tournoi", 112, 120)])
    assert len(near_same) == 1
    near_other = A.merge([det("Annonce du tournoi", 100, 110), det("Blague sur le chat", 112, 120)])
    assert len(near_other) == 2
    far = A.merge([det("Annonce du tournoi", 100, 110), det("Annonce du tournoi", 500, 510)])
    assert len(far) == 2


def test_merge_unaligned():
    a = det("x", None, None, passage="le texte inventé par le modèle ici")
    b = det("y", None, None, passage="le texte inventé par le modèle ici !")
    m = A.merge([a, b, det("z", 5, 9)])
    assert len(m) == 2 and m[1]["start"] is None and m[1]["needs_review"] and m[1]["aligned"] is False


# ---------- erreurs Ollama ----------

def test_ollama_unreachable(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(A.urllib.request, "urlopen", boom)
    with pytest.raises(A.OllamaError, match="injoignable"):
        A.list_models()


def test_ollama_bad_payload(monkeypatch):
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{"nope": 1}'
    monkeypatch.setattr(A.urllib.request, "urlopen", lambda *a, **k: R())
    with pytest.raises(A.OllamaError, match="inattendue"):
        A.list_models()


# ---------- API : analyse de bout en bout avec un faux Ollama ----------

MODEL = {"name": "fake:1b", "size": 1, "context": 32768, "thinking": False}


@pytest.fixture
def env(tmp_path, monkeypatch):
    import backend.main as m
    (tmp_path / "v.mp4").write_bytes(b"x")
    monkeypatch.setattr(m, "VIDEO_DIR", tmp_path)
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "data" / "t.db")
    m.init_db()
    c = TestClient(m.app)
    segs = [{"start": i * 30, "end": (i + 1) * 30, "text": f"segment numéro {i} avec du contenu"} for i in range(20)]
    ws = [[s["start"] + k * 5, s["start"] + k * 5 + 4, w] for s in segs for k, w in enumerate(s["text"].split())]
    js = tmp_path / "v_whisper.json"
    js.write_text(json.dumps({"duration": 600, "segments": segs, "words": [{"word": w[2], "start": w[0], "end": w[1]} for w in ws]}), encoding="utf-8")
    pid = c.post("/api/projects", json={"video": "v.mp4"}).json()["id"]
    m.q("UPDATE projects SET json_path=?, status='done' WHERE id=?", (str(js), pid))
    monkeypatch.setattr(A, "list_models", lambda: [MODEL])
    return m, c, pid


def fake_chat(calls, cancel_at=None):
    def chat(model, user, num_ctx, cancel, supports_thinking=False):
        calls.append(user)
        if cancel_at and len(calls) == cancel_at:
            cancel.set()
            raise A.Cancelled()
        line = user.splitlines()[2]  # 2e segment de l'extrait
        return json.dumps({"moments": [{"title": "T " + line, "summary": "s", "category": "topic", "justification": "j",
                                        "passage": line, "score": 7, "confidence": "high", "needs_review": False}]})
    return chat


def wait(c, pid):
    for _ in range(200):
        a = c.get(f"/api/projects/{pid}/analyses").json()[0]
        if a["status"] != "running":
            return a
        time.sleep(0.05)
    raise AssertionError("timeout")


BODY = {"model": "fake:1b", "window": 120, "overlap": 30}


def test_full_analysis_aligns_and_dedups(env, monkeypatch):
    m, c, pid = env
    calls = []
    before = (m.VIDEO_DIR / "v_whisper.json").read_bytes()
    monkeypatch.setattr(A, "chat", fake_chat(calls))
    assert c.post(f"/api/projects/{pid}/analyses", json=BODY).status_code == 200
    a = wait(c, pid)
    assert a["status"] == "done" and a["done"] == a["total"] == len(calls) > 1
    ms = c.get(f"/api/projects/{pid}/moments").json()
    assert ms and all(x["aligned"] and x["t_start"] < x["t_end"] for x in ms)
    assert len({x["passage"] for x in ms}) == len(ms)  # doublons du chevauchement fusionnés
    first = ms[0]  # « segment numéro 1 avec du contenu » : début = 1er mot du segment 1 (30 s)
    assert first["t_start"] == 30 and first["passage"] == "segment numéro 1 avec du contenu"
    # même analyse refusée, mais le transcript original n'est pas modifié
    assert c.post(f"/api/projects/{pid}/analyses", json=BODY).status_code == 409
    assert (m.VIDEO_DIR / "v_whisper.json").read_bytes() == before


def test_cancel_then_resume_skips_finished_chunks(env, monkeypatch):
    m, c, pid = env
    calls = []
    monkeypatch.setattr(A, "chat", fake_chat(calls, cancel_at=3))
    c.post(f"/api/projects/{pid}/analyses", json=BODY)
    a = wait(c, pid)
    assert a["status"] == "cancelled" and a["done"] == 2 and a["total"] > 3
    assert c.get(f"/api/projects/{pid}/moments").json() == []
    calls2 = []
    monkeypatch.setattr(A, "chat", fake_chat(calls2))
    c.post(f"/api/projects/{pid}/analyses", json=BODY)
    b = wait(c, pid)
    assert b["id"] == a["id"] and b["status"] == "done" and len(calls2) == a["total"] - 2
    # paramètres différents : nouvelle analyse distincte
    c.post(f"/api/projects/{pid}/analyses", json={**BODY, "window": 200})
    assert wait(c, pid)["id"] != a["id"]


def test_errors_model_missing_ollama_down_and_unusable_output(env, monkeypatch):
    m, c, pid = env
    r = c.post(f"/api/projects/{pid}/analyses", json={**BODY, "model": "absent:7b"})
    assert r.status_code == 422 and "pas installé" in r.json()["detail"]
    assert c.post(f"/api/projects/{pid}/analyses", json={"window": 120}).status_code in (200, 422)  # modèle de config éventuel
    monkeypatch.setattr(A, "chat", lambda *a, **k: "pas du json")
    c.post(f"/api/projects/{pid}/analyses", json=BODY)
    a = wait(c, pid)
    assert a["status"] == "error" and "relancez" in a["error"]

    def down():
        raise A.OllamaError("Ollama injoignable sur x (est-il lancé ?).")
    monkeypatch.setattr(A, "list_models", down)
    assert c.get("/api/ollama").json()["available"] is False
    assert c.post(f"/api/projects/{pid}/analyses", json=BODY).status_code == 503


def test_ollama_error_during_run_and_no_concurrency(env, monkeypatch):
    m, c, pid = env
    def boom(*a, **k):
        raise A.OllamaError("Erreur Ollama : modèle plantu")
    monkeypatch.setattr(A, "chat", boom)
    c.post(f"/api/projects/{pid}/analyses", json=BODY)
    assert "plantu" in wait(c, pid)["error"]
    m.acur["id"] = 999  # simule une analyse en cours
    try:
        assert c.post(f"/api/projects/{pid}/analyses", json=BODY).status_code == 409
        assert c.post(f"/api/projects/{pid}/transcription").status_code in (404, 409)
    finally:
        m.acur["id"] = None


def test_config_and_moment_edit(env, monkeypatch):
    m, c, pid = env
    assert c.put("/api/analysis/config", json={"model": "fake:1b", "window": 50}).status_code == 422
    assert c.put("/api/analysis/config", json={"model": "fake:1b", "window": 120}).json()["model"] == "fake:1b"
    assert c.get("/api/ollama").json()["config"]["window"] == 120
    monkeypatch.setattr(A, "chat", fake_chat([]))
    c.post(f"/api/projects/{pid}/analyses", json={})  # utilise la config enregistrée
    assert wait(c, pid)["status"] == "done"
    mo = c.get(f"/api/projects/{pid}/moments").json()[0]
    p = c.patch(f"/api/moments/{mo['id']}", json={"status": "accepted", "favorite": True, "note": "à garder", "start": 31.5, "end": 40}).json()
    assert (p["status"], p["favorite"], p["note"], p["t_start"], p["t_end"], p["edited"]) == ("accepted", True, "à garder", 31.5, 40, True)
    assert p["auto_start"] == mo["auto_start"]  # la valeur proposée par l'analyse reste disponible
    assert c.patch(f"/api/moments/{mo['id']}", json={"start": 50, "end": 40}).status_code == 422
    assert c.patch(f"/api/moments/{mo['id']}", json={"status": "bof"}).status_code == 422
    assert c.patch("/api/moments/99999", json={"note": "x"}).status_code == 404


def test_locate_rejoins_elisions_and_hyphens():
    ws = [[0, 1, "C"], [1, 2, "'est"], [2, 3, "peut"], [3, 4, "-être"], [4, 5, "oui"]]
    segs = [{"start": 0, "end": 5, "text": "C'est peut-être oui"}]
    r = A.locate({"title": "t", "passage": "c'est peut-être oui", "score": 5, "needs_review": False, "category": "topic",
                  "summary": "", "justification": "", "confidence": "high"}, segs, ws, {"start": 0, "end": 5})
    assert r["aligned"] and r["passage"] == "C'est peut-être oui"
