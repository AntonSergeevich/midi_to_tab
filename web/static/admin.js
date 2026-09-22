// Админка: безлимит, продление подписки, заметки.

const $ = (id) => document.getElementById(id);
let all = [];

const when = (ts) => ts ? new Date(ts * 1000).toLocaleDateString('ru-RU',
  { day: 'numeric', month: 'short', year: '2-digit' }) : '—';

async function boot() {
  const me = await (await fetch('/api/me')).json();
  if (me.isAdmin) {
    $('login').style.display = 'none';
    await loadUsers();
    await loadPayment();
    await loadNotices();
  }
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
  await loadPayment();
  await loadNotices();
};
$('key').onkeydown = (e) => { if (e.key === 'Enter') $('enter').click(); };

// Список постраничный: когда людей станет тысяча, выгружать их всех
// разом -- это полминуты ожидания ради одного экрана. Поиск тоже ушёл
// на сервер: искать надо среди всех, а не среди загруженной страницы.
let offset = 0;
let found = 0;
const PAGE = 50;
let searchTimer = null;

async function loadUsers() {
  const query = encodeURIComponent(($('search').value || '').trim());
  const data = await (await fetch(
    `/api/admin/users?q=${query}&offset=${offset}&limit=${PAGE}`)).json();
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
  found = data.total;
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

// Поиск идёт на сервере, поэтому не дёргаем его на каждую букву
$('search').oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { offset = 0; loadUsers(); }, 350);
};

function render() {
  const rows = all;
  const from = found ? offset + 1 : 0;
  const to = offset + rows.length;

  $('users').innerHTML = rows.map((u) => `
    <div class="part" data-id="${u.id}">
      <span class="name" style="font-family:Consolas,monospace"
        title="${u.id}">${u.email || u.short}</span>
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
    </div>`).join('') + `
    <div class="part" style="border:none;background:none">
      <span class="muted">${found ? `${from}–${to} из ${found}` : 'никого не найдено'}</span>
      <span class="spacer"></span>
      <button id="prev" ${offset === 0 ? 'disabled' : ''}>← назад</button>
      <button id="next" ${to >= found ? 'disabled' : ''}>вперёд →</button>
    </div>`;

  if ($('prev')) $('prev').onclick = () => {
    offset = Math.max(0, offset - PAGE);
    loadUsers();
  };
  if ($('next')) $('next').onclick = () => {
    offset += PAGE;
    loadUsers();
  };

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


// --------------------------------------------------------- приём оплаты

// Самая частая причина, по которой оплата "не работает", лежит не в коде:
// переменные не доехали до службы, ключ не заменили на настоящий, службу
// не перезапустили. Показываем ровно то, что видит процесс, -- гадать не
// приходится.
async function loadPayment() {
  const box = document.getElementById('payment');
  if (!box) return;
  const response = await fetch('/api/admin/payment');
  if (!response.ok) return;
  const { состояние: state } = await response.json();
  const ready = state['готов принимать оплату'];
  box.innerHTML = Object.entries(state).map(([key, value]) => {
    const bad = value === false || String(value).includes('НЕ ЗАДАН')
      || String(value).includes('ИЗ ПРИМЕРА');
    return `<div class="part">
      <span class="name">${key}</span><span class="spacer"></span>
      <span class="${bad ? 'bad' : 'muted'}">${value === true ? 'да'
        : value === false ? 'нет' : value}</span></div>`;
  }).join('') + (ready ? '' : `
    <p class="muted" style="margin-top:12px">
      Переменные читаются из <code>/opt/nasluh/nasluh.env</code>.
      После правки обязателен перезапуск:
      <code>systemctl restart nasluh</code>.
    </p>`);
}


// ------------------------------- уведомления от платёжного сервиса

// Формулу подписи GetPlatinum для версии 2 прочитать не удалось: их
// сайт закрыт. Но угадывать её и не нужно -- по первому настоящему
// уведомлению она определяется перебором ходовых способов.
async function loadNotices() {
  const box = document.getElementById('notices');
  if (!box) return;
  const response = await fetch('/api/admin/notices');
  if (!response.ok) return;
  const data = await response.json();
  const rows = data['уведомления'] || [];
  if (!rows.length) {
    box.innerHTML = '<p class="muted">Уведомлений пока не было.</p>';
    return;
  }
  box.innerHTML = rows.map((notice) => {
    const when = new Date(notice['когда'] * 1000).toLocaleString('ru-RU');
    const ok = notice['принято'];
    return `<div class="part" style="align-items:flex-start;flex-wrap:wrap">
      <span class="name">${when}</span>
      <span class="${ok ? 'ok' : 'bad'}">${ok ? 'принято' : 'отвергнуто'}</span>
      <span class="muted">${notice['причина'] || ''}</span>
      <div style="flex-basis:100%;margin-top:8px">
        <pre style="font:11px/1.5 Consolas,monospace;overflow-x:auto;margin:0"
>${JSON.stringify(notice['тело'], null, 1)}</pre>

      </div>
    </div>`;
  }).join('');
}
