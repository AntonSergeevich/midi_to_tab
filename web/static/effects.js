// Эффекты дорожки мультитрека -- как в Audition: у дорожки цепочка
// эффектов, сначала пустая; «＋ Добавить эффект» -- выбрать какой, у каждого
// свои ручки, вкл/выкл, порядок. Всё на Web Audio: звучит сразу и так же --
// в «Скачать микс» (OfflineAudioContext), исходная запись не меняется.
//
//   laneFx.TYPES                -- какие эффекты есть и их ручки
//   laneFx.make(type)           -- новый эффект с настройками по умолчанию
//   laneFx.build(c, effects)    -- цепочка на контексте c: {input, output, set(id, p)}
//   laneFx.clean(effects)       -- проверенная копия (из памяти браузера)
window.laneFx = (() => {
  const TYPES = {
    eq: {
      title: 'Эквалайзер', text: '5 полос: убрать лишнее, добавить нужное',
      params: [
        { k: 'low', label: 'Низ · 100 Гц', min: -12, max: 12, step: 0.5, unit: 'дБ', def: 0 },
        { k: 'lowmid', label: 'Нижняя середина · 300 Гц', min: -12, max: 12, step: 0.5, unit: 'дБ', def: 0 },
        { k: 'mid', label: 'Середина · 1 кГц', min: -12, max: 12, step: 0.5, unit: 'дБ', def: 0 },
        { k: 'pres', label: 'Разборчивость · 3,5 кГц', min: -12, max: 12, step: 0.5, unit: 'дБ', def: 0 },
        { k: 'air', label: 'Воздух · 10 кГц', min: -12, max: 12, step: 0.5, unit: 'дБ', def: 0 },
      ],
    },
    hpf: {
      title: 'Срез низов', text: 'Убирает гул, топот, «бубнёж» микрофона',
      params: [{ k: 'freq', label: 'Частота среза', min: 20, max: 600, step: 5, unit: 'Гц', def: 90 }],
    },
    lpf: {
      title: 'Срез верхов', text: 'Убирает шипение, делает звук мягче и «дальше»',
      params: [{ k: 'freq', label: 'Частота среза', min: 1000, max: 20000, step: 100, unit: 'Гц', def: 12000 }],
    },
    comp: {
      title: 'Компрессор', text: 'Выравнивает громкость: тихое громче, громкое тише',
      params: [
        { k: 'threshold', label: 'Порог', min: -60, max: 0, step: 1, unit: 'дБ', def: -20 },
        { k: 'ratio', label: 'Степень сжатия', min: 1, max: 20, step: 0.5, unit: ':1', def: 4 },
        { k: 'attack', label: 'Атака', min: 1, max: 100, step: 1, unit: 'мс', def: 10 },
        { k: 'release', label: 'Восстановление', min: 30, max: 800, step: 10, unit: 'мс', def: 160 },
        { k: 'makeup', label: 'Усиление после', min: -6, max: 18, step: 0.5, unit: 'дБ', def: 0 },
      ],
    },
    reverb: {
      title: 'Реверберация', text: 'Пространство: от комнаты до собора',
      params: [
        { k: 'size', label: 'Размер (длина хвоста)', min: 0.3, max: 6, step: 0.1, unit: 'с', def: 1.8 },
        { k: 'mix', label: 'Сколько эффекта', min: 0, max: 100, step: 1, unit: '%', def: 25 },
      ],
    },
    delay: {
      title: 'Дилей (эхо)', text: 'Повторы звука: от «удвоения» до длинного эха',
      params: [
        { k: 'time', label: 'Время повтора', min: 30, max: 1500, step: 5, unit: 'мс', def: 350 },
        { k: 'feedback', label: 'Сколько повторов', min: 0, max: 90, step: 1, unit: '%', def: 35 },
        { k: 'tone', label: 'Яркость повторов', min: 1000, max: 16000, step: 100, unit: 'Гц', def: 6000 },
        { k: 'mix', label: 'Сколько эффекта', min: 0, max: 100, step: 1, unit: '%', def: 25 },
      ],
    },
    chorus: {
      title: 'Хорус', text: 'Звук шире и «гуще», будто играют несколько',
      params: [
        { k: 'rate', label: 'Скорость', min: 0.1, max: 5, step: 0.1, unit: 'Гц', def: 1.2 },
        { k: 'depth', label: 'Глубина', min: 0.5, max: 10, step: 0.5, unit: 'мс', def: 3 },
        { k: 'mix', label: 'Сколько эффекта', min: 0, max: 100, step: 1, unit: '%', def: 40 },
      ],
    },
    sat: {
      title: 'Сатурация', text: 'Тёплый перегруз: от лампы до дисторшна',
      params: [
        { k: 'drive', label: 'Перегруз', min: 0, max: 100, step: 1, unit: '%', def: 30 },
        { k: 'mix', label: 'Сколько эффекта', min: 0, max: 100, step: 1, unit: '%', def: 100 },
      ],
    },
    pan: {
      title: 'Панорама', text: 'Левее или правее в стерео',
      params: [{ k: 'pan', label: 'Лево ← → право', min: -100, max: 100, step: 1, unit: '', def: 0 }],
    },
    gain: {
      title: 'Усиление', text: 'Громче или тише до остальных эффектов',
      params: [{ k: 'db', label: 'Усиление', min: -24, max: 24, step: 0.5, unit: 'дБ', def: 0 }],
    },
  };
  const MAX = 8;

  const db = (v) => 10 ** (v / 20);
  const clamp = (v, p) => Math.max(p.min, Math.min(p.max, Number.isFinite(Number(v)) ? Number(v) : p.def));
  const uid = () => `fx${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

  function make(type) {
    const spec = TYPES[type];
    const p = {};
    spec.params.forEach((q) => { p[q.k] = q.def; });
    return { id: uid(), type, on: true, p };
  }

  function clean(list) {
    if (!Array.isArray(list)) return [];
    return list.filter((e) => e && TYPES[e.type]).slice(0, MAX).map((e) => {
      const p = {};
      TYPES[e.type].params.forEach((q) => { p[q.k] = clamp((e.p || {})[q.k], q); });
      return { id: typeof e.id === 'string' ? e.id.slice(0, 40) : uid(), type: e.type, on: e.on !== false, p };
    });
  }

  // плавно, без щелчков -- на живом звуке; в сведении в файл -- сразу
  const put = (c, param, value) => {
    if (c.currentTime > 0) param.setTargetAtTime(value, c.currentTime, 0.02);
    else param.value = value;
  };

  const irCache = new WeakMap();
  function impulse(c, seconds) {
    const len = Math.round(seconds * 10) / 10;
    let byLen = irCache.get(c);
    if (!byLen) { byLen = {}; irCache.set(c, byLen); }
    if (byLen[len]) return byLen[len];
    const n = Math.max(1, Math.round(c.sampleRate * len));
    const buf = c.createBuffer(2, n, c.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n) ** 3.5;
    }
    byLen[len] = buf;
    return buf;
  }

  // перегруз k = 1..10; громкость выравнивает level() ниже
  const driveK = (drive) => 1 + drive * 0.09;
  function shaperCurve(drive) {
    const k = driveK(drive);
    const n = 2048;
    const curve = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const x = (i / (n - 1)) * 2 - 1;
      curve[i] = Math.tanh(k * x) / Math.tanh(k);
    }
    return curve;
  }

  // сухой и обработанный сигнал -- поровну по мощности, без провала громкости
  function wetDry(c, input, output, wetOut) {
    const dry = c.createGain();
    const wet = c.createGain();
    input.connect(dry).connect(output);
    wetOut.connect(wet).connect(output);
    return (mix) => {
      const m = mix / 100;
      put(c, dry.gain, Math.cos(m * Math.PI / 2));
      put(c, wet.gain, Math.sin(m * Math.PI / 2));
    };
  }

  // Один эффект: {input, output, set(p)}
  function unit(c, e) {
    const input = c.createGain();
    const output = c.createGain();
    let set = () => {};
    const biquad = (type, freq, q = 0.707) => {
      const f = c.createBiquadFilter();
      f.type = type; f.frequency.value = freq; f.Q.value = q;
      return f;
    };
    if (e.type === 'eq') {
      const bands = [biquad('lowshelf', 100), biquad('peaking', 300, 1), biquad('peaking', 1000, 1),
        biquad('peaking', 3500, 1), biquad('highshelf', 10000)];
      bands.reduce((node, b) => node.connect(b), input).connect(output);
      set = (p) => ['low', 'lowmid', 'mid', 'pres', 'air'].forEach((k, i) => put(c, bands[i].gain, p[k]));
    } else if (e.type === 'hpf' || e.type === 'lpf') {
      const f = biquad(e.type === 'hpf' ? 'highpass' : 'lowpass', e.p.freq);
      input.connect(f).connect(output);
      set = (p) => put(c, f.frequency, p.freq);
    } else if (e.type === 'comp') {
      // браузер сам добавляет компрессору усиление (как «auto makeup»):
      // своё «Усиление после» по умолчанию -- 0
      const comp = c.createDynamicsCompressor();
      const makeup = c.createGain();
      comp.knee.value = 6;
      input.connect(comp).connect(makeup).connect(output);
      set = (p) => {
        put(c, comp.threshold, p.threshold);
        put(c, comp.ratio, p.ratio);
        put(c, comp.attack, p.attack / 1000);
        put(c, comp.release, p.release / 1000);
        put(c, makeup.gain, db(p.makeup));
      };
    } else if (e.type === 'reverb') {
      const verb = c.createConvolver();
      const tail = c.createGain();
      input.connect(verb).connect(tail);
      const mix = wetDry(c, input, output, tail);
      let size = 0;
      set = (p) => {
        if (Math.abs(p.size - size) >= 0.1) { verb.buffer = impulse(c, p.size); size = p.size; }
        mix(p.mix);
      };
    } else if (e.type === 'delay') {
      const d = c.createDelay(2);
      const fb = c.createGain();
      const tone = biquad('lowpass', e.p.tone);
      input.connect(d).connect(tone);
      tone.connect(fb).connect(d);
      const mix = wetDry(c, input, output, tone);
      set = (p) => {
        put(c, d.delayTime, p.time / 1000);
        put(c, fb.gain, Math.min(0.9, p.feedback / 100));
        put(c, tone.frequency, p.tone);
        mix(p.mix);
      };
    } else if (e.type === 'chorus') {
      const d = c.createDelay(0.1);
      d.delayTime.value = 0.018;
      const lfo = c.createOscillator();
      const depth = c.createGain();
      lfo.connect(depth).connect(d.delayTime);
      lfo.start(0);
      input.connect(d);
      const mix = wetDry(c, input, output, d);
      set = (p) => {
        put(c, lfo.frequency, p.rate);
        put(c, depth.gain, p.depth / 1000);
        mix(p.mix);
      };
    } else if (e.type === 'sat') {
      const shaper = c.createWaveShaper();
      shaper.oversample = '4x';
      const level = c.createGain();
      input.connect(shaper).connect(level);
      const mix = wetDry(c, input, output, level);
      set = (p) => {
        shaper.curve = shaperCurve(p.drive);
        // перегруз громче исходника -- уравниваем (замер: ±4 дБ на всём ходе ручки)
        const k = driveK(p.drive);
        put(c, level.gain, Math.sqrt(Math.tanh(k) / k));
        mix(p.mix);
      };
    } else if (e.type === 'pan') {
      const pan = c.createStereoPanner();
      input.connect(pan).connect(output);
      set = (p) => put(c, pan.pan, p.pan / 100);
    } else if (e.type === 'gain') {
      const g = c.createGain();
      input.connect(g).connect(output);
      set = (p) => put(c, g.gain, db(p.db));
    } else {
      input.connect(output);
    }
    set(e.p);
    return { input, output, set };
  }

  // Цепочка включённых эффектов по порядку
  function build(c, effects) {
    const input = c.createGain();
    const output = c.createGain();
    const units = new Map();
    let node = input;
    (effects || []).forEach((e) => {
      if (!e.on || !TYPES[e.type]) return;
      const u = unit(c, e);
      node.connect(u.input);
      node = u.output;
      units.set(e.id, u);
    });
    node.connect(output);
    return { input, output, set: (id, p) => { const u = units.get(id); if (u) u.set(p); } };
  }

  // Подпись значения ручки: «−3 дБ», «350 мс», «1,2 кГц»
  function show(q, v) {
    if (q.unit === 'Гц' && v >= 1000) return `${(v / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} кГц`;
    if (q.k === 'pan') return v === 0 ? 'центр' : v < 0 ? `лево ${-v}` : `право ${v}`;
    const num = Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 });
    return `${v > 0 && q.unit === 'дБ' ? '+' : ''}${num}${q.unit === ':1' ? ':1' : q.unit ? ` ${q.unit}` : ''}`;
  }

  return { TYPES, MAX, make, clean, build, show };
})();
