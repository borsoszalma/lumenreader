// PDF viewer page (same origin as the app): lazily renders pages with pdf.js and exposes a small API
// to the reader shell (current page, go to page, re-apply highlights after a page was drawn).
import * as pdfjsLib from './pdfjs/pdf.mjs';

pdfjsLib.GlobalWorkerOptions.workerSrc = './pdfjs/pdf.worker.mjs';

const params = new URLSearchParams(location.search);
const container = document.getElementById('pages');
const pages = [];            // { el, width, height, rendered, rendering, task }
let pdf = null;
let ready;
const readyPromise = new Promise((r) => (ready = r));

function pageWidth() {
  return Math.min(container.clientWidth - 24, 1000);
}

async function open() {
  pdf = await pdfjsLib.getDocument({
    url: params.get('file'),
    cMapUrl: './pdfjs/cmaps/',
    cMapPacked: true,
    standardFontDataUrl: './pdfjs/standard_fonts/',
    wasmUrl: './pdfjs/wasm/',
    iccUrl: './pdfjs/iccs/',
    enableXfa: false,
  }).promise;

  const first = (await pdf.getPage(1)).getViewport({ scale: 1 });
  for (let i = 1; i <= pdf.numPages; i++) {
    const el = document.createElement('div');
    el.className = 'page';
    el.dataset.page = String(i);
    container.append(el);
    pages.push({ el, width: first.width, height: first.height, rendered: false });
  }
  layout();
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) render(Number(e.target.dataset.page)); });
  }, { rootMargin: '800px 0px' });
  pages.forEach((p) => io.observe(p.el));
  ready();

  // real page sizes (pages can differ) – fetched in the background
  for (let i = 2; i <= pdf.numPages; i++) {
    const vp = (await pdf.getPage(i)).getViewport({ scale: 1 });
    const p = pages[i - 1];
    if (vp.width !== p.width || vp.height !== p.height) {
      p.width = vp.width; p.height = vp.height;
      sizePage(p);
    }
  }
}

function sizePage(p) {
  const w = pageWidth();
  p.el.style.width = w + 'px';
  p.el.style.height = Math.round((w * p.height) / p.width) + 'px';
}

function layout() {
  pages.forEach(sizePage);
}

async function render(n) {
  const p = pages[n - 1];
  if (!p || p.rendered || p.rendering) return;
  p.rendering = true;
  try {
    const page = await pdf.getPage(n);
    const cssWidth = pageWidth();
    const scale = cssWidth / page.getViewport({ scale: 1 }).width;
    const viewport = page.getViewport({ scale });
    const dpr = window.devicePixelRatio || 1;

    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(viewport.width * dpr);
    canvas.height = Math.floor(viewport.height * dpr);
    const text = document.createElement('div');
    text.className = 'textLayer';

    p.el.style.setProperty('--scale-factor', String(scale));
    await page.render({
      canvasContext: canvas.getContext('2d'),
      viewport,
      transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
    }).promise;
    const layer = new pdfjsLib.TextLayer({ textContentSource: page.streamTextContent(), container: text, viewport });
    await layer.render();
    const end = document.createElement('div');
    end.className = 'endOfContent';
    text.append(end);

    p.el.replaceChildren(canvas, text);
    p.viewport = viewport;
    p.rendered = true;
    window.dispatchEvent(new CustomEvent('lumen-page', { detail: n }));
  } catch (e) {
    console.error(e);
  } finally {
    p.rendering = false;
  }
}

// selection helper used by pdf.js for nicer text selection
document.addEventListener('selectionchange', () => {
  const sel = document.getSelection();
  document.querySelectorAll('.textLayer.selecting').forEach((t) => t.classList.remove('selecting'));
  if (sel && sel.rangeCount && !sel.isCollapsed) {
    const node = sel.getRangeAt(0).startContainer;
    const layer = (node.nodeType === 1 ? node : node.parentElement)?.closest('.textLayer');
    if (layer) layer.classList.add('selecting');
  }
});

