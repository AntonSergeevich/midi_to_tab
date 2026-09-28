// Вход, регистрация, восстановление пароля.

const $ = (id) => document.getElementById(id);

// Откуда пришли: после входа или регистрации -- туда же (например, в
// Студию с недописанной песней или к оплате). Только свои пути сайта.
function nextPage() {
  const next = new URLSearchParams(location.search).get('next') || '';
  return /^\/[A-Za-z0-9/_?=&%.-]*$/.test(next) && !next.startsWith('//') ? next : '/library';
}

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
  if (me.registered && new URLSearchParams(location.search).get('next')) {
    location.replace(nextPage());   // уже вошли -- сразу дальше
    return;
  }
  // Вход в один клик через российские сервисы (разрешены 149-ФЗ)
  const oauth = me.oauth || {};
  if (!me.registered && (oauth.yandex || oauth.vk)) {
    const next = encodeURIComponent(nextPage());
    const error = new URLSearchParams(location.search).get('oauth_error');
    document.querySelectorAll('[data-oauth]').forEach((box) => {
      box.hidden = false;
      box.querySelectorAll('[data-provider]').forEach((a) => {
        a.hidden = !oauth[a.dataset.provider];
        a.href = `/api/auth/${a.dataset.provider}/start?next=${next}`;
      });
      const note = box.querySelector('.oauth-error');
      if (error) { note.textContent = `Не получилось войти: ${error}`; note.hidden = false; }
    });
  }
  if (!me.registered && new URLSearchParams(location.search).get('next')) {
    show('register');               // пришли из Студии или оплаты -- новичок
    $('regMsg').innerHTML = 'Заведите аккаунт за минуту — всё, что вы заполнили, дождётся вас.';
  }
  if (me.registered) {
    $('forms').style.display = 'none';
    $('profile').style.display = '';
    $('who').textContent = me.email;
    const status = me.unlimited ? 'Безлимитный доступ'
      : me.subscribed ? `Подписка до ${new Date(me.paidUntil * 1000).toLocaleDateString('ru-RU')}` : '';
    $('plan').textContent = [status, money(me)].filter(Boolean).join(' · ');
  }
  if (!me.mailReady) {
    $('showForgot').title = 'Отправка писем на сервере не настроена';
  }
  await loadBilling(me);
  await loadInvites(me);
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
    setTimeout(() => (location.href = nextPage()), 500);
  } catch (error) { fail('loginMsg', error); }
};

