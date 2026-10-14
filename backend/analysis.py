"""Phase 4 : analyse du transcript par un modèle Ollama local (logique pure, sans base de données).

Le modèle ne renvoie jamais de timecode : il cite le passage, et c'est `align` qui retrouve les
temps dans le transcript original (mots du JSON si disponibles, sinon segments du SRT).
"""
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from difflib import SequenceMatcher

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
PROMPT_VERSION = 1  # à incrémenter si le prompt ou le schéma change : invalide les analyses reprenables

CATEGORIES = {
    "topic": "Sujet principal / discussion importante",
    "argument": "Argumentaire ou explication",
    "punchline": "Punchline, réaction, échange marquant",
    "controversy": "Controverse ou désaccord",
    "announcement": "Information ou annonce importante",
    "standalone": "Passage autonome pour un clip",
    "review": "À vérifier par un humain",
}
CONFIDENCE = ["high", "medium", "low"]

SCHEMA = {
    "type": "object",
    "properties": {"moments": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "justification": {"type": "string"},
            "passage": {"type": "string"},
            "score": {"type": "integer", "minimum": 1, "maximum": 10},
            "confidence": {"type": "string", "enum": CONFIDENCE},
            "needs_review": {"type": "boolean"},
        },
        "required": ["title", "summary", "category", "justification", "passage", "score", "confidence", "needs_review"],
    }}},
    "required": ["moments"],
}

SYSTEM = f"""Tu aides un monteur à repérer des passages intéressants dans la transcription d'une VOD (stream) en français.
On te donne un extrait (une ligne par segment de sous-titre, sans horodatage). Renvoie un JSON {{"moments": [...]}}.
Règles :
- Ne propose que les passages réellement intéressants pour un montage ; une liste vide est une réponse valide. Pas de remplissage.
- Au plus 3 passages par extrait, uniquement les meilleurs.
- Ignore les simples mentions, les banalités et le bavardage ; ne répète pas le même passage.
- "passage" : citation EXACTE, copiée mot pour mot depuis l'extrait (1 à 8 phrases consécutives), sans rien reformuler ni ajouter. Ne donne AUCUN timecode.
- "title" court ; "summary" factuel (ce qui est dit, sans interprétation) ; "justification" : l'intérêt éditorial.
- "category" parmi : {", ".join(f"{k} ({v})" for k, v in CATEGORIES.items())}.
- "score" de 1 (faible) à 10 (excellent) ; "confidence" high/medium/low ; "needs_review" true si le passage est ambigu, incomplet ou douteux.
- L'extrait peut commencer ou finir au milieu d'une idée : n'invente pas le contexte manquant."""


class OllamaError(Exception):
    pass


class Cancelled(Exception):
    pass


# ---------- client Ollama (API officielle, urllib uniquement) ----------

