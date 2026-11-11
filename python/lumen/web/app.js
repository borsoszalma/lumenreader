'use strict';
/* Lumen Reader – browser UI. Everything is served by the local Python process (same origin), so the
   document frame can be themed, measured and annotated directly. */

const $ = (id) => document.getElementById(id);
const view = $('view');
let S = {};                 // localized strings
let state = null;           // last /api/state
let session = null;         // currently open document
let theme = 'light';
let chapter = -1;           // EPUB spine index
let annotations = [];       // highlights and notes of the open document
let pendingSel = null;      // selection waiting for a colour
let editing = null;         // annotation in the note editor
let afterLoad = null;       // action to run after the next frame load (e.g. jump to a note)
let copyToastShown = false;
let tocFlat = [];           // flattened table of contents: { item, level, row, li }
let findState = { q: '', matches: [], cur: -1, seq: 0, ids: [] };

const fmt = (s, ...a) => String(s || '').replace(/\{(\d)\}/g, (_, i) => a[i]);

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
  if (state.session) showSession(state.session); else showHome();

  const ping = () => fetch('api/ping', { method: 'POST' }).catch(() => {});
  ping();
  setInterval(ping, 3000);
  window.addEventListener('pagehide', () => { savePosition(true); navigator.sendBeacon('api/bye'); });

  $('btnHome').onclick = showHome;
  $('btnOpen').onclick = chooseFile;
  $('btnToc').onclick = () => setToc(!state.settings.tocVisible);
  $('btnPrev').onclick = () => goChapter(-1);
  $('btnNext').onclick = () => goChapter(1);
  $('btnFull').onclick = toggleFullscreen;
  $('tabContents').onclick = () => showPanel('contents');
  $('tabNotes').onclick = () => showPanel('notes');
  $('hotzone').addEventListener('mouseenter', () => document.body.classList.add('bar'));
  document.querySelector('.toolbar').addEventListener('mouseleave', (e) => {
    if (!document.querySelector('.toolbar').contains(document.activeElement) || e.clientY > 60)
      document.body.classList.remove('bar');
  });
  document.addEventListener('fullscreenchange', syncFullscreen);
  window.addEventListener('resize', syncFullscreen);
  document.querySelectorAll('.theme').forEach((b) => (b.onclick = () => setTheme(b.dataset.theme)));
  $('lang').onchange = async (e) => {
    applyState(await api('api/settings', { language: e.target.value }));
    if (!session) showHome(); else { renderNotes(); updateProgress(); updateCopyInfo(session.copyPath); renderFindInfo(); }
  };
  $('fileInput').onchange = (e) => { if (e.target.files[0]) uploadFile(e.target.files[0]); e.target.value = ''; };
  view.addEventListener('load', onFrameLoad);
  wireDragDrop(document);
  document.addEventListener('keydown', onKey);
  document.addEventListener('mousedown', (e) => {
    if (!$('selPopup').contains(e.target)) hideSelPopup();
    if (!$('editor').hidden && !$('editor').contains(e.target)) closeEditor();
  });
  document.querySelectorAll('#selPopup .dot').forEach((d) => (d.onclick = () => createAnnotation(d.dataset.color, false)));
  $('selNote').onclick = () => createAnnotation('yellow', true);
  document.querySelectorAll('#editor .dot').forEach((d) => (d.onclick = () => setColor(d.dataset.color)));
  $('edNote').addEventListener('input', () => {
    if (editing) { editing.note = $('edNote').value; editing.updated = Date.now(); scheduleSave(); }
  });
  $('edDelete').onclick = deleteEditing;
  $('edClose').onclick = closeEditor;
  $('btnFind').onclick = openFind;
  $('findClose').onclick = closeFind;
  $('findNext').onclick = () => stepMatch(1);
  $('findPrev').onclick = () => stepMatch(-1);
  let findTimer = 0;
  $('findInput').addEventListener('input', () => {
    clearTimeout(findTimer);
    findTimer = setTimeout(() => runFind($('findInput').value), 250);
  });
  $('findInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); if (findState.q !== $('findInput').value.trim()) runFind($('findInput').value); else stepMatch(e.shiftKey ? -1 : 1); }
    else if (e.key === 'Escape') { e.preventDefault(); closeFind(); }
  });
  $('restoreInput').onchange = async (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    const res = await api('api/restore', await file.text(), true).catch(() => ({ error: S.BackupInvalid }));
    if (res.error) { showError(res.error); return; }
    showInfo(fmt(S.BackupImported, res.imported));
    state.recent = res.recent;
    if (!session) renderHome();
  };
  window.addEventListener('resize', () => setTimeout(renderRail, 300));
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
  $('btnHome').title = k('Home');
  $('btnOpen').title = k('Open', 'Ctrl+O');
  $('btnToc').title = k('Contents', 'Ctrl+B');
  $('btnPrev').title = k('PrevChapter', 'Alt+←');
  $('btnNext').title = k('NextChapter', 'Alt+→');
  $('btnFull').title = k('Fullscreen', 'F11');
  const tips = { light: k('ThemeLight', 'Alt+1'), dark: k('ThemeDark', 'Alt+2'), reading: k('ThemeReading', 'Alt+3') };
  document.querySelectorAll('.theme').forEach((b) => (b.title = tips[b.dataset.theme]));
  $('tabContents').textContent = S.Contents;
  $('tabNotesLabel').textContent = S.Notes;
  $('noToc').textContent = S.NoContents;
  $('noNotes').textContent = S.NoNotes;
  $('busyText').textContent = S.Loading;
  $('dropText').textContent = S.WelcomeTitle;
  $('selNote').title = S.AddNote;
  document.querySelectorAll('.dot').forEach((d) => (d.title = S.Highlight));
  $('edNote').placeholder = S.NotePlaceholder;
  $('edDelete').title = S.Delete;
  $('edClose').textContent = S.Close;
  $('lang').title = S.Language;
  $('btnFind').title = k('Search', 'Ctrl+F');
  $('findInput').placeholder = S.SearchPlaceholder;
  $('findPrev').title = (S.Search || '') + ' ↑ (Shift+Enter)';
  $('findNext').title = (S.Search || '') + ' ↓ (Enter, F3)';
  $('findClose').title = S.Close + ' (Esc)';
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

