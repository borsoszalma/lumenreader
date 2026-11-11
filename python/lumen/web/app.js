'use strict';
/* Lumen Reader – browser UI. Everything is served by the local Python process (same origin),
   so the document frame can be themed and inspected directly. */

const $ = (id) => document.getElementById(id);
const view = $('view');
let S = {};                 // localized strings
let state = null;           // last /api/state
let session = null;         // currently open document
let theme = 'light';
let chapter = -1;
let pdfNav = 0;

// ------------------------------------------------------------------ API
async function api(path, body, raw) {
  const opts = { method: body === undefined ? 'GET' : 'POST' };
  if (body !== undefined) opts.body = raw ? body : JSON.stringify(body);
  const r = await fetch(path, opts);
  return r.json();
}

// ------------------------------------------------------------------ startup
async function init() {
  applyState(await api('api/state'));
  if (state.session) showSession(state.session); else showWelcome();

  const ping = () => fetch('api/ping', { method: 'POST' }).catch(() => {});
  ping();
  setInterval(ping, 3000);
  window.addEventListener('pagehide', () => navigator.sendBeacon('api/bye'));

  $('btnOpen').onclick = chooseFile;
  $('btnToc').onclick = () => setToc(!state.settings.tocVisible);
  $('btnFull').onclick = toggleFullscreen;
  $('hotzone').addEventListener('mouseenter', () => document.body.classList.add('bar'));
  document.querySelector('.toolbar').addEventListener('mouseleave', (e) => {
    if (!document.querySelector('.toolbar').contains(document.activeElement) || e.clientY > 60)
      document.body.classList.remove('bar');
  });
  document.addEventListener('fullscreenchange', syncFullscreen);
  window.addEventListener('resize', syncFullscreen);
  $('btnPrev').onclick = () => goChapter(-1);
  $('btnNext').onclick = () => goChapter(1);
  document.querySelectorAll('.theme').forEach((b) => (b.onclick = () => setTheme(b.dataset.theme)));
  $('lang').onchange = async (e) => {
    applyState(await api('api/settings', { language: e.target.value }));
    if (!session) showWelcome();
  };
  $('fileInput').onchange = (e) => { if (e.target.files[0]) uploadFile(e.target.files[0]); e.target.value = ''; };
  view.addEventListener('load', onFrameLoad);
  wireDragDrop(document);
  document.addEventListener('keydown', onKey);
  initSplitter();
}

function applyState(st) {
  state = st;
  S = st.strings;
  document.documentElement.lang = st.lang;
  theme = st.settings.theme || 'light';
  applyTheme();
  setToc(st.settings.tocVisible !== false, true);
  if (st.settings.tocWidth) $('toc').style.width = st.settings.tocWidth + 'px';

  const k = (key, extra) => (S[key] || key) + (extra ? ` (${extra})` : '');
  $('lblOpen').textContent = S.Open;
  $('btnOpen').title = k('Open', 'Ctrl+O');
  $('btnToc').title = k('Contents', 'Ctrl+B');
  $('btnFull').title = k('Fullscreen', 'F11');
  $('btnPrev').title = k('PrevChapter', 'Alt+←');
  $('btnNext').title = k('NextChapter', 'Alt+→');
  const tips = { light: k('ThemeLight', 'Alt+1'), dark: k('ThemeDark', 'Alt+2'), reading: k('ThemeReading', 'Alt+3') };
  document.querySelectorAll('.theme').forEach((b) => (b.title = tips[b.dataset.theme]));
  $('tocHeader').textContent = S.Contents;
  $('noToc').textContent = S.NoContents;
  $('busyText').textContent = S.Loading;
  $('dropText').textContent = S.WelcomeTitle;
  $('lang').title = S.Language;
  $('fileInput').accept = st.extensions.join(',');

  const sel = $('lang');
  sel.innerHTML = '';
  sel.add(new Option(S.LanguageAuto, 'auto'));
  st.languages.forEach((l) => sel.add(new Option(l.name, l.code)));
  sel.value = st.settings.language || 'auto';
}

