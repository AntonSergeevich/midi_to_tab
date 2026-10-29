import json, os, shutil, subprocess, sys, time
import numpy as np
D, A = sys.argv[1], sys.argv[2]
os.environ["MIDI2TAB_DATA"] = D; os.environ["MIDI2TAB_SECRET"] = "x"
sys.path.insert(0, "/home/user/midi_to_tab")
from web import app as M
st, studio = M.storage, M.studio
u = st.ensure_user(None)
st.register(u.id, "anton@naslux.ru", "x" * 40)
with st._connect() as c:
    c.execute("UPDATE users SET unlimited = 1 WHERE id = ?", (u.id,))

def cover(path, hue):
    w = 640; y, x = np.mgrid[0:w, 0:w] / w
    r = np.clip(0.08 + 0.9 * np.exp(-((x - .62) ** 2 + (y - .38) ** 2) / .03) * hue[0] + y * .35 * hue[1], 0, 1)
    g = np.clip(0.06 + 0.55 * np.exp(-((x - .62) ** 2 + (y - .38) ** 2) / .03) * hue[0] + y * .12, 0, 1)
    b = np.clip(0.12 + 0.25 * (1 - y) + 0.15 * np.exp(-((x - .3) ** 2 + (y - .8) ** 2) / .05), 0, 1)
    img = (np.dstack([r, g, b]) * 255 + np.random.default_rng(1).normal(0, 6, (w, w, 3))).clip(0, 255).astype(np.uint8)
    # силуэт гаража и снег
    img[int(w * .72):, :] = (img[int(w * .72):, :] * 0.25).astype(np.uint8)
    img[int(w * .60):int(w * .72), int(w * .18):int(w * .58)] = 18
    img[int(w * .64):int(w * .72), int(w * .30):int(w * .46)] = [200, 140, 70]
    pts = np.random.default_rng(3).integers(0, w, (500, 2))
    img[pts[:, 1], pts[:, 0]] = 235
    raw = path + ".rgb"; img.tofile(raw)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{w}", "-i", raw,
                    "-q:v", "3", path], check=True)
    os.remove(raw)

def job(name, settings, files, extra_files=(), **result):
    j = st.create_job(u.id, name, settings)
    folder = M.studio_runner.folder(j.id); os.makedirs(folder, exist_ok=True)
    for src, dst in list(files) + list(extra_files):
        shutil.copy(os.path.join(A, src), os.path.join(folder, dst))
    st.update_job(j.id, status="done", progress=100, stage="Готово",
                  result={"files": [{"name": dst, "label": studio.label_of(settings["mode"], dst), "seconds": 48}
                                    for _, dst in files], **result})
    return j

lyrics = """[Verse]
За окном метель и минус двадцать пять,
В гараже обогреватель тянет ток опять,
Пальцы липнут к струнам, пар идёт из рта,
Но гремит над крышей наша громкота.

[Chorus]
Эй, зажигай, пусть сугробы до плеча,
Мы горим, как в морозе свеча!"""
style = "Гаражный рок, мрачный и холодный звук, перегруженные гитары, живые барабаны, мужской хрипловатый вокал, 120 BPM"
song = job("Гаражный рок", {"kind": "studio", "mode": "create", "title": "Песня с нуля", "voice": "male",
                            "input": {"prompt": style, "lyrics": lyrics}},
           [("create_1.mp3", "create_1.mp3"), ("create_2.mp3", "create_2.mp3")],
           [("extra_mine01.mp3", "extra_mine01.mp3")],
           lyrics=lyrics, extras=[{"name": "extra_mine01.mp3", "label": "Мой голос", "for": "create_1.mp3"}])
cover(os.path.join(M.studio_runner.folder(song.id), "cover.jpg"), (1, 1))
stems = job("Гаражный рок", {"kind": "studio", "mode": "stems", "title": "Партии", "from": song.id,
                             "sourceFile": "create_1.mp3"},
            [(n + ".mp3", n + ".mp3") for n in ("vocals", "drums", "bass", "guitar", "other")])
time.sleep(0.01)
up = job("Репетиция 24.10", {"kind": "studio", "mode": "upload", "title": "Свой трек", "rights": {"kind": "own"}},
         [("create_2.mp3", "track.mp3")])
cover(os.path.join(M.studio_runner.folder(up.id), "cover.jpg"), (0.6, 0.3))
json.dump({"cookie": M.signer.dumps(u.id), "song": song.id, "stems": stems.id}, open(os.path.join(D, "demo.json"), "w"))
print("ok", song.id)
