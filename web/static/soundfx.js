// Звук плеера Студии (владелец, 30.10): эквалайзер -- автоматический,
// готовые настройки и ручной -- и объёмное звучание: «Объём» (шире
// стерео), «Зал» (пространство) и «8D» (звук кружит вокруг головы).
//
// Всё -- Web Audio поверх того же <audio>. Граф создаётся только когда
// человек включил эффекты (или на iPhone -- ради громкости): пока эффекты
// выключены, звук идёт напрямую, как и раньше, -- надёжнее всего при
// заблокированном экране. Настройки -- в браузере (localStorage).
(function () {
  const BANDS = [60, 170, 400, 1000, 2500, 6000, 12000];
  const BAND_LABELS = ['60', '170', '400', '1к', '2,5к', '6к', '12к'];
  const PRESETS = {
    flat: { title: 'Ровно', gains: [0, 0, 0, 0, 0, 0, 0] },
    auto: { title: 'Авто', gains: null },
    vocal: { title: 'Голос', gains: [-3, -2, 0, 2, 4, 3, 1] },     // слова разборчивее -- заучивать
    bass: { title: 'Бас', gains: [6, 4, 1, 0, 0, 0, 0] },
    rock: { title: 'Рок', gains: [4, 2, -1, -1, 1, 3, 3] },
    street: { title: 'Улица', gains: [3, 2, 0, 1, 3, 3, 1] },      // сквозь шум города
    manual: { title: 'Ручной', gains: null },
  };
  const SPACES = { off: 'Обычно', wide: 'Объём', hall: 'Зал', orbit: '8D' };
  const KEY = 'naslux.fx';

  const state = { on: false, eq: 'auto', manual: [0, 0, 0, 0, 0, 0, 0], space: 'wide' };
  try { Object.assign(state, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch (error) { /* нет хранилища */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (error) { /* */ } };

  let audio = null;
  let ctx = null;
  let nodes = null;
  let volume = 1;
  let autoTimer = null;
  let orbitTimer = null;

  function ensure(element) {
    audio = element || audio;
    if (nodes || !audio) return Boolean(nodes);
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return false;
    try {
      if (navigator.audioSession) navigator.audioSession.type = 'playback';   // iPhone: играть и в беззвучном
      ctx = new Context();
      const source = ctx.createMediaElementSource(audio);
      const input = ctx.createGain();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 4096;
      analyser.smoothingTimeConstant = 0.85;
      const filters = BANDS.map((f, i) => {
        const filter = ctx.createBiquadFilter();
        filter.type = i === 0 ? 'lowshelf' : i === BANDS.length - 1 ? 'highshelf' : 'peaking';
        filter.frequency.value = f;
        filter.Q.value = 1.0;
        return filter;
      });
      const spaceIn = ctx.createGain();
      // моно-запись -- в стерео, иначе «Объём» тянет звук в одно ухо
      spaceIn.channelCount = 2; spaceIn.channelCountMode = 'explicit'; spaceIn.channelInterpretation = 'speakers';
      const spaceOut = ctx.createGain();
      // Громкость (владелец, 30.10: «стало тише»): эффекты выравнивают её
      // вверх -- тихий трек подтягивается до громкости хорошего релиза
      // (makeup до +9 дБ), а ограничитель только ловит пики выше −1 дБ,
      // чтобы не хрипело. Выключили эффекты -- ни того, ни другого в цепи нет.
      const makeup = ctx.createGain();
      const limiter = ctx.createDynamicsCompressor();
      limiter.threshold.value = -1; limiter.knee.value = 2; limiter.ratio.value = 20;
      limiter.attack.value = 0.002; limiter.release.value = 0.12;
      const out = ctx.createGain();
      source.connect(input);
      input.connect(analyser);
      filters.reduce((prev, f) => { prev.connect(f); return f; }, input).connect(spaceIn);
      out.connect(ctx.destination);
      makeup.connect(limiter);
      nodes = { source, input, analyser, filters, spaceIn, spaceOut, makeup, limiter, out, space: [] };
      setInterval(loudnessStep, 400);
      audio.addEventListener('loadstart', () => { loud = null; avg = null; });   // новый трек -- слушаем заново
      audio.addEventListener('play', () => { if (ctx.state !== 'running') ctx.resume().catch(() => {}); });
      apply();
      return true;
    } catch (error) {
      nodes = null;
      return false;
    }
  }

  // ------------------------------------------------------ пространство
  function stereoWidth(width) {
    // L' = L·(1+w)/2 + R·(1−w)/2, R' -- зеркально: w>1 -- шире, чем записано
    const split = ctx.createChannelSplitter(2);
    const merge = ctx.createChannelMerger(2);
    const same = ctx.createGain(); same.gain.value = (1 + width) / 2;
    const same2 = ctx.createGain(); same2.gain.value = (1 + width) / 2;
    const cross = ctx.createGain(); cross.gain.value = (1 - width) / 2;
    const cross2 = ctx.createGain(); cross2.gain.value = (1 - width) / 2;
    split.connect(same, 0); same.connect(merge, 0, 0);
    split.connect(cross, 1); cross.connect(merge, 0, 0);
    split.connect(same2, 1); same2.connect(merge, 0, 1);
    split.connect(cross2, 0); cross2.connect(merge, 0, 1);
    return { input: split, output: merge, all: [split, merge, same, same2, cross, cross2] };
  }

  let impulse = null;
  function hallImpulse() {
    if (impulse) return impulse;
    const seconds = 2.4;
    const length = Math.floor(ctx.sampleRate * seconds);
    impulse = ctx.createBuffer(2, length, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch += 1) {
      const data = impulse.getChannelData(ch);
      for (let i = 0; i < length; i += 1) data[i] = (Math.random() * 2 - 1) * (1 - i / length) ** 3.2;
    }
    return impulse;
  }

  function reverb(wet, into) {
    const conv = ctx.createConvolver();
    conv.buffer = hallImpulse();
    const g = ctx.createGain(); g.gain.value = wet;
    nodes.spaceIn.connect(conv); conv.connect(g); g.connect(into);
    return [conv, g];
  }

  function buildSpace() {
    const { spaceIn, spaceOut } = nodes;
    clearInterval(orbitTimer);
    spaceIn.disconnect();
    nodes.space.forEach((n) => { try { n.disconnect(); } catch (error) { /* */ } });
    nodes.space = [];
    const space = state.on ? state.space : 'off';
    if (space === 'off') { spaceIn.connect(spaceOut); return; }
    if (space === 'wide' || space === 'hall') {
      const wide = stereoWidth(space === 'wide' ? 1.8 : 1.35);
      spaceIn.connect(wide.input); wide.output.connect(spaceOut);
      nodes.space.push(...wide.all);
      if (space === 'hall') nodes.space.push(...reverb(0.28, spaceOut));
      return;
    }
    // 8D: звук -- точкой, которая кружит вокруг головы (HRTF), плюс немного зала
    const dry = ctx.createGain(); dry.gain.value = 0.25;
    const panner = ctx.createPanner();
    panner.panningModel = 'HRTF';
    panner.distanceModel = 'inverse';
    panner.refDistance = 2;                 // по кругу радиусом 1,5 -- без потери громкости
    spaceIn.connect(dry); dry.connect(spaceOut);
    spaceIn.connect(panner); panner.connect(spaceOut);
    nodes.space.push(dry, panner, ...reverb(0.18, spaceOut));
    const started = ctx.currentTime;
    const turn = () => {
      const a = ((ctx.currentTime - started) / 10) * 2 * Math.PI;    // круг за 10 секунд
      const x = Math.sin(a) * 1.5;
      const z = -Math.cos(a) * 1.5;
      if (panner.positionX) {
        panner.positionX.setTargetAtTime(x, ctx.currentTime, 0.05);
        panner.positionZ.setTargetAtTime(z, ctx.currentTime, 0.05);
      } else panner.setPosition(x, 0, z);
    };
    turn();
    orbitTimer = setInterval(turn, 50);
  }

  // ------------------------------------------------------ эквалайзер
  function gainsNow() {
    if (!state.on) return BANDS.map(() => 0);
    if (state.eq === 'manual') return state.manual;
    if (state.eq === 'auto') return null;
    return (PRESETS[state.eq] || PRESETS.flat).gains;
  }

  function setGains(gains, smooth) {
    const now = ctx.currentTime;
    nodes.filters.forEach((f, i) => f.gain.setTargetAtTime(gains[i] || 0, now, smooth || 0.05));
    // подняли полосы -- общий уровень чуть вниз, чтобы ограничитель не давил
    const boost = Math.max(0, ...gains) * 0.5;
    nodes.input.gain.setTargetAtTime(10 ** (-boost / 20), now, smooth || 0.05);
  }

  // Выравнивание громкости: средний уровень (RMS) трека -> к −14 дБ
  // (громкость стримингов), только вверх и плавно; паузы не считаем
  let loud = null;
  function loudnessStep() {
    if (!nodes || !state.on || audio.paused) return;
    const data = new Float32Array(nodes.analyser.fftSize);
    nodes.analyser.getFloatTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i += 1) sum += data[i] * data[i];
    const rms = 10 * Math.log10(sum / data.length + 1e-12);
    if (rms < -50) return;
    loud = loud == null ? rms : loud * 0.92 + rms * 0.08;
    const gain = Math.max(0, Math.min(9, -14 - loud));
    nodes.makeup.gain.setTargetAtTime(10 ** (gain / 20), ctx.currentTime, 1.2);
  }

  // Авто: слушаем средний спектр трека и мягко выравниваем его к
  // «студийному» наклону (−4,5 дБ на октаву, как у хорошо сведённой
  // музыки): глухой трек -- светлее, резкий -- мягче, без баса -- плотнее.
  // Поправка не больше ±6 дБ и плавная -- никаких скачков.
  let avg = null;
  function autoStep() {
    if (!nodes || state.eq !== 'auto' || !state.on || audio.paused) return;
    const data = new Float32Array(nodes.analyser.frequencyBinCount);
    nodes.analyser.getFloatFrequencyData(data);
    const hz = ctx.sampleRate / 2 / data.length;
    const levels = BANDS.map((f) => {
      const lo = Math.max(1, Math.floor((f / Math.SQRT2) / hz));
      const hi = Math.min(data.length - 1, Math.ceil((f * Math.SQRT2) / hz));
      let sum = 0;
      for (let i = lo; i <= hi; i += 1) sum += 10 ** (data[i] / 10);
      return 10 * Math.log10(sum / (hi - lo + 1) + 1e-12);
    });
    if (Math.max(...levels) < -85) return;                 // тишина -- не учиться на ней
    avg = avg ? avg.map((v, i) => v * 0.9 + levels[i] * 0.1) : levels;
    const tilt = BANDS.map((f) => -4.5 * Math.log2(f / 1000));
    const offset = avg.reduce((s, v, i) => s + v - tilt[i], 0) / BANDS.length;
    const fix = avg.map((v, i) => Math.max(-6, Math.min(6, (tilt[i] + offset - v) * 0.5)));
    setGains(fix, 1.5);
  }

  function apply() {
    if (!nodes) return;
    const gains = gainsNow();
    clearInterval(autoTimer);
    if (gains) setGains(gains);
    else { avg = null; autoTimer = setInterval(autoStep, 400); }
    buildSpace();
    // эффекты выключены -- звук идёт мимо выравнивания и ограничителя, как был
    nodes.spaceOut.disconnect();
    nodes.limiter.disconnect();
    if (state.on) { nodes.spaceOut.connect(nodes.makeup); nodes.limiter.connect(nodes.out); } else nodes.spaceOut.connect(nodes.out);
    nodes.out.gain.value = volume;
  }

  // ------------------------------------------------------------ панель
  function panel(box) {
    const chips = (items, current, attr) => Object.entries(items).map(([k, v]) =>
      `<button type="button" class="fx-chip${k === current ? ' on' : ''}" data-${attr}="${k}">${typeof v === 'string' ? v : v.title}</button>`).join('');
    box.innerHTML = `
      <label class="fx-switch"><input type="checkbox" id="fxOn"${state.on ? ' checked' : ''}>
        <span>Эффекты звука</span></label>
      <div class="fx-body"${state.on ? '' : ' hidden'}>
        <div class="fx-label">Эквалайзер</div>
        <div class="fx-chips">${chips(PRESETS, state.eq, 'fxeq')}</div>
        <div class="fx-bands"${state.eq === 'manual' ? '' : ' hidden'}>${BANDS.map((f, i) => `
          <label class="fx-band"><input type="range" min="-12" max="12" step="1" value="${state.manual[i] || 0}"
            data-fxband="${i}" data-nohaptic aria-label="${BAND_LABELS[i]} Гц"><span>${BAND_LABELS[i]}</span></label>`).join('')}</div>
        <p class="fx-note">${state.eq === 'auto' ? 'Авто подстраивает звук под каждый трек за несколько секунд.'
    : state.eq === 'vocal' ? 'Голос впереди — удобно заучивать слова.' : ''}</p>
        <div class="fx-label">Пространство</div>
        <div class="fx-chips">${chips(SPACES, state.space, 'fxspace')}</div>
        <p class="fx-note">С эффектами тихие треки звучат громче — без хрипа. Лучше всего в наушниках. Если при заблокированном экране звук прервётся — выключите эффекты.</p>
      </div>`;
  }

  function onPanel(e, box) {
    const on = e.target.closest('#fxOn');
    const eq = e.target.closest('[data-fxeq]');
    const space = e.target.closest('[data-fxspace]');
    if (on) { state.on = on.checked; if (state.on) ensure(); }
    else if (eq) state.eq = eq.dataset.fxeq;
    else if (space) state.space = space.dataset.fxspace;
    else return;
    save();
    apply();
    panel(box);
  }

  function bind(box) {
    panel(box);
    box.addEventListener('click', (e) => onPanel(e, box));
    box.addEventListener('input', (e) => {
      const band = e.target.closest('[data-fxband]');
      if (!band) return;
      state.manual[Number(band.dataset.fxband)] = Number(band.value);
      save();
      apply();
    });
  }

  // Разбудить звук -- вызывать из нажатия: граф создаётся (если эффекты
  // включены) и контекст запускается в том же касании, иначе iPhone молчит
  function wake() {
    if (!nodes && state.on && audio) ensure();
    if (ctx && ctx.state !== 'running') ctx.resume().catch(() => {});
  }

  window.SoundFX = {
    debug: () => (nodes ? { makeup: nodes.makeup.gain.value, loud, state: ctx.state } : null),
    attach(element) { audio = element; },
    wake,
    ensure,
    bind,
    get on() { return state.on; },
    // громкость: через граф, если он есть (iPhone), иначе -- false
    setVolume(level) { volume = level; if (nodes) { nodes.out.gain.value = level; return true; } return false; },
  };
  // iPhone будит звук Web Audio только из нажатия (не из события «play»):
  // после паузы, блокировки экрана или звонка контекст «спит», а созданный
  // не из касания -- молчит с самого начала, и звук пропадал (владелец,
  // 30.10: «выключил, включил -- звука нет»). Любое касание -- будим.
  ['touchend', 'pointerup', 'click', 'keydown'].forEach((ev) => document.addEventListener(ev, wake, true));
}());