// ------------------------------------------------------------------ opening documents
async function chooseFile() {
  showBusy(true);
  let res;
  try { res = await api('api/choose', {}); } catch (e) { res = { unavailable: true }; }
  showBusy(false);
  if (res.unavailable) { $('fileInput').click(); return; }
  handleOpenResult(res);
}

async function uploadFile(file) {
  showBusy(true);
  try {
    handleOpenResult(await api('api/upload?name=' + encodeURIComponent(file.name), file, true));
  } catch (e) {
    showError(String(e));
  } finally {
    showBusy(false);
  }
}

function handleOpenResult(res) {
  if (res.error) showError(res.error);
  else if (res.session) showSession(res.session);
}

function docUrl(rel) {
  let frag = '';
  const i = rel.indexOf('#');
  if (i >= 0) { frag = rel.slice(i); rel = rel.slice(0, i); }
  return 'doc/' + session.id + '/' + rel.split('/').map(encodeURIComponent).join('/') + frag;
}

function showSession(s) {
  session = s;
  chapter = s.kind === 'epub' ? 0 : -1;
  $('welcome').hidden = true;
  view.hidden = false;
  setTitle(s.title);
  renderToc(s.toc, s.toc === null);
  updateChapterButtons();
  applyTheme();
  showBusy(true);
  view.src = docUrl(s.start);
}

function showWelcome() {
  session = null;
  view.hidden = true;
  view.removeAttribute('src');
  setTitle('');
  renderToc([], true);
  updateChapterButtons();
  const T = (k) => esc(S[k] || k);
  const K = (...keys) => keys.map((x) => `<kbd>${x}</kbd>`).join('+');
  const rows = [
    [K('Ctrl', 'O'), T('Open')],
    [K('Ctrl', 'B'), T('Contents')],
    [K('Alt', '1') + ' / ' + K('2') + ' / ' + K('3'), `${T('ThemeLight')} / ${T('ThemeDark')} / ${T('ThemeReading')}`],
    [K('Ctrl', '+') + ' / ' + K('Ctrl', '−') + ' / ' + K('Ctrl', '0'), T('Zoom')],
    [K('Alt', '←') + ' / ' + K('Alt', '→'), T('Chapters')],
    [K('F11'), T('Fullscreen')],
  ];
  $('welcome').innerHTML = `<main>
      <img src="icon.png" alt="">
      <h1>${T('WelcomeTitle')}</h1><p>${T('WelcomeText')}</p>
      <button class="primary" id="welcomeOpen">${T('Open')}</button>
      <h2>${T('SupportedFormats')}</h2>
      <div class="formats">${['EPUB', 'PDF', 'DOCX', 'DOC', 'RTF', 'Markdown', 'HTML', 'TXT'].map((f) => `<span>${f}</span>`).join('')}</div>
      <h2>${T('Shortcuts')}</h2>
      <table class="keys">${rows.map(([a, b]) => `<tr><td>${a}</td><td>${b}</td></tr>`).join('')}</table>
    </main>`;
  $('welcome').hidden = false;
  $('welcomeOpen').onclick = chooseFile;
}

function setTitle(t) {
  $('docTitle').textContent = t || '';
  document.title = t ? `${t} – Lumen Reader` : 'Lumen Reader';
}

// ------------------------------------------------------------------ the document frame
function frameDoc() {
  try { return view.contentDocument; } catch (e) { return null; }
}

function onFrameLoad() {
  showBusy(false);
  if (!session || session.kind === 'pdf') return;
  const doc = frameDoc();
  if (!doc || !doc.documentElement) return;

  if (!doc.getElementById('lumen-viewer-css')) {
    const link = doc.createElement('link');
    link.id = 'lumen-viewer-css';
    link.rel = 'stylesheet';
    link.href = new URL('viewer.css', location.href).href;
    (doc.head || doc.documentElement).appendChild(link);
  }
  doc.documentElement.setAttribute('data-lumen-theme', theme);
  if (doc.body) doc.body.classList.add('lumen-kind-' + session.kind);

  if (session.kind === 'html' && doc.title) setTitle(doc.title);
  if (session.toc === null) renderToc(collectHeadings(doc), false);
  if (session.kind === 'epub') {
    const prefix = '/doc/' + session.id + '/';
    const path = decodeURIComponent(view.contentWindow.location.pathname);
    const rel = path.slice(path.indexOf(prefix) + prefix.length);
    const idx = session.spine.findIndex((p) => p.toLowerCase() === rel.toLowerCase());
    if (idx >= 0) chapter = idx;
    updateChapterButtons();
  }

  if (!doc.__lumenWired) {
    doc.__lumenWired = true;
    doc.addEventListener('keydown', onKey);
    wireDragDrop(doc);
    doc.addEventListener('click', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      const url = new URL(a.getAttribute('href'), doc.baseURI);
      if (url.origin !== location.origin) {
        e.preventDefault();
        api('api/external', { url: url.href });
      }
    });
  }
}

