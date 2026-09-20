// Согласие на cookie.
//
// По требованиям 2026 года баннер обязан давать выбор, а не просто
// уведомлять, и необязательные cookie не должны ставиться до согласия.
// Поэтому аналитика подключается ТОЛЬКО после нажатия «принять все»:
// сначала спрашиваем, потом ставим, а не наоборот.
//
// Техническая кука входа (uid) к необязательным не относится: без неё
// сервис не работает вовсе -- не запомнить, чьи это треки и сколько
// песен осталось. Согласия она не требует, но о ней сказано в политике.

(function () {
  const KEY = 'naslux_consent';
  const saved = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const save = (v) => { try { localStorage.setItem(KEY, v); } catch {} };

  window.consentGiven = (kind) => saved() === kind;

  // Сюда подключается аналитика, когда она появится. Вызывается только
  // после явного согласия -- на этом и держится соответствие требованиям.
  function enableAnalytics() {
    window.dispatchEvent(new CustomEvent('consent:analytics'));
  }

  if (saved() === 'all') { enableAnalytics(); return; }
  if (saved() === 'necessary') return;

  const bar = document.createElement('div');
  bar.className = 'cookiebar';
  bar.innerHTML = `
    <div class="cookiebar-in">
      <div>
        <b>Мы используем cookie</b>
        <p>Технические cookie нужны, чтобы сервис вас узнавал и помнил ваши треки —
           без них он не работает. Остальные ставим только с вашего согласия.
           Подробности — в <a href="/privacy">политике конфиденциальности</a>.</p>
      </div>
      <div class="cookiebar-btns">
        <button id="ckNec">Только необходимые</button>
        <button class="primary" id="ckAll">Принять все</button>
      </div>
    </div>`;
  document.body.appendChild(bar);
  requestAnimationFrame(() => bar.classList.add('visible'));

  const close = (choice) => {
    save(choice);
    bar.classList.remove('visible');
    setTimeout(() => bar.remove(), 300);
    if (choice === 'all') enableAnalytics();
  };
  bar.querySelector('#ckNec').onclick = () => close('necessary');
  bar.querySelector('#ckAll').onclick = () => close('all');
})();
