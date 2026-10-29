// Подписи, шаг и курсор для съёмки инструкции (только для записи видео)
(() => {
  const css = `
  #dmCap{position:fixed;left:50%;bottom:34px;transform:translateX(-50%) translateY(12px);z-index:2147483646;
    max-width:min(1040px,calc(100vw - 40px));padding:16px 26px;border-radius:16px;background:rgba(14,15,20,.93);
    border:1px solid rgba(217,154,78,.55);box-shadow:0 18px 50px rgba(0,0,0,.55);color:#f2f3f7;
    font:600 24px/1.35 Inter,-apple-system,Segoe UI,sans-serif;text-align:center;opacity:0;transition:opacity .35s,transform .35s;pointer-events:none}
  #dmCap.on{opacity:1;transform:translateX(-50%)}
  #dmCap b{color:#f0b66c}
  #dmStep{position:fixed;left:24px;top:18px;z-index:2147483646;padding:8px 14px;border-radius:999px;
    background:linear-gradient(180deg,#f0b66c,#d99a4e);color:#1a1206;font:800 15px/1 Inter,sans-serif;letter-spacing:.02em;
    box-shadow:0 8px 24px rgba(217,154,78,.35);opacity:0;transition:opacity .3s;pointer-events:none}
  #dmStep.on{opacity:1}
  #dmCur{position:fixed;left:0;top:0;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;z-index:2147483647;
    background:rgba(255,255,255,.92);box-shadow:0 0 0 3px rgba(217,154,78,.9),0 6px 18px rgba(0,0,0,.5);pointer-events:none;
    transition:transform .75s cubic-bezier(.3,.9,.3,1);transform:translate(640px,420px)}
  .dmRip{position:fixed;width:20px;height:20px;margin:-10px 0 0 -10px;border-radius:50%;z-index:2147483646;pointer-events:none;
    border:3px solid #f0b66c;animation:dmRip .6s ease-out forwards}
  @keyframes dmRip{to{transform:scale(3.4);opacity:0}}
  @media (max-width:500px){#dmCap{font-size:16px;padding:10px 14px;bottom:18px}#dmStep{font-size:12px;left:12px;top:10px}}`;
  const ready = () => {
    if (document.getElementById('dmCap')) return;
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    for (const [id, tag] of [['dmCap', 'div'], ['dmStep', 'div'], ['dmCur', 'div']]) {
      const el = document.createElement(tag); el.id = id; el.setAttribute('data-noi18n', ''); document.body.appendChild(el);
    }
    const cur = document.getElementById('dmCur');
    const last = sessionStorage.getItem('dmCur');
    if (last) cur.style.transform = last;
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready); else ready();
  window.__dm = {
    cap(html) { ready(); const c = document.getElementById('dmCap'); c.classList.remove('on');
      setTimeout(() => { c.innerHTML = html; c.classList.toggle('on', Boolean(html)); }, html ? 180 : 0); },
    step(text) { ready(); const s = document.getElementById('dmStep'); s.textContent = text; s.classList.toggle('on', Boolean(text)); },
    at(x, y) { ready(); const t = `translate(${x}px,${y}px)`; document.getElementById('dmCur').style.transform = t; sessionStorage.setItem('dmCur', t); },
    point(sel, dx = 0, dy = 0) {
      const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
      if (!el) return null;
      const r = el.getBoundingClientRect();
      if (r.top < 70 || r.bottom > innerHeight - 150) { el.scrollIntoView({ block: 'center' }); }
      const b = el.getBoundingClientRect();
      const x = b.left + b.width / 2 + dx, y = b.top + b.height / 2 + dy;
      this.at(x, y); return [x, y];
    },
    rip(x, y) { const r = document.createElement('div'); r.className = 'dmRip'; r.style.left = `${x}px`; r.style.top = `${y}px`;
      document.body.appendChild(r); setTimeout(() => r.remove(), 700); },
  };
})();
