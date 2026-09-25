// Страница тарифов. Она нужна отдельно и в меню: раньше оплата жила
// только внутри кабинета и на главной -- и то лишь после того, как
// кончались пробные песни. Человек, готовый заплатить прямо сейчас, не
// находил куда, а владелец с безлимитом не видел её никогда.

const $ = (id) => document.getElementById(id);
let me = null;

const date = (ts) => new Date(ts * 1000).toLocaleDateString('ru-RU');

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
  $('account').textContent = me.registered ? me.email : 'Вход';

  const badge = $('access');
  if (me.unlimited) {
    badge.textContent = me.isAdmin ? 'Владелец · безлимит' : 'Безлимитный доступ';
    badge.className = 'badge pro';
  } else if (me.subscribed) {
    badge.textContent = 'Подписка активна';
    badge.className = 'badge pro';
  } else if (me.credits > 0) {
    badge.textContent = `Оплачено треков: ${me.credits}`;
    badge.className = 'badge pro';
  } else if (me.balance > 0) {
    badge.textContent = `Баланс: ${me.balance} ₽`;
    badge.className = 'badge pro';
  } else {
    badge.textContent = `Бесплатно песен: ${me.freeLeft} из ${me.freeSongs}`;
  }
  // Баланс -- живые деньги, он виден при любом виде доступа.
  if (me.balance > 0 && !badge.textContent.includes('₽')) {
    badge.textContent += ` · ${me.balance} ₽`;
  }

  const state = me.unlimited
    ? 'У вас безлимитный доступ — платить не нужно. Ниже показано то, что видят остальные.'
    : me.subscribed
      ? `Подписка активна до ${date(me.paidUntil)}. Можно продлить заранее — дни прибавятся к остатку.`
      : me.credits > 0
        ? `Оплачено треков: ${me.credits}. Докупить можно в любой момент.`
        : me.balance > 0
          ? `На балансе: ${me.balance} ₽. Спишется по ${me.priceSingle} ₽, когда начнёте разбор песни.`
          : me.freeLeft > 0
            ? `Бесплатных песен осталось: ${me.freeLeft} из ${me.freeSongs}. Платить пока не нужно.`
            : 'Бесплатные песни закончились. Дальше — разово, по подписке или с баланса.';
  $('state').textContent = me.balance > 0 && (me.unlimited || me.subscribed || me.credits > 0)
    ? `${state} На балансе: ${me.balance} ₽.`
    : state;

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
      return fetch('/api/topup', { method: 'POST', body: form });
    });
  } else {
    pay(() => {
      const form = new FormData();
      form.append('plan', selectedPlan);
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
      'Сначала <a href="/account">заведите учётную запись</a> — иначе оплаченное ' +
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
