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
    me.subscribed && me.paidUntil ? `подписка до ${date(me.paidUntil)}` : '',   // у безлимита даты нет
    me.studioCredits > 0 ? `кредитов Студии: ${me.studioCredits}` : '',
    me.credits > 0 ? `оплачено разборов: ${me.credits}` : '',
    me.balance > 0 ? `на балансе ${me.balance} ₽` : '',
  ].filter(Boolean);
  $('state').textContent = have.length
    ? `У вас: ${have.join(', ')}. Аккорды — бесплатно всегда.`
    : $('state').textContent;            // как написал сервер: подарок за регистрацию

  if (!me.paymentReady) {
    $('msg').innerHTML =
      '<span class="bad">Приём оплаты на сервере ещё не подключён.</span>';
  }
}

// ------------------------------------------------------- выбор варианта
// Нажали на карточку -- она подсвечена, а под рядом раскрываются её
// подробности в той же подсветке, со стрелкой к выбранной: видно, что к чему.
// Оплата -- кнопкой внутри подробностей, а не сразу по нажатию на карточку.

function pick(plan) {
  const card = document.querySelector(`[data-pick="${plan}"]`);
  if (!card) return;
  document.querySelectorAll('[data-pick]').forEach((b) => {
    b.classList.toggle('selected', b === card);
    b.setAttribute('aria-pressed', b === card ? 'true' : 'false');
  });
  document.querySelectorAll('[data-info]').forEach((d) => { d.hidden = d.dataset.info !== plan; });
  placeDetail(card);
  pointAt(card);
  $('msg').textContent = '';
}

// Панель -- сразу под рядом выбранной карточки: на телефоне карточки в
// столбик, и подробности не должны оказываться через три карточки от неё
function placeDetail(card) {
  const detail = $('detail');
  $('plans').append(detail);           // сначала в конец: ряды считаются без неё
  const cards = [...document.querySelectorAll('[data-pick]')];
  const row = cards.filter((c) => Math.abs(c.offsetTop - card.offsetTop) < 4);
  const last = row[row.length - 1];
  last.after(detail);
}

// Стрелка панели -- под серединой выбранной карточки (и на телефоне, где они в столбик)
function pointAt(card) {
  const box = $('detail').getBoundingClientRect();
  const at = card.getBoundingClientRect();
  $('detail').style.setProperty('--arrow', `${Math.max(24, Math.min(box.width - 24, at.left + at.width / 2 - box.left))}px`);
}
window.addEventListener('resize', () => {
  const card = document.querySelector('[data-pick].selected');
  if (card) { placeDetail(card); pointAt(card); }
});

document.querySelectorAll('[data-pick]').forEach((el) => {
  el.addEventListener('click', () => pick(el.dataset.pick));
});
document.querySelectorAll('[data-plan]').forEach((el) => {
  el.addEventListener('click', () => buy(el.dataset.plan, el));
});

const wanted = params.get('plan');
pick(document.querySelector(`[data-pick="${wanted}"]`) ? wanted : 'studio_month');

// Из Студии за конкретной песней (?need=49): кредитов не хватило --
// показываем «Докупить кредиты», после оплаты вернём туда же.
if (need > 0) {
  $('credits').classList.add('wanted');
  $('credits').scrollIntoView({ block: 'center' });
  $('msg').innerHTML = 'Не хватило кредитов — докупите или оформите «Студию». '
    + 'После оплаты вернём вас в Студию: всё, что вы заполнили, на месте.';
}

function buy(plan, button) {
  pay(button, () => {
    const form = new FormData();
    form.append('plan', plan);
    form.append('next', nextPath);
    return fetch('/api/subscribe', { method: 'POST', body: form });
  });
}

// Общая часть покупки любого из трёх вариантов: проверки доступности,
// отправка и разбор ответа отличаются только тем, какой запрос слать.
async function pay(button, makeRequest) {
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
    window.top.location.href = data.paymentUrl;
  } else {
    $('msg').innerHTML =
      '<span class="bad">Платёжный сервис не вернул ссылку на оплату. ' +
      'Загляните в админку — там видно, что он ответил.</span>';
  }
}

load();
