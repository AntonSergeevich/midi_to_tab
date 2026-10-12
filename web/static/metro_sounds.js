// Звуки метронома -- общие для мультитрека и плеера. Всё синтезируется на
// лету (Web Audio): ни одного файла качать не нужно, щелчок звучит ровно
// в назначенный момент. Выбор звука помнится в браузере.
(function () {
  const SOUNDS = {
    beep: 'Писк',
    wood: 'Деревяшка',
    clave: 'Клава',
    cowbell: 'Ковбелл',
    rim: 'Римшот',
    hat: 'Хай-хэт',
    kick: 'Бочка',
  };
  const KEY = 'naslux-metro-sound';
  const noises = new WeakMap();

  function noise(ctx) {
    let buffer = noises.get(ctx);
    if (!buffer) {
      buffer = ctx.createBuffer(1, Math.round(ctx.sampleRate * 0.3), ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
      noises.set(ctx, buffer);
    }
    return buffer;
  }

  // Огибающая: почти мгновенная атака и спад до тишины за decay секунд
  function env(ctx, when, peak, decay) {
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(peak, when + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, when + decay);
    return g;
  }

  function tone(ctx, dest, when, { type = 'sine', freq, to, peak, decay }) {
    const o = ctx.createOscillator();
    o.type = type;
    o.frequency.setValueAtTime(freq, when);
    if (to) o.frequency.exponentialRampToValueAtTime(to, when + decay * 0.6);
    const g = env(ctx, when, peak, decay);
    o.connect(g).connect(dest);
    o.start(when);
    o.stop(when + decay + 0.02);
  }

  function hiss(ctx, dest, when, { filter, freq, q = 1, peak, decay }) {
    const src = ctx.createBufferSource();
    src.buffer = noise(ctx);
    const f = ctx.createBiquadFilter();
    f.type = filter;
    f.frequency.value = freq;
    f.Q.value = q;
    const g = env(ctx, when, peak, decay);
    src.connect(f).connect(g).connect(dest);
    src.start(when);
    src.stop(when + decay + 0.02);
  }

  // Сильная доля (начало такта) -- выше и громче, остальные -- тише
  function play(ctx, dest, when, accent, kind = current()) {
    const a = accent ? 1 : 0.6;
    switch (kind) {
      case 'wood':
        tone(ctx, dest, when, { type: 'triangle', freq: accent ? 1250 : 950, peak: a, decay: 0.05 });
        hiss(ctx, dest, when, { filter: 'bandpass', freq: accent ? 1900 : 1500, q: 5, peak: a * 0.8, decay: 0.025 });
        break;
      case 'clave':
        tone(ctx, dest, when, { freq: accent ? 2600 : 2100, peak: a, decay: 0.045 });
        break;
      case 'cowbell': {
        const bp = ctx.createBiquadFilter();
        bp.type = 'bandpass';
        bp.frequency.value = accent ? 900 : 800;
        bp.Q.value = 1.2;
        bp.connect(dest);
        tone(ctx, bp, when, { type: 'square', freq: accent ? 600 : 540, peak: a * 0.8, decay: 0.22 });
        tone(ctx, bp, when, { type: 'square', freq: accent ? 890 : 800, peak: a * 0.8, decay: 0.22 });
        break;
      }
      case 'rim':
        tone(ctx, dest, when, { type: 'triangle', freq: accent ? 1800 : 1600, peak: a, decay: 0.035 });
        hiss(ctx, dest, when, { filter: 'bandpass', freq: 2500, q: 2, peak: a * 1.6, decay: 0.03 });
        break;
      case 'hat':
        hiss(ctx, dest, when, { filter: 'highpass', freq: 7000, peak: a, decay: accent ? 0.12 : 0.05 });
        break;
      case 'kick':
        tone(ctx, dest, when, { freq: accent ? 170 : 140, to: 45, peak: a, decay: 0.25 });
        hiss(ctx, dest, when, { filter: 'bandpass', freq: 3000, q: 1, peak: a * 0.25, decay: 0.01 });
        break;
      default:          // beep -- прежний писк
        tone(ctx, dest, when, { freq: accent ? 1760 : 1100, peak: a, decay: 0.06 });
    }
  }

  function current() {
    try {
      const saved = localStorage.getItem(KEY);
      if (saved && SOUNDS[saved]) return saved;
    } catch (error) { /* приватный режим */ }
    return 'beep';
  }

  function choose(kind) {
    if (!SOUNDS[kind]) return;
    try { localStorage.setItem(KEY, kind); } catch (error) { /* не страшно */ }
  }

  // Выход метронома, громкость в процентах. До 100% -- обычная громкость.
  // Выше -- щелчок «разгоняется» в мягкое насыщение: становится плотнее и
  // громче на слух, но выше 0 дБ не выходит и не хрипит, как перегруз.
  let curve = null;

  function output(ctx, percent = 100) {
    const drive = ctx.createGain();
    if (!curve) {
      curve = new Float32Array(4096);
      for (let i = 0; i < curve.length; i++) {
        const x = (i / (curve.length - 1)) * 2 - 1;
        curve[i] = Math.tanh(1.5 * x) / Math.tanh(1.5);
      }
    }
    const soft = ctx.createWaveShaper();
    soft.curve = curve;
    const level = ctx.createGain();
    drive.connect(soft).connect(level).connect(ctx.destination);
    drive.setVolume = (value) => {
      const v = Math.max(0, Number(value) || 0) / 100;
      drive.gain.value = v > 1 ? 1 + (v - 1) * 3 : 1;
      level.gain.value = Math.min(1, v);
    };
    drive.setVolume(percent);
    return drive;
  }

  // <select> со звуками: заполнить, отметить текущий, по выбору -- запомнить
  // и сразу дать послушать
  function bindSelect(select, preview) {
    select.innerHTML = Object.entries(SOUNDS)
      .map(([id, title]) => `<option value="${id}">${title}</option>`).join('');
    select.value = current();
    select.addEventListener('change', () => {
      choose(select.value);
      if (preview) preview(select.value);
    });
  }

  window.metroSound = { SOUNDS, play, current, choose, output, bindSelect };
})();