async function openKey(key) {
  showBusy(true);
  try { handleOpenResult(await api('api/open', { key })); } finally { showBusy(false); }
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
  savePosition(true);
  closeEditor();
  hideSelPopup();
  closeFind();
  session = s;
  annotations = Array.isArray(s.annotations) ? s.annotations : [];
  copyToastShown = false;
  chapter = -1;
  $('welcome').hidden = true;
  view.hidden = false;
  $('status').hidden = false;
  setTitle(s.title);
  renderToc(s.toc, s.toc === null);
  renderNotes();
  updateCopyInfo(s.copyPath);
  applyTheme();
  showBusy(true);

  let src;
  if (s.kind === 'pdf') {
    src = 'pdfview.html?file=' + encodeURIComponent(docUrl(s.start));
  } else if (s.kind === 'epub') {
    const saved = s.position && s.position.chapter;
    chapter = Math.max(0, saved ? s.spine.indexOf(saved) : 0);
    src = docUrl(s.spine[chapter] || s.start);
  } else {
    src = docUrl(s.start);
  }
  afterLoad = restorePosition;
  updateChapterButtons();
  updateProgress();
  view.src = src;
}

async function showHome() {
  savePosition(true);
  closeEditor();
  hideSelPopup();
  closeFind();
  session = null;
  annotations = [];
  view.hidden = true;
  view.removeAttribute('src');
  $('status').hidden = true;
  setTitle('');
  renderToc([], true);
  renderNotes();
  updateCopyInfo(null);
  updateChapterButtons();
  try { state.recent = (await api('api/state')).recent; } catch (e) { /* keep the old list */ }
  renderHome();
}

function renderHome() {
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
  const recent = (state.recent || []).slice(0, 12);
  const pct = (r) => Math.round((r.progress || 0) * 100);
  const recentHtml = recent.length ? `<h2>${T('Recent')}</h2><div class="recent">${recent.map((r) => `
      <div class="recent-item${r.exists ? '' : ' missing'}" data-key="${esc(r.key)}" title="${esc(r.path)}">
        <span class="kind">${esc(r.ext || r.kind)}</span>
        <div class="info"><div class="t">${esc(r.title)}</div>
          <div class="meta"><span>${new Date(r.opened * 1000).toLocaleDateString(state.lang)}</span>
            <span class="pbar"><div style="width:${pct(r)}%"></div></span><span>${pct(r)}%</span>
            ${r.notes ? `<span>✎ ${r.notes}</span>` : ''}</div></div>
        <button class="x" data-forget="${esc(r.key)}" title="${T('RemoveFromList')}">✕</button>
      </div>`).join('')}</div>` : '';

  $('welcome').innerHTML = `<main>
      <img src="icon.png" alt="">
      <h1>${T('WelcomeTitle')}</h1><p>${T('WelcomeText')}</p>
      <button class="primary" id="welcomeOpen">${T('Open')}</button>
      ${recentHtml}
      <h2>${T('Backup')}</h2>
      <div class="backup"><a href="api/backup" download>${T('BackupExport')}</a> · <a href="#" id="restoreLink">${T('BackupImport')}</a></div>
      <h2>${T('SupportedFormats')}</h2>
      <div class="formats">${['EPUB', 'PDF', 'DOCX', 'DOC', 'RTF', 'Markdown', 'HTML', 'TXT'].map((f) => `<span>${f}</span>`).join('')}</div>
      <h2>${T('Shortcuts')}</h2>
      <table class="keys">${rows.map(([a, b]) => `<tr><td>${a}</td><td>${b}</td></tr>`).join('')}</table>
    </main>`;
  $('welcome').hidden = false;
  $('welcomeOpen').onclick = chooseFile;
  $('restoreLink').onclick = (e) => { e.preventDefault(); $('restoreInput').click(); };
  $('welcome').querySelectorAll('.recent-item').forEach((el) => (el.onclick = (e) => {
    if (e.target.dataset.forget) return;
    openKey(el.dataset.key);
  }));
  $('welcome').querySelectorAll('[data-forget]').forEach((b) => (b.onclick = async (e) => {
    e.stopPropagation();
    state.recent = (await api('api/forget', { key: b.dataset.forget })).recent;
    renderHome();
  }));
}

function setTitle(t) {
  $('docTitle').textContent = t || '';
  document.title = t ? `${t} – Lumen Reader` : 'Lumen Reader';
}

// ------------------------------------------------------------------ the document frame
function frameDoc() {
  try { return view.contentDocument; } catch (e) { return null; }
}

function frameWin() {
  try { return view.contentWindow; } catch (e) { return null; }
}

