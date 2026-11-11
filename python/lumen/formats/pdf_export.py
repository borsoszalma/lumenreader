"""Writes highlights and notes into a copy of a PDF as standard PDF annotations.

Each highlight becomes a /Highlight annotation (with its own appearance, so every viewer shows it);
a note becomes the annotation's text plus a /Popup, which Acrobat, Edge, Foxit, pdf.js etc. show as a
comment. The original bytes are kept and an *incremental update* is appended, as PDF editors do.
Coordinates come from the reader (pdf.js text layer → PDF user space).
"""
from __future__ import annotations

import os
import zlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .pdf import Name, PdfReader, Ref, Stream

COLORS = {"yellow": (1.0, 0.83, 0.0), "green": (0.30, 0.80, 0.40), "blue": (0.35, 0.65, 1.0),
          "pink": (1.0, 0.45, 0.72)}


def can_annotate(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            r = PdfReader(f.read())
        return "/Encrypt" not in r.trailer and bool(r.pages())
    except Exception:
        return False


# ---------------------------------------------------------------- serialization

def _num(v: float) -> bytes:
    if isinstance(v, int) or float(v).is_integer():
        return str(int(v)).encode()
    return f"{v:.3f}".rstrip("0").rstrip(".").encode()


def _name(n: str) -> bytes:
    out = bytearray(b"/")
    for ch in n.lstrip("/").encode("utf-8"):
        if ch < 0x21 or ch > 0x7E or ch in b"#()<>[]{}/%":
            out += b"#%02X" % ch
        else:
            out.append(ch)
    return bytes(out)


def _text(s: str) -> bytes:
    """PDF text string: plain ASCII literal or UTF-16BE with BOM."""
    try:
        raw = s.encode("ascii")
        if all(32 <= c < 127 for c in raw):
            return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"
    except UnicodeEncodeError:
        pass
    return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode() + b">"


def ser(o: Any) -> bytes:
    if isinstance(o, _Raw):
        return bytes(o)
    if isinstance(o, Ref):
        return b"%d %d R" % (o[0], o[1])
    if isinstance(o, Name):
        return _name(o)
    if o is True:
        return b"true"
    if o is False:
        return b"false"
    if o is None:
        return b"null"
    if isinstance(o, (int, float)):
        return _num(o)
    if isinstance(o, bytes):
        return b"<" + o.hex().encode() + b">"
    if isinstance(o, list):
        return b"[" + b" ".join(ser(x) for x in o) + b"]"
    if isinstance(o, dict):
        return b"<<" + b"".join(_name(k) + b" " + ser(v) for k, v in o.items()) + b">>"
    if isinstance(o, str):  # keyword-like leftovers
        return o.encode("latin-1", errors="replace")
    raise TypeError(type(o))


class _Raw(bytes):
    """Already serialized PDF fragment."""


# ---------------------------------------------------------------- export

def export(src: str, dst: str, annotations: list[dict], author: str = "Lumen Reader") -> int:
    with open(src, "rb") as f:
        data = f.read()
    reader = PdfReader(data)
    if "/Encrypt" in reader.trailer:
        raise ValueError("encrypted PDF")
    pages = reader.pages()
    size = reader.resolve(reader.trailer.get("/Size")) or 0
    next_num = max([size] + [n + 1 for n in reader.offsets] + [n + 1 for n in reader.in_stream])

    objects: dict[int, bytes] = {}
    per_page: dict[int, list[Ref]] = defaultdict(list)
    now = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
    written = 0

    for a in annotations:
        try:
            page_no = int(a.get("section") or 0)
        except ValueError:
            continue
        rects = [r for r in (a.get("pdfRects") or []) if len(r) == 4]
        if not rects or not 1 <= page_no <= len(pages):
            continue
        page_ref = Ref(pages[page_no - 1], 0)
        x1 = min(r[0] for r in rects)
        y1 = min(r[1] for r in rects)
        x2 = max(r[2] for r in rects)
        y2 = max(r[3] for r in rects)
        quads: list[float] = []
        for r in rects:  # upper-left, upper-right, lower-left, lower-right
            quads += [r[0], r[3], r[2], r[3], r[0], r[1], r[2], r[1]]
        color = COLORS.get(a.get("color", "yellow"), COLORS["yellow"])

        # appearance: coloured boxes, multiplied with the page so the text stays readable
        content = b"/GS0 gs " + b" ".join(_num(c) for c in color) + b" rg\n" + b"".join(
            b"%s %s %s %s re f\n" % (_num(r[0]), _num(r[1]), _num(r[2] - r[0]), _num(r[3] - r[1])) for r in rects)
        ap_num = next_num
        next_num += 1
        ap_dict = {"/Type": Name("/XObject"), "/Subtype": Name("/Form"), "/BBox": [x1, y1, x2, y2],
                   "/Resources": {"/ExtGState": {"/GS0": {"/Type": Name("/ExtGState"), "/BM": Name("/Multiply"),
                                                          "/CA": 1, "/ca": 1}}},
                   "/Length": len(content)}
        objects[ap_num] = ser(ap_dict) + b"\nstream\n" + content + b"\nendstream"

        hl_num = next_num
        next_num += 1
        hl = {"/Type": Name("/Annot"), "/Subtype": Name("/Highlight"), "/Rect": [x1, y1, x2, y2],
              "/QuadPoints": quads, "/C": list(color), "/F": 4, "/P": page_ref,
              "/T": _Raw(_text(author)), "/M": _Raw(_text(now)), "/CreationDate": _Raw(_text(now)),
              "/NM": _Raw(_text("lumen-" + str(a.get("id", hl_num)))),
              "/AP": {"/N": Ref(ap_num, 0)}}
        note = (a.get("note") or "").strip()
        if note:
            pop_num = next_num
            next_num += 1
            hl["/Contents"] = _Raw(_text(note))
            hl["/Popup"] = Ref(pop_num, 0)
            objects[pop_num] = ser({"/Type": Name("/Annot"), "/Subtype": Name("/Popup"), "/Parent": Ref(hl_num, 0),
                                    "/Rect": [x2, max(0, y2 - 120), x2 + 220, y2], "/Open": False, "/F": 28})
            per_page[page_no].append(Ref(hl_num, 0))
            per_page[page_no].append(Ref(pop_num, 0))
        else:
            per_page[page_no].append(Ref(hl_num, 0))
        objects[hl_num] = ser(hl)
        written += 1

    # pages get their (extended) /Annots array
    for page_no, refs in per_page.items():
        num = pages[page_no - 1]
        page = dict(reader.resolve(Ref(num, 0)))
        existing = reader.resolve(page.get("/Annots"))
        page["/Annots"] = (list(existing) if isinstance(existing, list) else []) + refs
        objects[num] = ser(page)

    out = bytearray(data)
    if not out.endswith(b"\n"):
        out += b"\n"
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objects[num] + b"\nendobj\n"

    trailer = {k: v for k, v in reader.trailer.items() if k in ("/Root", "/Info", "/ID")}
    trailer["/Size"] = next_num
    if reader.startxref is not None:
        trailer["/Prev"] = reader.startxref

    # contiguous runs of object numbers for the cross-reference section
    runs: list[list[int]] = []
    for num in sorted(offsets):
        if runs and runs[-1][-1] == num - 1:
            runs[-1].append(num)
        else:
            runs.append([num])

    xref_pos = len(out)
    if reader.xref_is_stream:
        # newer files use cross-reference streams; the update uses one too
        xref_num = next_num
        trailer["/Size"] = next_num + 1
        runs.append([xref_num])
        offsets[xref_num] = xref_pos
        rows = b"".join(b"\x01" + offsets[n].to_bytes(4, "big") + b"\x00\x00" for run in runs for n in run)
        body = zlib.compress(rows)
        xd = dict(trailer)
        xd.update({"/Type": Name("/XRef"), "/W": [1, 4, 2], "/Index": [x for run in runs for x in (run[0], len(run))],
                   "/Filter": Name("/FlateDecode"), "/Length": len(body)})
        out += b"%d 0 obj\n" % xref_num + ser(xd) + b"\nstream\n" + body + b"\nendstream\nendobj\n"
    else:
        out += b"xref\n"
        for run in runs:
            out += b"%d %d\n" % (run[0], len(run))
            for n in run:
                out += b"%010d 00000 n\r\n" % offsets[n]
        out += b"trailer\n" + ser(trailer) + b"\n"
    out += b"startxref\n%d\n%%%%EOF\n" % xref_pos

    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(out)
    os.replace(tmp, dst)
    return written
