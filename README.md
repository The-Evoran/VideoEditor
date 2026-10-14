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

## Moments détectés (phase 4)
Analyse locale du transcript par un modèle Ollama (`http://127.0.0.1:11434`, variable `OLLAMA_URL`), sans dépendance ajoutée ni téléchargement de modèle.
- Le transcript est découpé en blocs (taille/chevauchement/contexte réglables dans le panneau « Moments détectés », enregistrés dans la table `settings`).
- Le modèle cite le passage ; les timecodes sont retrouvés par le backend dans le JSON mot par mot (sinon le SRT). Passage non retrouvé = « Non aligné », sans timecode inventé.
- Une analyse annulée ou interrompue reprend sans retraiter les blocs terminés (mêmes données et paramètres). Une seule analyse à la fois.
- Décisions (accepter/rejeter/favori), notes et timecodes corrigés sont stockés dans `moments` ; les fichiers SRT/JSON ne sont jamais modifiés.