async function onFrameLoad() {
  showBusy(false);
  if (!session) return;
  const doc = frameDoc();
  if (!doc || !doc.documentElement) return;
  doc.documentElement.setAttribute('data-lumen-theme', theme);

  if (session.kind === 'pdf') {
    wireFrame(doc);
    const win = frameWin();
    if (!win.__lumenPdfWired) {
      win.__lumenPdfWired = true;
      win.addEventListener('lumen-page', (e) => {
        applyMarks(String(e.detail));
        if (findState.matches.length) applyFindMarks(Number(e.detail));
      });
      win.addEventListener('scroll', onFrameScroll, { passive: true });
    }
    const go = () => { const fn = afterLoad; afterLoad = null; if (fn) fn(); updateProgress(); updateCurrentToc(); };
    const waitApi = () => (win.lumenPdf ? win.lumenPdf.ready.then(go) : setTimeout(waitApi, 50));
    waitApi();
    return;
  }

  if (!doc.getElementById('lumen-viewer-css')) {
    const link = doc.createElement('link');
    link.id = 'lumen-viewer-css';
    link.rel = 'stylesheet';
    link.href = new URL('viewer.css', location.href).href;
    const loaded = new Promise((res) => { link.onload = res; link.onerror = res; setTimeout(res, 1500); });
    (doc.head || doc.documentElement).appendChild(link);
    await loaded; // layout must be final before restoring the scroll position
    if (frameDoc() !== doc) return; // another document was opened meanwhile
  }
  if (doc.body) doc.body.classList.add('lumen-kind-' + session.kind);

  if (session.kind === 'html' && doc.title) setTitle(doc.title);
  if (session.toc === null) renderToc(collectHeadings(doc), false);
  if (session.kind === 'epub') {
    const rel = framePath();
    const idx = session.spine.findIndex((p) => p.toLowerCase() === rel.toLowerCase());
    if (idx >= 0) chapter = idx;
    updateChapterButtons();
  }
  wireFrame(doc);
  frameWin().addEventListener('scroll', onFrameScroll, { passive: true });
  applyMarks(currentSection());
  if (findState.matches.length) { applyFindMarks(session.kind === 'epub' ? chapter : 0); renderRail(); }
  const fn = afterLoad;
  afterLoad = null;
  if (fn) fn();
  updateProgress();
  updateCurrentToc();
}

function framePath() {
  const prefix = '/doc/' + session.id + '/';
  try {
    const p = decodeURIComponent(frameWin().location.pathname);
    const i = p.indexOf(prefix);
    return i < 0 ? '' : p.slice(i + prefix.length);
  } catch (e) {
    return '';
  }
}

