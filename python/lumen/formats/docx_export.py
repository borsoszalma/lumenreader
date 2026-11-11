"""Writes highlights and notes into a copy of a .docx as real Word highlights and comments (balloons).

Paragraphs are addressed by the same index the HTML conversion puts into data-p (see docx.py), and
characters are counted with the same rules, so a selection made in the reader maps 1:1 onto Word runs.
Everything else in the package is copied byte for byte.
"""
from __future__ import annotations

import copy
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML = "http://www.w3.org/XML/1998/namespace"
COMMENTS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
COMMENTS_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"

HIGHLIGHT = {"yellow": "yellow", "green": "green", "blue": "cyan", "pink": "magenta"}

# w:rPr children that must come *after* w:highlight (schema order)
_AFTER_HIGHLIGHT = {"u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang",
                    "eastAsianLayout", "specVanish", "oMath", "rPrChange"}


def w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------- XML that keeps its prefixes

def _register_namespaces(data: bytes) -> None:
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        if prefix and not re.match(r"ns\d+$", prefix):
            try:
                ET.register_namespace(prefix, uri)
            except ValueError:
                pass


def _serialize(root: ET.Element, original: bytes) -> bytes:
    """ElementTree drops unused namespace declarations; Word needs them (mc:Ignorable), so the
    original root start tag is put back."""
    out = ET.tostring(root, encoding="unicode").encode("utf-8")
    body = re.sub(rb"^\s*<\?xml[^>]*\?>\s*", b"", original)
    orig_start = re.match(rb"<[^>]+>", body).group(0)
    new_start = re.match(rb"<[^>]+>", out).group(0)
    if orig_start.endswith(b"/>"):
        return out
    # namespaces ElementTree hoisted to the root (originally declared deeper) must stay declared
    have = set(re.findall(rb'xmlns(?::([\w.-]+))?="', orig_start))
    missing = [m.group(0) for m in re.finditer(rb'\sxmlns(?::([\w.-]+))?="[^"]*"', new_start)
               if m.group(1) not in have and (m.group(1) or b"") not in have]
    merged = orig_start[:-1] + b"".join(missing) + b">"
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + merged + out[len(new_start):]


# ---------------------------------------------------------------- paragraph / run model

def _paragraphs(body: ET.Element) -> list[ET.Element]:
    """Paragraphs in the order docx.py numbers them."""
    result: list[ET.Element] = []

    def blocks(parent):
        for el in parent:
            tag = el.tag
            if tag == w("p"):
                result.append(el)
            elif tag == w("tbl"):
                for tr in el.findall(w("tr")):
                    for tc in tr.findall(w("tc")):
                        blocks(tc)
            elif tag == w("sdt"):
                content = el.find(w("sdtContent"))
                if content is not None:
                    blocks(content)
            elif tag == w("customXml"):
                blocks(el)

    blocks(body)
    return result


def _vanished(r: ET.Element) -> bool:
    rpr = r.find(w("rPr"))
    v = rpr.find(w("vanish")) if rpr is not None else None
    return v is not None and (v.get(w("val")) or "true").lower() not in ("0", "false", "off")


def _child_len(c: ET.Element) -> int:
    tag = c.tag
    if tag == w("t"):
        return len(c.text or "")
    if tag in (w("tab"), w("noBreakHyphen")):
        return 1
    return 0


def _run_len(r: ET.Element) -> int:
    return 0 if _vanished(r) else sum(_child_len(c) for c in r)


def _runs(container: ET.Element):
    """(parent, run) pairs in the same traversal order as docx.py's inline renderer."""
    for el in list(container):
        tag = el.tag
        if tag in (w("pPr"), w("del"), w("rPr")):
            continue
        if tag == w("r"):
            yield container, el
        elif tag == w("hyperlink"):
            yield from _runs(el)
        elif tag == w("bookmarkStart"):
            continue
        elif len(el):
            yield from _runs(el)


