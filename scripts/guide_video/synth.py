"""Демо-песня для видеоинструкции: 120 BPM, Am F C G, 48 с -- свои партии."""
import sys, numpy as np, subprocess, os
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
SR = 44100; BPM = 120; BEAT = 60 / BPM; BARS = 24; N = int(SR * BEAT * 4 * BARS)
rng = np.random.default_rng(7)
t = np.arange(N) / SR
def env(n, a=0.005, d=0.2):
    x = np.arange(n) / SR
    return np.minimum(1, x / a) * np.exp(-x / d)
def put(buf, at, sig):
    i = int(at * SR); j = min(len(buf), i + len(sig))
    if i < len(buf): buf[i:j] += sig[:j - i]
CH = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]   # Am F C G
f = lambda m: 440 * 2 ** ((m - 69) / 12)
drums = np.zeros(N); bass = np.zeros(N); gtr = np.zeros(N); keys = np.zeros(N); voc = np.zeros(N); me = np.zeros(N)
for bar in range(BARS):
    t0 = bar * 4 * BEAT; chord = CH[bar % 4]
    intro = bar < 2
    for b in range(4):
        tb = t0 + b * BEAT
        if not intro or b % 2 == 0:
            if b in (0, 2):
                n = int(0.3 * SR); x = np.arange(n) / SR
                put(drums, tb, np.sin(2 * np.pi * (50 + 90 * np.exp(-x * 30)) * x) * env(n, 0.001, 0.12) * 0.9)
            else:
                n = int(0.2 * SR); put(drums, tb, rng.standard_normal(n) * env(n, 0.001, 0.07) * 0.45)
        for h in range(2):
            n = int(0.05 * SR); noise = rng.standard_normal(n); noise = np.diff(noise, prepend=0)
            put(drums, tb + h * BEAT / 2, noise * env(n, 0.001, 0.015) * 0.18)
        if not intro:
            for h in range(2):
                n = int(BEAT / 2 * SR); x = np.arange(n) / SR; fr = f(chord[0] - 24)
                sig = sum(np.sin(2 * np.pi * fr * k * x) / k for k in range(1, 6)) * env(n, 0.004, 0.18)
                put(bass, tb + h * BEAT / 2, sig * 0.35)
        n = int(BEAT * SR); x = np.arange(n) / SR
        strum = sum(np.tanh(2.5 * sum(np.sin(2 * np.pi * f(m) * k * x) / k for k in range(1, 4)))
                    for m in chord) / 3
        put(gtr, tb + 0.012 * b, strum * env(n, 0.003, 0.35) * (0.25 if intro else 0.32))
    n = int(4 * BEAT * SR); x = np.arange(n) / SR
    pad = sum(np.sin(2 * np.pi * f(m + 12) * x) for m in chord) / 3
    keys[int(t0 * SR):int(t0 * SR) + n] += (pad * np.minimum(1, x / 0.4) * np.minimum(1, (4 * BEAT - x) / 0.4) * 0.12)[:max(0, min(n, N - int(t0 * SR)))]
def sing(buf, melody, start_bar, gain, vib=5.5):
    for k, (beat, m, dur) in enumerate(melody):
        at = start_bar * 4 * BEAT + beat * BEAT; n = int(dur * BEAT * SR); x = np.arange(n) / SR
        ph = 2 * np.pi * np.cumsum(f(m) * (1 + 0.004 * np.sin(2 * np.pi * vib * x))) / SR
        tone = np.sin(ph) + 0.5 * np.sin(2 * ph) + 0.3 * np.sin(3 * ph) + 0.12 * np.sin(5 * ph)
        shape = np.minimum(1, x / 0.04) * np.minimum(1, (dur * BEAT - x) / 0.08) * (0.8 + 0.2 * np.sin(2 * np.pi * 0.7 * x))
        put(buf, at, tone * shape * gain)
verse = [(0, 69, 1), (1, 72, 1), (2, 71, 1.5), (4, 69, 1), (5, 67, 1), (6, 64, 2), (8, 65, 1), (9, 69, 1),
         (10, 72, 1.5), (12, 71, 1), (13, 69, 1), (14, 67, 2)]
for s in range(2, BARS, 4):
    sing(voc, verse, s, 0.22)
    sing(me, [(b, m + (2 if k % 3 == 0 else 0), d) for k, (b, m, d) in enumerate(verse)], s, 0.26, vib=6.2)
parts = {"drums": drums, "bass": bass, "guitar": gtr, "other": keys, "vocals": voc}
def write(name, y):
    y = y / max(1e-9, np.abs(y).max()) * 0.85
    raw = os.path.join(out, name + ".f32"); y.astype(np.float32).tofile(raw)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", raw,
                    "-ac", "2", "-b:a", "160k", os.path.join(out, name + ".mp3")], check=True)
    os.remove(raw)
for k, v in parts.items(): write(k, v)
write("create_1", drums * 0.9 + bass + gtr * 0.8 + keys + voc * 1.1)
write("create_2", drums * 0.8 + bass * 0.9 + gtr + keys * 1.2 + me)
write("extra_mine01", me)
print("ok", N / SR)
