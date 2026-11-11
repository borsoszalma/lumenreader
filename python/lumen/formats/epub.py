"""EPUB 2/3: served straight from the zip; TOC from nav.xhtml (EPUB 3) or toc.ncx (EPUB 2)."""
from __future__ import annotations

import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import unquote

from .common import DocumentError, TocItem, ZipSession, collapse_ws

OPS = "http://www.idpf.org/2007/ops"
DC = "http://purl.org/dc/elements/1.1/"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml(zf: zipfile.ZipFile, names: dict[str, str], path: str) -> Optional[ET.Element]:
    real = names.get(path.lower())
    if real is None:
        return None
    data = zf.read(real)
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        # XHTML with HTML entities (&nbsp; …) that plain XML does not know: replace them
        text = data.decode("utf-8", errors="replace")
        import html.entities as ent
        text = re.sub(r"&([a-zA-Z][a-zA-Z0-9]*);",
                      lambda m: m.group(0) if m.group(1) in ("amp", "lt", "gt", "quot", "apos")
                      else chr(ent.name2codepoint.get(m.group(1), 0x3F)), text)
        text = re.sub(r"<!DOCTYPE[^>]*>", "", text)
        try:
            return ET.fromstring(text.encode("utf-8"))
        except ET.ParseError:
            return None


def _resolve(base_dir: str, href: str) -> tuple[str, str]:
    """(path, path#fragment) of an href relative to a folder inside the archive."""
    frag = ""
    if "#" in href:
        href, frag = href.split("#", 1)
        frag = "#" + frag
    if not href:
        return "", frag
    path = posixpath.normpath(posixpath.join(base_dir, unquote(href))).lstrip("/")
    if path.startswith("../"):
        path = path.replace("../", "")
    return path, path + frag


def load(path: str, chapter_label) -> ZipSession:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise DocumentError("ErrorTitle")
    try:
        names = {n.replace("\\", "/").lower(): n for n in zf.namelist()}
        container = _xml(zf, names, "META-INF/container.xml")
        rootfile = None if container is None else next(
            (e for e in container.iter() if _local(e.tag) == "rootfile"), None)
        opf_path = rootfile.get("full-path") if rootfile is not None else None
        opf = _xml(zf, names, opf_path) if opf_path else None
        if opf is None:
            raise DocumentError("ErrorTitle")
        opf_dir = posixpath.dirname(opf_path)

        title_el = next((e for e in opf.iter() if e.tag == f"{{{DC}}}title"), None)
        title = collapse_ws(title_el.text if title_el is not None else "") or \
            posixpath.splitext(posixpath.basename(path.replace("\\", "/")))[0]

        manifest: dict[str, tuple[str, str, str]] = {}
        for item in opf.iter():
            if _local(item.tag) == "item" and item.get("id") and item.get("href"):
                manifest[item.get("id")] = (_resolve(opf_dir, item.get("href"))[0],
                                            item.get("media-type", ""), item.get("properties", ""))

        spine_el = next((e for e in opf.iter() if _local(e.tag) == "spine"), None)
        spine = []
        if spine_el is not None:
            for ref in spine_el:
                if _local(ref.tag) == "itemref":
                    m = manifest.get(ref.get("idref", ""))
                    if m and m[0].lower() in names:
                        spine.append(m[0])

        toc: list[TocItem] = []
        nav = next((m for m in manifest.values() if "nav" in m[2].split()), None)
        if nav:
            toc = _parse_nav(zf, names, nav[0])
        if not toc:
            ncx_id = spine_el.get("toc") if spine_el is not None else None
            ncx = manifest.get(ncx_id or "") or next(
                (m for m in manifest.values() if m[1] == "application/x-dtbncx+xml"), None)
            if ncx:
                toc = _parse_ncx(zf, names, ncx[0])
        if not toc:
            toc = [TocItem(title=chapter_label(i + 1), href=p) for i, p in enumerate(spine)]

        session = ZipSession(path, title, zf)
        session.toc = toc
        session.spine = spine
        session.start = spine[0] if spine else ""
        return session
    except Exception:
        zf.close()
        raise


def _parse_nav(zf, names, nav_path) -> list[TocItem]:
    doc = _xml(zf, names, nav_path)
    if doc is None:
        return []
    navs = [e for e in doc.iter() if _local(e.tag) == "nav"]
    toc_nav = next((n for n in navs if "toc" in (n.get(f"{{{OPS}}}type") or "").split()), navs[0] if navs else None)
    if toc_nav is None:
        return []
    ol = next((c for c in toc_nav if _local(c.tag) == "ol"), None)
    return _nav_list(ol, posixpath.dirname(nav_path)) if ol is not None else []


def _nav_list(ol, base) -> list[TocItem]:
    items = []
    for li in ol:
        if _local(li.tag) != "li":
            continue
        label = next((c for c in li if _local(c.tag) in ("a", "span")), None)
        href = label.get("href") if label is not None else None
        item = TocItem(title=collapse_ws("".join(label.itertext())) if label is not None else "",
                       href=_resolve(base, href)[1] if href else None)
        sub = next((c for c in li if _local(c.tag) == "ol"), None)
        if sub is not None:
            item.children = _nav_list(sub, base)
        if item.title or item.children:
            items.append(item)
    return items


def _parse_ncx(zf, names, ncx_path) -> list[TocItem]:
    doc = _xml(zf, names, ncx_path)
    nav_map = None if doc is None else next((e for e in doc.iter() if _local(e.tag) == "navMap"), None)
    return _nav_points(nav_map, posixpath.dirname(ncx_path)) if nav_map is not None else []


def _nav_points(parent, base) -> list[TocItem]:
    items = []
    for np in parent:
        if _local(np.tag) != "navPoint":
            continue
        label = next((c for c in np.iter() if _local(c.tag) == "text"), None)
        content = next((c for c in np if _local(c.tag) == "content"), None)
        src = content.get("src") if content is not None else None
        item = TocItem(title=collapse_ws(label.text if label is not None else ""),
                       href=_resolve(base, src)[1] if src else None)
        item.children = _nav_points(np, base)
        items.append(item)
    return items


# ---------------------------------------------------------------- search support

class _BodyText(__import__("html.parser").parser.HTMLParser):
    """Text of <body> exactly as the browser's textContent sees it, plus the offset of every id."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.parts: list[str] = []
        self.length = 0
        self.ids: dict[str, int] = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower().rsplit(":", 1)[-1] == "body":
            self.in_body = True
        if self.in_body:
            for k, v in attrs:
                if k == "id" and v and v not in self.ids:
                    self.ids[v] = self.length

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if tag.lower().rsplit(":", 1)[-1] == "body":
            self.in_body = False

    def handle_data(self, data):
        if self.in_body:
            self.parts.append(data)
            self.length += len(data)


def chapter_text(session: ZipSession, path: str) -> tuple[str, dict[str, int]]:
    cache = session.__dict__.setdefault("_text_cache", {})
    if path not in cache:
        res = session.serve(path)
        p = _BodyText()
        if res:
            raw = res[0].decode("utf-8", errors="replace")
            raw = re.sub(r"<!\[CDATA\[(.*?)\]\]>", lambda m: m.group(1), raw, flags=re.S)
            p.feed(raw)
            p.close()
        cache[path] = ("".join(p.parts), p.ids)
    return cache[path]
