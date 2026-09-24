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
    await loadFinance();
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
  await loadFinance();
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
        ${u.credits > 0 ? ` · куплено треков ${u.credits}` : ''}
        ${u.balance > 0 ? ` · баланс ${u.balance} ₽` : ''}
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


// ---------------------------------------------------------------- финансы

// Отчёт для владельца: сколько пришло, что не прошло и дотянет ли месяц
// до цели. Сырые уведомления платёжного сервиса -- внизу, в "Техническом":
// они нужны, только когда оплата сломалась.
const rub = (value) => `${Math.round(value).toLocaleString('ru-RU')} ₽`;
const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн',
  'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const monthName = (key, long) => {
  const [year, month] = key.split('-').map(Number);
  return long
    ? new Date(year, month - 1, 1).toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' })
    : MONTHS[month - 1];
};

async function loadFinance(month = '') {
  const response = await fetch(`/api/admin/finance?month=${encodeURIComponent(month)}`);
  if (!response.ok) return;
  const data = await response.json();
  const s = data.summary;

  const select = $('financeMonth');
  select.innerHTML = [...data.months].reverse().map((m) =>
    `<option value="${m.month}" ${m.month === data.month ? 'selected' : ''}>
      ${monthName(m.month, true)}</option>`).join('');
  select.onchange = () => { financeShowAll = false; loadFinance(select.value); };

  const tile = (label, value, sub = '') => `
    <div class="fin-tile"><div class="label">${label}</div>
      <div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ''}</div>`;
  $('financeTiles').innerHTML = [
    tile('Выручка', rub(s.revenue), `за ${monthName(data.month, true)}`),
    tile('Чистыми', rub(s.net), s.commission ? `комиссия ${rub(s.commission)}` : 'комиссия не пришла'),
    tile('Оплачено', s.paid, `платящих: ${s.payers}`),
    tile('Не прошло', s.failed, s.pending ? `ещё ждут оплаты: ${s.pending}` : ''),
    tile('Средний чек', s.paid ? rub(s.avgCheck) : '—'),
    tile('За всё время', rub(data.allTimeRevenue)),
  ].join('');

  // Прогноз -- только для текущего месяца: по темпу с его начала.
  if (data.forecast) {
    const f = data.forecast;
    const share = Math.min(100, (f.projected / data.goal) * 100);
    $('financeGoal').innerHTML = `<div class="fin-goal">
      <div class="fin-track" role="progressbar" aria-valuemin="0" aria-valuemax="${data.goal}"
        aria-valuenow="${Math.round(f.projected)}"><div class="fill" style="width:${share}%"></div></div>
      <div class="text">Прогноз на месяц: <b>${rub(f.projected)}</b> из ${rub(data.goal)}
        (${share.toFixed(share < 10 ? 1 : 0)}%) — по темпу за ${f.daysPassed} из ${f.daysInMonth} дней.
        ${s.revenue < data.goal
          ? `До цели в этом месяце: <b>${rub(data.goal - s.revenue)}</b>.` : '<b>Цель достигнута.</b>'}
      </div></div>`;
  } else {
    $('financeGoal').innerHTML = '';
  }

  drawRevenue(data.months, data.month);

  const plans = Object.entries(s.byPlan);
  $('financePlans').textContent = plans.length
    ? plans.map(([name, p]) => `${name}: ${p.count} на ${rub(p.sum)}`).join(' · ')
    : '';
  const marks = { paid: ['ok', '✓'], failed: ['bad', '✕'], pending: ['muted', '…'] };
  // Первые 20 -- остальные по кнопке: когда платежей станут сотни,
  // бесконечный список закроет всё, что ниже.
  const LIMIT = 20;
  const shown = financeShowAll ? data.payments : data.payments.slice(0, LIMIT);
  const more = data.payments.length - shown.length;
  $('financeRows').innerHTML = (data.payments.length
    ? shown.map((p) => {
      const [cls, icon] = marks[p.state];
      return `<div class="fin-row">
        <span class="when">${new Date(p.when * 1000).toLocaleString('ru-RU',
          { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</span>
        <span class="who" title="${escapeHtml(p.who)}">${escapeHtml(p.who)}</span>
        <span class="what">${p.what}</span>
        <span class="amount">${rub(p.amount)}</span>
        <span class="${cls}">${icon} ${p.status}</span></div>`;
    }).join('')
    : '<p class="muted">Платежей в этом месяце не было.</p>')
    + (more > 0 ? `<button id="financeMore" style="margin-top:12px">Показать все (ещё ${more})</button>` : '');
  if (more > 0) {
    $('financeMore').onclick = () => { financeShowAll = true; loadFinance(data.month); };
  }
}
let financeShowAll = false;

// Столбцы выручки за 12 месяцев. Одна серия -- без легенды, заголовок её
// называет. Подписаны только лучший и выбранный месяц, остальное -- по
// наведению; клик по столбцу открывает месяц.
function drawRevenue(months, selected) {
  const box = $('financeChart');
  const max = Math.max(...months.map((m) => m.revenue), 1);
  const best = months.reduce((a, b) => (b.revenue > a.revenue ? b : a));
  box.innerHTML = `<div class="fin-chart">
    <div class="fin-bars">${months.map((m) => {
      const label = m.month === selected || (m === best && m.revenue > 0) ? rub(m.revenue) : '';
      return `<div class="fin-col ${m.month === selected ? 'on' : ''}" data-month="${m.month}"
        tabindex="0" role="button" aria-label="${monthName(m.month, true)}: ${rub(m.revenue)}">
        <div class="top">${m.revenue > 0 ? label : ''}</div>
        <div class="fin-bar" style="height:${(m.revenue / max) * 100 * 0.82}%"></div></div>`;
    }).join('')}</div>
    <div class="fin-months">${months.map((m) =>
      `<span class="${m.month === selected ? 'on' : ''}">${monthName(m.month)}</span>`).join('')}</div>
    <div class="fin-tip"></div></div>`;

  const chart = box.querySelector('.fin-chart');
  const tip = box.querySelector('.fin-tip');
  box.querySelectorAll('.fin-col').forEach((col) => {
    const m = months.find((x) => x.month === col.dataset.month);
    const show = () => {
      tip.innerHTML = `<b>${monthName(m.month, true)}</b><br>Выручка: ${rub(m.revenue)}
        <br><span class="muted">оплачено ${m.paid} · не прошло ${m.failed}</span>`;
      tip.style.display = 'block';
      const left = col.offsetLeft + col.offsetWidth / 2 - tip.offsetWidth / 2;
      tip.style.left = `${Math.max(0, Math.min(left, chart.offsetWidth - tip.offsetWidth))}px`;
      tip.style.top = '0px';
    };
    col.onmouseenter = show;
    col.onfocus = show;
    col.onmouseleave = () => { tip.style.display = 'none'; };
    col.onblur = () => { tip.style.display = 'none'; };
    col.onclick = () => { financeShowAll = false; loadFinance(m.month); };
    col.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') loadFinance(m.month); };
  });
}
