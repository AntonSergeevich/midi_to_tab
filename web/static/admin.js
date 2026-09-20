// Админка: безлимит, продление подписки, заметки.

const $ = (id) => document.getElementById(id);
let all = [];

const when = (ts) => ts ? new Date(ts * 1000).toLocaleDateString('ru-RU',
  { day: 'numeric', month: 'short', year: '2-digit' }) : '—';

async function boot() {
  const me = await (await fetch('/api/me')).json();
  if (me.isAdmin) { $('login').style.display = 'none'; await loadUsers(); }
}

$('enter').onclick = async () => {
  const form = new FormData();
  form.append('key', $('key').value);
  const response = await fetch('/api/admin/login', { method: 'POST', body: form });
  if (!response.ok) {
    $('loginMsg').innerHTML = `<span class="bad">${(await response.json()).detail}</span>`;
    return;
  }
  $('login').style.display = 'none';
  await loadUsers();
};
$('key').onkeydown = (e) => { if (e.key === 'Enter') $('enter').click(); };

async function loadUsers() {
  const data = await (await fetch('/api/admin/users')).json();
  $('panel').style.display = '';

  const s = data.stats;
  $('stats').innerHTML = [
    ['Пользователей', s.users],
    ['С подпиской', s.paid],
    ['Безлимит', s.unlimited],
    ['Треков обработано', s.jobs],
    ['Сбоев', s.failed],
    ['Получено, ₽', Math.round(s.revenue)],
    ['Обращений ждёт', s.open_tickets],
  ].map(([label, value]) => `
    <div class="card" style="margin:0;padding:14px">
      <div class="muted" style="font-size:12px">${label}</div>
      <div style="font-size:26px;font-weight:700;color:var(--accent)">${value}</div>
    </div>`).join('');

  all = data.users;
  render();
  await loadTickets();
}

const escapeHtml = (text) => {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
};

async function loadTickets() {
  const { tickets } = await (await fetch('/api/admin/tickets')).json();
  if (!tickets.length) {
    $('tickets').innerHTML = '<p class="muted">Обращений нет.</p>';
    return;
  }
  const colour = { late: 'var(--red)', soon: 'var(--accent)', ok: 'var(--muted)' };
  $('tickets').innerHTML = tickets.map((t) => `
    <div class="part" style="display:block" data-ticket="${t.id}">
      <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
        <b>${t.topicTitle}</b>
        <span style="color:${colour[t.urgency.level]};font-size:13px">
          ${t.urgency.overdue
            ? `просрочено на ${Math.abs(t.urgency.daysLeft).toFixed(0)} дн`
            : `осталось ${t.urgency.daysLeft.toFixed(0)} дн`}
        </span>
      </div>
      <div class="muted" style="font-size:12px;margin:4px 0">
        ${t.email || t.userId} · ${new Date(t.at * 1000).toLocaleString('ru-RU')}
        · ${t.status === 'new' ? 'новое' : 'отвечено'}
      </div>
      <div style="margin:8px 0">${escapeHtml(t.body)}</div>
      ${t.answer
        ? `<div style="border-left:2px solid var(--accent);padding-left:10px">
             <div style="color:var(--accent);font-size:13px">Ваш ответ</div>
             <div>${escapeHtml(t.answer)}</div></div>`
        : `<textarea rows="3" data-answer placeholder="Ответ"></textarea>
           <div style="display:flex;gap:10px;align-items:center;margin-top:8px">
             <button class="primary" data-send>Ответить</button>
             <label class="check"><input type="checkbox" data-notify checked>
               отправить на почту</label>
             <span class="muted" data-result></span>
           </div>`}
    </div>`).join('');

  document.querySelectorAll('[data-ticket]').forEach((row) => {
    const send = row.querySelector('[data-send]');
    if (!send) return;
    send.onclick = async () => {
      const answer = row.querySelector('[data-answer]').value.trim();
      const result = row.querySelector('[data-result]');
      if (answer.length < 2) { result.textContent = 'пустой ответ'; return; }
      send.disabled = true;
      const form = new FormData();
      form.append('answer', answer);
      form.append('notify', row.querySelector('[data-notify]').checked);
      const response = await fetch(`/api/admin/ticket/${row.dataset.ticket}`,
        { method: 'POST', body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        result.innerHTML = `<span class="bad">${data.detail || 'ошибка'}</span>`;
        send.disabled = false;
        return;
      }
      result.innerHTML = data.emailed
        ? '<span class="ok">отправлено на почту</span>'
        : '<span class="ok">сохранено</span>';
      setTimeout(loadTickets, 900);
    };
  });
}

$('search').oninput = render;

function render() {
  const q = $('search').value.trim().toLowerCase();
  const rows = all.filter((u) =>
    !q || u.short.includes(q) || (u.note || '').toLowerCase().includes(q));

  $('users').innerHTML = rows.map((u) => `
    <div class="part" data-id="${u.id}">
      <span class="name" style="font-family:Consolas,monospace">${u.short}</span>
      <label class="check"><input type="checkbox" data-act="unlimited"
        ${u.unlimited ? 'checked' : ''}> безлимит</label>
      <label class="check"><input type="checkbox" data-act="admin"
        ${u.isAdmin ? 'checked' : ''}> админ</label>
      <input type="text" data-act="note" value="${u.note || ''}" placeholder="заметка"
        style="width:150px">
      <span class="spacer"></span>
      <span class="muted" style="font-size:12px">
        треков ${u.tracks} · проб ${u.freeUsed} ·
        ${u.subscribed ? 'до ' + when(u.paidUntil) : 'без подписки'}
      </span>
      <button data-act="grant">+30 дней</button>
      <span class="muted" data-role="status"></span>
    </div>`).join('');

  document.querySelectorAll('[data-id]').forEach((row) => {
    const id = row.dataset.id;
    const status = row.querySelector('[data-role="status"]');
    const send = async (fields) => {
      const form = new FormData();
      Object.entries(fields).forEach(([k, v]) => form.append(k, v));
      const response = await fetch(`/api/admin/user/${id}`, { method: 'POST', body: form });
      if (!response.ok) {
        status.innerHTML = `<span class="bad">${(await response.json()).detail}</span>`;
        await loadUsers();
        return;
      }
      status.innerHTML = '<span class="ok">сохранено</span>';
      setTimeout(() => { status.textContent = ''; }, 1500);
    };
    row.querySelector('[data-act="unlimited"]').onchange = (e) =>
      send({ unlimited: e.target.checked });
    row.querySelector('[data-act="admin"]').onchange = (e) =>
      send({ is_admin: e.target.checked });
    row.querySelector('[data-act="note"]').onchange = (e) =>
      send({ note: e.target.value });
    row.querySelector('[data-act="grant"]').onclick = async () => {
      await send({ grant_days: 30 });
      await loadUsers();
    };
  });
}

boot();