function wireFrame(doc) {
  if (doc.__lumenWired) return;
  doc.__lumenWired = true;
  doc.addEventListener('keydown', onKey);
  wireDragDrop(doc);
  doc.addEventListener('mouseup', () => setTimeout(() => onSelection(doc), 10));
  doc.addEventListener('keyup', (e) => { if (e.shiftKey) setTimeout(() => onSelection(doc), 10); });
  doc.addEventListener('mousedown', () => {
    hideSelPopup();
    if (!$('editor').hidden) closeEditor();
  });
  doc.addEventListener('click', (e) => {
    const t = e.target;
    const mark = t.closest && t.closest('mark.lumen-hl');
    if (mark && doc.getSelection().isCollapsed) {
      const a = annotations.find((x) => x.id === mark.dataset.id);
      const frame = view.getBoundingClientRect();
      if (a) { e.preventDefault(); openEditor(a, mark, { x: frame.left + e.clientX, y: frame.top + e.clientY }); return; }
    }
    const link = t.closest && t.closest('a[href]');
    if (!link) return;
    const url = new URL(link.getAttribute('href'), doc.baseURI);
    if (url.origin !== location.origin) {
      e.preventDefault();
      api('api/external', { url: url.href });
    }
  });
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

// ------------------------------------------------------------------ reading position and progress
function readState() {
  if (!session) return null;
  const win = frameWin();
  if (!win) return null;
  if (session.kind === 'pdf') {
    if (!win.lumenPdf || !win.lumenPdf.numPages) return null;
    const p = win.lumenPdf.position();
    const n = win.lumenPdf.numPages;
    return { position: { page: p.page, offset: p.offset }, progress: (p.page - 1 + p.offset) / n, page: p.page, pages: n };
  }
  const doc = frameDoc();
  if (!doc || !doc.documentElement || !doc.body) return null;
  const max = Math.max(1, doc.documentElement.scrollHeight - win.innerHeight);
  const ratio = doc.documentElement.scrollHeight <= win.innerHeight + 2 ? 1 : Math.max(0, Math.min(1, win.scrollY / max));
  if (session.kind === 'epub') {
    const n = Math.max(1, session.spine.length);
    return { position: { chapter: session.spine[chapter], ratio }, progress: (Math.max(0, chapter) + ratio) / n, ratio };
  }
  return { position: { ratio }, progress: ratio, ratio };
}

function restorePosition() {
  const pos = session && session.position;
  if (!pos) return;
  const win = frameWin();
  if (session.kind === 'pdf') {
    if (pos.page) win.lumenPdf.goTo(pos.page, pos.offset || 0);
  } else if (session.kind === 'epub') {
    if (pos.chapter === session.spine[chapter] && pos.ratio) scrollToRatio(pos.ratio);
  } else if (pos.ratio) {
    scrollToRatio(pos.ratio);
  }
}

function scrollToRatio(r) {
  const win = frameWin();
  const doc = frameDoc();
  win.scrollTo(0, r * Math.max(0, doc.documentElement.scrollHeight - win.innerHeight));
}

let posTimer = 0;
let curTocFrame = 0;
function onFrameScroll() {
  updateProgress();
  cancelAnimationFrame(curTocFrame);
  curTocFrame = requestAnimationFrame(updateCurrentToc);
  clearTimeout(posTimer);
  posTimer = setTimeout(() => savePosition(false), 1200);
}

function savePosition(now) {
  clearTimeout(posTimer);
  if (!session) return;
  const st = readState();
  if (!st) return;
  session.position = st.position;
  const body = JSON.stringify({ key: session.key, position: st.position, progress: st.progress });
  if (now) navigator.sendBeacon('api/position', body);
  else fetch('api/position', { method: 'POST', body }).catch(() => {});
}

function updateProgress() {
  const st = readState();
  if (!st) { $('statusText').textContent = ''; $('statusBar').style.width = '0'; return; }
  const pct = Math.round(st.progress * 100) + '%';
  let text = pct;
  if (session.kind === 'pdf') text = fmt(S.PageOf, st.page, st.pages) + ' · ' + pct;
  else if (session.kind === 'epub') text = fmt(S.ChapterOf, chapter + 1, session.spine.length) + ' · ' + pct;
  $('statusText').textContent = text;
  $('statusBar').style.width = (st.progress * 100).toFixed(1) + '%';
}

// ------------------------------------------------------------------ highlights and notes
/* An annotation is anchored to a "section" (EPUB chapter path, PDF page number, or '' for single-page
   documents) by character offsets plus the quoted text and some context, so it can be found again even
   if offsets shift. Word documents also get paragraph-based offsets (data-p) for the Word copy. */

function currentSection() {
  if (!session) return '';
  if (session.kind === 'epub') return session.spine[chapter] || '';
  return '';
}

function sectionRoot(section) {
  const doc = frameDoc();
  if (!doc) return null;
  if (session.kind === 'pdf') {
    const win = frameWin();
    return win && win.lumenPdf ? win.lumenPdf.textLayer(Number(section)) : null;
  }
  if (session.kind === 'epub' && section !== currentSection()) return null;
  return doc.body;
}

function textOf(root) {
  const r = root.ownerDocument.createRange();
  r.selectNodeContents(root);
  return r.toString();
}

function offsetIn(root, node, offset) {
  const r = root.ownerDocument.createRange();
  r.setStart(root, 0);
  r.setEnd(node, offset);
  return r.toString().length;
}

function onSelection(doc) {
  const sel = doc.getSelection();
  if (!session || !sel || sel.rangeCount === 0 || sel.isCollapsed || !sel.toString().trim()) {
    hideSelPopup();
    return;
  }
  const range = sel.getRangeAt(0);
  let section = currentSection();
  let root;
  if (session.kind === 'pdf') {
    const el = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
    const page = el && el.closest('.page');
    root = page && page.querySelector('.textLayer');
    if (!root) return hideSelPopup();
    section = page.dataset.page;
    if (!root.contains(range.endContainer)) range.setEnd(root, root.childNodes.length); // keep to one page
  } else {
    root = doc.body;
  }
  const start = offsetIn(root, range.startContainer, range.startOffset);
  const quote = range.toString();
  const end = start + quote.length;
  if (!quote.trim()) return hideSelPopup();
  const text = textOf(root);
  pendingSel = {
    section, start, end, quote,
    prefix: text.slice(Math.max(0, start - 40), start),
    suffix: text.slice(end, end + 40),
    docx: docxAnchor(range),
  };

  // place it next to where the selection ended (the mouse is there), always inside the visible area
  const rects = range.getClientRects();
  const r = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
  const frame = view.getBoundingClientRect();
  const pop = $('selPopup');
  pop.hidden = false;
  placePopup(pop, frame.left + r.right, frame.top + r.top, frame.top + r.bottom, frame);
}

/** Puts a popup above (or below) a point, clamped to the visible part of the document. */
function placePopup(el, cx, top, bottom, frame) {
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  const minY = Math.max(8, frame.top + 4);
  const maxY = Math.min(window.innerHeight, frame.bottom) - h - 8;
  let y = top - h - 8;
  if (y < minY) y = bottom + 8;
  y = Math.max(minY, Math.min(maxY, y));
  const x = Math.max(8, Math.min(window.innerWidth - w - 8, cx - w / 2));
  el.style.left = x + 'px';
  el.style.top = y + 'px';
}

/** Paragraph index + offset inside it, for writing the note into the Word copy. */
function docxAnchor(range) {
  if (!session.word) return null;
  const boundary = (container, offset, isEnd) => {
    let el = container.nodeType === 1 ? container : container.parentElement;
    let p = el && el.closest('[data-p]');
    if (p) return { p, off: offsetIn(p, container, offset) };
    // boundary between block elements: use the nearest paragraph
    if (container.nodeType === 1) {
      const child = isEnd ? container.childNodes[offset - 1] : container.childNodes[offset];
      if (child && child.nodeType === 1) {
        const all = child.matches('[data-p]') ? [child] : Array.from(child.querySelectorAll('[data-p]'));
        p = isEnd ? all[all.length - 1] : all[0];
        if (p) return { p, off: isEnd ? textOf(p).length : 0 };
      }
    }
    return null;
  };
  const s = boundary(range.startContainer, range.startOffset, false);
  const e = boundary(range.endContainer, range.endOffset, true);
  if (!s || !e) return null;
  return { sp: Number(s.p.dataset.p), so: s.off, ep: Number(e.p.dataset.p), eo: e.off };
}

function hideSelPopup() {
  $('selPopup').hidden = true;
}

function createAnnotation(color, withNote) {
  if (!pendingSel) return;
  const a = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
    color, note: '', created: Date.now(), updated: Date.now(), ...pendingSel,
  };
  pendingSel = null;
  annotations.push(a);
  hideSelPopup();
  const doc = frameDoc();
  if (doc) doc.getSelection().removeAllRanges();
  applyMarks(a.section);
  renderNotes();
  scheduleSave();
  if (withNote) {
    const root = sectionRoot(a.section);
    openEditor(a, root && root.querySelector(`mark.lumen-hl[data-id="${a.id}"]`));
  }
}