function collectHeadings(doc) {
  const items = [];
  const stack = [];
  let n = 0;
  doc.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach((h) => {
    const text = (h.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text) return;
    if (!h.id) h.id = 'lumen-h-' + ++n;
    const level = Number(h.tagName.substring(1));
    const node = { title: text.slice(0, 200), anchor: h.id, children: [] };
    while (stack.length && stack[stack.length - 1].level >= level) stack.pop();
    if (stack.length) stack[stack.length - 1].node.children.push(node); else items.push(node);
    stack.push({ level, node });
  });
  return items;
}

// ------------------------------------------------------------------ table of contents
function renderToc(items, waiting) {
  const tree = $('tocTree');
  tree.innerHTML = '';
  $('noToc').hidden = !(session && !waiting && (!items || items.length === 0));
  if (!items || !items.length) return;
  const expandTop = items.length <= 60;
  const build = (list, depth) => {
    const ul = document.createElement('ul');
    if (depth === 0) ul.className = 'tree';
    list.forEach((item) => {
      const li = document.createElement('li');
      const row = document.createElement('div');
      row.className = 'row';
      const tw = document.createElement('span');
      const kids = item.children && item.children.length;
      tw.className = 'tw' + (kids ? '' : ' leaf');
      tw.textContent = kids ? '▸' : '';
      const lbl = document.createElement('span');
      lbl.className = 'lbl';
      lbl.textContent = item.title;
      lbl.title = item.title;
      row.append(tw, lbl);
      li.append(row);
      if (kids) {
        li.append(build(item.children, depth + 1));
        const collapsed = !(depth === 0 && expandTop);
        li.classList.toggle('collapsed', collapsed);
        tw.textContent = collapsed ? '▸' : '▾';
        tw.onclick = () => { const c = li.classList.toggle('collapsed'); tw.textContent = c ? '▸' : '▾'; };
      }
      lbl.onclick = () => {
        tree.querySelectorAll('.row.active').forEach((r) => r.classList.remove('active'));
        row.classList.add('active');
        navigate(item);
      };
      ul.append(li);
    });
    return ul;
  };
  tree.append(build(items, 0));
}

function navigate(item) {
  if (!session) return;
  if (item.page) {
    // the query part forces the built-in PDF viewer to honour the new #page
    view.src = docUrl(session.start) + '?n=' + ++pdfNav + '#page=' + item.page;
    return;
  }
  const doc = frameDoc();
  if (item.anchor) {
    const el = doc && doc.getElementById(item.anchor);
    if (el) el.scrollIntoView({ block: 'start' });
    return;
  }
  if (!item.href) return;
  const [target, frag] = item.href.split('#');
  const prefix = '/doc/' + session.id + '/';
  let current = '';
  try {
    const p = decodeURIComponent(view.contentWindow.location.pathname);
    current = p.slice(p.indexOf(prefix) + prefix.length);
  } catch (e) { /* ignore */ }
  if (doc && current.toLowerCase() === target.toLowerCase()) {
    const el = frag ? (doc.getElementById(decodeURIComponent(frag)) || doc.getElementsByName(frag)[0]) : null;
    if (el) el.scrollIntoView({ block: 'start' }); else view.contentWindow.scrollTo(0, 0);
  } else {
    view.src = docUrl(item.href);
  }
}

function setToc(visible, silent) {
  document.body.classList.toggle('toc-hidden', !visible);
  $('btnToc').classList.toggle('on', visible);
  if (!silent) {
    state.settings.tocVisible = visible;
    api('api/settings', { tocVisible: visible });
  }
}

