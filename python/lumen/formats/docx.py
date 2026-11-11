"""DOCX → HTML (standard library: zipfile + ElementTree).

Headings (also localized Word styles such as "Címsor 1" – resolved through the English built-in style
name), bold/italic/underline/strike, super/subscript, bullet and numbered lists, tables, hyperlinks,
bookmarks and embedded images.
"""
from __future__ import annotations

import base64
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional

from .common import esc

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _val(el: Optional[ET.Element], attr: str = "val") -> Optional[str]:
    return None if el is None else el.get(w(attr))


def _on(el: Optional[ET.Element]) -> bool:
    if el is None:
        return False
    v = el.get(w("val"))
    return v is None or v.lower() not in ("0", "false", "off", "none")


def convert(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        return _Converter(z).run()


class _Converter:
    def __init__(self, z: zipfile.ZipFile):
        self.z = z
        names = set(z.namelist())
        main = "word/document.xml"
        try:
            rels = ET.fromstring(z.read("_rels/.rels"))
            for r in rels.findall(f"{{{PR}}}Relationship"):
                if r.get("Type", "").endswith("/officeDocument"):
                    main = r.get("Target", main).lstrip("/")
        except KeyError:
            pass
        self.main = main
        self.base = posixpath.dirname(main)
        self.doc = ET.fromstring(z.read(main))

        self.rels: dict[str, tuple[str, str]] = {}  # id -> (target, mode)
        rel_path = posixpath.join(self.base, "_rels", posixpath.basename(main) + ".rels")
        if rel_path in names:
            for r in ET.fromstring(z.read(rel_path)).findall(f"{{{PR}}}Relationship"):
                self.rels[r.get("Id", "")] = (r.get("Target", ""), r.get("TargetMode", ""))

        self.styles: dict[str, ET.Element] = {}
        styles_path = posixpath.join(self.base, "styles.xml")
        if styles_path in names:
            for s in ET.fromstring(z.read(styles_path)).findall(w("style")):
                sid = s.get(w("styleId"))
                if sid:
                    self.styles.setdefault(sid, s)

        self.numbering: Optional[ET.Element] = None
        num_path = posixpath.join(self.base, "numbering.xml")
        if num_path in names:
            self.numbering = ET.fromstring(z.read(num_path))
        self.ordered_cache: dict[tuple[str, int], bool] = {}
        self.out: list[str] = []
        self.lists: list[str] = []

    def run(self) -> str:
        body = self.doc.find(w("body"))
        if body is None:
            return ""
        self.blocks(body)
        self.close_lists(0)
        return "".join(self.out)

    # ------------------------------------------------------------ blocks
    def blocks(self, parent: ET.Element) -> None:
        for el in parent:
            tag = el.tag
            if tag == w("p"):
                self.paragraph(el)
            elif tag == w("tbl"):
                self.close_lists(0)
                self.table(el)
            elif tag == w("sdt"):
                content = el.find(w("sdtContent"))
                if content is not None:
                    self.blocks(content)
            elif tag == w("customXml"):
                self.blocks(el)

    def paragraph(self, p: ET.Element) -> None:
        ppr = p.find(w("pPr"))
        level = self.heading_level(ppr)
        inner = self.inlines(p)
        if level == 0:
            num = self.numbering_of(ppr)
            if num:
                num_id, ilvl = num
                self.open_list(ilvl + 1, "ol" if self.is_ordered(num_id, ilvl) else "ul")
                self.out.append(f"<li>{inner}</li>")
                return
        self.close_lists(0)
        jc = _val(ppr.find(w("jc")) if ppr is not None else None)
        align = {"center": "center", "right": "right", "end": "right", "both": "justify",
                 "distribute": "justify"}.get(jc or "", "")
        style = f' style="text-align:{align}"' if align else ""
        if level:
            self.out.append(f"<h{level}{style}>{inner}</h{level}>")
        elif _is_blank(inner):
            self.out.append('<p class="lumen-empty"></p>')
        else:
            self.out.append(f"<p{style}>{inner}</p>")

    def table(self, tbl: ET.Element) -> None:
        self.out.append("<table>")
        for tr in tbl.findall(w("tr")):
            self.out.append("<tr>")
            for tc in tr.findall(w("tc")):
                tcpr = tc.find(w("tcPr"))
                span = _val(tcpr.find(w("gridSpan")) if tcpr is not None else None)
                self.out.append(f'<td colspan="{int(span)}">' if span and span.isdigit() and int(span) > 1 else "<td>")
                saved = self.lists
                self.lists = []
                self.blocks(tc)
                self.close_lists(0)
                self.lists = saved
                self.out.append("</td>")
            self.out.append("</tr>")
        self.out.append("</table>")

    # ------------------------------------------------------------ inlines
    def inlines(self, container: ET.Element) -> str:
        parts: list[str] = []
        for el in container:
            tag = el.tag
            if tag in (w("pPr"), w("del"), w("rPr")):
                continue
            if tag == w("r"):
                parts.append(self.run_(el))
            elif tag == w("hyperlink"):
                rid = el.get(f"{{{R}}}id")
                anchor = el.get(w("anchor"))
                href = None
                if rid and rid in self.rels:
                    href = self.rels[rid][0]
                elif anchor:
                    href = "#" + anchor
                inner = self.inlines(el)
                parts.append(f'<a href="{esc(href)}">{inner}</a>' if href else inner)
            elif tag == w("bookmarkStart"):
                name = el.get(w("name"), "")
                if name and not name.startswith("_GoBack"):
                    parts.append(f'<a id="{esc(name)}"></a>')
            elif len(el):
                parts.append(self.inlines(el))
        return "".join(parts)

    def run_(self, r: ET.Element) -> str:
        rpr = r.find(w("rPr"))
        if rpr is not None and _on(rpr.find(w("vanish"))):
            return ""
        text: list[str] = []
        for c in r:
            tag = c.tag
            if tag == w("t"):
                text.append(esc(c.text))
            elif tag == w("tab"):
                text.append("&emsp;")
            elif tag == w("br"):
                if c.get(w("type")) != "page":
                    text.append("<br>")
            elif tag == w("cr"):
                text.append("<br>")
            elif tag == w("noBreakHyphen"):
                text.append("&#8209;")
            elif tag in (w("drawing"), w("pict"), f"{{{MC}}}AlternateContent"):
                text.append(self.image(c))
        s = "".join(text)
        if not s or rpr is None:
            return s
        if _on(rpr.find(w("b"))):
            s = f"<strong>{s}</strong>"
        if _on(rpr.find(w("i"))):
            s = f"<em>{s}</em>"
        u = _val(rpr.find(w("u")))
        if u and u != "none":
            s = f"<u>{s}</u>"
        if _on(rpr.find(w("strike"))) or _on(rpr.find(w("dstrike"))):
            s = f"<s>{s}</s>"
        va = _val(rpr.find(w("vertAlign")))
        if va == "superscript":
            s = f"<sup>{s}</sup>"
        elif va == "subscript":
            s = f"<sub>{s}</sub>"
        return s

    def image(self, el: ET.Element) -> str:
        rid = None
        blip = el.find(f".//{{{A}}}blip")
        if blip is not None:
            rid = blip.get(f"{{{R}}}embed")
        if not rid:
            for d in el.iter():
                if d.tag.endswith("}imagedata"):
                    rid = d.get(f"{{{R}}}id")
                    if rid:
                        break
        if not rid or rid not in self.rels:
            return ""
        target = self.rels[rid][0]
        part = posixpath.normpath(posixpath.join(self.base, target)) if not target.startswith("/") else target[1:]
        ext = posixpath.splitext(part)[1].lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".bmp": "image/bmp", ".svg": "image/svg+xml", ".webp": "image/webp"}.get(ext)
        if not mime:
            return ""  # EMF/WMF are not displayable in a browser
        try:
            data = self.z.read(part)
        except KeyError:
            return ""
        width = ""
        extent = el.find(f".//{{{WP}}}extent")
        if extent is not None and (extent.get("cx") or "").isdigit():
            width = f' width="{int(extent.get("cx")) // 9525}"'
        return f'<img src="data:{mime};base64,{base64.b64encode(data).decode()}"{width} alt="">'

    # ------------------------------------------------------------ styles / lists
    def heading_level(self, ppr: Optional[ET.Element]) -> int:
        if ppr is None:
            return 0
        ol = _val(ppr.find(w("outlineLvl")))
        if ol and ol.isdigit() and int(ol) < 6:
            return int(ol) + 1
        sid = _val(ppr.find(w("pStyle")))
        return self.style_heading(sid, 0) if sid else 0

    def style_heading(self, sid: str, depth: int) -> int:
        st = self.styles.get(sid)
        if st is None or depth > 10:
            m = re.match(r"^heading\s*(\d)$", sid, re.I)
            return min(max(int(m.group(1)), 1), 6) if m else 0
        name = (_val(st.find(w("name"))) or "").lower()
        if name == "title":
            return 1
        m = re.match(r"^heading (\d)$", name)
        if m:
            return min(max(int(m.group(1)), 1), 6)
        ppr = st.find(w("pPr"))
        ol = _val(ppr.find(w("outlineLvl"))) if ppr is not None else None
        if ol and ol.isdigit() and int(ol) < 6:
            return int(ol) + 1
        based = _val(st.find(w("basedOn")))
        return self.style_heading(based, depth + 1) if based else 0

    def numbering_of(self, ppr: Optional[ET.Element]) -> Optional[tuple[str, int]]:
        if ppr is None:
            return None
        numpr = ppr.find(w("numPr"))
        if numpr is None:
            sid = _val(ppr.find(w("pStyle")))
            st = self.styles.get(sid or "")
            sppr = st.find(w("pPr")) if st is not None else None
            numpr = sppr.find(w("numPr")) if sppr is not None else None
        if numpr is None:
            return None
        num_id = _val(numpr.find(w("numId")))
        ilvl = _val(numpr.find(w("ilvl"))) or "0"
        if not num_id or num_id == "0":
            return None
        return num_id, min(max(int(ilvl) if ilvl.isdigit() else 0, 0), 8)

    def is_ordered(self, num_id: str, ilvl: int) -> bool:
        key = (num_id, ilvl)
        if key in self.ordered_cache:
            return self.ordered_cache[key]
        fmt = None
        if self.numbering is not None:
            abs_id = None
            for num in self.numbering.findall(w("num")):
                if num.get(w("numId")) == num_id:
                    abs_id = _val(num.find(w("abstractNumId")))
                    break
            for an in self.numbering.findall(w("abstractNum")):
                if an.get(w("abstractNumId")) == abs_id:
                    for lvl in an.findall(w("lvl")):
                        if lvl.get(w("ilvl")) == str(ilvl):
                            fmt = _val(lvl.find(w("numFmt")))
                    break
        ordered = fmt is not None and fmt not in ("bullet", "none")
        self.ordered_cache[key] = ordered
        return ordered

    def open_list(self, depth: int, tag: str) -> None:
        self.close_lists(depth)
        if len(self.lists) == depth and depth > 0 and self.lists[-1] != tag:
            self.close_lists(depth - 1)
        while len(self.lists) < depth:
            self.out.append(f"<{tag}>")
            self.lists.append(tag)

    def close_lists(self, depth: int) -> None:
        while len(self.lists) > depth:
            self.out.append(f"</{self.lists.pop()}>")


def _is_blank(html_text: str) -> bool:
    if "<img" in html_text:
        return False
    return not re.sub(r"<[^>]+>|&emsp;|&nbsp;", "", html_text).strip()
