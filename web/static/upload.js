// Страница загрузки: выбор файла, настройки, постановка в очередь,
// ожидание результата и переход в плеер.

const $ = (id) => document.getElementById(id);
let chosenFile = null;
let me = null;

function fill(select, values) {
  select.innerHTML = values.map((v) => `<option>${v}</option>`).join('');
}

async function loadMe() {
  me = await (await fetch('/api/me')).json();
  fill($('tuning'), me.tunings);
  fill($('grid'), me.grids);
  fill($('model'), me.models);
  $('grid').value = me.grids.find((g) => g.includes('1/16')) || me.grids[0];

  const badge = $('access');
  if (me.subscribed) {
    badge.textContent = 'Подписка активна';
    badge.className = 'badge pro';
  } else {
    badge.textContent = `Бесплатных песен: ${me.freeLeft} из ${me.freeSongs}`;
    badge.className = 'badge';
  }

  const notes = [];
  if (!me.recognitionReady) notes.push('распознавание аудио не установлено на сервере');
  if (!me.separationReady) notes.push('разделение на дорожки не установлено на сервере');
  if (!me.paymentReady) notes.push('приём оплаты не подключён');
  $('caps').textContent = notes.length ? 'На сервере: ' + notes.join('; ') + '.' : '';
  $('caps').className = notes.length ? 'muted bad' : 'muted';
  if (!me.separationReady) { $('sep').checked = false; $('sep').disabled = true; }

  if (me.jobs && me.jobs.length) {
    $('history').style.display = '';
    $('jobs').innerHTML = me.jobs.map((j) => {
      const label = j.status === 'done'
        ? `<a href="/player/${j.id}">открыть плеер</a>`
        : `<span class="muted">${j.status}</span>`;
      return `<div style="padding:7px 0;border-bottom:1px solid var(--border)">
                ${j.name} — ${label}</div>`;
    }).join('');
  }
  updateButton();
}

function updateButton() {
  const blocked = me && !me.allowed;
  $('go').disabled = !chosenFile || blocked;
  if (blocked) {
    $('msg').innerHTML = `${me.reason} ` +
      (me.paymentReady
        ? '<a href="#" id="pay">Оформить подписку</a>'
        : '<span class="bad">Оплата пока не подключена.</span>');
    const pay = $('pay');
    if (pay) pay.onclick = subscribe;
  }
}

async function subscribe(event) {
  event.preventDefault();
  const response = await fetch('/api/subscribe', { method: 'POST' });
  if (!response.ok) {
    $('msg').textContent = (await response.json()).detail;
    return;
  }
  const data = await response.json();
  if (data.paymentUrl) location.href = data.paymentUrl;
}

// --- выбор файла

$('drop').onclick = () => $('file').click();
$('file').onchange = (e) => pick(e.target.files[0]);
['dragover', 'dragenter'].forEach((t) =>
  $('drop').addEventListener(t, (e) => { e.preventDefault(); $('drop').classList.add('over'); }));
['dragleave', 'drop'].forEach((t) =>
  $('drop').addEventListener(t, () => $('drop').classList.remove('over')));
$('drop').addEventListener('drop', (e) => {
  e.preventDefault();
  pick(e.dataTransfer.files[0]);
});

function pick(file) {
  if (!file) return;
  chosenFile = file;
  const mb = (file.size / 1048576).toFixed(1);
  $('picked').textContent = `Выбрано: ${file.name} (${mb} МБ)`;
  const isMidi = /\.midi?$/i.test(file.name);
  if (isMidi) { $('sep').checked = false; $('sep').disabled = true; }
  else if (me && me.separationReady) { $('sep').disabled = false; }
  updateButton();
}

// --- отправка

$('go').onclick = async () => {
  if (!chosenFile) return;
  const form = new FormData();
  form.append('file', chosenFile);
  form.append('tuning', $('tuning').value);
  form.append('capo', $('capo').value);
  form.append('tempo', $('tempo').value);
  form.append('grid', $('grid').value);
  form.append('model', $('model').value);
  form.append('separate_track', $('sep').checked);
  form.append('remove_ghosts', $('ghosts').checked);
  form.append('max_polyphony', $('poly').value);
  form.append('min_chord', $('minchord').value);

  $('go').disabled = true;
  $('progress').style.display = '';
  $('msg').textContent = '';

  const response = await fetch('/api/upload', { method: 'POST', body: form });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Ошибка загрузки' }));
    $('progress').style.display = 'none';
    $('msg').innerHTML = `<span class="bad">${error.detail}</span>`;
    await loadMe();
    return;
  }
  watch((await response.json()).jobId);
};

function watch(jobId) {
  const timer = setInterval(async () => {
    const job = await (await fetch(`/api/job/${jobId}`)).json();
    $('stage').textContent = job.stage || job.status;
    if (job.status === 'done') {
      clearInterval(timer);
      location.href = `/player/${jobId}`;
    } else if (job.status === 'error') {
      clearInterval(timer);
      $('progress').style.display = 'none';
      $('msg').innerHTML = `<span class="bad">${job.error}</span>`;
      $('go').disabled = false;
    }
  }, 1200);
}

loadMe();
