"""Как добиться нужного голоса у Mureka song/generate: живой замер.

Для каждого способа (разметка строк, параметр gender, описание) заказывается
одна песня; вокал выделяется Demucs, высота голоса (pyin) по окнам 2 с
делит время на «мужской» (< 165 Гц) и «женский» (> 185 Гц). Итог -- доли и
лента по времени: M -- мужской, F -- женский, . -- тишина/неясно.

    MUREKA_API_KEY=... python scripts/voice_test.py --out voice
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web import studio  # noqa: E402

LINES = {
    "v1": ["Я включаю свет в пустом окне", "Город спит, а песня бьётся в мне"],
    "v2": ["Ты рисуешь ноты на стекле", "Каждый звук горит в моём тепле"],
    "ch": ["Мы звучим, мы звучим до утра", "Это наша с тобой игра"],
}


def text(style: str) -> str:
    if style == "plain":
        return (f"[Verse]\n{chr(10).join(LINES['v1'])}\n{chr(10).join(LINES['v2'])}\n"
                f"[Chorus]\n{chr(10).join(LINES['ch'])}\n{chr(10).join(LINES['ch'])}")
    if style == "lines":
        return ("[Verse]\n" + "\n".join(f"(Male) {l}" for l in LINES["v1"]) + "\n"
                + "\n".join(f"(Female) {l}" for l in LINES["v2"]) + "\n[Chorus]\n"
                + "\n".join(f"(Male) {l}" for l in LINES["ch"]) + "\n"
                + "\n".join(f"(Female) {l}" for l in LINES["ch"]))
    if style == "sections":
        return ("[Verse 1: Male Vocal]\n" + "\n".join(LINES["v1"])
                + "\n\n[Verse 2: Female Vocal]\n" + "\n".join(LINES["v2"])
                + "\n\n[Chorus: Male Vocal]\n" + "\n".join(LINES["ch"])
                + "\n\n[Chorus: Female Vocal]\n" + "\n".join(LINES["ch"]))
    raise ValueError(style)


CASES = [
    ("lines_duet_prompt", "lines", None, "pop rock, duet, male and female vocals"),
    ("sections_duet_prompt", "sections", None, "pop rock, duet, male and female vocals"),
    ("lines_gender_female", "lines", "female", "pop rock, duet, male and female vocals"),
    ("plain_gender_female", "plain", "female", "pop rock, female vocals"),
    ("plain_prompt_female", "plain", None, "pop rock, female vocals"),
]


def gender_timeline(path: str) -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(path, sr=16000, mono=True)
    f0, voiced, _ = librosa.pyin(y, fmin=70, fmax=600, sr=sr, frame_length=1024)
    hop = 256 / sr
    marks, male, female = [], 0, 0
    per = int(2.0 / hop)
    for start in range(0, len(f0), per):
        chunk = f0[start:start + per][voiced[start:start + per]]
        if len(chunk) < per * 0.25:
            marks.append(".")
            continue
        median = float(np.median(chunk))
        mark = "M" if median < 165 else "F" if median > 185 else "?"
        marks.append(mark)
        male += mark == "M"
        female += mark == "F"
    total = max(1, male + female)
    return {"male": round(male / total, 2), "female": round(female / total, 2),
            "timeline": "".join(marks)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="voice")
    parser.add_argument("--cases", default="")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    chosen = [c for c in CASES if not args.cases or c[0] in args.cases.split(",")]
    report = {}
    for name, style, gender, prompt in chosen:
        body = {"lyrics": text(style), "prompt": prompt, "model": studio.MUREKA_SONG_MODEL, "n": 1}
        if gender:
            body["gender"] = gender
        task = studio.mureka_call("POST", "/v1/song/generate", body)
        while True:
            time.sleep(5)
            status = studio.mureka_call("GET", f"/v1/song/query/{task['id']}")
            if status.get("status") not in studio.MUREKA_STATES:
                break
        if status.get("status") != "succeeded":
            print(name, "не удалось:", status.get("failed_reason") or status.get("status"), flush=True)
            continue
        song = os.path.join(args.out, f"{name}.mp3")
        studio.download(status["choices"][0]["url"], song)
        subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
                        "-o", "sep", song], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = gender_timeline(f"sep/htdemucs/{name}/vocals.wav")
        report[name] = {"gender": gender, "prompt": prompt, "lyrics": style, **result}
        print(f"{name:24} мужской {result['male']:.0%} женский {result['female']:.0%}  {result['timeline']}",
              flush=True)
    Path(args.out, "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
