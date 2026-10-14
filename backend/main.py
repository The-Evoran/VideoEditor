import json
import os
import sqlite3
import subprocess
import sys
import threading
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import transcript

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", ROOT)).resolve()
SCRIPT = Path(os.environ.get("TRANSCRIPTION_SCRIPT", ROOT / "transcription.py"))
# Chemin absolu, indépendant du répertoire de lancement du serveur
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "data" / "projects.db")).resolve()
# cuBLAS/cuDNN installés par pip dans le venv ne sont pas dans le PATH Windows
NVIDIA_BINS = [str(p) for p in (ROOT / "venv/Lib/site-packages/nvidia").glob("*/bin")]
EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4a", ".mp3", ".wav"}

lock = threading.Lock()
job = {"status": "idle", "video": None, "project_id": None, "logs": deque(maxlen=500), "error": None, "files": None}


# ---------- base de données (sqlite3 standard, schéma additif uniquement) ----------

def q(sql, args=()):
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        with c:
            return c.execute(sql, args).fetchall()
    finally:
        c.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    q("""CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, video_path TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'idle', srt_path TEXT, json_path TEXT,
        duration REAL, error TEXT)""")
    q("""CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL, error TEXT)""")
    # un redémarrage tue le thread de transcription : ne pas rester « en cours » à jamais
    msg = "Interrompu par un redémarrage du backend."
    q("UPDATE runs SET status='error', error=?, ended_at=? WHERE status='running'", (msg, now()))
    q("UPDATE projects SET status='error', error=? WHERE status='running'", (msg,))


@asynccontextmanager
async def lifespan(_):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)


# ---------- transcription (mécanisme de la phase 1, partagé avec les projets) ----------

class Start(BaseModel):
    video: str  # nom de fichier dans VIDEO_DIR, jamais un chemin libre


def list_videos():
    return sorted(p.name for p in VIDEO_DIR.iterdir() if p.is_file() and p.suffix.lower() in EXTS)


def outputs(video: Path):
    return video.with_name(video.stem + "_whisper.srt"), video.with_name(video.stem + "_whisper.json")


def run(video: Path, pid=None, run_id=None):
    srt, js = outputs(video)
    base = video.with_name(video.stem + "_whisper.new")  # sortie temporaire : les anciens résultats restent intacts
    tmp = [base.with_name(base.name + ".srt"), base.with_name(base.name + ".json")]
    duration = None
    try:
        for f in tmp:
            f.unlink(missing_ok=True)
        proc = subprocess.Popen(
            [sys.executable, "-u", str(SCRIPT), str(video), str(base)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8",
                 "PATH": os.pathsep.join(NVIDIA_BINS + [os.environ.get("PATH", "")])},
        )
        for line in proc.stdout:
            job["logs"].append(line.rstrip())
        code = proc.wait()
        if code != 0:
            job["error"] = f"Le script a échoué (code {code})."
        elif not all(f.exists() for f in tmp):
            job["error"] = "Le script a terminé mais le SRT et/ou le JSON n'ont pas été générés."
        else:
            try:
                duration = json.loads(tmp[1].read_text(encoding="utf-8")).get("duration")
            except (OSError, ValueError):
                pass
            os.replace(tmp[0], srt)
            os.replace(tmp[1], js)
            job["files"] = {"srt": str(srt), "json": str(js)}
    except Exception as e:
        job["error"] = f"Impossible de lancer ou de finaliser la transcription : {e}"
    for f in tmp:
        f.unlink(missing_ok=True)
    status = "error" if job["error"] else "done"
    if pid:
        ts = now()
        try:
            q("UPDATE runs SET status=?, error=?, ended_at=? WHERE id=?", (status, job["error"], ts, run_id))
            if status == "done":
                q("UPDATE projects SET status='done', error=NULL, srt_path=?, json_path=?, duration=?, updated_at=? WHERE id=?",
                  (str(srt), str(js), duration, ts, pid))
            else:  # srt_path/json_path conservés : un échec ne détruit pas les résultats précédents
                q("UPDATE projects SET status='error', error=?, updated_at=? WHERE id=?", (job["error"], ts, pid))
        except sqlite3.Error as e:
            job["error"] = f"Enregistrement du résultat impossible : {e}"
            status = "error"
    job["status"] = status