function initSplitter() {
  const sp = $('splitter');
  sp.addEventListener('mousedown', (e) => {
    e.preventDefault();
    document.body.classList.add('resizing');
    const move = (ev) => { $('toc').style.width = Math.min(600, Math.max(160, ev.clientX)) + 'px'; };
    const up = () => {
      document.body.classList.remove('resizing');
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      api('api/settings', { tocWidth: parseInt($('toc').style.width, 10) });
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });
}

// ------------------------------------------------------------------ EPUB chapters
function updateChapterButtons() {
  const epub = session && session.kind === 'epub';
  $('btnPrev').disabled = !epub || chapter <= 0;
  $('btnNext').disabled = !epub || chapter < 0 || chapter >= session.spine.length - 1;
}

function goChapter(delta) {
  if (!session || session.kind !== 'epub') return;
  const target = chapter + delta;
  if (target < 0 || target >= session.spine.length) return;
  chapter = target;
  view.src = docUrl(session.spine[target]);
  updateChapterButtons();
}

// ------------------------------------------------------------------ themes
function setTheme(t) {
  theme = t;
  applyTheme();
  state.settings.theme = t;
  api('api/settings', { theme: t });
}

function applyTheme() {
  document.body.dataset.theme = theme;
  document.querySelectorAll('.theme').forEach((b) => b.classList.toggle('on', b.dataset.theme === theme));
  view.className = session && session.kind === 'pdf' && theme !== 'light' ? 'pdf-' + theme : '';
  const doc = session && session.kind !== 'pdf' ? frameDoc() : null;
  if (doc && doc.documentElement) doc.documentElement.setAttribute('data-lumen-theme', theme);
}

// ------------------------------------------------------------------ keyboard
function onKey(e) {
  const key = e.key;
  let handled = true;
  if (e.ctrlKey && !e.altKey && (key === 'o' || key === 'O')) chooseFile();
  else if (e.ctrlKey && !e.altKey && (key === 'b' || key === 'B')) setToc(!state.settings.tocVisible);
  else if (key === 'F11') toggleFullscreen();
  else if (e.altKey && key === '1') setTheme('light');
  else if (e.altKey && key === '2') setTheme('dark');
  else if (e.altKey && key === '3') setTheme('reading');
  else if (e.altKey && key === 'ArrowLeft' && session && session.kind === 'epub') goChapter(-1);
  else if (e.altKey && key === 'ArrowRight' && session && session.kind === 'epub') goChapter(1);
  else handled = false;
  if (handled) e.preventDefault();
}

// ------------------------------------------------------------------ full screen
function isFullscreen() {
  // Fullscreen API, or the browser's own F11 full screen (window as large as the screen)
  return !!document.fullscreenElement ||
    (Math.abs(window.innerWidth - screen.width) <= 1 && Math.abs(window.innerHeight - screen.height) <= 1);
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen({ navigationUI: 'hide' });
  } catch (e) { /* not allowed: ignore */ }
  syncFullscreen();
}

function syncFullscreen() {
  const fs = isFullscreen();
  document.body.classList.toggle('fs', fs);
  if (!fs) document.body.classList.remove('bar');
  $('btnFull').querySelector('.ic').innerHTML = fs ? '&#xE73F;' : '&#xE740;';
  $('btnFull').classList.toggle('on', fs);
}

// ------------------------------------------------------------------ drag & drop
let dragDepth = 0;
function hasFiles(e) { return e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files'); }

function wireDragDrop(doc) {
  doc.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth++;
    $('drop').hidden = false;
  });
  doc.addEventListener('dragover', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    $('drop').hidden = false;
  });
  doc.addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth || e.target === $('drop')) $('drop').hidden = true;
  });
  doc.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    $('drop').hidden = true;
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  });
}

// ------------------------------------------------------------------ misc
function showBusy(on) { $('busy').hidden = !on; }

let toastTimer = 0;
function showError(msg) {
  const t = $('toast');
  const title = S.ErrorTitle || '';
  t.textContent = !title || msg.startsWith(title) ? msg : title + '\n' + msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 7000);
  t.onclick = () => (t.hidden = true);
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

init();
