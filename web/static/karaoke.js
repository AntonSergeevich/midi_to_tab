// Суфлёр NASLUX: текст песни строками, слово подсвечивается в такт музыке.
// Слова с таймкодами считает нейросеть по версии трека (GET
// /api/studio/<трек>/karaoke?file=<версия>). Общий для плеера Студии и
// мультитрека: render() -- разметить текст в элементе, update() -- подсветить
// слово на секунде t и прокрутить текущую строку в середину.
window.Karaoke = (function () {
  const cache = {};
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  async function load(job, file) {
    const key = `${job}/${file}`;
    if (!cache[key]) {
      cache[key] = fetch(`/api/studio/${job}/karaoke?file=${encodeURIComponent(file)}`)
        .then((r) => (r.ok ? r.json() : { words: [] }))
        .then((d) => d.words || [])
        .catch(() => []);
    }
    return cache[key];
  }

  function render(el, words) {
    const lines = [];
    words.forEach((w, i) => {
      if (w.nl || !lines.length) lines.push([]);
      lines[lines.length - 1].push([w, i]);
    });
    el.innerHTML = lines.map((line, li) => `<span class="kk-line" data-l="${li}">${
      line.map(([w, i], n) => `${n && !/[«„“(]$/.test(line[n - 1][0].w) ? ' ' : ''}<span class="kk-w" data-i="${i}">${esc(w.w)}</span>`).join('')}</span>`).join('\n');
    el.dataset.noi18n = '';
    el.classList.add('kk');
    el._kk = { words, spans: [...el.querySelectorAll('.kk-w')], current: -1 };
  }

  // Последнее слово, начавшееся к секунде t (поиск делением пополам)
  function at(words, t) {
    let lo = 0;
    let hi = words.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (words[mid].s <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return found;
  }

  function update(el, t) {
    const k = el && el._kk;
    if (!k || !k.words.length) return;
    const i = at(k.words, t + 0.05);
    if (i === k.current) return;
    k.spans.forEach((s, n) => {
      s.classList.toggle('on', n === i);
      s.classList.toggle('past', n < i);
    });
    const before = k.current >= 0 ? k.spans[k.current].parentNode : null;
    k.current = i;
    const line = i >= 0 ? k.spans[i].parentNode : el.querySelector('.kk-line');
    if (line !== before) {
      el.querySelectorAll('.kk-line.on').forEach((x) => x.classList.remove('on'));
      if (line) {
        line.classList.add('on');
        el.scrollTo({ top: line.offsetTop - el.clientHeight / 2 + line.offsetHeight / 2, behavior: 'smooth' });
      }
    }
  }

  return { load, render, update };
}());