/** Finds the annotation's text again: exact offsets first, then the quote with the best context. */
function locate(text, a) {
  if (text.slice(a.start, a.end) === a.quote) return [a.start, a.end];
  let best = null;
  let bestScore = -Infinity;
  const prefix = a.prefix || '';
  const suffix = a.suffix || '';
  for (let i = text.indexOf(a.quote); i >= 0; i = text.indexOf(a.quote, i + 1)) {
    let score = 0;
    const pre = text.slice(Math.max(0, i - prefix.length), i);
    const suf = text.slice(i + a.quote.length, i + a.quote.length + suffix.length);
    for (let k = 1; k <= pre.length && pre[pre.length - k] === prefix[prefix.length - k]; k++) score++;
    for (let k = 0; k < suf.length && suf[k] === suffix[k]; k++) score++;
    score -= Math.abs(i - a.start) / 100000;
    if (score > bestScore) { bestScore = score; best = i; }
  }
  return best === null ? null : [best, best + a.quote.length];
}

function unwrap(root, selector) {
  root.querySelectorAll(selector).forEach((m) => {
    const parent = m.parentNode;
    while (m.firstChild) parent.insertBefore(m.firstChild, m);
    parent.removeChild(m);
    parent.normalize();
  });
}

function applyMarks(section) {
  if (!session) return;
  const root = sectionRoot(section);
  if (!root) return;
  unwrap(root, 'mark.lumen-hl');
  let changed = false;
  for (const a of annotations.filter((x) => String(x.section) === String(section))) {
    const pos = locate(textOf(root), a);
    if (!pos) continue;
    const marks = wrapRange(root, pos[0], pos[1], 'lumen-hl' + (a.note && a.note.trim() ? ' note' : ''), { id: a.id, c: a.color });
    if (marks.length) marks[marks.length - 1].classList.add('last');
    if (session.kind === 'pdf' && marks.length) {
      // PDF coordinates, so the note can be written into the PDF copy
      const rects = frameWin().lumenPdf.pdfRects(Number(section), marks);
      if (rects && JSON.stringify(rects) !== JSON.stringify(a.pdfRects)) { a.pdfRects = rects; changed = true; }
    }
  }
  if (changed) scheduleSave();
}

function wrapRange(root, start, end, className, data) {
  const doc = root.ownerDocument;
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  let pos = 0;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const len = n.data.length;
    if (pos + len > start && pos < end) nodes.push([n, Math.max(0, start - pos), Math.min(len, end - pos)]);
    pos += len;
    if (pos >= end) break;
  }
  const marks = [];
  for (let [node, s, e] of nodes) {
    const parentTag = node.parentNode && node.parentNode.nodeName.toUpperCase();
    if (['TABLE', 'TBODY', 'THEAD', 'TR', 'UL', 'OL', 'STYLE', 'SCRIPT'].includes(parentTag)) continue;
    if (e <= s) continue;
    if (s > 0) { node = node.splitText(s); e -= s; }
    if (e < node.data.length) node.splitText(e);
    const mark = doc.createElement('mark');
    mark.className = className;
    for (const [k, v] of Object.entries(data || {})) mark.dataset[k] = v;
    node.parentNode.insertBefore(mark, node);
    mark.appendChild(node);
    marks.push(mark);
  }
  return marks;
}

// ---- note editor
function openEditor(a, anchorEl, point) {
  hideSelPopup();
  editing = a;
  const ed = $('editor');
  $('edQuote').textContent = a.quote.length > 220 ? a.quote.slice(0, 220) + '…' : a.quote;
  $('edNote').value = a.note || '';
  ed.querySelectorAll('.dot').forEach((d) => d.classList.toggle('on', d.dataset.color === a.color));
  ed.hidden = false;
  const frame = view.getBoundingClientRect();
  if (point) {
    placePopup(ed, point.x, point.y - 6, point.y + 14, frame);
  } else if (anchorEl && anchorEl.getClientRects().length) {
    const r = anchorEl.getClientRects()[0];
    placePopup(ed, frame.left + r.left + r.width / 2, frame.top + r.top, frame.top + r.bottom, frame);
  } else {
    placePopup(ed, frame.left + frame.width / 2, frame.top + 60, frame.top + 60, frame);
  }
  $('edNote').focus({ preventScroll: true });
}

function closeEditor() {
  if (!editing) { $('editor').hidden = true; return; }
  const a = editing;
  editing = null;
  $('editor').hidden = true;
  applyMarks(a.section);
  renderNotes();
  scheduleSave();
}

function setColor(color) {
  if (!editing) return;
  editing.color = color;
  editing.updated = Date.now();
  $('editor').querySelectorAll('.dot').forEach((d) => d.classList.toggle('on', d.dataset.color === color));
  const root = sectionRoot(editing.section);
  if (root) root.querySelectorAll(`mark.lumen-hl[data-id="${editing.id}"]`).forEach((m) => (m.dataset.c = color));
  renderNotes();
  scheduleSave();
}

function deleteEditing() {
  if (!editing) return;
  const a = editing;
  editing = null;
  $('editor').hidden = true;
  annotations = annotations.filter((x) => x.id !== a.id);
  applyMarks(a.section);
  renderNotes();
  scheduleSave();
}

// ---- saving (and the Word copy with real comments)
let saveTimer = 0;
function scheduleSave() {
  clearTimeout(saveTimer);
  const key = session && session.key;
  const list = annotations;
  saveTimer = setTimeout(async () => {
    if (!key) return;
    const res = await api('api/annotations', { key, annotations: list }).catch(() => ({}));
    if (res.copyError) showError(res.copyError);
    else if (res.copy && session && session.key === key) {
      session.copyPath = res.copy;
      updateCopyInfo(res.copy);
      const name = res.copy.split(/[\\/]/).pop();
      if (res.copyChanged) showInfo(fmt(S.CopyChanged, name));
      else if (!copyToastShown) showInfo(fmt(S.SavedCopy, name));
      copyToastShown = true;
    }
  }, 700);
}

