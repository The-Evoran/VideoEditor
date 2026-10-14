import hashlib
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

from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import analysis, transcript

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
    init_analysis_db(msg)


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
        if acur["id"]:
            raise HTTPException(409, "Une analyse Ollama est en cours : annulez-la ou attendez sa fin.")
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


# ---------- phase 4 : analyse Ollama et moments détectés ----------

acur = {"id": None, "cancel": threading.Event()}  # une seule analyse à la fois (VRAM/RAM locales)
DEFAULTS = {"model": None, "window": 300, "overlap": 30, "num_ctx": 8192}


def init_analysis_db(interrupted):
    q("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    q("""CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        fingerprint TEXT NOT NULL, model TEXT NOT NULL, params TEXT NOT NULL,
        status TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    q("""CREATE TABLE IF NOT EXISTS analysis_chunks (
        analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
        idx INTEGER NOT NULL, result TEXT NOT NULL, PRIMARY KEY (analysis_id, idx))""")
    q("""CREATE TABLE IF NOT EXISTS moments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        title TEXT NOT NULL, summary TEXT, category TEXT NOT NULL, justification TEXT, passage TEXT,
        score INTEGER NOT NULL, confidence TEXT, needs_review INTEGER NOT NULL, aligned INTEGER NOT NULL,
        t_start REAL, t_end REAL, auto_start REAL, auto_end REAL, hint TEXT, sources TEXT,
        status TEXT NOT NULL DEFAULT 'pending', favorite INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '', edited INTEGER NOT NULL DEFAULT 0)""")
    # une analyse interrompue par un redémarrage reste reprenable (les blocs finis sont conservés)
    q("UPDATE analyses SET status='error', error=?, updated_at=? WHERE status IN ('running','pending')", (interrupted, now()))


def get_config():
    cfg = dict(DEFAULTS)
    for r in q("SELECT key, value FROM settings WHERE key LIKE 'analysis.%'"):
        cfg[r["key"][9:]] = json.loads(r["value"])
    return cfg


def check_config(c):
    if not (60 <= c["window"] <= 1800):
        raise HTTPException(422, "La taille des blocs doit être comprise entre 60 et 1800 secondes.")
    if not (0 <= c["overlap"] <= c["window"] / 2):
        raise HTTPException(422, "Le chevauchement doit être compris entre 0 et la moitié de la taille des blocs.")
    if not (2048 <= c["num_ctx"] <= 131072):
        raise HTTPException(422, "Le contexte (num_ctx) doit être compris entre 2048 et 131072.")
    return c


class AnalysisConfig(BaseModel):
    model: str | None = None
    window: int | None = None
    overlap: int | None = None
    num_ctx: int | None = None


def merged_config(req):
    # exclude_none : un champ nul (modèle non choisi) ne doit pas écraser la valeur enregistrée
    return check_config({**get_config(), **req.model_dump(exclude_none=True)})


@app.get("/api/ollama")
def ollama_status():
    cfg = get_config()
    try:
        return {"available": True, "models": analysis.list_models(), "error": None, "config": cfg}
    except analysis.OllamaError as e:
        return {"available": False, "models": [], "error": str(e), "config": cfg}


@app.put("/api/analysis/config")
def set_config(req: AnalysisConfig):
    cfg = merged_config(req)
    for k, v in cfg.items():
        q("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
          (f"analysis.{k}", json.dumps(v)))
    return cfg


def analysis_view(r):
    a = dict(r)
    a["params"] = json.loads(a["params"])
    a.pop("fingerprint")
    return a


def set_analysis(aid, **kw):
    cols = ", ".join(f"{k}=?" for k in kw)
    q(f"UPDATE analyses SET {cols}, updated_at=? WHERE id=?", (*kw.values(), now(), aid))


def run_analysis(aid, pid, segs, words, model, params, thinking, ctx, cancel):
    try:
        num_ctx = min(params["num_ctx"], ctx or params["num_ctx"])
        # ~3 caractères par jeton en français ; on garde 4000 jetons pour consigne + réponse
        chunks = analysis.make_chunks(segs, params["window"], params["overlap"], max(2000, (num_ctx - 4000) * 3))
        set_analysis(aid, total=len(chunks), error=None)
        done = {r["idx"] for r in q("SELECT idx FROM analysis_chunks WHERE analysis_id=?", (aid,))}
        set_analysis(aid, done=len(done))
        failed = 0
        for idx, ch in enumerate(chunks):
            if idx in done:
                continue
            dets = None
            for _ in range(2):  # une relance si le modèle renvoie un JSON inexploitable
                raw = analysis.chat(model, analysis.chunk_prompt(segs, ch), num_ctx, cancel, thinking)
                try:
                    dets = analysis.parse_response(raw)
                    break
                except ValueError:
                    pass
            if dets is None:
                failed += 1
                continue
            located = [analysis.locate(d, segs, words, ch) for d in dets]
            q("INSERT OR REPLACE INTO analysis_chunks (analysis_id, idx, result) VALUES (?, ?, ?)", (aid, idx, json.dumps(located)))
            done.add(idx)
            set_analysis(aid, done=len(done))
        if cancel.is_set():
            raise analysis.Cancelled()
        if failed:
            set_analysis(aid, status="error", error=f"{failed} bloc(s) sans réponse exploitable du modèle : relancez pour les reprendre.")
            return
        dets = [d for r in q("SELECT result FROM analysis_chunks WHERE analysis_id=? ORDER BY idx", (aid,)) for d in json.loads(r["result"])]
        q("DELETE FROM moments WHERE analysis_id=?", (aid,))
        for m in analysis.merge(dets):
            q("""INSERT INTO moments (analysis_id, project_id, title, summary, category, justification, passage, score, confidence,
                 needs_review, aligned, t_start, t_end, auto_start, auto_end, hint, sources) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (aid, pid, m["title"], m["summary"], m["category"], m["justification"], m["passage"], m["score"], m["confidence"],
               int(m["needs_review"]), int(m["aligned"]), m["start"], m["end"], m["start"], m["end"],
               json.dumps(m["hint"]), json.dumps(m["sources"])))
        set_analysis(aid, status="done")
    except analysis.Cancelled:
        set_analysis(aid, status="cancelled")
    except analysis.OllamaError as e:
        set_analysis(aid, status="error", error=str(e))
    except Exception as e:
        set_analysis(aid, status="error", error=f"Erreur inattendue : {e}")
    finally:
        acur["id"] = None


@app.get("/api/projects/{pid}/analyses")
def list_analyses(pid: str):
    get_row(pid)
    return [analysis_view(r) for r in q("SELECT * FROM analyses WHERE project_id=? ORDER BY id DESC", (pid,))]


@app.post("/api/projects/{pid}/analyses")
def start_analysis(pid: str, req: AnalysisConfig):
    row = get_row(pid)
    cfg = merged_config(req)
    if not cfg["model"]:
        raise HTTPException(422, "Sélectionnez un modèle Ollama.")
    try:
        info = next((m for m in analysis.list_models() if m["name"] == cfg["model"]), None)
    except analysis.OllamaError as e:
        raise HTTPException(503, str(e))
    if not info:  # jamais de téléchargement automatique
        raise HTTPException(422, f"Le modèle « {cfg['model']} » n'est pas installé dans Ollama.")
    data = transcript.load(row["srt_path"], row["json_path"])
    if not data["segments"]:
        raise HTTPException(422, "Aucun transcript exploitable pour ce projet.")
    params = {k: cfg[k] for k in ("window", "overlap", "num_ctx")} | {"prompt_version": analysis.PROMPT_VERSION}
    fp = hashlib.sha256(json.dumps([data["segments"], data["words"], cfg["model"], params], sort_keys=True).encode()).hexdigest()
    with lock:
        if acur["id"] or job["status"] == "running":
            raise HTTPException(409, "Une analyse ou une transcription est déjà en cours (ressources locales limitées).")
        old = q("SELECT id, status FROM analyses WHERE project_id=? AND fingerprint=?", (pid, fp))
        if old and old[0]["status"] == "done":
            raise HTTPException(409, "Cette analyse (mêmes données et paramètres) est déjà terminée. Supprimez-la pour la refaire.")
        ts = now()
        if old:  # reprise : les blocs déjà terminés sont conservés
            aid = old[0]["id"]
            set_analysis(aid, status="running", error=None)
        else:
            aid = q("INSERT INTO analyses (project_id, fingerprint, model, params, status, created_at, updated_at) "
                    "VALUES (?,?,?,?, 'running', ?, ?) RETURNING id", (pid, fp, cfg["model"], json.dumps(params), ts, ts))[0][0]
        acur["id"] = aid
        acur["cancel"] = cancel = threading.Event()
    threading.Thread(target=run_analysis, args=(aid, pid, data["segments"], data["words"], cfg["model"], params,
                                                info["thinking"], info["context"], cancel), daemon=True).start()
    return analysis_view(q("SELECT * FROM analyses WHERE id=?", (aid,))[0])


@app.post("/api/analyses/{aid}/cancel")
def cancel_analysis(aid: int):
    if acur["id"] != aid:
        raise HTTPException(409, "Cette analyse n'est pas en cours.")
    acur["cancel"].set()
    return {"cancelling": aid}


@app.delete("/api/analyses/{aid}")
def delete_analysis(aid: int):
    if acur["id"] == aid:
        raise HTTPException(409, "Analyse en cours : annulez-la d'abord.")
    q("DELETE FROM analyses WHERE id=?", (aid,))  # supprime aussi blocs et moments ; jamais le transcript
    return {"deleted": aid}


def moment_view(r):
    m = dict(r)
    for k in ("needs_review", "aligned", "favorite", "edited"):
        m[k] = bool(m[k])
    m["hint"] = json.loads(m["hint"] or "null")
    m["sources"] = json.loads(m["sources"] or "[]")
    return m


@app.get("/api/projects/{pid}/moments")
def list_moments(pid: str, analysis_id: int | None = None):
    get_row(pid)
    if analysis_id is None:
        last = q("SELECT id FROM analyses WHERE project_id=? AND status='done' ORDER BY id DESC LIMIT 1", (pid,))
        if not last:
            return []
        analysis_id = last[0]["id"]
    return [moment_view(r) for r in q("SELECT * FROM moments WHERE project_id=? AND analysis_id=? ORDER BY id", (pid, analysis_id))]


class MomentPatch(BaseModel):
    status: Literal["pending", "accepted", "rejected"] | None = None
    favorite: bool | None = None
    note: str | None = None
    start: float | None = None
    end: float | None = None


@app.patch("/api/moments/{mid}")
def patch_moment(mid: int, req: MomentPatch):
    rows = q("SELECT * FROM moments WHERE id=?", (mid,))
    if not rows:
        raise HTTPException(404, "Moment introuvable.")
    d = req.model_dump(exclude_unset=True)
    sets = {}
    if d.get("status") is not None:
        sets["status"] = d["status"]
    if d.get("favorite") is not None:
        sets["favorite"] = int(d["favorite"])
    if d.get("note") is not None:
        sets["note"] = d["note"][:5000]
    if "start" in d or "end" in d:
        a, b = d.get("start", rows[0]["t_start"]), d.get("end", rows[0]["t_end"])
        if a is None or b is None or a < 0 or b <= a:
            raise HTTPException(422, "Timecodes invalides : le début doit être positif et précéder la fin.")
        sets.update(t_start=a, t_end=b, edited=1)
    if sets:
        q(f"UPDATE moments SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), mid))
    return moment_view(q("SELECT * FROM moments WHERE id=?", (mid,))[0])