$('doRegister').onclick = async () => {
  $('regMsg').textContent = 'Создаю…';
  try {
    await post('/api/auth/register', {
      email: $('regEmail').value, password: $('regPassword').value,
    });
    $('regMsg').innerHTML = '<span class="ok">Готово</span>';
    setTimeout(() => (location.href = nextPage()), 500);
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
    setTimeout(() => (location.href = nextPage()), 700);
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


// ------------------------------------------------------------------ тариф

// Сколько у человека оплачено -- независимо от того, какой у него доступ.
function money(me) {
  const parts = [];
  if (me.studioCredits > 0) parts.push(`${me.studioCredits} ${plural(me.studioCredits, 'кредит', 'кредита', 'кредитов')} Студии`);
  if (me.balance > 0) parts.push(`баланс ${Math.round(me.balance)} ₽`);
  if (me.credits > 0) parts.push(`оплачено разборов: ${me.credits}`);
  return parts.join(', ');
}

function plural(n, one, few, many) {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

// Оплата должна быть видна ВСЕГДА, а не только когда упёрся в предел.
// Раньше кнопки появлялись лишь после того, как кончались пробные песни,
// и владелец с безлимитом своей же оплаты не видел никогда.
async function loadBilling(me) {
  if (!me.registered) return;
  const now = $('billingNow');
  const status = me.unlimited
    ? 'Безлимитный доступ — платить не нужно.'
    : me.subscribed
      ? `Подписка активна до ${new Date(me.paidUntil * 1000).toLocaleDateString('ru-RU')}.`
      : 'Подписки нет.';
  const paid = money(me);
  now.innerHTML = `${escapeHtml(paid ? `${paid[0].toUpperCase()}${paid.slice(1)}. ` : '')}${escapeHtml(status)}
    <br>Аккорды — бесплатно, до ${me.freeChordsPerDay || 30} песен в день.`;
  loadPayments();

  // Клик отмечает тариф, платит отдельная кнопка ниже -- чтобы не улетать
  // на оплату по первому касанию. Подписки дают кредиты на 30 дней, пакеты
  // -- кредиты без подписки, они не сгорают.
  const card = (p) => `
    <button class="choice" type="button" data-plan="${p.id}">
      <b>${escapeHtml(p.title.replace(' на 30 дней', ''))} — ${Math.round(p.price)} ₽${p.subscription ? '/мес' : ''}</b>
      <small>${p.studioCredits} кредитов${p.subscription ? ' на 30 дней + разборы без ограничений' : ', не сгорают'}</small>
    </button>`;
  const plans = me.plans || [];
  $('plans').innerHTML = plans.filter((p) => p.subscription).map(card).join('')
    + plans.filter((p) => !p.subscription).map(card).join('');
  $('billing').style.display = '';

  if (!me.paymentReady) {
    $('billingMsg').innerHTML =
      '<span class="bad">Приём оплаты ещё не подключён на сервере.</span>';
    $('plans').querySelectorAll('button').forEach((b) => (b.disabled = true));
    return;
  }

  let billingPlan = null;
  $('plans').querySelectorAll('[data-plan]').forEach((button) => {
    button.onclick = () => {
      $('plans').querySelectorAll('[data-plan]').forEach((b) => b.classList.remove('selected'));
      button.classList.add('selected');
      billingPlan = button.dataset.plan;
      const pay = $('billingPay');
      pay.style.display = 'block';
      pay.textContent = `Оплатить ${button.querySelector('b').textContent}`;
    };
  });
  $('billingPay').onclick = async () => {
    if (!billingPlan) return;
    const button = $('billingPay');
    button.classList.add('busy');
    const form = new FormData();
    form.append('plan', billingPlan);
    const response = await fetch('/api/subscribe', { method: 'POST', body: form });
    const data = await response.json();
    button.classList.remove('busy');
    if (!response.ok) {
      $('billingMsg').innerHTML = `<span class="bad">${data.detail}</span>`;
      return;
    }
    if (data.paymentUrl) location.href = data.paymentUrl;
  };
}

// История платежей: видно не только итог, но и каждый платёж со статусом.
// "Ожидает оплаты" -- платёжный сервис ещё не подтвердил; значит, деньги
// не зачислены, и это не ошибка отображения, а вопрос к оплате.
async function loadPayments() {
  const box = $('payments');
  if (!box) return;
  const { payments } = await (await fetch('/api/payments')).json();
  if (!payments.length) {
    box.innerHTML = '';
    return;
  }
  box.innerHTML = '<h3 style="margin:18px 0 8px;font-size:15px">Платежи</h3>' +
    payments.map((p) => `
      <div class="muted" style="padding:6px 0;border-bottom:1px solid var(--border)">
        ${new Date(p.when * 1000).toLocaleString('ru-RU')} ·
        ${p.what} · <b>${p.amount} ₽</b> ·
        <span class="${p.paid ? 'ok' : 'bad'}">${p.status}</span>
      </div>`).join('');
}

// ------------------------------------------------------------ приглашения

async function loadInvites(me) {
  if (!me.isAdmin) return;
  $('invites').style.display = '';
  const draw = async () => {
    const { invites } = await (await fetch('/api/invites')).json();
    $('inviteList').innerHTML = invites.length ? invites.map((i) => `
      <div class="part">
        <span class="name" style="font:14px/1.4 Consolas,monospace">${i.url}</span>
        <span class="spacer"></span>
        <span class="muted">${i.note || ''} · осталось ${i.uses_left}, прошло ${i.used}</span>
        <button data-copy="${i.url}">Копировать</button>
        <button class="del" data-kill="${i.code}">Удалить</button>
      </div>`).join('') : '<p class="muted">Ссылок пока нет.</p>';

    $('inviteList').querySelectorAll('[data-copy]').forEach((b) => {
      b.onclick = async () => {
        try {
          await navigator.clipboard.writeText(b.dataset.copy);
          b.textContent = 'скопировано';
          setTimeout(() => (b.textContent = 'Копировать'), 1500);
        } catch (e) {
          prompt('Скопируйте ссылку:', b.dataset.copy);
        }
      };
    });
    $('inviteList').querySelectorAll('[data-kill]').forEach((b) => {
      b.onclick = async () => {
        await fetch(`/api/invites/${b.dataset.kill}`, { method: 'DELETE' });
        draw();
      };
    });
  };
  $('makeInvite').onclick = async () => {
    const form = new FormData();
    form.append('uses', $('inviteUses').value);
    form.append('note', $('inviteNote').value);
    await fetch('/api/invites', { method: 'POST', body: form });
    $('inviteNote').value = '';
    draw();
  };
  draw();
}