function updateCopyInfo(path) {
  const box = $('wordInfo');
  if (!session || !session.copy) { box.hidden = true; return; }
  box.hidden = false;
  const info = session.copy === 'pdf' ? S.PdfNotesInfo : S.WordNotesInfo;
  box.innerHTML = `${esc(info)}<br><b>${esc(path ? path.split(/[\\/]/).pop() : '—')}</b>` +
    (path ? `<br><button id="wordReveal">${esc(S.ShowInFolder)}</button>` : '');
  if (path) $('wordReveal').onclick = () => api('api/reveal', { path });
}

// ---- notes panel
function sectionOrder(a) {
  if (session.kind === 'pdf') return Number(a.section) || 0;
  if (session.kind === 'epub') return session.spine.indexOf(a.section);
  return 0;
}

function renderNotes() {
  const list = $('notesList');
  list.innerHTML = '';
  $('notesCount').textContent = annotations.length ? `(${annotations.length})` : '';
  $('noNotes').hidden = annotations.length > 0;
  if (!session) return;
  const sorted = [...annotations].sort((x, y) => sectionOrder(x) - sectionOrder(y) || x.start - y.start);
  for (const a of sorted) {
    const item = document.createElement('div');
    item.className = 'note-item';
    let loc = '';
    if (session.kind === 'pdf') loc = fmt(S.Page, a.section);
    else if (session.kind === 'epub') loc = fmt(S.Chapter, session.spine.indexOf(a.section) + 1);
    item.innerHTML = `<div class="cbar ${esc(a.color)}"></div><div class="body">
        <div class="q">${esc(a.quote)}</div>${a.note ? `<div class="n">${esc(a.note)}</div>` : ''}
        ${loc ? `<div class="loc">${esc(loc)}</div>` : ''}</div>`;
    item.onclick = () => goToAnnotation(a, true);
    list.append(item);
  }
}

function goToAnnotation(a, edit) {
  const show = () => {
    const root = sectionRoot(a.section);
    const mark = root && root.querySelector(`mark.lumen-hl[data-id="${a.id}"]`);
    if (mark) {
      mark.scrollIntoView({ block: 'center' });
      if (edit) setTimeout(() => openEditor(a, mark), 60);
    }
    return !!mark;
  };
  if (session.kind === 'pdf') {
    frameWin().lumenPdf.goTo(Number(a.section));
    let tries = 0;
    const wait = () => { if (!show() && tries++ < 50) setTimeout(wait, 100); };
    wait();
  } else if (session.kind === 'epub' && a.section !== currentSection()) {
    afterLoad = show;
    chapter = session.spine.indexOf(a.section);
    view.src = docUrl(a.section);
    updateChapterButtons();
  } else {
    show();
  }
}

function showPanel(which) {
  $('tabContents').classList.toggle('on', which === 'contents');
  $('tabNotes').classList.toggle('on', which === 'notes');
  $('panelContents').hidden = which !== 'contents';
  $('panelNotes').hidden = which !== 'notes';
}

// ------------------------------------------------------------------ table of contents
function renderToc(items, waiting) {
  const tree = $('tocTree');
  tree.innerHTML = '';
  tocFlat = [];
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
      const cnt = document.createElement('span');
      cnt.className = 'cnt';
      cnt.hidden = true;
      row.append(tw, lbl, cnt);
      li.append(row);
      tocFlat.push({ item, level: depth, row, li, cnt });
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
  if (findState.matches.length) updateTocCounts();
}

