// Вход, регистрация, восстановление пароля.

const $ = (id) => document.getElementById(id);

const show = (name) => {
  ['login', 'register', 'forgot', 'reset'].forEach((pane) => {
    $(`pane-${pane}`).style.display = pane === name ? '' : 'none';
  });
  document.querySelectorAll('.tab').forEach((tab) =>
    tab.classList.toggle('active', tab.dataset.tab === name));
  document.querySelector('.tabs').style.display =
    (name === 'forgot' || name === 'reset') ? 'none' : '';
};

async function post(url, fields) {
  const form = new FormData();
  Object.entries(fields).forEach(([k, v]) => form.append(k, v));
  const response = await fetch(url, { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Ошибка');
  return data;
}

const fail = (el, error) =>
  ($(el).innerHTML = `<span class="bad">${error.message}</span>`);

async function boot() {
  const token = new URLSearchParams(location.search).get('reset');
  if (token) { show('reset'); return; }

  const me = await (await fetch('/api/me')).json();
  if (me.registered) {
    $('forms').style.display = 'none';
    $('profile').style.display = '';
    $('who').textContent = me.email;
    $('plan').textContent = me.subscribed ? 'Подписка активна'
      : me.unlimited ? 'Безлимитный доступ'
      : me.credits > 0 ? `Оплачено треков: ${me.credits}`
      : `Бесплатно песен: ${me.freeLeft} из ${me.freeSongs}`;
  }
  if (!me.mailReady) {
    $('showForgot').title = 'Отправка писем на сервере не настроена';
  }
}

document.querySelectorAll('.tab').forEach((tab) => {
  tab.onclick = () => show(tab.dataset.tab);
});
$('showForgot').onclick = (e) => { e.preventDefault(); show('forgot'); };
$('backToLogin').onclick = () => show('login');

$('doLogin').onclick = async () => {
  $('loginMsg').textContent = 'Проверяю…';
  try {
    const data = await post('/api/auth/login', {
      email: $('loginEmail').value, password: $('loginPassword').value,
    });
    $('loginMsg').innerHTML = data.moved
      ? `<span class="ok">Вошли. Перенесено треков: ${data.moved}</span>`
      : '<span class="ok">Вошли</span>';
    setTimeout(() => (location.href = '/library'), 500);
  } catch (error) { fail('loginMsg', error); }
};

$('doRegister').onclick = async () => {
  $('regMsg').textContent = 'Создаю…';
  try {
    await post('/api/auth/register', {
      email: $('regEmail').value, password: $('regPassword').value,
    });
    $('regMsg').innerHTML = '<span class="ok">Готово</span>';
    setTimeout(() => (location.href = '/library'), 500);
  } catch (error) { fail('regMsg', error); }
};

$('doForgot').onclick = async () => {
  $('forgotMsg').textContent = 'Отправляю…';
  try {
    const data = await post('/api/auth/forgot', { email: $('forgotEmail').value });
    $('forgotMsg').innerHTML = `<span class="ok">${data.message}</span>`;
  } catch (error) { fail('forgotMsg', error); }
};

$('doReset').onclick = async () => {
  const token = new URLSearchParams(location.search).get('reset');
  $('resetMsg').textContent = 'Сохраняю…';
  try {
    await post('/api/auth/reset', { token, password: $('newPassword').value });
    $('resetMsg').innerHTML = '<span class="ok">Пароль изменён</span>';
    setTimeout(() => (location.href = '/library'), 700);
  } catch (error) { fail('resetMsg', error); }
};

$('logout').onclick = async () => {
  await fetch('/api/auth/logout', { method: 'POST' });
  location.href = '/';
};

['loginPassword', 'regPassword', 'newPassword'].forEach((id) => {
  $(id).onkeydown = (e) => {
    if (e.key !== 'Enter') return;
    ({ loginPassword: 'doLogin', regPassword: 'doRegister', newPassword: 'doReset' })[id]
      && $({ loginPassword: 'doLogin', regPassword: 'doRegister', newPassword: 'doReset' }[id]).click();
  };
});

// ------------------------------------------------------------- обращения

let topics = {};

async function loadSupport() {
  const data = await (await fetch('/api/support')).json();
  topics = data.topics;
  $('topic').innerHTML = Object.entries(topics)
    .map(([key, spec]) => `<option value="${key}">${spec.title}</option>`).join('');
  showTopicNote();

  const me = await (await fetch('/api/me')).json();
  // Анонимному ответ слать некуда -- спрашиваем почту
  $('emailField').style.display = me.registered ? 'none' : '';

  if (data.tickets.length) {
    $('history').style.display = '';
    $('tickets').innerHTML = data.tickets.map((t) => `
      <div style="padding:12px 0;border-bottom:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
          <b>${t.topic}</b>
          <span class="muted">${new Date(t.at * 1000).toLocaleDateString('ru-RU')}</span>
        </div>
        <div class="muted" style="margin:6px 0">${escapeHtml(t.body)}</div>
        ${t.answer
          ? `<div style="border-left:2px solid var(--accent);padding-left:10px;margin-top:8px">
               <div style="color:var(--accent);font-size:13px">Ответ</div>
               <div>${escapeHtml(t.answer)}</div></div>`
          : '<div class="muted" style="font-size:13px">Ждём ответа</div>'}
      </div>`).join('');
  }
}

const escapeHtml = (text) => {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
};

function showTopicNote() {
  const spec = topics[$('topic').value];
  $('topicNote').textContent = spec ? spec.note : '';
}
$('topic').onchange = showTopicNote;

$('sendTicket').onclick = async () => {
  $('ticketMsg').textContent = 'Отправляю…';
  try {
    const data = await post('/api/support', {
      topic: $('topic').value,
      body: $('body').value,
      email: $('supportEmail').value || '',
    });
    $('ticketMsg').innerHTML = `<span class="ok">Отправлено. ${data.note}</span>`;
    $('body').value = '';
    await loadSupport();
  } catch (error) { fail('ticketMsg', error); }
};

boot();
loadSupport();
