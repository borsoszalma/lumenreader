using System.Text.Json;
using LumenReader.Documents;
using LumenReader.Theming;

namespace LumenReader.Viewer;

/// <summary>
/// JavaScript + CSS injected into every page shown in WebView2: themes, heading collection for the
/// navigation panel, drag &amp; drop forwarding and in-page anchor scrolling.
/// </summary>
public static class ViewerScript
{
    public static string ThemeName(ReaderTheme theme) => theme switch
    {
        ReaderTheme.Dark => "dark",
        ReaderTheme.Reading => "reading",
        _ => "light",
    };

    public static string Build(ReaderTheme theme, string kind, bool collectHeadings)
    {
        var cfg = JsonSerializer.Serialize(new { theme = ThemeName(theme), kind, toc = collectHeadings });
        return Script
            .Replace("__CFG__", cfg)
            .Replace("__CSS__", JsonSerializer.Serialize(Css));
    }

    public static string KindName(DocumentKind? kind) => kind switch
    {
        DocumentKind.Flow => "flow",
        DocumentKind.Html => "html",
        DocumentKind.Epub => "epub",
        DocumentKind.Pdf => "pdf",
        _ => "welcome",
    };

    private const string Script = """
(() => {
  if (window.__lumen) return;
  const cfg = __CFG__;
  const css = __CSS__;
  const post = (m) => { try { window.chrome.webview.postMessage(m); } catch (e) { } };

  function setTheme(t) {
    cfg.theme = t;
    if (document.documentElement) document.documentElement.setAttribute('data-lumen-theme', t);
  }

  function ensureStyle() {
    if (document.getElementById('lumen-style')) return;
    const s = document.createElement('style');
    s.id = 'lumen-style';
    s.textContent = css;
    (document.head || document.documentElement).appendChild(s);
  }

  function scrollToId(id) {
    const el = document.getElementById(id) || document.getElementsByName(id)[0];
    if (el) { el.scrollIntoView({ block: 'start' }); return true; }
    return false;
  }

  function collectHeadings() {
    if (!cfg.toc) return;
    const items = [];
    let n = 0;
    document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach((h) => {
      const text = (h.textContent || '').replace(/\s+/g, ' ').trim();
      if (!text) return;
      if (!h.id) h.id = 'lumen-h-' + (++n);
      items.push({ level: Number(h.tagName.substring(1)), id: h.id, text: text.slice(0, 200) });
    });
    post({ type: 'toc', items });
  }

  window.__lumen = { setTheme, scrollToId, scrollTop: () => window.scrollTo(0, 0) };
  setTheme(cfg.theme);

  document.addEventListener('DOMContentLoaded', () => {
    ensureStyle();
    setTheme(cfg.theme);
    if (document.body) document.body.classList.add('lumen-kind-' + cfg.kind);
    collectHeadings();
  });

  // ----- drag & drop: hand the dropped file to the app (CoreWebView2File carries the path) -----
  const hasFiles = (e) => !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  let depth = 0;
  const root = () => document.documentElement;
  window.addEventListener('dragenter', (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth++; root().classList.add('lumen-dragging'); });
  window.addEventListener('dragover', (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  window.addEventListener('dragleave', (e) => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) root().classList.remove('lumen-dragging'); });
  window.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    root().classList.remove('lumen-dragging');
    const files = e.dataTransfer.files;
    if (files && files.length) {
      try { window.chrome.webview.postMessageWithAdditionalObjects({ type: 'drop' }, files); } catch (err) { }
    }
  });

  // ----- clicks: in-page anchors and the welcome page "Open" button -----
  document.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest) return;
    if (t.closest('[data-lumen-open]')) { e.preventDefault(); post({ type: 'open' }); return; }
    const a = t.closest('a[href^="#"]');
    if (a) {
      const id = decodeURIComponent(a.getAttribute('href').substring(1));
      if (id && scrollToId(id)) e.preventDefault();
    }
  });
})();
""";

