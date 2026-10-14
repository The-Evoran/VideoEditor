# Préparation VOD — phases 1 et 2

## Installation
```
venv\Scripts\python.exe -m pip install fastapi uvicorn pytest
cd frontend && npm install
```
## Lancement (2 terminaux, depuis E:\Whisper)
```
venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
cd frontend && npm run dev      # http://localhost:5173
```
Les vidéos listées sont celles du dossier `E:\Whisper` (variable `VIDEO_DIR` pour changer).
Sortie : `<video>_whisper.srt` et `<video>_whisper.json` à côté de la vidéo.
Script seul : `venv\Scripts\python.exe transcription.py "E:\VOD\ma vidéo.mp4"`.

## Tests
`venv\Scripts\python.exe -m pytest backend`

## Projets (phase 2)
Bibliothèque de projets persistée dans `data/projects.db` (SQLite, variable `DB_PATH`). Un projet = une vidéo de `VIDEO_DIR`.
Supprimer un projet ne supprime jamais la vidéo ni le SRT/JSON. Le script écrit en fichiers temporaires `*_whisper.new.*`, renommés seulement en cas de succès.