def _split_run(parent: ET.Element, run: ET.Element, k: int) -> ET.Element:
    """Splits run at character k; returns the new second run (inserted right after the first)."""
    second = ET.Element(w("r"), dict(run.attrib))
    rpr = run.find(w("rPr"))
    if rpr is not None:
        second.append(copy.deepcopy(rpr))
    pos = 0
    moving = False
    for c in list(run):
        if c.tag == w("rPr"):
            continue
        if moving:
            run.remove(c)
            second.append(c)
            continue
        n = _child_len(c)
        if pos + n <= k:
            pos += n
            if pos == k and n:
                moving = True
            continue
        # this child straddles k (only w:t can)
        cut = k - pos
        tail = ET.Element(w("t"))
        tail.text = (c.text or "")[cut:]
        tail.set(f"{{{XML}}}space", "preserve")
        c.text = (c.text or "")[:cut]
        c.set(f"{{{XML}}}space", "preserve")
        second.append(tail)
        moving = True
        pos = k
    idx = list(parent).index(run)
    parent.insert(idx + 1, second)
    return second


def _select(paras: list[ET.Element], sp: int, so: int, ep: int, eo: int):
    """Runs (with their parents) exactly covering the character range, splitting runs as needed."""
    chosen = []
    for pi in range(sp, min(ep, len(paras) - 1) + 1):
        p = paras[pi]
        start = so if pi == sp else 0
        end = eo if pi == ep else 10 ** 9
        pos = 0
        for parent, r in list(_runs(p)):
            n = _run_len(r)
            a, b = pos, pos + n
            pos = b
            if n == 0 or b <= start or a >= end:
                continue
            if a < start:
                r = _split_run(parent, r, start - a)
                a = start
            if b > end:
                _split_run(parent, r, end - a)
            chosen.append((parent, r))
    return chosen


def _set_highlight(run: ET.Element, color: str) -> None:
    rpr = run.find(w("rPr"))
    if rpr is None:
        rpr = ET.Element(w("rPr"))
        run.insert(0, rpr)
    for old in rpr.findall(w("highlight")):
        rpr.remove(old)
    hl = ET.Element(w("highlight"), {w("val"): HIGHLIGHT.get(color, "yellow")})
    idx = next((i for i, c in enumerate(rpr) if _local(c.tag) in _AFTER_HIGHLIGHT), len(rpr))
    rpr.insert(idx, hl)


