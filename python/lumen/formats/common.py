"""Shared helpers and document sessions (pure standard library)."""
from __future__ import annotations

import html
import mimetypes
import os
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Optional


class DocumentError(Exception):
    """Error with a user-facing message key (localized by the caller) and optional argument."""

    def __init__(self, key: str, arg: str = ""):
        super().__init__(key)
        self.key = key
        self.arg = arg


@dataclass
class TocItem:
    title: str
    href: Optional[str] = None      # session-relative path (+ #fragment) – EPUB chapters
    anchor: Optional[str] = None    # element id inside the page
    page: Optional[int] = None      # PDF page number (1-based)
    top: Optional[float] = None     # PDF: y coordinate of the destination on the page (user space)
    children: list["TocItem"] = field(default_factory=list)

    def to_json(self) -> dict:
        d: dict = {"title": self.title}
        if self.href is not None:
            d["href"] = self.href
        if self.anchor is not None:
            d["anchor"] = self.anchor
        if self.page is not None:
            d["page"] = self.page
        if self.top is not None:
            d["top"] = self.top
        if self.children:
            d["children"] = [c.to_json() for c in self.children]
        return d


def esc(s: Optional[str]) -> str:
    return html.escape(s or "", quote=True)


def wrap(title: str, body: str) -> str:
    """Full HTML page for converted documents (the viewer adds the theme CSS)."""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{esc(title)}</title></head>"
        f'<body class="lumen-doc">{body}</body></html>'
    )


def text_to_html(text: str) -> str:
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip():
            out.append("<p>" + esc(line).replace("\t", "&emsp;") + "</p>")
        else:
            out.append('<p class="lumen-empty"></p>')
    return "".join(out)


def decode_text(data: bytes) -> str:
    """UTF-8 (with/without BOM), UTF-16 with BOM, otherwise the Windows ANSI code page."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    import locale
    enc = locale.getpreferredencoding(False) or "cp1252"
    try:
        return data.decode(enc)
    except (UnicodeDecodeError, LookupError):
        return data.decode("cp1252", errors="replace")


_MIME_EXTRA = {
    ".xhtml": "application/xhtml+xml",
    ".ncx": "application/x-dtbncx+xml",
    ".opf": "application/oebps-package+xml",
    ".css": "text/css",
    ".js": "text/javascript",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".webp": "image/webp",
    ".md": "text/plain",
    ".mjs": "text/javascript",
    ".wasm": "application/wasm",
    ".bcmap": "application/octet-stream",
    ".pfb": "application/octet-stream",
    ".icc": "application/octet-stream",
}


def mime_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _MIME_EXTRA:
        return _MIME_EXTRA[ext]
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def safe_join(root: str, rel: str) -> Optional[str]:
    """Path inside root, or None when rel tries to escape it."""
    root_full = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root_full, rel.replace("/", os.sep)))
    if full == root_full or not full.startswith(root_full + os.sep):
        return None
    return full if os.path.isfile(full) else None


# ---------------------------------------------------------------------------
#  Sessions: what the viewer requests under /doc/<id>/...
# ---------------------------------------------------------------------------

class Session:
    kind = "flow"          # flow | html | epub | pdf
    start = ""

    def __init__(self, path: str, title: str):
        self.id = uuid.uuid4().hex[:12]
        self.path = path
        self.title = title
        self.toc: Optional[list[TocItem]] = None   # None -> viewer collects headings
        self.spine: list[str] = []
        self.pages = 0
        self.docx_source: Optional[str] = None   # .docx the notes can be written into

    def serve(self, rel: str) -> Optional[tuple[bytes, str]]:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def info(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "start": self.start,
            "toc": None if self.toc is None else [t.to_json() for t in self.toc],
            "spine": self.spine,
            "pages": self.pages,
            "word": self.docx_source is not None,
        }


class FlowSession(Session):
    """Document converted to one HTML page. Folder files are served so relative images resolve."""
    kind = "flow"
    start = "__lumen_view__.html"

    def __init__(self, path: str, title: str, html_page: str, folder: Optional[str]):
        super().__init__(path, title)
        self._html = html_page.encode("utf-8")
        self._folder = folder

    def serve(self, rel):
        if rel == self.start:
            return self._html, "text/html; charset=utf-8"
        if self._folder:
            full = safe_join(self._folder, rel)
            if full:
                with open(full, "rb") as f:
                    return f.read(), mime_for(full)
        return None


class HtmlSession(Session):
    kind = "html"

    def __init__(self, path: str, title: str):
        super().__init__(path, title)
        self._folder = os.path.dirname(os.path.abspath(path))
        self.start = os.path.basename(path)

    def serve(self, rel):
        full = safe_join(self._folder, rel)
        if not full:
            return None
        with open(full, "rb") as f:
            return f.read(), mime_for(full)


class ZipSession(Session):
    """Serves files straight out of a zip archive (EPUB)."""
    kind = "epub"

    def __init__(self, path: str, title: str, zf: zipfile.ZipFile):
        super().__init__(path, title)
        self._zip = zf
        self._names = {n.replace("\\", "/").lower(): n for n in zf.namelist()}

    def has(self, rel: str) -> bool:
        return rel.lower() in self._names

    def serve(self, rel):
        name = self._names.get(rel.lower())
        if name is None:
            return None
        return self._zip.read(name), mime_for(rel)

    def close(self):
        self._zip.close()


class PdfSession(Session):
    """The PDF itself is shown by the browser's built-in PDF viewer."""
    kind = "pdf"
    start = "document.pdf"

    def serve(self, rel):
        if rel != self.start:
            return None
        with open(self.path, "rb") as f:
            return f.read(), "application/pdf"


def collapse_ws(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", s or "").strip()