let resizeTimer = 0;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    const pos = window.lumenPdf.position();
    layout();
    pages.forEach((p) => { p.rendered = false; p.el.replaceChildren(); });
    window.lumenPdf.goTo(pos.page, pos.offset);
    pages.forEach((p, i) => {
      const r = p.el.getBoundingClientRect();
      if (r.bottom > -800 && r.top < innerHeight + 800) render(i + 1);
    });
  }, 250);
});

window.lumenPdf = {
  ready: readyPromise,
  get numPages() { return pdf ? pdf.numPages : 0; },
  /** Current page (1-based) and how far into it (0..1) the top of the window is. */
  position() {
    const y = window.scrollY + 1;
    for (let i = 0; i < pages.length; i++) {
      const el = pages[i].el;
      const top = el.offsetTop;
      const bottom = top + el.offsetHeight + 14;
      if (y < bottom) {
        const offset = Math.max(0, Math.min(1, (y - top) / el.offsetHeight));
        // the same point in PDF units (y grows upwards), and how many PDF units 90 screen pixels are
        return { page: i + 1, offset, pdfY: pages[i].height * (1 - offset), unit: pages[i].height / el.offsetHeight };
      }
    }
    return { page: pages.length || 1, offset: 1 };
  },
  goTo(page, offset = 0) {
    const p = pages[Math.max(1, Math.min(page, pages.length)) - 1];
    if (!p) return;
    window.scrollTo(0, p.el.offsetTop + offset * p.el.offsetHeight - (offset ? 0 : 8));
  },
  textLayer(page) {
    const p = pages[page - 1];
    return p && p.rendered ? p.el.querySelector('.textLayer') : null;
  },
  pageBox(page) {
    const p = pages[page - 1];
    return p ? { top: p.el.offsetTop, height: p.el.offsetHeight } : null;
  },
  /** Screen rectangles of the given elements → merged rectangles in PDF user space [x1, y1, x2, y2]. */
  pdfRects(page, elements) {
    const p = pages[page - 1];
    if (!p || !p.viewport) return null;
    const base = p.el.getBoundingClientRect();
    const rects = [];
    for (const el of elements) {
      for (const r of el.getClientRects()) {
        if (r.width < 0.5 || r.height < 0.5) continue;
        const [ax, ay] = p.viewport.convertToPdfPoint(r.left - base.left, r.top - base.top);
        const [bx, by] = p.viewport.convertToPdfPoint(r.right - base.left, r.bottom - base.top);
        rects.push([Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)]);
      }
    }
    // merge pieces of the same line
    rects.sort((a, b) => b[1] - a[1] || a[0] - b[0]);
    const out = [];
    for (const r of rects) {
      const last = out[out.length - 1];
      if (last && Math.abs(last[1] - r[1]) < 2 && Math.abs(last[3] - r[3]) < 2 && r[0] <= last[2] + 4) {
        last[2] = Math.max(last[2], r[2]);
      } else {
        out.push([...r]);
      }
    }
    return out.map((r) => r.map((v) => Math.round(v * 100) / 100));
  },
  /** Plain text of every page (same text the text layer shows), for searching. */
  async pageTexts() {
    await this.pageIndex();
    return this._texts;
  },
  /** Per page: texts plus [offset, y] of every text piece, to know how high on the page a match is. */
  async pageIndex() {
    if (!this._texts) {
      const texts = [];
      const ys = [];
      for (let i = 1; i <= pdf.numPages; i++) {
        const tc = await (await pdf.getPage(i)).getTextContent();
        let off = 0;
        const map = [];
        for (const it of tc.items) {
          const str = it.str || '';
          if (str) map.push([off, it.transform ? it.transform[5] : 0]);
          off += str.length;
        }
        texts.push(tc.items.map((it) => it.str || '').join(''));
        ys.push(map);
      }
      this._texts = texts;
      this._ys = ys;
    }
    return { texts: this._texts, ys: this._ys };
  },
};

open().catch((e) => {
  document.body.textContent = String(e && e.message ? e.message : e);
  window.dispatchEvent(new CustomEvent('lumen-error', { detail: String(e) }));
});
