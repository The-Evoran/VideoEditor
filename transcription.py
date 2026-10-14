from faster_whisper import WhisperModel
from pathlib import Path
import json

# =========================
# CONFIGURATION
# =========================

import sys

# Chemin passé en argument ; sinon valeur par défaut (workflow historique)
VIDEO = sys.argv[1] if len(sys.argv) > 1 else r"E:\Whisper\lyceen2.mp4"

MODEL_SIZE = "large-v3"
LANGUAGE = "fr"

# =========================
# FICHIERS
# =========================

VIDEO_PATH = Path(VIDEO)

# 2e argument optionnel : préfixe de sortie (le backend écrit en temporaire puis renomme)
BASE = Path(sys.argv[2]) if len(sys.argv) > 2 else VIDEO_PATH.with_name(VIDEO_PATH.stem + "_whisper")

OUTPUT_SRT = BASE.with_name(BASE.name + ".srt")
OUTPUT_JSON = BASE.with_name(BASE.name + ".json")

# =========================
# FORMAT TEMPS SRT
# =========================

def format_srt_time(seconds):
    total_ms = int(round(seconds * 1000))

    hours = total_ms // 3_600_000
    total_ms %= 3_600_000

    minutes = total_ms // 60_000
    total_ms %= 60_000

    secs = total_ms // 1000
    milliseconds = total_ms % 1000

    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


# =========================
# WHISPER
# =========================

print("Chargement de Whisper...")

model = WhisperModel(
    MODEL_SIZE,
    device="cuda",
    compute_type="float16"
)

print("Transcription...")

segments, info = model.transcribe(
    VIDEO,
    language=LANGUAGE,
    beam_size=5,
    vad_filter=True,
    word_timestamps=True
)

# =========================
# DONNEES JSON
# =========================

transcript = {
    "language": info.language,
    "language_probability": info.language_probability,
    "duration": info.duration,
    "segments": [],
    "words": []
}

# =========================
# SRT
# =========================

subtitle_number = 1

with open(
    OUTPUT_SRT,
    "w",
    encoding="utf-8-sig",
    newline="\r\n"
) as srt:

    for segment in segments:

        text = segment.text.strip()

        if not text:
            continue

        start = max(0, segment.start)
        end = max(start + 0.01, segment.end)

        # -------------------------
        # SRT
        # -------------------------

        srt.write(f"{subtitle_number}\r")
        srt.write(
            f"{format_srt_time(start)} --> "
            f"{format_srt_time(end)}\r"
        )
        srt.write(f"{text}\r\n")

        # -------------------------
        # JSON segment
        # -------------------------

        segment_data = {
            "id": segment.id,
            "start": start,
            "end": end,
            "text": text,
            "words": []
        }

        # -------------------------
        # WORD TIMESTAMPS
        # -------------------------

        if segment.words:

            for word in segment.words:

                word_text = word.word.strip()

                if not word_text:
                    continue

                word_data = {
                    "word": word_text,
                    "start": word.start,
                    "end": word.end,
                    "probability": word.probability
                }

                segment_data["words"].append(word_data)
                transcript["words"].append(word_data)

        transcript["segments"].append(segment_data)

        print(
            f"[{format_srt_time(start)} --> "
            f"{format_srt_time(end)}] {text}"
        )

        subtitle_number += 1


# =========================
# JSON
# =========================

with open(
    OUTPUT_JSON,
    "w",
    encoding="utf-8"
) as json_file:

    json.dump(
        transcript,
        json_file,
        ensure_ascii=False,
        indent=2
    )


print()
print("===================================")
print("TRANSCRIPTION TERMINÉE")
print("===================================")
print(f"SRT  : {OUTPUT_SRT}")
print(f"JSON : {OUTPUT_JSON}")
print(f"Langue : {info.language}")
print(f"Segments : {len(transcript['segments'])}")
print(f"Mots : {len(transcript['words'])}")