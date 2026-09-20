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
  ].map(([label, value]) => `
    <div class="card" style="margin:0;padding:14px">
      <div class="muted" style="font-size:12px">${label}</div>
      <div style="font-size:26px;font-weight:700;color:var(--accent)">${value}</div>
    </div>`).join('');

  all = data.users;
  render();
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
