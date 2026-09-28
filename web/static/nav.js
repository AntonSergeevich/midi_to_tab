// Шапка на любой странице: кто вошёл и сколько на балансе. Страницы со
// своим скриптом (главная, студия) заполняют её сами -- тогда тут ничего
// не перетирается: трогаем только то, что ещё стоит заглушкой.
(async () => {
  const account = document.getElementById('account');
  const badge = document.getElementById('navBalance');
  if (!account && !badge) return;
  try {
    const me = await (await fetch('/api/me')).json();
    if (account && account.textContent.trim() === 'Вход' && me.registered) {
      account.textContent = me.email;
      account.title = me.email;
    }
    if (badge) {
      badge.textContent = me.unlimited ? 'Безлимит' : `Баланс: ${Math.round(me.balance || 0)} ₽`;
      badge.classList.toggle('pro', Boolean(me.unlimited || me.balance > 0));
      badge.hidden = false;
    }
  } catch (error) {
    // Шапка -- не повод ломать страницу
  }
})();
