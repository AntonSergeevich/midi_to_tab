// Личный кабинет: треки и всё, что с ними сделано.

const $ = (id) => document.getElementById(id);

const when = (ts) => new Date(ts * 1000).toLocaleString('ru-RU',
  { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

const FILE_NAMES = { gp5: 'Guitar Pro', txt: 'Табы .txt', mid: 'MIDI' };

// Пока что-то считается, список сам обновляется: иначе человек сидит
// перед застывшим «обрабатывается» и жмёт F5.
let timer = null;

async function load() {
  const me = await (await fetch('/api/me')).json();
  const badge = $('access');
  $('account').textContent = me.registered ? me.email : 'Вход';
  if (me.unlimited) {
    badge.textContent = 'Безлимит';
    badge.className = 'badge pro';
  } else if (me.subscribed) {
    badge.textContent = 'Подписка активна';
    badge.className = 'badge pro';
  } else {
    badge.textContent = `Бесплатных песен: ${me.freeLeft} из ${me.freeSongs}`;
  }

  const { tracks } = await (await fetch('/api/library')).json();

  // Список очищаем ДО проверки на пустоту: иначе после удаления
  // последнего трека его карточка остаётся висеть на экране, и человек
  // уверен, что удаление не сработало.
  $('list').innerHTML = tracks.map(card).join('');
  $('empty').style.display = tracks.length ? 'none' : '';
  if (!tracks.length) return;
  document.querySelectorAll('[data-open]').forEach((el) => {
    el.onclick = (event) => {
      if (event.target.closest('[data-del]')) return;   // клик по «удалить»
      location.href = `/player/${el.dataset.open}`;
    };
  });
  document.querySelectorAll('[data-del]').forEach((button) => {
    button.onclick = async (event) => {
      event.stopPropagation();
      const id = button.dataset.del;
      const name = button.dataset.name;
      // Удаление необратимо и уносит файлы с диска -- спрашиваем прямо
      if (!confirm(`Удалить «${name}» вместе с табами и MIDI?\n\nОтменить это нельзя.`)) return;
      button.disabled = true;
      button.textContent = 'удаляю…';
      const response = await fetch(`/api/job/${id}`, { method: 'DELETE' });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        alert(error.detail || 'Не удалось удалить');
        button.disabled = false;
        button.textContent = 'Удалить';
        return;
      }
      await load();
    };
  });

  const busy = tracks.some((t) => t.status === 'running' || t.status === 'queued')
    || tracks.some((t) => t.made.some((m) => m.status === 'running' || m.status === 'queued'));
  clearTimeout(timer);
  if (busy) timer = setTimeout(load, 2000);
}

function card(track) {
  const facts = [];
  if (track.tempo) facts.push(`темп ${track.tempo}`);
  if (track.chords) facts.push(`аккордов ${track.chords}`);
  if (track.parts.length) facts.push(`партий ${track.parts.length}`);
  if (track.hasLyrics) facts.push('текст распознан');

  // Ссылки прямо в списке: за файлами возвращаются чаще, чем за всем
  // остальным, и ради них не должно приходиться открывать плеер.
  const made = track.made
    .filter((m) => m.status === 'done' && m.files.length)
    .map((m) => m.files.map((f) =>
      `<a href="/api/file/${m.id}/${f}" download onclick="event.stopPropagation()"
          class="badge" style="font-size:12px">${m.stem}: ${FILE_NAMES[f] || f}</a>`).join(' '))
    .join(' ');

  const pending = track.made.filter((m) => m.status === 'running' || m.status === 'queued');
  const percent = Math.round(track.progress || 0);
  const state = track.status === 'done'
    ? (made || '<span class="muted">табы ещё не создавались</span>')
    : track.status === 'error'
      ? `<span class="bad">${track.stage || 'ошибка'}</span>`
      : `<span class="muted">${track.stage || track.status} — ${percent}%</span>
         <div class="bar done" style="flex-basis:100%;margin-top:6px">
           <i style="width:${percent}%"></i></div>`;

  return `
    <div class="card" style="cursor:pointer" data-open="${track.id}">
      <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center">
        <div style="font-weight:600;font-size:16px">${track.name}</div>
        <div style="display:flex;gap:10px;align-items:center">
          <span class="muted">${when(track.at)}</span>
          <button class="del" data-del="${track.id}"
                  data-name="${track.name.replace(/"/g, '&quot;')}">Удалить</button>
        </div>
      </div>
      <div class="muted" style="margin:6px 0 10px">${facts.join(' · ') || '—'}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        ${state}
        ${pending.length ? `<span class="muted">в работе: ${pending.length} — ${Math.round(pending[0].progress || 0)}%</span>` : ''}
      </div>
    </div>`;
}

load();
