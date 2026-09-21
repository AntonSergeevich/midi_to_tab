// Страница тарифов. Она нужна отдельно и в меню: раньше оплата жила
// только внутри кабинета и на главной -- и то лишь после того, как
// кончались пробные песни. Человек, готовый заплатить прямо сейчас, не
// находил куда, а владелец с безлимитом не видел её никогда.

const $ = (id) => document.getElementById(id);
let me = null;

const date = (ts) => new Date(ts * 1000).toLocaleDateString('ru-RU');

async function load() {
  me = await (await fetch('/api/me')).json();
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
  } else {
    badge.textContent = `Бесплатно песен: ${me.freeLeft} из ${me.freeSongs}`;
  }

  $('state').textContent = me.unlimited
    ? 'У вас безлимитный доступ — платить не нужно. Ниже показано то, что видят остальные.'
    : me.subscribed
      ? `Подписка активна до ${date(me.paidUntil)}. Можно продлить заранее — дни прибавятся к остатку.`
      : me.credits > 0
        ? `Оплачено треков: ${me.credits}. Докупить можно в любой момент.`
        : me.freeLeft > 0
          ? `Бесплатных песен осталось: ${me.freeLeft} из ${me.freeSongs}. Платить пока не нужно.`
          : 'Бесплатные песни закончились. Дальше — разово или по подписке.';

  // Кнопки уже на странице -- их отдал сервер. Здесь только оживляем.
  if (!me.paymentReady) {
    $('msg').innerHTML =
      '<span class="bad">Приём оплаты на сервере ещё не подключён.</span>';
    $('plans').querySelectorAll('button').forEach((b) => (b.disabled = true));
    return;
  }

  $('plans').querySelectorAll('[data-plan]').forEach((button) => {
    button.onclick = () => buy(button.dataset.plan, button);
  });
}

async function buy(plan, button) {
  // Платёж привязывается к учётной записи, а не к браузеру: иначе
  // оплаченное пропадёт вместе с куками или при заходе с телефона.
  if (!me.registered) {
    $('msg').innerHTML =
      'Сначала <a href="/account">заведите учётную запись</a> — иначе оплаченное ' +
      'потеряется при смене браузера.';
    return;
  }
  button.classList.add('busy');
  $('msg').textContent = 'Готовлю оплату…';
  const form = new FormData();
  form.append('plan', plan);
  const response = await fetch('/api/subscribe', { method: 'POST', body: form });
  const data = await response.json().catch(() => ({}));
  button.classList.remove('busy');
  if (!response.ok) {
    $('msg').innerHTML = `<span class="bad">${data.detail || 'Не получилось'}</span>`;
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
