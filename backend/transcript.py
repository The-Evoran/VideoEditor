"""Lecture (sans jamais écrire) des SRT/JSON produits par transcription.py.

Formats réels constatés : SRT avec BOM et fins de ligne « \\r » seules ; JSON
{duration, segments:[{start,end,text,words:[{word,start,end}]}], words:[...]}
avec des temps en secondes, absolus (relatifs au début de la VOD).
"""
import json
import math
import re
from pathlib import Path

TC = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def _sec(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str):
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, cur = [], None
    for line in lines:
        m = TC.search(line)
        if m:
            cur = {"start": _sec(*m.groups()[:4]), "end": _sec(*m.groups()[4:]), "text": []}
            out.append(cur)
        elif not line.strip():
            cur = None  # une ligne vide ferme le bloc (le numéro suivant est alors ignoré)
        elif cur is not None:
            cur["text"].append(line.strip())
    for s in out:
        s["text"] = " ".join(s["text"])
    return clean_segments(out)



def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def clean_segments(segs):
    out = []
    for s in segs:
        a, b = _num(s.get("start")), _num(s.get("end"))
        if a is None or a < 0:
            continue
        out.append({"start": a, "end": b if b is not None and b >= a else a, "text": str(s.get("text", "")).strip()})
    out.sort(key=lambda s: s["start"])
    return out


def clean_words(words):
    """Mots valides triés par début ; [start, end, texte]. Fin >= début et sans chevauchement du mot suivant."""
    out = []
    for w in words:
        a, b, t = _num(w.get("start")), _num(w.get("end")), str(w.get("word", "")).strip()
        if a is None or a < 0 or not t:
            continue
        out.append([a, b if b is not None and b >= a else a, t])
    out.sort(key=lambda w: w[0])
    for w, nxt in zip(out, out[1:]):
        w[1] = min(w[1], nxt[0])
    return out


def parse_json(data: dict):
    segs = data.get("segments") or []
    words = data.get("words") or [w for s in segs for w in (s.get("words") or [])]
    segs, words = clean_segments(segs), clean_words(words)
    # secours si les temps semblent en millisecondes (durée connue, fin >> durée)
    dur = _num(data.get("duration"))
    if dur and words and words[-1][1] > dur * 10:
        for s in segs:
            s["start"], s["end"] = s["start"] / 1000, s["end"] / 1000
        for w in words:
            w[0], w[1] = w[0] / 1000, w[1] / 1000
    return segs, words


def load(srt: str | None, js: str | None):
    """-> {segments, words, source, warnings}. Un fichier absent/illisible n'empêche pas l'autre."""
    segs, words, warnings = [], [], []
    srt_ok = False
    if srt:
        try:
            segs = parse_srt(Path(srt).read_text(encoding="utf-8", errors="replace"))
            srt_ok = bool(segs)
        except OSError:
            warnings.append("Fichier SRT introuvable ou illisible.")
    if js:
        try:
            jsegs, words = parse_json(json.loads(Path(js).read_text(encoding="utf-8")))
            if not srt_ok:
                segs = jsegs
        except (OSError, ValueError, AttributeError):
            warnings.append("Fichier JSON introuvable ou invalide.")
    if not segs and words:  # JSON sans segments : regroupement par pauses / longueur
        cur = []
        for w in words:
            if cur and (w[0] - cur[-1][1] > 1.0 or len(cur) >= 20):
                segs.append({"start": cur[0][0], "end": cur[-1][1], "text": " ".join(x[2] for x in cur)})
                cur = []
            cur.append(w)
        if cur:
            segs.append({"start": cur[0][0], "end": cur[-1][1], "text": " ".join(x[2] for x in cur)})
    source = "srt" if srt_ok else "json" if segs else None
    return {"segments": segs, "words": words, "source": source, "warnings": warnings}
