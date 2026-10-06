// Разметка текста песни: куплет, припев, бридж -- в квадратных скобках,
// как их понимают нейросети (Mureka, YuE2, Suno). Кнопки вставляют метку в
// место курсора, «Разметить» расставляет части сама: припев -- по повторам
// строк (даже если он разбит на строки по-разному или с опечатками),
// а по стилю добавляет уместные вставки -- соло, брейкдаун, дроп.
(() => {
  const norm = (line) => line.toLowerCase().replace(/ё/g, 'е').replace(/[^a-zа-я0-9\s]/g, ' ')
    .replace(/\s+/g, ' ').trim();
  const isTag = (line) => /^\s*\[[^\]]*\]\s*$/.test(line);

  // Строка «повторяется», если большая часть её пар слов встречается в песне ещё раз
  function repeatedLines(lines) {
    const words = lines.map((l) => norm(l).split(' ').filter(Boolean));
    const count = new Map();
    const all = words.flat();
    for (let i = 0; i + 1 < all.length; i++) {
      const key = `${all[i]} ${all[i + 1]}`;
      count.set(key, (count.get(key) || 0) + 1);
    }
    return words.map((w) => {
      if (w.length < 2) return null;              // одно слово -- решат соседи
      let rep = 0;
      for (let i = 0; i + 1 < w.length; i++) if ((count.get(`${w[i]} ${w[i + 1]}`) || 0) >= 2) rep++;
      return rep / (w.length - 1) >= 0.6;
    });
  }

  // Куски песни: по пустым строкам, а если их нет -- по смене «повтор/новое»
  function blocksOf(lines) {
    const text = lines.join('\n');
    if (/\n\s*\n/.test(text)) {
      return text.split(/\n\s*\n/).map((b) => b.split('\n').filter((l) => l.trim())).filter((b) => b.length);
    }
    const rep = repeatedLines(lines);
    for (let i = 0; i < rep.length; i++) if (rep[i] === null) rep[i] = i ? rep[i - 1] : rep[i + 1] ?? false;
    const runs = [];
    lines.forEach((line, i) => {
      const last = runs[runs.length - 1];
      if (last && last.rep === rep[i]) last.lines.push(line);
      else runs.push({ rep: rep[i], lines: [line] });
    });
    // Одинокая строка -- не часть, а хвост соседней
    for (let i = runs.length - 1; i >= 0; i--) {
      if (runs[i].lines.length === 1 && runs.length > 1) {
        const into = i ? runs[i - 1] : runs[i + 1];
        if (i) into.lines.push(...runs[i].lines); else into.lines.unshift(...runs[i].lines);
        runs.splice(i, 1);
      }
    }
    // Внутри повтора -- речёвка: строка повторяется сразу подряд («Мы просто
    // пчёлы / Мы просто пчёлы»). Так пишут предприпев, и это отдельный кусок
    const parts = [];
    for (const run of runs) {
      if (!run.rep) { parts.push(run); continue; }
      const n = run.lines.map(norm);
      const chant = n.map((x, i) => x === n[i - 1] || x === n[i + 1]);
      run.lines.forEach((line, i) => {
        const last = parts[parts.length - 1];
        if (last && last.rep && last.chant === chant[i] && last.from === run) last.lines.push(line);
        else parts.push({ rep: true, chant: chant[i], lines: [line], from: run });
      });
    }
    // Длинные куски -- по 8 строк (или по 4, если делится)
    const out = [];
    for (const part of parts) {
      const size = part.lines.length > 8 ? (part.lines.length % 8 && !(part.lines.length % 4) ? 4 : 8)
        : part.lines.length;
      for (let i = 0; i < part.lines.length; i += size) {
        const lines = part.lines.slice(i, i + size);
        lines.chant = Boolean(part.chant);
        out.push(lines);
      }
    }
    return out;
  }

  function label(blocks) {
    const words = blocks.map((b) => b.map(norm).join(' '));
    const pairs = (text) => { const w = text.split(' '); return w.slice(1).map((x, i) => `${w[i]} ${x}`); };
    const kinds = blocks.map((b, i) => {
      const mine = pairs(words[i]);
      if (!mine.length) return 'verse';
      const elsewhere = new Set(words.filter((_, j) => j !== i).flatMap(pairs));
      const outside = mine.filter((p) => elsewhere.has(p)).length / mine.length;
      const inside = new Set(mine).size / mine.length;          // повтор внутри куска
      if (b.chant) return 'hook';
      if (outside >= 0.5) return 'chorus';
      if (inside <= 0.6) return 'hook';
      return 'verse';
    });
    // Повтор внутри куска перед припевом -- предприпев, иначе -- тоже припев
    kinds.forEach((k, i) => { if (k === 'hook') kinds[i] = kinds[i + 1] === 'chorus' ? 'pre' : 'chorus'; });
    // Новый кусок перед последним припевом, когда куплеты уже были, -- бридж
    const lastChorus = kinds.lastIndexOf('chorus');
    const verses = kinds.filter((k) => k === 'verse').length;
    if (lastChorus > 0 && kinds[lastChorus - 1] === 'verse' && verses >= 3
        && kinds.slice(0, lastChorus - 1).includes('chorus')) kinds[lastChorus - 1] = 'bridge';
    return kinds;
  }

  const TAG = { verse: '[Verse]', chorus: '[Chorus]', pre: '[Pre-Chorus]', bridge: '[Bridge]' };

  // Вставки по стилю: что уместно в этом жанре
  function extrasFor(style) {
    const s = (style || '').toLowerCase();
    const has = (...words) => words.some((w) => s.includes(w));
    return {
      solo: has('metal', 'метал', 'rock', 'рок', 'punk', 'панк', 'grunge', 'гранж', 'blues', 'блюз', 'guitar solo', 'соло')
        ? (has('sax', 'саксоф') ? '[Sax Solo]' : '[Guitar Solo]') : has('jazz', 'джаз') ? '[Instrumental Solo]' : '',
      breakdown: has('nu metal', 'ню-метал', 'ню метал', 'metalcore', 'металкор', 'core', 'djent', 'deathcore') ? '[Breakdown]' : '',
      drop: has('edm', 'dubstep', 'дабстеп', 'house', 'хаус', 'techno', 'техно', 'trance', 'транс', 'drum and bass',
        'dnb', 'электрон', 'electro') ? true : false,
      soft: has('ballad', 'баллад', 'acoustic', 'акуст', 'piano', 'фортепиан', 'lo-fi', 'lofi'),
    };
  }

  const SINGERS = { male: 'male vocals', female: 'female vocals', duet: 'duet' };

  window.structureLyrics = (text, style, voice) => {
    const lines = (text || '').split('\n').map((l) => l.replace(/\s+$/, '')).filter((l) => !isTag(l));
    while (lines.length && !lines[0].trim()) lines.shift();
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    if (!lines.some((l) => l.trim())) return text;
    let blocks = blocksOf(lines);
    let kinds = label(blocks);
    // Соседние куски припева -- один припев (до 8 строк)
    for (let i = blocks.length - 1; i > 0; i--) {
      if (kinds[i] === 'chorus' && kinds[i - 1] === 'chorus' && blocks[i - 1].length + blocks[i].length <= 8) {
        blocks[i - 1] = blocks[i - 1].concat(blocks[i]);
        blocks.splice(i, 1);
        kinds.splice(i, 1);
      }
    }
    const extra = extrasFor(style);
    // Соло и брейкдаун -- перед последней связкой «предприпев + припев»
    let finale = kinds.lastIndexOf('chorus');
    if (finale > 0 && kinds[finale - 1] === 'pre') finale -= 1;
    // В короткой песне (один куплет) соло и брейкдаун только мешают
    if (kinds.filter((k) => k === 'verse').length < 2) finale = -1;
    const out = [extra.soft ? '[Intro: soft]' : '[Intro]'];
    // Дуэт: куплеты по очереди -- он, она; припевы и бридж -- вместе
    let turn = 0;
    const singer = (kind) => {
      if (voice !== 'duet') return '';
      if (kind === 'verse' || kind === 'pre') return `: ${kind === 'verse' && turn++ % 2 ? SINGERS.female : SINGERS.male}`;
      return `: ${SINGERS.duet}`;
    };
    blocks.forEach((block, i) => {
      if (i === finale && i > 0) {
        if (extra.solo) out.push('', extra.solo);
        if (extra.breakdown) out.push('', extra.breakdown);
        if (extra.drop) out.push('', '[Build-Up]');
      }
      out.push('', TAG[kinds[i]].replace(']', `${singer(kinds[i])}]`), ...block);
      if (extra.drop && kinds[i] === 'chorus' && kinds[i + 1] !== 'chorus') out.push('', '[Drop]');
    });
    out.push('', '[Outro]');
    return out.join('\n');
  };

  // Кто поёт эту часть: дописать к ближайшей метке выше курсора
  window.setSinger = (area, who) => {
    const lines = area.value.split('\n');
    let row = area.value.slice(0, area.selectionStart).split('\n').length - 1;
    while (row >= 0 && !isTag(lines[row])) row--;
    if (row < 0) { window.insertLyricsTag(area, `[Verse: ${SINGERS[who]}]`); return false; }
    const name = lines[row].trim().slice(1, -1).split(':')[0].trim();
    lines[row] = `[${name}: ${SINGERS[who]}]`;
    const caret = area.selectionStart;
    area.value = lines.join('\n');
    area.setSelectionRange(caret, caret);
    area.focus();
    area.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  };

  // Вставить метку отдельной строкой в место курсора
  window.insertLyricsTag = (area, tag) => {
    const { value, selectionStart: at } = area;
    const before = value.slice(0, at);
    const after = value.slice(at);
    const lead = !before || before.endsWith('\n\n') ? '' : before.endsWith('\n') ? '\n' : '\n\n';
    const tail = after.startsWith('\n') ? '' : '\n';
    area.value = `${before}${lead}${tag}${tail}${after}`;
    const caret = (before + lead + tag + tail).length;
    area.setSelectionRange(caret, caret);
    area.focus();
    area.dispatchEvent(new Event('input', { bubbles: true }));
  };
})();