def launch(video: Path, pid=None):
    with lock:  # protège contre les doubles lancements
        if job["status"] == "running":
            raise HTTPException(409, "Une transcription est déjà en cours.")
        job.update(status="running", video=video.name, project_id=pid, error=None, files=None)
        job["logs"].clear()
    run_id = None
    if pid:
        ts = now()
        run_id = q("INSERT INTO runs (project_id, started_at, status) VALUES (?, ?, 'running') RETURNING id", (pid, ts))[0][0]
        q("UPDATE projects SET status='running', error=NULL, updated_at=? WHERE id=?", (ts, pid))
    threading.Thread(target=run, args=(video, pid, run_id), daemon=True).start()
    return {"status": "running"}


@app.get("/api/videos")
def videos():
    return list_videos()


@app.post("/api/transcriptions")
def start(req: Start):
    if req.video not in list_videos():
        raise HTTPException(404, "Vidéo introuvable.")
    return launch(VIDEO_DIR / req.video)


@app.get("/api/transcriptions/current")
def current():
    return {**job, "logs": list(job["logs"])}


# ---------- projets ----------

class NewProject(BaseModel):
    video: str  # nom de fichier dans VIDEO_DIR
    name: str | None = None


class Rename(BaseModel):
    name: str


def clean_name(name):
    name = (name or "").strip()
    if not name:
        raise HTTPException(422, "Le nom du projet ne peut pas être vide.")
    return name[:200]


def view(row, with_runs=False):
    p = dict(row)
    video = Path(p["video_path"])
    p["video_name"] = video.name
    p["video_exists"] = video.is_file()
    for k in ("srt", "json"):  # existence vérifiée à chaque lecture : fichiers déplacés/supprimés signalés
        path = p.pop(f"{k}_path")
        p[k] = {"path": path, "exists": Path(path).is_file()} if path else None
    if with_runs:
        p["runs"] = [dict(r) for r in q(
            "SELECT started_at, ended_at, status, error FROM runs WHERE project_id=? ORDER BY id DESC LIMIT 20", (p["id"],))]
    return p


def get_row(pid):
    rows = q("SELECT * FROM projects WHERE id=?", (pid,))
    if not rows:
        raise HTTPException(404, "Projet introuvable.")
    return rows[0]


@app.get("/api/projects")
def projects():
    return [view(r) for r in q("SELECT * FROM projects ORDER BY created_at DESC")]


@app.post("/api/projects")
def create_project(req: NewProject):
    if req.video not in list_videos():
        raise HTTPException(404, "Vidéo introuvable.")
    video = VIDEO_DIR / req.video
    srt = outputs(video)[0]
    # les sorties sont déduites du nom de la vidéo : deux projets ne doivent jamais viser les mêmes fichiers
    for r in q("SELECT video_path, name FROM projects"):
        if outputs(Path(r["video_path"]))[0] == srt:
            raise HTTPException(409, f"Le projet « {r['name']} » utilise déjà cette vidéo (ou une vidéo de même nom).")
    pid, ts = uuid.uuid4().hex, now()
    q("INSERT INTO projects (id, name, video_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
      (pid, clean_name(req.name or video.stem), str(video), ts, ts))
    return view(get_row(pid))


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return view(get_row(pid), with_runs=True)


@app.patch("/api/projects/{pid}")
def rename_project(pid: str, req: Rename):
    get_row(pid)
    q("UPDATE projects SET name=?, updated_at=? WHERE id=?", (clean_name(req.name), now(), pid))
    return view(get_row(pid))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    row = get_row(pid)
    if row["status"] == "running":
        raise HTTPException(409, "Une transcription est en cours pour ce projet.")
    q("DELETE FROM projects WHERE id=?", (pid,))  # ne touche ni la vidéo ni le SRT/JSON
    return {"deleted": pid}


@app.post("/api/projects/{pid}/transcription")
def start_project(pid: str):
    video = Path(get_row(pid)["video_path"])
    if not video.is_file():
        raise HTTPException(404, "Le fichier vidéo est introuvable (déplacé ou supprimé ?).")
    return launch(video, pid)


# ---------- phase 3 : lecture vidéo et transcript (lecture seule) ----------

@app.get("/api/projects/{pid}/video")
def project_video(pid: str):
    # seul le chemin enregistré pour ce projet est servi ; FileResponse gère les requêtes Range en flux
    video = Path(get_row(pid)["video_path"])
    if not video.is_file():
        raise HTTPException(404, "Le fichier vidéo est introuvable (déplacé ou supprimé ?).")
    return FileResponse(video)


@app.get("/api/projects/{pid}/transcript")
def project_transcript(pid: str):
    row = get_row(pid)
    return transcript.load(row["srt_path"], row["json_path"])
