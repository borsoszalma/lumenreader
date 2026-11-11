"""Opens a document file and returns a Session the viewer can display."""
from __future__ import annotations

import os
import shutil
from typing import Callable

from . import doc, docx, epub, markdown, pdf, rtf
from .common import (DocumentError, FlowSession, HtmlSession, PdfSession, Session, TocItem, decode_text,
                     esc, text_to_html, wrap)

EXTENSIONS = [".epub", ".pdf", ".docx", ".doc", ".rtf", ".md", ".markdown", ".mdown", ".mkd",
              ".html", ".htm", ".xhtml", ".txt"]


def is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in EXTENSIONS


def load(path: str, t: Callable[[str], str]) -> Session:
    """t(key) returns a localized string (used for generated labels and notes)."""
    ext = os.path.splitext(path)[1].lower()
    title = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.dirname(os.path.abspath(path))

    if ext == ".epub":
        return epub.load(path, lambda n: t("Chapter").format(n))

    if ext == ".pdf":
        session = PdfSession(path, title)
        toc, pages = pdf.read_structure(path)
        session.pages = pages
        if not toc and 0 < pages <= 3000:
            toc = [TocItem(title=t("Page").format(n), page=n) for n in range(1, pages + 1)]
        session.toc = toc
        return session

    if ext == ".docx":
        return FlowSession(path, title, wrap(title, docx.convert(path)), folder)

    if ext == ".doc":
        return _load_doc(path, title, folder, t)

    if ext == ".rtf":
        return FlowSession(path, title, wrap(title, rtf.convert(path)), folder)

    if ext in (".md", ".markdown", ".mdown", ".mkd"):
        with open(path, "rb") as f:
            body = markdown.to_html(decode_text(f.read()))
        return FlowSession(path, title, wrap(title, body), folder)

    if ext in (".html", ".htm", ".xhtml"):
        return HtmlSession(path, title)

    if ext == ".txt":
        with open(path, "rb") as f:
            body = text_to_html(decode_text(f.read()))
        return FlowSession(path, title, wrap(title, body), folder)

    raise DocumentError("UnsupportedFormat", ext)


def _load_doc(path: str, title: str, folder: str, t) -> Session:
    kind = doc.sniff(path)
    if kind == "rtf":
        return FlowSession(path, title, wrap(title, rtf.convert(path)), folder)
    if kind == "zip":
        return FlowSession(path, title, wrap(title, docx.convert(path)), folder)
    if kind == "html":
        return HtmlSession(path, title)

    converted, tmp = doc.convert_to_docx(path)
    if converted:
        try:
            return FlowSession(path, title, wrap(title, docx.convert(converted)), folder)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    text = doc.extract_text(path)
    note = f'<div class="lumen-note">{esc(t("DocLimitedPreview"))}</div>'
    return FlowSession(path, title, wrap(title, note + text_to_html(text)), folder)
