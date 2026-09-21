// Первая страница: шаги открываются по мере надобности, а не висят все сразу.

const $ = (id) => document.getElementById(id);
let chosenFile = null;
let me = null;

// Плавное появление: сначала выводим блок в поток, затем на следующем
// кадре включаем прозрачность -- иначе переход не запускается.
function reveal(id) {
  const el = $(id);
  el.classList.add('shown');
  requestAnimationFrame(() => el.classList.add('visible'));
}

function hide(id) {
  const el = $(id);
  el.classList.remove('visible');
  setTimeout(() => el.classList.remove('shown'), 320);
}

const fill = (select, values) =>
  (select.innerHTML = values.map((v) => `<option>${v}</option>`).join(''));

// ------------------------------------------------------------------ профиль

async function loadMe() {
  me = await (await fetch('/api/me')).json();
  fill($('tuning'), me.tunings);
  fill($('grid'), me.grids);
  $('grid').value = me.grids.find((g) => g.includes('1/16')) || me.grids[0];

  const account = $('account');
  if (account) account.textContent = me.registered ? me.email : 'Вход';
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
    badge.className = 'badge';
  }

  const missing = [];
  if (!me.recognitionReady) missing.push('распознавание нот');
  if (!me.separationReady) missing.push('разделение на партии');
  if (!me.lyricsReady) missing.push('распознавание текста');
  $('caps').textContent = missing.length
    ? 'На сервере не установлено: ' + missing.join(', ') + '.'
    : '';
  $('caps').className = missing.length ? 'muted bad' : 'muted';

  if (me.jobs && me.jobs.length) {
    $('jobs').innerHTML = me.jobs.map((j) => `
      <div style="padding:8px 0;border-bottom:1px solid var(--border)">
        ${j.name} — ${j.status === 'done'
          ? `<a href="/player/${j.id}">открыть</a>`
          : j.status === 'error'
            ? '<span class="bad">ошибка</span>'
            : `<span class="muted">${j.stage || j.status} ${Math.round(j.progress || 0)}%</span>`}
      </div>`).join('');
    reveal('history');
  }

  // Обработка идёт на сервере и не прерывается уходом со страницы. Раньше
  // человек, заглянувший в «Мои треки» и вернувшийся назад, видел чистую
  // форму и думал, что всё пропало. Теперь подхватываем то, что считается.
  const busy = (me.jobs || []).find((j) => j.status === 'running' || j.status === 'queued');
  if (busy && !chosenFile) {
    $('hint').textContent = `«${busy.name}» — можно закрыть страницу, работа не пропадёт.`;
    reveal('progress');
    watch(busy.id, null);
  }
}

// ------------------------------------------------------------- выбор файла

$('drop').onclick = () => $('file').click();
$('file').onchange = (e) => pick(e.target.files[0]);
['dragover', 'dragenter'].forEach((t) =>
  $('drop').addEventListener(t, (e) => {
    e.preventDefault();
    $('drop').classList.add('over');
  }));
['dragleave', 'drop'].forEach((t) =>
  $('drop').addEventListener(t, () => $('drop').classList.remove('over')));
$('drop').addEventListener('drop', (e) => {
  e.preventDefault();
  pick(e.dataTransfer.files[0]);
});

function pick(file) {
  if (!file) return;
  chosenFile = file;
  $('fileName').textContent = file.name;
  $('fileMeta').textContent = `${(file.size / 1048576).toFixed(1)} МБ`;

  const isMidi = /\.midi?$/i.test(file.name);
  const canSplit = me && me.separationReady && !isMidi;
  const parts = $('modeParts');
  parts.disabled = !canSplit;
  parts.querySelector('small').textContent = isMidi
    ? 'для MIDI не нужно — дорожки уже разделены'
    : canSplit
      ? 'гитара, бас, барабаны, вокал отдельно — потом табы для нужной'
      : 'на сервере не установлено разделение';

  hide('history');
  reveal('chosen');
  $('chosen').scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  if (me && !me.allowed) blocked();
}