    private const string Css = """
:root[data-lumen-theme="dark"] { color-scheme: dark; }

/* drag & drop highlight */
:root.lumen-dragging::after {
  content: ""; position: fixed; inset: 10px; z-index: 2147483647; pointer-events: none;
  border: 3px dashed #5c7cfa; border-radius: 14px; background: rgba(92,124,250,.10);
}

/* ---------- documents converted by the app ---------- */
body.lumen-doc {
  box-sizing: border-box; max-width: 48em; margin: 0 auto; padding: 2.5em 2em 6em;
  font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif;
  font-size: 17px; line-height: 1.65; color: #1f2328; background: #fff;
}
html:has(> body.lumen-doc) { background: #fff; }
body.lumen-doc h1, body.lumen-doc h2, body.lumen-doc h3, body.lumen-doc h4 { line-height: 1.25; margin: 1.5em 0 .6em; }
body.lumen-doc p { margin: 0 0 .8em; }
body.lumen-doc p.lumen-empty { margin: 0; height: .8em; }
body.lumen-doc img { max-width: 100%; height: auto; }
body.lumen-doc table { border-collapse: collapse; margin: 1em 0; max-width: 100%; }
body.lumen-doc td, body.lumen-doc th { border: 1px solid #d0d7de; padding: .35em .7em; vertical-align: top; }
body.lumen-doc td > p:last-child { margin-bottom: 0; }
body.lumen-doc pre { background: #f6f8fa; padding: .8em 1em; border-radius: 6px; overflow: auto; }
body.lumen-doc code, body.lumen-doc pre { font-family: "Cascadia Mono", Consolas, monospace; font-size: .92em; }
body.lumen-doc blockquote { margin: 1em 0; padding: 0 1em; border-left: 4px solid #d0d7de; color: #57606a; }
body.lumen-doc hr { border: 0; border-top: 1px solid #d0d7de; margin: 2em 0; }
.lumen-note { padding: .8em 1em; border-radius: 8px; background: #fff4ce; color: #5c4400; margin-bottom: 1.6em; font-size: .9em; }

/* ---------- EPUB: comfortable reading column ---------- */
body.lumen-kind-epub { max-width: 46em; margin: 0 auto !important; padding: 2em 2em 5em !important; line-height: 1.6; }
body.lumen-kind-epub img, body.lumen-kind-epub svg { max-width: 100%; height: auto; }

/* ---------- PDF ---------- */
body.lumen-kind-pdf { margin: 0; background: #e8e8eb; }
.lumen-pages { display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 18px 12px 48px; }
.lumen-page { width: min(100%, 980px); background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.2); }
.lumen-page img { display: block; width: 100%; height: 100%; }

/* ---------- Dark ---------- */
:root[data-lumen-theme="dark"], :root[data-lumen-theme="dark"] body { background: #1b1d21 !important; color: #d9dce1 !important; }
:root[data-lumen-theme="dark"] body:not(.lumen-kind-pdf):not(.lumen-kind-welcome) :not(img):not(svg):not(svg *):not(video):not(canvas) {
  color: inherit !important; background-color: transparent !important; border-color: #3b4048 !important;
}
:root[data-lumen-theme="dark"] body:not(.lumen-kind-welcome) a, :root[data-lumen-theme="dark"] body:not(.lumen-kind-welcome) a * { color: #8ab4ff !important; }
:root[data-lumen-theme="dark"] body:not(.lumen-kind-pdf) pre { background-color: #262a31 !important; }
:root[data-lumen-theme="dark"] .lumen-note { background-color: #3d3420 !important; color: #f1dca7 !important; }
:root[data-lumen-theme="dark"] body.lumen-kind-pdf { background: #111316 !important; }
:root[data-lumen-theme="dark"] .lumen-page { background: #1b1d21; box-shadow: 0 1px 4px rgba(0,0,0,.6); }
:root[data-lumen-theme="dark"] .lumen-page img { filter: invert(.9) hue-rotate(180deg); }

/* ---------- Reading mode (sepia, serif, narrower column) ---------- */
:root[data-lumen-theme="reading"], :root[data-lumen-theme="reading"] body { background: #f4ecd8 !important; color: #3d3123 !important; }
:root[data-lumen-theme="reading"] body:not(.lumen-kind-pdf):not(.lumen-kind-welcome) :not(img):not(svg):not(svg *):not(video):not(canvas) {
  color: inherit !important; background-color: transparent !important; border-color: #d9c9a7 !important;
}
:root[data-lumen-theme="reading"] body:not(.lumen-kind-pdf):not(.lumen-kind-welcome) :not(pre):not(code):not(pre *):not(kbd):not(svg):not(svg *) {
  font-family: Georgia, "Iowan Old Style", "Palatino Linotype", Cambria, serif !important;
}
:root[data-lumen-theme="reading"] body:not(.lumen-kind-pdf):not(.lumen-kind-welcome) {
  font-family: Georgia, "Iowan Old Style", "Palatino Linotype", Cambria, serif !important;
  line-height: 1.8 !important; max-width: 40em !important; margin-left: auto !important; margin-right: auto !important;
  padding: 2.5em 2em 6em !important; box-sizing: border-box;
}
:root[data-lumen-theme="reading"] body:not(.lumen-kind-welcome) a, :root[data-lumen-theme="reading"] body:not(.lumen-kind-welcome) a * { color: #8b4513 !important; }
:root[data-lumen-theme="reading"] body:not(.lumen-kind-pdf) pre { background-color: #ebe0c6 !important; }
:root[data-lumen-theme="reading"] body.lumen-kind-pdf { background: #e4d9bf !important; }
:root[data-lumen-theme="reading"] .lumen-page img { filter: sepia(.45) saturate(.9) brightness(.96); }
""";
}
