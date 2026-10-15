// Сведение «как у звукорежиссёра» одной кнопкой: голос не лежит поверх
// минусовки отдельным слоем, а встраивается в неё. Что делает живой
// звукач и что здесь повторено автоматически:
//
//  1. Уровни (gain staging): громкость голоса относительно музыки меряется
//     по-настоящему (LUFS, ITU-R BS.1770) и ставится по стилю: в попе голос
//     чуть громче музыки, в роке -- внутри гитарной стены, в рэпе -- впереди.
//  2. Цепочка на голос: срез гула (HPF), убрать «бубнёж» (~300 Гц),
//     разборчивость (~3 кГц), приглушить свист «с/ш» (~7 кГц), «воздух»
//     сверху, компрессор -- чтобы тихие слова не тонули, а громкие не лезли.
//  3. Ниша под голос: музыка чуть проваливается в полосе 2–4 кГц, где
//     разборчивость голоса, -- инструменты не спорят с ним.
//  4. Подмешивание (ducking): пока звучит голос, музыка на 1–3 дБ тише,
//     в паузах возвращается -- как рукой на фейдере.
//  5. Одно пространство: голос и (чуть-чуть) музыка уходят в общий зал с
//     предзадержкой и в дилей в темп песни; возвраты обрезаны снизу и
//     сверху, чтобы хвосты не мутили микс. Это главное, чтобы голос звучал
//     «в песне», а не «поверх».
//  6. Склейка (glue): мягкий компрессор на весь микс -- всё дышит вместе.
//  7. Громкость для публикации: -14 LUFS, как у треков в стримингах, пики
//     не выше -1 дБ (ограничитель с упреждением).
(function () {
  const STYLES = {
    pop: {
      title: 'Поп', text: 'Голос впереди: яркий, плотный, лёгкий дилей в темп',
      voice: 1, duck: 2, carve: 2.5, hpf: 100, mud: -2.5, presence: 3, air: 2.5, deess: -2.5, comp: [-24, 4],
      verb: { len: 1.8, decay: 3.2, wet: 0.13, pre: 0.03 }, delay: { beats: 0.5, wet: 0.08, fb: 0.25 },
    },
    rock: {
      title: 'Рок', text: 'Голос внутри плотной гитарной стены, короткая комната',
      voice: -1.5, duck: 1.5, carve: 3, hpf: 110, mud: -3, presence: 4, air: 1.5, deess: -2, comp: [-26, 5],
      verb: { len: 1.1, decay: 4, wet: 0.09, pre: 0.02 }, delay: { beats: 0.25, wet: 0.06, fb: 0.2 },
    },
    ballad: {
      title: 'Баллада', text: 'Голос крупным планом, большой зал и длинный дилей',
      voice: 1.5, duck: 1.5, carve: 2, hpf: 90, mud: -2, presence: 2.5, air: 3, deess: -2.5, comp: [-22, 3],
      verb: { len: 3, decay: 2.6, wet: 0.24, pre: 0.05 }, delay: { beats: 1, wet: 0.1, fb: 0.3 },
    },
    rap: {
      title: 'Рэп, речитатив', text: 'Сухо, громко и разборчиво — музыка уступает слову',
      voice: 3, duck: 3, carve: 3, hpf: 120, mud: -3, presence: 4, air: 2, deess: -3, comp: [-28, 6],
      verb: { len: 0.8, decay: 5, wet: 0.05, pre: 0.01 }, delay: null,
    },
    acoustic: {
      title: 'Акустика', text: 'Естественно, будто голос и гитара в одной комнате',
      voice: 0, duck: 1, carve: 1.5, hpf: 80, mud: -1.5, presence: 2, air: 1.5, deess: -2, comp: [-20, 2.5],
      verb: { len: 1.4, decay: 3.5, wet: 0.14, pre: 0.02 }, delay: null,
    },
  };
  const TARGET = -14;          // LUFS готового трека
  const CEILING = -1;          // дБ, пики не выше
  const STAGE = -16;           // LUFS суммы до склейки: компрессор работает одинаково на любом треке

  const irs = new WeakMap();
  function impulse(c, seconds, decay) {
    let byLen = irs.get(c);
    if (!byLen) { byLen = {}; irs.set(c, byLen); }
    const id = `${seconds}:${decay}`;
    if (byLen[id]) return byLen[id];
    const n = Math.round(c.sampleRate * seconds);
    const buf = c.createBuffer(2, n, c.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      // ранние отражения плотнее, хвост -- экспоненциально; каналы разные -- ширина
      for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, decay);
    }
    byLen[id] = buf;
    return buf;
  }

  function filter(c, type, freq, q = 0.7, gain = 0) {
    const f = c.createBiquadFilter();
    f.type = type;
    f.frequency.value = Math.min(freq, c.sampleRate / 2 - 100);
    f.Q.value = q;
    f.gain.value = gain;
    return f;
  }

  function compressor(c, threshold, ratio, attack, release, knee = 6) {
    const k = c.createDynamicsCompressor();
    k.threshold.value = threshold;
    k.ratio.value = ratio;
    k.attack.value = attack;
    k.release.value = release;
    k.knee.value = knee;
    return k;
  }

  function line(nodes) {
    for (let i = 0; i + 1 < nodes.length; i++) nodes[i].connect(nodes[i + 1]);
    return { input: nodes[0], output: nodes[nodes.length - 1] };
  }

  // Цепочка голоса в сведении: {input, output}
  function voiceChain(c, style) {
    const s = STYLES[style] || STYLES.pop;
    return line([
      c.createGain(),
      filter(c, 'highpass', s.hpf, 0.7),
      filter(c, 'peaking', 300, 1, s.mud),
      filter(c, 'peaking', 3000, 0.9, s.presence),
      filter(c, 'peaking', 7200, 2.5, s.deess),
      filter(c, 'highshelf', 10000, 0.7, s.air),
      compressor(c, s.comp[0], s.comp[1], 0.005, 0.12),
      c.createGain(),
    ]);
  }

  // Шина сведения на контексте c, выход -- в out. bpm -- для дилея в темп.
  // Возвращает {voice, music, duck}: голосовые дорожки подключать в voice,
  // остальные -- в music; duck -- громкость музыки (подмешивание).
  function bus(c, style, out, bpm) {
    const s = STYLES[style] || STYLES.pop;
    const glue = compressor(c, -18, 2, 0.03, 0.25, 8);
    glue.connect(out);
    const voice = c.createGain();
    const music = c.createGain();
    const duck = c.createGain();
    music.connect(filter(c, 'peaking', 2900, 1, -s.carve)).connect(duck).connect(glue);
    voice.connect(glue);
    // возвраты обрезаны снизу и сверху -- хвосты не мутят микс
    const back = filter(c, 'highpass', 250, 0.7);
    back.connect(filter(c, 'lowpass', 9000, 0.7)).connect(glue);
    // общий зал: голос и чуть-чуть музыки -- одно пространство
    const pre = c.createDelay(0.2);
    pre.delayTime.value = s.verb.pre;
    const verb = c.createConvolver();
    verb.buffer = impulse(c, s.verb.len, s.verb.decay);
    pre.connect(verb).connect(back);
    const voiceSend = c.createGain();
    voiceSend.gain.value = s.verb.wet;
    voice.connect(voiceSend).connect(pre);
    const musicSend = c.createGain();
    musicSend.gain.value = 0.035;
    music.connect(musicSend).connect(pre);
    if (s.delay && bpm) {
      const echo = c.createDelay(4);
      echo.delayTime.value = Math.min(3.9, (60 / bpm) * s.delay.beats * 2);   // в восьмых/четвертях
      const fb = c.createGain();
      fb.gain.value = s.delay.fb;
      const dark = filter(c, 'lowpass', 5000, 0.7);
      echo.connect(dark).connect(fb).connect(echo);
      const send = c.createGain();
      send.gain.value = s.delay.wet;
      voice.connect(send).connect(echo);
      dark.connect(back);
    }
    return { voice, music, duck, glue };
  }

  // ---------------------------------------------- громкость (BS.1770)
  // Коэффициенты фильтра K-взвешивания для любой частоты дискретизации
  function kCoefs(rate) {
    const shelf = (() => {
      const f0 = 1681.974450955533; const G = 3.999843853973347; const Q = 0.7071752369554196;
      const K = Math.tan(Math.PI * f0 / rate); const Vh = Math.pow(10, G / 20); const Vb = Math.pow(Vh, 0.4996667741545416);
      const a0 = 1 + K / Q + K * K;
      return { b: [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0],
        a: [2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0] };
    })();
    const high = (() => {
      const f0 = 38.13547087602444; const Q = 0.5003270373238773;
      const K = Math.tan(Math.PI * f0 / rate);
      const a0 = 1 + K / Q + K * K;
      return { b: [1, -2, 1], a: [2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0] };
    })();
    return [shelf, high];
  }

  function biquad(x, { b, a }) {
    const y = new Float32Array(x.length);
    let x1 = 0; let x2 = 0; let y1 = 0; let y2 = 0;
    for (let i = 0; i < x.length; i++) {
      const v = b[0] * x[i] + b[1] * x1 + b[2] * x2 - a[0] * y1 - a[1] * y2;
      x2 = x1; x1 = x[i]; y2 = y1; y1 = v;
      y[i] = v;
    }
    return y;
  }

  // Средняя мощность K-взвешенного сигнала по блокам 400 мс с шагом 100 мс
  function blocks(channels, rate) {
    const k = kCoefs(rate);
    const weighted = channels.map((ch) => biquad(biquad(ch, k[0]), k[1]));
    const size = Math.round(rate * 0.4);
    const hop = Math.round(rate * 0.1);
    const n = weighted[0].length;
    // префиксные суммы квадратов -- блоки считаются за O(n)
    const sums = weighted.map((w) => {
      const s = new Float64Array(n + 1);
      for (let i = 0; i < n; i++) s[i + 1] = s[i] + w[i] * w[i];
      return s;
    });
    const out = [];
    for (let at = 0; at + size <= n; at += hop) {
      let z = 0;
      sums.forEach((s) => { z += (s[at + size] - s[at]) / size; });
      out.push(z);
    }
    return out;
  }

  const lufsOf = (z) => -0.691 + 10 * Math.log10(z + 1e-12);

  // Интегральная громкость, LUFS (с абсолютным и относительным порогом)
  function loudness(channels, rate) {
    const z = blocks(channels, rate);
    const loud = z.filter((v) => lufsOf(v) > -70);
    if (!loud.length) return -Infinity;
    const mean = (list) => list.reduce((s, v) => s + v, 0) / list.length;
    const gate = lufsOf(mean(loud)) - 10;
    const kept = loud.filter((v) => lufsOf(v) > gate);
    return lufsOf(mean(kept.length ? kept : loud));
  }

  // Где звучит голос: огибающая подмешивания для музыки, 50 точек в секунду
  // (1 -- музыка как есть, меньше -- тише). Атака 40 мс, отпускание 300 мс.
  function duckCurve(channel, rate, depthDb, seconds) {
    const step = Math.round(rate / 50);
    const n = Math.ceil(seconds * 50);
    const level = new Float32Array(n);
    for (let k = 0; k < n; k++) {
      let s = 0; let m = 0;
      for (let i = k * step; i < Math.min(channel.length, (k + 1) * step); i++) { s += channel[i] * channel[i]; m++; }
      level[k] = m ? 10 * Math.log10(s / m + 1e-12) : -120;
    }
    const sorted = [...level].filter((v) => v > -80).sort((x, y) => x - y);
    const curve = new Float32Array(n).fill(1);
    if (sorted.length < 10) return curve;
    const loudest = sorted[Math.floor(sorted.length * 0.95)];
    const low = Math.pow(10, -depthDb / 20);
    const down = 1 - Math.exp(-1 / (0.04 * 50));
    const up = 1 - Math.exp(-1 / (0.3 * 50));
    let g = 1;
    for (let k = 0; k < n; k++) {
      const want = level[k] > loudest - 18 ? low : 1;
      g += (want - g) * (want < g ? down : up);
      curve[k] = g;
    }
    return curve;
  }

  // Ограничитель с упреждением: пики не выше ceiling (в долях), без щелчков
  function limit(channels, rate, ceiling) {
    const n = channels[0].length;
    const L = Math.max(1, Math.round(rate * 0.005));
    const need = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let p = 0;
      for (const ch of channels) p = Math.max(p, Math.abs(ch[i]));
      need[i] = p > ceiling ? ceiling / p : 1;
    }
    // минимум по окну [i, i+L) -- очередь с монотонностью, O(n)
    const m = new Float32Array(n);
    const q = new Int32Array(n);
    let head = 0; let tail = 0;
    for (let i = n - 1; i >= 0; i--) {
      while (tail > head && need[q[tail - 1]] >= need[i]) tail--;
      q[tail++] = i;
      while (q[head] >= i + L) head++;
      m[i] = need[q[head]];
    }
    // сглаживание средним по L -- огибающая плавная и всё равно не выше нужной
    let acc = 0;
    for (let i = 0; i < n; i++) {
      acc += m[i];
      if (i >= L) acc -= m[i - L];
      const g = acc / Math.min(i + 1, L);
      for (const ch of channels) ch[i] *= g;
    }
  }

  // Готовый микс -- к громкости публикации: -14 LUFS, пики не выше -1 дБ
  function master(buffer) {
    const channels = [];
    for (let c = 0; c < buffer.numberOfChannels; c++) channels.push(buffer.getChannelData(c));
    const now = loudness(channels, buffer.sampleRate);
    if (!Number.isFinite(now)) return { before: now, after: now };
    const gain = Math.pow(10, (TARGET - now) / 20);
    channels.forEach((ch) => { for (let i = 0; i < ch.length; i++) ch[i] *= gain; });
    limit(channels, buffer.sampleRate, Math.pow(10, CEILING / 20));
    return { before: now, after: loudness(channels, buffer.sampleRate) };
  }

  window.mixBus = { STYLES, TARGET, STAGE, voiceChain, bus, loudness, duckCurve, limit, master };
})();