function navigate(item) {
  if (!session) return;
  if (item.page) {
    const win = frameWin();
    if (win && win.lumenPdf) win.lumenPdf.goTo(item.page);
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
  if (doc && framePath().toLowerCase() === target.toLowerCase()) {
    const el = frag ? (doc.getElementById(decodeURIComponent(frag)) || doc.getElementsByName(frag)[0]) : null;
    if (el) el.scrollIntoView({ block: 'start' }); else frameWin().scrollTo(0, 0);
  } else {
    savePosition(false);
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

// ------------------------------------------------------------------ where am I (highlighted in the panel)
function tocAnchorTop(doc, id) {
  const el = doc && id ? (doc.getElementById(id) || doc.getElementsByName(id)[0]) : null;
  return el ? el.getBoundingClientRect().top : null;
}

function updateCurrentToc() {
  if (!session || !tocFlat.length) return;
  let current = null;
  const doc = frameDoc();
  const LINE = 90;
  if (session.kind === 'pdf') {
    const win = frameWin();
    if (!win || !win.lumenPdf || !win.lumenPdf.numPages) return;
    const pos = win.lumenPdf.position();
    const limit = pos.pdfY - LINE * (pos.unit || 1);
    for (const e of tocFlat) {
      const it = e.item;
      if (!it.page) continue;
      if (it.page < pos.page || (it.page === pos.page && (it.top == null || it.top >= limit))) current = e;
    }
  } else if (session.kind === 'epub') {
    for (const e of tocFlat) {
      if (!e.item.href) continue;
      const [path, frag] = e.item.href.split('#');
      const idx = session.spine.indexOf(path);
      if (idx < 0 || idx > chapter) continue;
      if (idx < chapter || !frag) { current = e; continue; }
      const top = tocAnchorTop(doc, decodeURIComponent(frag));
      if (top === null || top <= LINE) current = e;
    }
  } else {
    for (const e of tocFlat) {
      const top = tocAnchorTop(doc, e.item.anchor);
      if (top !== null && top <= LINE) current = e;
    }
    if (!current) current = tocFlat[0];
  }
  tocFlat.forEach((e) => e.row.classList.toggle('current', e === current));
  if (!current) return;
  for (let li = current.li.parentElement && current.li.parentElement.closest('li'); li; li = li.parentElement && li.parentElement.closest('li')) {
    if (li.classList.contains('collapsed')) {
      li.classList.remove('collapsed');
      const tw = li.querySelector(':scope > .row > .tw');
      if (tw) tw.textContent = '▾';
    }
  }
  if (!$('panelContents').hidden) current.row.scrollIntoView({ block: 'nearest' });
}

// ------------------------------------------------------------------ search (Ctrl+F)
/* Matches are {sec, pos}: sec = 0 for single-page documents, the chapter index for EPUB (counted by the
   server over the whole book), the page number for PDF. The panel shows how many fall in each chapter. */

function openFind() {
  if (!session) return;
  $('find').hidden = false;
  $('findInput').focus();
  $('findInput').select();
}

function closeFind() {
  $('find').hidden = true;
  findState = { q: '', matches: [], cur: -1, seq: findState.seq + 1, ids: [] };
  clearFindMarks();
  tocFlat.forEach((e) => { e.cnt.hidden = true; });
  renderFindInfo();
  renderRail();
}

function findRoots() {
  if (!session) return [];
  const doc = frameDoc();
  if (!doc || !doc.body) return [];
  if (session.kind === 'pdf') return Array.from(doc.querySelectorAll('.textLayer'));
  return [doc.body];
}

function clearFindMarks() {
  findRoots().forEach((r) => unwrap(r, 'mark.lumen-find'));
}

async function runFind(raw) {
  const seq = ++findState.seq;
  const q = (raw || '').trim();
  clearFindMarks();
  findState.q = q;
  findState.matches = [];
  findState.cur = -1;
  if (!session || q.length < 2) { updateTocCounts(); renderFindInfo(); renderRail(); return; }
  const ql = q.toLowerCase();
  const matches = [];
  const scan = (text, sec) => {
    const low = text.toLowerCase();
    for (let i = low.indexOf(ql); i >= 0 && matches.length < 20000; i = low.indexOf(ql, i + ql.length)) matches.push({ sec, pos: i });
  };
  if (session.kind === 'pdf') {
    const idx = await frameWin().lumenPdf.pageIndex();
    idx.texts.forEach((t, i) => scan(t, i + 1));
    findState.pageLen = idx.texts.map((t) => t.length);
    // height of each match on its page (PDF y grows upwards), to split pages between chapters
    for (const m of matches) {
      let y = 0;
      for (const [off, yy] of idx.ys[m.sec - 1]) { if (off > m.pos) break; y = yy; }
      m.y = y;
    }
  } else if (session.kind === 'epub') {
    const res = await api('api/search?q=' + encodeURIComponent(q));
    (res.chapters || []).forEach((c, i) => c.positions.forEach((p) => matches.push({ sec: i, pos: p })));
    findState.ids = (res.chapters || []).map((c) => c.ids || {});
  } else {
    const doc = frameDoc();
    if (doc && doc.body) scan(textOf(doc.body), 0);
  }
  if (seq !== findState.seq) return;   // a newer search started meanwhile
  findState.matches = matches;
  if (session.kind === 'pdf') {
    findRoots().forEach((r) => applyFindMarks(Number(r.closest('.page').dataset.page)));
  } else {
    applyFindMarks(session.kind === 'epub' ? chapter : 0);
  }
  updateTocCounts();
  renderRail();
  if (matches.length) showMatch(firstMatchFromHere()); else renderFindInfo();
}

function firstMatchFromHere() {
  const m = findState.matches;
  if (session.kind === 'pdf') {
    const st = readState();
    const i = m.findIndex((x) => x.sec >= (st ? st.page : 1));
    return i < 0 ? 0 : i;
  }
  if (session.kind === 'epub') {
    const i = m.findIndex((x) => x.sec >= chapter);
    if (i < 0) return 0;
    if (m[i].sec > chapter) return i;
  }
  const root = findRoots()[0];
  for (let k = 0; k < m.length; k++) {
    const mark = root && root.querySelector(`mark.lumen-find[data-k="${k}"]`);
    if (mark && mark.getBoundingClientRect().top >= 0) return k;
  }
  return session.kind === 'epub' ? Math.max(0, m.findIndex((x) => x.sec >= chapter)) : 0;
}

function applyFindMarks(sec) {
  const q = findState.q;
  let root = null;
  if (session.kind === 'pdf') root = frameWin().lumenPdf.textLayer(sec);
  else if (session.kind === 'epub') root = sec === chapter ? frameDoc().body : null;
  else root = frameDoc().body;
  if (!root || !q) return;
  unwrap(root, 'mark.lumen-find');
  findState.matches.forEach((m, k) => {
    if (m.sec !== sec) return;
    const marks = wrapRange(root, m.pos, m.pos + q.length, 'lumen-find' + (k === findState.cur ? ' cur' : ''), { k: String(k) });
    if (!marks.length) m.missing = true;
  });
}

function stepMatch(d) {
  const n = findState.matches.length;
  if (!n) return;
  showMatch((findState.cur + d + n) % n);
}

function showMatch(k) {
  const m = findState.matches[k];
  if (!m) return;
  findState.cur = k;
  renderFindInfo();
  renderRail();
  const focus = () => {
    let found = null;
    findRoots().forEach((root) => root.querySelectorAll('mark.lumen-find').forEach((el) => {
      const on = el.dataset.k === String(k);
      el.classList.toggle('cur', on);
      if (on && !found) found = el;
    }));
    if (found) found.scrollIntoView({ block: 'center' });
    return !!found;
  };
  if (session.kind === 'pdf') {
    const win = frameWin();
    win.lumenPdf.goTo(m.sec);
    let tries = 0;
    const wait = () => {
      if (findState.cur !== k) return;
      if (!focus() && tries++ < 50) setTimeout(wait, 80);
    };
    wait();
  } else if (session.kind === 'epub' && m.sec !== chapter) {
    afterLoad = focus;
    chapter = m.sec;
    view.src = docUrl(session.spine[m.sec]);
    updateChapterButtons();
  } else {
    focus();
  }
}

function renderFindInfo() {
  const el = $('findCount');
  const n = findState.matches.length;
  el.classList.toggle('none', !!findState.q && findState.q.length >= 2 && n === 0);
  if (!findState.q || findState.q.length < 2) el.textContent = '';
  else if (!n) el.textContent = S.NoMatches || '0';
  else el.textContent = fmt(S.SearchCount, findState.cur + 1, n);
}

/** Small numbers in the navigation panel: how many matches fall into each chapter/section. */
function updateTocCounts() {
  const m = findState.matches;
  if (!session || !findState.q || !tocFlat.length) { tocFlat.forEach((e) => (e.cnt.hidden = true)); return; }
  const doc = frameDoc();
  const coord = (item) => {
    if (session.kind === 'pdf') return item.page ? [item.page, item.top != null ? -item.top - 2 : -Infinity] : null;
    if (session.kind === 'epub') {
      if (!item.href) return null;
      const [path, frag] = item.href.split('#');
      const idx = session.spine.indexOf(path);
      if (idx < 0) return null;
      if (!frag) return [idx, 0];
      const ids = findState.ids[idx] || {};
      const f = decodeURIComponent(frag);
      return [idx, ids[f] !== undefined ? ids[f] : ids[frag] !== undefined ? ids[frag] : 0];
    }
    const el = doc && item.anchor ? doc.getElementById(item.anchor) : null;
    return el ? [0, offsetIn(doc.body, el, 0)] : null;
  };
  const less = (a, b) => a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);
  const starts = tocFlat.map((e) => coord(e.item));
  tocFlat.forEach((e, i) => {
    const start = starts[i];
    if (!start) { e.cnt.hidden = true; return; }
    let end = [Infinity, 0];
    for (let j = i + 1; j < tocFlat.length; j++) {
      if (tocFlat[j].level <= e.level && starts[j]) { end = starts[j]; break; }
    }
    let n = 0;
    for (const x of m) {
      const c = session.kind === 'pdf' ? [x.sec, -(x.y || 0)] : [x.sec, x.pos];
      if (!less(c, start) && less(c, end)) n++;
    }
    e.cnt.textContent = n > 999 ? '999+' : String(n);
    e.cnt.hidden = n === 0;
  });
}

/** Ticks next to the scroll bar, like the browser's own find. */
function renderRail() {
  const rail = $('rail');
  const m = findState.matches;
  rail.innerHTML = '';
  rail.hidden = !session || !m.length;
  if (rail.hidden) return;
  const doc = frameDoc();
  const win = frameWin();
  if (!doc || !win) return;
  const total = Math.max(1, doc.documentElement.scrollHeight);
  const ticks = [];
  m.forEach((x, k) => {
    let y = null;
    if (session.kind === 'pdf') {
      const box = win.lumenPdf.pageBox(x.sec);
      const len = (findState.pageLen || [])[x.sec - 1] || 1;
      if (box) y = box.top + box.height * (x.pos / len);
    } else if (session.kind !== 'epub' || x.sec === chapter) {
      const el = doc.querySelector(`mark.lumen-find[data-k="${k}"]`);
      if (el) y = el.getBoundingClientRect().top + win.scrollY;
    }
    if (y !== null) ticks.push([y / total, k === findState.cur]);
  });
  const html = [];
  const seen = new Set();
  for (const [f, cur] of ticks) {
    const px = Math.round(f * 1000);
    if (seen.has(px) && !cur) continue;
    seen.add(px);
    html.push(`<i${cur ? ' class="cur"' : ''} style="top:${(f * 100).toFixed(2)}%"></i>`);
  }
  rail.innerHTML = html.join('');
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
  const doc = session ? frameDoc() : null;
  if (doc && doc.documentElement) doc.documentElement.setAttribute('data-lumen-theme', theme);
}

// ------------------------------------------------------------------ keyboard
function onKey(e) {
  const key = e.key;
  let handled = true;
  if (key === 'Escape' && (!$('editor').hidden || !$('selPopup').hidden)) { closeEditor(); hideSelPopup(); }
  else if (key === 'Escape' && !$('find').hidden) closeFind();
  else if (e.target && (e.target.id === 'edNote' || e.target.id === 'findInput')) handled = false;
  else if (e.ctrlKey && !e.altKey && (key === 'f' || key === 'F')) openFind();
  else if (key === 'F3') { if ($('find').hidden) openFind(); else stepMatch(e.shiftKey ? -1 : 1); }
  else if (e.ctrlKey && !e.altKey && (key === 'o' || key === 'O')) chooseFile();
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
function toast(msg, info) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.toggle('info', !!info);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), info ? 4000 : 7000);
  t.onclick = () => (t.hidden = true);
}

function showError(msg) {
  const title = S.ErrorTitle || '';
  toast(!title || msg.startsWith(title) ? msg : title + '\n' + msg, false);
}

function showInfo(msg) { toast(msg, true); }

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

init();