def _comment(cid: int, author: str, text: str) -> ET.Element:
    c = ET.Element(w("comment"), {w("id"): str(cid), w("author"): author, w("initials"): _initials(author),
                                  w("date"): datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    lines = text.replace("\r\n", "\n").split("\n") or [""]
    for i, line in enumerate(lines):
        p = ET.SubElement(c, w("p"))
        ppr = ET.SubElement(p, w("pPr"))
        ET.SubElement(ppr, w("pStyle"), {w("val"): "CommentText"})
        if i == 0:
            r = ET.SubElement(p, w("r"))
            rpr = ET.SubElement(r, w("rPr"))
            ET.SubElement(rpr, w("rStyle"), {w("val"): "CommentReference"})
            ET.SubElement(r, w("annotationRef"))
        r = ET.SubElement(p, w("r"))
        t = ET.SubElement(r, w("t"), {f"{{{XML}}}space": "preserve"})
        t.text = line
    return c


def _initials(name: str) -> str:
    return "".join(p[0] for p in name.split() if p)[:4].upper() or "LR"


# ---------------------------------------------------------------- main entry

def export(src: str, dst: str, annotations: list[dict], author: str = "Lumen Reader") -> int:
    """Writes a copy of src with the annotations. Returns the number of annotations written."""
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        main = "word/document.xml"
        try:
            for rel in ET.fromstring(zin.read("_rels/.rels")).findall(f"{{{PR}}}Relationship"):
                if rel.get("Type", "").endswith("/officeDocument"):
                    main = rel.get("Target", main).lstrip("/")
        except KeyError:
            pass
        base = main.rsplit("/", 1)[0]
        rels_name = f"{base}/_rels/{main.rsplit('/', 1)[1]}.rels"
        comments_name = None

        doc_bytes = zin.read(main)
        _register_namespaces(doc_bytes)
        ET.register_namespace("w", W)
        ET.register_namespace("r", R)
        root = ET.fromstring(doc_bytes)
        body = root.find(w("body"))
        paras = _paragraphs(body) if body is not None else []

        rels_bytes = zin.read(rels_name) if rels_name in names else (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        ET.register_namespace("", PR)
        rels = ET.fromstring(rels_bytes)
        for rel in rels.findall(f"{{{PR}}}Relationship"):
            if rel.get("Type") == COMMENTS_REL:
                comments_name = f"{base}/{rel.get('Target').lstrip('/')}" if not rel.get("Target", "").startswith("/") \
                    else rel.get("Target").lstrip("/")
        comments_bytes = zin.read(comments_name) if comments_name and comments_name in names else None
        if comments_bytes:
            _register_namespaces(comments_bytes)
            comments = ET.fromstring(comments_bytes)
        else:
            comments = ET.Element(w("comments"))
        next_id = max([int(c.get(w("id"), "0")) for c in comments.findall(w("comment"))] + [-1]) + 1
        first_new_id = next_id

        written = 0
        for a in annotations:
            d = a.get("docx")
            if not d:
                continue
            sp, so, ep, eo = (int(d.get(k, 0)) for k in ("sp", "so", "ep", "eo"))
            if sp >= len(paras) or (sp, so) >= (ep, eo):
                continue
            chosen = _select(paras, sp, so, ep, eo)
            if not chosen:
                continue
            for _, r in chosen:
                _set_highlight(r, a.get("color", "yellow"))
            note = (a.get("note") or "").strip()
            if note:
                cid = next_id
                next_id += 1
                p0, r0 = chosen[0]
                p1, r1 = chosen[-1]
                p0.insert(list(p0).index(r0), ET.Element(w("commentRangeStart"), {w("id"): str(cid)}))
                i1 = list(p1).index(r1)
                p1.insert(i1 + 1, ET.Element(w("commentRangeEnd"), {w("id"): str(cid)}))
                ref = ET.Element(w("r"))
                rpr = ET.SubElement(ref, w("rPr"))
                ET.SubElement(rpr, w("rStyle"), {w("val"): "CommentReference"})
                ET.SubElement(ref, w("commentReference"), {w("id"): str(cid)})
                p1.insert(i1 + 2, ref)
                comments.append(_comment(cid, author, note))
            written += 1

        new_doc = _serialize(root, doc_bytes)
        extra: dict[str, bytes] = {}
        if next_id > first_new_id:
            if comments_bytes:
                extra[comments_name] = _serialize(comments, comments_bytes)
            else:
                comments_name = f"{base}/comments.xml"
                extra[comments_name] = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' +
                                        ET.tostring(comments, encoding="unicode").encode("utf-8"))
                rid = "rIdLumenComments"
                ET.SubElement(rels, f"{{{PR}}}Relationship", {"Id": rid, "Type": COMMENTS_REL, "Target": "comments.xml"})
                extra[rels_name] = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' +
                                    ET.tostring(rels, encoding="unicode").encode("utf-8"))
                ct_bytes = zin.read("[Content_Types].xml")
                ET.register_namespace("", CT)
                ct = ET.fromstring(ct_bytes)
                part = "/" + comments_name
                if not any(o.get("PartName") == part for o in ct.findall(f"{{{CT}}}Override")):
                    ET.SubElement(ct, f"{{{CT}}}Override", {"PartName": part, "ContentType": COMMENTS_CT})
                extra["[Content_Types].xml"] = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' +
                                                ET.tostring(ct, encoding="unicode").encode("utf-8"))

        tmp = dst + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                if info.filename == main:
                    zout.writestr(info, new_doc)
                elif info.filename in extra:
                    zout.writestr(info, extra.pop(info.filename))
                else:
                    zout.writestr(info, zin.read(info.filename))
            for name, data in extra.items():
                zout.writestr(name, data)
    import os
    os.replace(tmp, dst)
    return written
