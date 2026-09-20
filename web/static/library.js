// Личный кабинет: треки и всё, что с ними сделано.

const $ = (id) => document.getElementById(id);

const when = (ts) => new Date(ts * 1000).toLocaleString('ru-RU',
  { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

const FILE_NAMES = { gp5: 'Guitar Pro', txt: 'Табы .txt', mid: 'MIDI' };

async function load() {
  const me = await (await fetch('/api/me')).json();
  const badge = $('access');
  if (me.subscribed) {
    badge.textContent = 'Подписка активна';
    badge.className = 'badge pro';
  } else {
    badge.textContent = `Бесплатных песен: ${me.freeLeft} из ${me.freeSongs}`;
  }

  const { tracks } = await (await fetch('/api/library')).json();
  if (!tracks.length) {
    $('empty').style.display = '';
    return;
  }

  $('list').innerHTML = tracks.map(card).join('');
  document.querySelectorAll('[data-open]').forEach((el) => {
    el.onclick = () => (location.href = `/player/${el.dataset.open}`);
  });
}

function card(track) {
  const facts = [];
  if (track.tempo) facts.push(`темп ${track.tempo}`);
  if (track.chords) facts.push(`аккордов ${track.chords}`);
  if (track.parts.length) facts.push(`партий ${track.parts.length}`);
  if (track.hasLyrics) facts.push('текст распознан');

  const made = track.made
    .filter((m) => m.status === 'done')
    .map((m) => {
      const files = m.files.map((f) => FILE_NAMES[f] || f).join(', ');
      return `<span class="badge" style="font-size:12px">${m.stem}: ${files}</span>`;
    })
    .join(' ');

  const pending = track.made.filter((m) => m.status === 'running' || m.status === 'queued');
  const state = track.status === 'done'
    ? (made || '<span class="muted">табы ещё не создавались</span>')
    : track.status === 'error'
      ? `<span class="bad">${track.stage || 'ошибка'}</span>`
      : `<span class="muted">${track.stage || track.status}…</span>`;

  return `
    <div class="card" style="cursor:pointer" data-open="${track.id}">
      <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <div style="font-weight:600;font-size:16px">${track.name}</div>
        <div class="muted">${when(track.at)}</div>
      </div>
      <div class="muted" style="margin:6px 0 10px">${facts.join(' · ') || '—'}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        ${state}
        ${pending.length ? `<span class="muted">в работе: ${pending.length}</span>` : ''}
      </div>
    </div>`;
}

load();