def _open(path, body=None, timeout=10):
    req = urllib.request.Request(OLLAMA_URL + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except Exception:
            msg = None
        raise OllamaError(f"Ollama a refusé la requête ({e.code}) : {msg or e.reason}") from e
    except (urllib.error.URLError, OSError) as e:
        raise OllamaError(f"Ollama injoignable sur {OLLAMA_URL} (est-il lancé ?).") from e


def list_models():
    """-> [{name, size, context, thinking}] ; lève OllamaError si Ollama est indisponible."""
    try:
        with _open("/api/tags") as r:
            data = json.loads(r.read())
        return [{"name": m["name"], "size": m.get("size"),
                 "context": (m.get("details") or {}).get("context_length"),
                 "thinking": "thinking" in (m.get("capabilities") or [])}
                for m in data["models"] if "embedding" not in (m.get("capabilities") or [])]
    except (ValueError, KeyError, TypeError) as e:
        raise OllamaError("Réponse inattendue d'Ollama.") from e


def chat(model, user, num_ctx, cancel, supports_thinking=False):
    """Appel en flux (annulable entre deux fragments) ; renvoie le texte brut du modèle (JSON contraint par SCHEMA)."""
    body = {"model": model, "stream": True, "format": SCHEMA,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            "options": {"temperature": 0.2, "num_ctx": num_ctx}}
    if supports_thinking:
        body["think"] = False  # sorties structurées : pas de phase de raisonnement
    out = []
    with _open("/api/chat", body, timeout=600) as r:  # timeout de lecture, large : chargement du modèle
        for line in r:
            if cancel.is_set():
                raise Cancelled()
            try:
                d = json.loads(line)
            except ValueError as e:
                raise OllamaError("Flux Ollama illisible.") from e
            if d.get("error"):
                raise OllamaError(f"Erreur Ollama : {d['error']}")
            out.append((d.get("message") or {}).get("content", ""))
            if d.get("done"):
                break
    return "".join(out)


# ---------- découpage en blocs ----------

def _sentence_end(t):
    return bool(re.search(r"[.!?…]['\")»]*\s*$", t))


def make_chunks(segs, window, overlap, max_chars):
    """Blocs [{i0, i1, start, end}] (segments i0..i1-1). Jamais de coupe au milieu d'un segment ;
    un bloc dépassant `window` est prolongé (max +20 %) jusqu'à une fin de phrase. Chevauchement = `overlap` s."""
    chunks, i, n = [], 0, len(segs)
    while i < n:
        j, chars = i, 0
        while j < n:
            chars += len(segs[j]["text"]) + 1
            j += 1
            if chars > max_chars or segs[j - 1]["end"] - segs[i]["start"] >= window:
                break
        while (j < n and chars <= max_chars and not _sentence_end(segs[j - 1]["text"])
               and segs[j - 1]["end"] - segs[i]["start"] < window * 1.2):
            chars += len(segs[j]["text"]) + 1
            j += 1
        chunks.append({"i0": i, "i1": j, "start": segs[i]["start"], "end": segs[j - 1]["end"]})
        if j >= n:
            break
        nxt = j
        while nxt - 1 > i and segs[nxt - 1]["start"] >= segs[j - 1]["end"] - overlap:
            nxt -= 1
        i = max(nxt, i + 1)
    return chunks


def chunk_prompt(segs, chunk):
    return "Extrait :\n" + "\n".join(s["text"] for s in segs[chunk["i0"]:chunk["i1"]])


# ---------- parsing de la réponse du modèle ----------

def parse_response(raw):
    """-> liste de moments valides (champs normalisés). Lève ValueError si la réponse n'est pas exploitable."""
    txt = raw.strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
    try:
        data = json.loads(txt)
    except ValueError:
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            raise ValueError("Réponse du modèle non JSON.")
        data = json.loads(m.group(0))  # peut relever ValueError
    items = data.get("moments") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Champ « moments » absent.")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        passage = str(it.get("passage") or "").strip()
        title = str(it.get("title") or "").strip()
        try:
            score = int(round(float(it.get("score"))))
        except (TypeError, ValueError):
            continue
        if not passage or not title or not 1 <= score <= 10:
            continue
        cat = it.get("category")
        conf = it.get("confidence")
        out.append({
            "title": title[:200], "summary": str(it.get("summary") or "").strip(),
            "category": cat if cat in CATEGORIES else "review", "justification": str(it.get("justification") or "").strip(),
            "passage": passage, "score": score, "confidence": conf if conf in CONFIDENCE else "low",
            "needs_review": bool(it.get("needs_review")) or cat not in CATEGORIES,
        })
    return out


# ---------- alignement sur le transcript original ----------

def tokens(text):
    t = unicodedata.normalize("NFD", text.lower())
    return re.findall(r"\w+", "".join(c for c in t if not unicodedata.combining(c)))


def build_tokens(segs, words, start, end):
    """Jetons normalisés (tok, start, end) du transcript entre start et end : mots du JSON si dispo, sinon segments."""
    out = []
    if words:
        for w in words:
            if w[1] >= start and w[0] <= end:
                out += [(t, w[0], w[1]) for t in tokens(w[2])]
    else:
        for s in segs:
            if s["end"] >= start and s["start"] <= end:
                out += [(t, s["start"], s["end"]) for t in tokens(s["text"])]
    return out


def align(passage, toks, min_cover=0.6):
    """Retrouve `passage` dans `toks` -> {start, end, cover, i0, i1} ou None. Tolère erreurs de transcription,
    ponctuation et répétitions : ancrage sur les plus longs blocs communs, regroupés autour d'une même zone."""
    p = tokens(passage)
    if not p or not toks:
        return None
    seq = [t[0] for t in toks]
    blocks = [b for b in SequenceMatcher(None, p, seq, autojunk=False).get_matching_blocks()
              if b.size >= (1 if len(p) < 3 else 2)]
    best = None
    for k in range(len(blocks)):  # grappes de blocs séparés de moins de len(p) jetons ; on garde la plus couvrante
        grp = [blocks[k]]
        for b in blocks[k + 1:]:
            if b.b - (grp[-1].b + grp[-1].size) <= len(p):
                grp.append(b)
        matched = sum(b.size for b in grp)
        if best is None or matched > best[0]:
            best = (matched, grp)
    if not best:
        return None
    matched, grp = best
    i0, i1 = grp[0].b, grp[-1].b + grp[-1].size
    cover = matched / len(p)
    if cover < min_cover or (i1 - i0) > 1.6 * len(p) + 5:
        return None
    return {"start": toks[i0][1], "end": toks[i1 - 1][2], "cover": cover, "i0": i0, "i1": i1}


def locate(det, segs, words, chunk):
    """Complète une détection avec start/end (None si non alignée) et passage tiré du transcript original."""
    toks = build_tokens(segs, words, chunk["start"] - 1, chunk["end"] + 1)
    a = align(det["passage"], toks)
    det = {**det, "model_passage": det["passage"], "hint": [chunk["start"], chunk["end"]]}
    if not a:
        return {**det, "start": None, "end": None, "aligned": False, "needs_review": True}
    if words:
        txt = " ".join(w[2] for w in words if w[0] >= a["start"] - 1e-6 and w[1] <= a["end"] + 1e-6)
        # les mots Whisper séparent « c »/« 'est » et « peut »/« -être » : on recolle l'élision et le trait d'union
        det["passage"] = re.sub(r"\s+(['’-])(?=\w)", lambda m: m.group(1), txt) or det["passage"]
    return {**det, "start": a["start"], "end": a["end"], "aligned": True,
            "needs_review": det["needs_review"] or a["cover"] < 0.8}


# ---------- fusion des doublons dus au chevauchement ----------

def _same(a, b, gap=5.0):
    if a["aligned"] and b["aligned"]:
        inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
        small = max(min(a["end"] - a["start"], b["end"] - b["start"]), 0.001)
        if inter > 0 and inter / small >= 0.5:
            return True  # même passage détecté deux fois
        if a["category"] == b["category"] and -inter <= gap:  # proches : même sujet seulement si les titres se ressemblent
            ta, tb = set(tokens(a["title"])), set(tokens(b["title"]))
            return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.5
        return False
    if a["aligned"] != b["aligned"]:
        return False
    return SequenceMatcher(None, tokens(a["model_passage"]), tokens(b["model_passage"]), autojunk=False).ratio() >= 0.8


def merge(dets):
    """dets : détections localisées. -> moments fusionnés ; chaque groupe garde ses détections dans `sources`,
    les bornes sont l'union des bornes originales, le texte est celui de la meilleure détection."""
    groups = []
    for d in sorted(dets, key=lambda d: (d["start"] is None, d["start"] or 0)):
        for g in groups:
            if any(_same(d, m) for m in g):
                g.append(d)
                break
        else:
            groups.append([d])
    out = []
    for g in groups:
        best = max(g, key=lambda d: (d["score"], d["aligned"], len(d["passage"])))
        al = [d for d in g if d["aligned"]]
        out.append({
            **{k: best[k] for k in ("title", "summary", "category", "justification", "passage", "score", "confidence", "hint")},
            "start": min(d["start"] for d in al) if al else None, "end": max(d["end"] for d in al) if al else None,
            "aligned": bool(al), "needs_review": best["needs_review"] or not al,
            "sources": [{k: d[k] for k in ("title", "category", "score", "model_passage", "start", "end", "aligned")} for d in g],
        })
    return sorted(out, key=lambda m: (m["start"] is None, m["start"] or 0))