$('reset').onclick = () => {
  chosenFile = null;
  $('file').value = '';
  hide('chosen');
  hide('progress');
  loadMe();
};

function blocked() {
  $('modeParts').disabled = true;
  $('modeChords').disabled = true;
  if (!me.paymentReady) {
    $('msg').innerHTML =
      `<span class="bad">${me.reason}</span> Оплата пока не подключена.`;
    return;
  }
  // Два тарифа рядом: разовый для «попробовать ещё одну»,
  // подписка для тех, кто разбирает песни постоянно.
  $('msg').innerHTML = `
    <div class="bad" style="margin-bottom:12px">${me.reason}</div>
    <div class="choices">
      <button class="choice" data-plan="single">
        <b>${me.priceSingle} ₽ — один трек</b>
        <small>разово, без подписки</small>
      </button>
      <button class="choice" data-plan="month">
        <b>${me.price} ₽ — месяц без ограничений</b>
        <small>выгоднее с одиннадцатого трека</small>
      </button>
    </div>`;
  document.querySelectorAll('[data-plan]').forEach((button) => {
    button.onclick = () => subscribe(button.dataset.plan, button);
  });
}

async function subscribe(plan, button) {
  button.classList.add('busy');
  const form = new FormData();
  form.append('plan', plan);
  const response = await fetch('/api/subscribe', { method: 'POST', body: form });
  if (!response.ok) {
    button.classList.remove('busy');
    $('msg').innerHTML = `<span class="bad">${(await response.json()).detail}</span>`;
    return;
  }
  const data = await response.json();
  if (data.paymentUrl) location.href = data.paymentUrl;
}

// --------------------------------------------------------------- отправка

[$('modeParts'), $('modeChords')].forEach((button) => {
  button.onclick = () => start(button.dataset.sep === '1', button);
});

async function start(separate, button) {
  if (!chosenFile) return;
  button.classList.add('busy');
  [$('modeParts'), $('modeChords')].forEach((b) => (b.disabled = true));

  const form = new FormData();
  form.append('file', chosenFile);
  form.append('separate_track', separate);
  form.append('tuning', $('tuning').value);
  form.append('capo', $('capo').value);
  form.append('tempo', $('tempo').value);
  form.append('grid', $('grid').value);
  form.append('min_chord', $('minchord').value);
  form.append('vocabulary', $('vocabulary').value);
  form.append('quality', $('quality').value);
  form.append('chords', $('chords').value);
  form.append('remove_ghosts', $('ghosts').checked);
  form.append('max_polyphony', $('poly').value);

  $('hint').textContent = separate
    ? 'Разделение — самый долгий шаг. Трек останется в «Моих треках».'
    : 'Аккорды считаются быстро.';
  reveal('progress');
  $('msg').textContent = '';

  const response = await fetch('/api/upload', { method: 'POST', body: form });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Ошибка загрузки' }));
    hide('progress');
    button.classList.remove('busy');
    $('msg').innerHTML = `<span class="bad">${error.detail}</span>`;
    await loadMe();
    if (!me.allowed) blocked();
    return;
  }
  watch((await response.json()).jobId, button);
}

function watch(jobId, button) {
  const show = (percent) => {
    const value = Math.max(0, Math.min(100, Math.round(percent || 0)));
    $('percent').textContent = `${value}%`;
    $('barFill').style.width = `${value}%`;
  };
  const timer = setInterval(async () => {
    const job = await (await fetch(`/api/job/${jobId}`)).json();
    if (job.stage) $('stage').textContent = job.stage;
    show(job.progress);
    if (job.status === 'done') {
      clearInterval(timer);
      show(100);
      $('stage').textContent = 'Готово, открываю…';
      setTimeout(() => (location.href = `/player/${jobId}`), 320);
    } else if (job.status === 'error') {
      clearInterval(timer);
      hide('progress');
      if (button) button.classList.remove('busy');
      [$('modeParts'), $('modeChords')].forEach((b) => (b.disabled = false));
      $('msg').innerHTML = `<span class="bad">${job.error}</span>`;
    }
  }, 1200);
}

loadMe();
