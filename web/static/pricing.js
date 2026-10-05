// Страница тарифов. Она нужна отдельно и в меню: раньше оплата жила
// только внутри кабинета и на главной -- и то лишь после того, как
// кончались пробные песни. Человек, готовый заплатить прямо сейчас, не
// находил куда, а владелец с безлимитом не видел её никогда.

const $ = (id) => document.getElementById(id);
let me = null;

const date = (ts) => new Date(ts * 1000).toLocaleDateString('ru-RU');

// Пришли из Студии (?next=/studio&need=49): после оплаты -- обратно туда,
// где ждёт недописанная песня, а не на главную.
const params = new URLSearchParams(location.search);
const nextPath = /^\/[A-Za-z0-9/_-]*$/.test(params.get('next') || '') ? params.get('next') : '/';
const need = parseFloat(params.get('need') || '0');

// Обработчик вешается СРАЗУ, ещё до того, как придут данные о человеке.
// Иначе выходит ловушка: кнопки уже нарисованы сервером и выглядят
// рабочими, а нажатие проваливается в пустоту, пока не ответит /api/me.
// На быстрой связи это доли секунды, на телефоне в метро -- несколько,
// и человек успевает решить, что сайт сломан.
const ready = (async () => {
  me = await (await fetch('/api/me')).json();
  return me;
})();

async function load() {
  await ready;
  if (window.refreshNav) window.refreshNav(me);   // шапка -- общая, nav.js

  // Что у человека уже есть -- одной строкой: подписка, кредиты, деньги
  const have = [
    me.unlimited ? 'безлимитный доступ' : '',
    me.subscribed ? `подписка до ${date(me.paidUntil)}` : '',
    me.studioCredits > 0 ? `кредитов Студии: ${me.studioCredits}` : '',
    me.credits > 0 ? `оплачено разборов: ${me.credits}` : '',
    me.balance > 0 ? `на балансе ${me.balance} ₽` : '',
  ].filter(Boolean);
  $('state').textContent = have.length
    ? `У вас: ${have.join(', ')}. Аккорды — бесплатно всегда.`
    : 'Аккорды любой песни — бесплатно. За регистрацию — 20 кредитов Студии: две песни на пробу.';

  if (!me.paymentReady) {
    $('msg').innerHTML =
      '<span class="bad">Приём оплаты на сервере ещё не подключён.</span>';
  }
}

// ------------------------------------------------------- выбор варианта
// Клик отмечает вариант, а не сразу уводит на оплату -- отдельная кнопка
// "Оплатить" подтверждает выбор. Для пополнения так ещё и даёт вписать
// сумму, прежде чем платить.

let selectedPlan = null;

document.querySelectorAll('[data-plan]').forEach((el) => {
  el.onclick = () => selectPlan(el.dataset.plan, el);
  if (el.dataset.plan === 'topup') {
    el.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') selectPlan('topup', el); };
  }
});
$('amount').addEventListener('input', updatePayLabel);
$('amount').addEventListener('click', (e) => e.stopPropagation());

function selectPlan(plan, el) {
  document.querySelectorAll('[data-plan]').forEach((b) => b.classList.remove('selected'));
  el.classList.add('selected');
  selectedPlan = plan;
  $('topupAmount').style.display = plan === 'topup' ? 'block' : 'none';
  if (plan === 'topup') $('amount').focus();
  $('pay').style.display = 'block';
  updatePayLabel();
}

// Из Студии за конкретной песней: сразу предлагаем пополнить на её цену.
if (need > 0) {
  ready.then(() => {
    const tile = document.querySelector('[data-plan="topup"]');
    if (!tile) return;
    $('amount').value = Math.max(need - (me.balance || 0), me.topupMin || 0, 1);
    selectPlan('topup', tile);
    $('msg').innerHTML = 'После оплаты вернём вас в Студию — всё, что вы заполнили, на месте.';
  });
}

function updatePayLabel() {
  const pay = $('pay');
  if (selectedPlan === 'topup') {
    const amount = parseFloat($('amount').value || '0');
    pay.textContent = amount > 0 ? `Оплатить ${amount} ₽` : 'Оплатить';
  } else {
    const tile = document.querySelector(`[data-plan="${selectedPlan}"] b`);
    pay.textContent = `Оплатить ${tile.textContent}`;
  }
}

$('pay').onclick = () => {
  if (!selectedPlan) return;
  if (selectedPlan === 'topup') {
    const amount = parseFloat($('amount').value || '0');
    if (!amount || amount < me.topupMin || amount > me.topupMax) {
      $('msg').innerHTML =
        `<span class="bad">Сумма — от ${me.topupMin} до ${me.topupMax} ₽</span>`;
      return;
    }
    pay(() => {
      const form = new FormData();
      form.append('amount', amount);
      form.append('next', nextPath);
      return fetch('/api/topup', { method: 'POST', body: form });
    });
  } else {
    pay(() => {
      const form = new FormData();
      form.append('plan', selectedPlan);
      form.append('next', nextPath);
      return fetch('/api/subscribe', { method: 'POST', body: form });
    });
  }
};

// Общая часть покупки любого из трёх вариантов: проверки доступности,
// отправка и разбор ответа отличаются только тем, какой запрос слать.
async function pay(makeRequest) {
  const button = $('pay');
  button.classList.add('busy');
  $('msg').textContent = 'Готовлю оплату…';
  await ready;                     // данные могли ещё не прийти

  if (!me.paymentReady) {
    button.classList.remove('busy');
    $('msg').innerHTML =
      '<span class="bad">Приём оплаты на сервере ещё не подключён.</span>';
    return;
  }
  // Платёж привязывается к учётной записи, а не к браузеру: иначе
  // оплаченное пропадёт вместе с куками или при заходе с телефона.
  if (!me.registered) {
    button.classList.remove('busy');
    $('msg').innerHTML =
      `Сначала <a href="/account?next=${encodeURIComponent(location.pathname + location.search)}">заведите учётную запись</a> — иначе оплаченное ` +
      'потеряется при смене браузера.';
    return;
  }
  let response;
  try {
    response = await makeRequest();
  } catch (error) {
    button.classList.remove('busy');
    $('msg').innerHTML =
      `<span class="bad">Связь с сервером прервалась: ${error.message}</span>`;
    return;
  }
  const data = await response.json().catch(() => ({}));
  button.classList.remove('busy');
  if (!response.ok) {
    // Если сервер упал без внятного тела -- так и говорим, а не "не
    // получилось": человеку нужно знать, что это не он виноват.
    $('msg').innerHTML = `<span class="bad">${data.detail
      || `Сервер ответил ошибкой ${response.status}. Загляните в админку —
          там сохранена причина.`}</span>`;
    return;
  }
  if (data.paymentUrl) {
    location.href = data.paymentUrl;
  } else {
    $('msg').innerHTML =
      '<span class="bad">Платёжный сервис не вернул ссылку на оплату. ' +
      'Загляните в админку — там видно, что он ответил.</span>';
  }
}

load();
