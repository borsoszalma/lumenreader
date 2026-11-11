"""Reads the outline (bookmarks) and page count of a PDF – standard library only.

The page content itself is displayed by the browser's built-in PDF viewer; this module only needs
enough of the PDF object model to walk the outline: classic xref tables, cross-reference streams,
object streams, Flate/PNG-predictor decoding, named destinations, and a brute-force object scan as a
fallback for damaged files. All traversals are protected against reference cycles.
"""
from __future__ import annotations

import base64
import re
import zlib
from typing import Any, Optional

from .common import TocItem


class Ref(tuple):
    __slots__ = ()

    def __new__(cls, num: int, gen: int):
        return super().__new__(cls, (num, gen))

    @property
    def num(self) -> int:
        return self[0]


class Name(str):
    pass


class Stream:
    def __init__(self, d: dict, raw: bytes):
        self.dict = d
        self.raw = raw


_WS = b"\x00\t\n\x0c\r "
_DELIM = b"()<>[]{}/%"
_NUM = re.compile(rb"[+-]?(\d+\.?\d*|\.\d+)")


class PdfReader:
    def __init__(self, data: bytes):
        self.data = data
        self.offsets: dict[int, int] = {}
        self.in_stream: dict[int, tuple[int, int]] = {}
        self.trailer: dict = {}
        self.cache: dict[int, Any] = {}
        self._objstm_cache: dict[int, dict[int, Any]] = {}
        self._defined: set[int] = set()        # object numbers already set by a newer xref section
        self.startxref: Optional[int] = None    # offset of the newest cross-reference section
        self.xref_is_stream = False
        try:
            self._read_xref_chain()
        except Exception:
            pass
        if not isinstance(self.resolve(self.trailer.get("/Root")), dict):
            self._brute_force()

    # ------------------------------------------------------------ lexer / parser
    def _skip_ws(self, pos: int) -> int:
        d, n = self.data, len(self.data)
        while pos < n:
            c = d[pos]
            if c in _WS:
                pos += 1
            elif c == 0x25:  # % comment
                while pos < n and d[pos] not in b"\r\n":
                    pos += 1
            else:
                break
        return pos

    def parse(self, pos: int) -> tuple[Any, int]:
        d = self.data
        pos = self._skip_ws(pos)
        if pos >= len(d):
            raise ValueError("eof")
        c = d[pos:pos + 1]
        if d.startswith(b"<<", pos):
            pos += 2
            result: dict = {}
            while True:
                pos = self._skip_ws(pos)
                if d.startswith(b">>", pos):
                    pos += 2
                    break
                key, pos = self.parse(pos)
                val, pos = self.parse(pos)
                if isinstance(key, Name):
                    result[str(key)] = val
            after = self._skip_ws(pos)
            if d.startswith(b"stream", after):
                return self._stream(result, after + 6), -1
            return result, pos
        if c == b"[":
            pos += 1
            arr = []
            while True:
                pos = self._skip_ws(pos)
                if d.startswith(b"]", pos):
                    return arr, pos + 1
                val, pos = self.parse(pos)
                arr.append(val)
        if c == b"/":
            end = pos + 1
            while end < len(d) and d[end] not in _WS and d[end] not in _DELIM:
                end += 1
            raw = d[pos + 1:end]
            raw = re.sub(rb"#([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)
            return Name("/" + raw.decode("latin-1")), end
        if c == b"(":
            return self._literal(pos)
        if c == b"<":
            end = d.index(b">", pos)
            hexs = re.sub(rb"[^0-9a-fA-F]", b"", d[pos + 1:end])
            if len(hexs) % 2:
                hexs += b"0"
            return bytes.fromhex(hexs.decode()), end + 1
        m = _NUM.match(d, pos)
        if m:
            tok = m.group(0)
            end = m.end()
            if b"." not in tok:
                # "num gen R" reference?
                m2 = re.compile(rb"\s+(\d+)\s+R(?![A-Za-z])").match(d, end)
                if m2:
                    return Ref(int(tok), int(m2.group(1))), m2.end()
                return int(tok), end
            return float(tok), end
        end = pos
        while end < len(d) and d[end] not in _WS and d[end] not in _DELIM:
            end += 1
        word = d[pos:end]
        if end == pos:
            return None, pos + 1
        return {b"true": True, b"false": False, b"null": None}.get(word, word.decode("latin-1")), end

    def _literal(self, pos: int) -> tuple[bytes, int]:
        d = self.data
        out = bytearray()
        depth = 0
        pos += 1
        esc_map = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
        while pos < len(d):
            c = d[pos]
            if c == 0x5C:  # backslash
                pos += 1
                nc = d[pos]
                if nc in esc_map:
                    out.append(esc_map[nc])
                    pos += 1
                elif 0x30 <= nc <= 0x37:
                    m = re.compile(rb"[0-7]{1,3}").match(d, pos)
                    out.append(int(m.group(0), 8) & 0xFF)
                    pos = m.end()
                elif nc in b"\r\n":
                    pos += 2 if d[pos:pos + 2] == b"\r\n" else 1
                else:
                    out.append(nc)
                    pos += 1
                continue
            if c == 0x28:
                depth += 1
            elif c == 0x29:
                if depth == 0:
                    return bytes(out), pos + 1
                depth -= 1
            out.append(c)
            pos += 1
        return bytes(out), pos

    def _stream(self, d: dict, pos: int) -> Stream:
        data = self.data
        if data.startswith(b"\r\n", pos):
            pos += 2
        elif data[pos:pos + 1] in (b"\n", b"\r"):
            pos += 1
        length = d.get("/Length")
        if isinstance(length, Ref):
            try:
                length = self._get_direct(length.num)
            except Exception:
                length = None
        if isinstance(length, int) and 0 <= length and data.startswith(b"endstream", self._skip_ws(pos + length)):
            return Stream(d, data[pos:pos + length])
        end = data.find(b"endstream", pos)
        raw = data[pos:end if end >= 0 else len(data)]
        return Stream(d, raw.rstrip(b"\r\n"))

    # ------------------------------------------------------------ objects
    def _object_at(self, offset: int) -> Any:
        m = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj").match(self.data, offset)
        if not m:
            raise ValueError("no object")
        obj, _ = self.parse(m.end())
        return obj

    def _get_direct(self, num: int) -> Any:
        if num in self.cache:
            return self.cache[num]
        if num in self.in_stream:
            stm, idx = self.in_stream[num]
            objs = self._objstm(stm)
            obj = objs.get(num)
        elif num in self.offsets:
            obj = self._object_at(self.offsets[num])
        else:
            obj = None
        self.cache[num] = obj
        return obj

    def _objstm(self, stm_num: int) -> dict[int, Any]:
        if stm_num in self._objstm_cache:
            return self._objstm_cache[stm_num]
        self._objstm_cache[stm_num] = {}  # cycle guard
        stm = self._get_direct(stm_num)
        result: dict[int, Any] = {}
        if isinstance(stm, Stream):
            data = decode_stream(stm, self)
            n = self.resolve(stm.dict.get("/N")) or 0
            first = self.resolve(stm.dict.get("/First")) or 0
            nums = list(map(int, re.findall(rb"\d+", data[:first])))
            sub = PdfReader.__new__(PdfReader)
            sub.data = data
            for k in range(min(n, len(nums) // 2)):
                try:
                    obj, _ = sub.parse(first + nums[2 * k + 1])
                    result[nums[2 * k]] = obj
                except Exception:
                    pass
        self._objstm_cache[stm_num] = result
        return result

    def resolve(self, obj: Any, depth: int = 0) -> Any:
        while isinstance(obj, Ref) and depth < 32:
            obj = self._get_direct(obj.num)
            depth += 1
        return obj

    # ------------------------------------------------------------ cross-reference
    def _read_xref_chain(self) -> None:
        tail = self.data[-2048:]
        idx = tail.rfind(b"startxref")
        if idx < 0:
            raise ValueError("no startxref")
        offset = int(re.compile(rb"startxref\s+(\d+)").search(tail, idx).group(1))
        self.startxref = offset
        self.xref_is_stream = not self.data.startswith(b"xref", self._skip_ws(offset))
        seen = set()
        while offset is not None and offset not in seen and 0 <= offset < len(self.data):
            seen.add(offset)
            trailer = self._read_xref(offset)
            for k, v in trailer.items():
                self.trailer.setdefault(k, v)
            stm = trailer.get("/XRefStm")
            if isinstance(stm, int) and stm not in seen:
                seen.add(stm)
                try:
                    self._read_xref(stm)
                except Exception:
                    pass
            prev = trailer.get("/Prev")
            offset = prev if isinstance(prev, int) else None

    def _read_xref(self, offset: int) -> dict:
        pos = self._skip_ws(offset)
        d = self.data
        if d.startswith(b"xref", pos):
            pos += 4
            sub = re.compile(rb"\s*(\d+)\s+(\d+)")
            entry = re.compile(rb"\s*(\d{10})\s+(\d{5})\s+([nf])")
            while True:
                m = sub.match(d, pos)
                if not m:
                    break
                start, count = int(m.group(1)), int(m.group(2))
                pos = m.end()
                for k in range(count):
                    e = entry.match(d, pos)
                    if not e:
                        break
                    pos = e.end()
                    num = start + k
                    if num in self._defined:
                        continue
                    self._defined.add(num)
                    if e.group(3) == b"n":
                        self.offsets[num] = int(e.group(1))
            t = d.find(b"trailer", pos)
            trailer, _ = self.parse(t + 7)
            return trailer if isinstance(trailer, dict) else {}
        stm = self._object_at(offset)
        if not isinstance(stm, Stream):
            raise ValueError("bad xref")
        widths = [self.resolve(x) for x in stm.dict.get("/W", [1, 2, 1])]
        size = self.resolve(stm.dict.get("/Size", 0))
        index = stm.dict.get("/Index", [0, size])
        data = decode_stream(stm, self)
        rec = sum(widths)
        pos = 0
        for s in range(0, len(index) - 1, 2):
            start, count = index[s], index[s + 1]
            for k in range(count):
                if pos + rec > len(data):
                    break
                fields, p = [], pos
                for w in widths:
                    fields.append(int.from_bytes(data[p:p + w], "big") if w else None)
                    p += w
                pos += rec
                typ = fields[0] if widths[0] else 1
                num = start + k
                if num in self._defined:
                    continue
                self._defined.add(num)
                if typ == 1:
                    self.offsets[num] = fields[1]
                elif typ == 2:
                    self.in_stream[num] = (fields[1], fields[2] or 0)
        return stm.dict

    def _brute_force(self) -> None:
        for m in re.finditer(rb"(?<![\d])(\d+)\s+(\d+)\s+obj\b", self.data):
            self.offsets[int(m.group(1))] = m.start()
        self.cache.clear()
        for m in re.finditer(rb"trailer\s*<<", self.data):
            try:
                t, _ = self.parse(m.start() + 7)
                if isinstance(t, dict):
                    self.trailer.update(t)
            except Exception:
                pass
        # objects inside object streams
        for num in list(self.offsets):
            try:
                obj = self._get_direct(num)
            except Exception:
                continue
            if isinstance(obj, Stream) and obj.dict.get("/Type") == "/ObjStm":
                for inner in self._objstm(num):
                    self.in_stream.setdefault(inner, (num, 0))
        if not isinstance(self.resolve(self.trailer.get("/Root")), dict):
            for num in list(self.offsets) + list(self.in_stream):
                try:
                    obj = self._get_direct(num)
                except Exception:
                    continue
                if isinstance(obj, dict) and obj.get("/Type") == "/Catalog":
                    self.trailer["/Root"] = Ref(num, 0)
                    break

    # ------------------------------------------------------------ document structure
    def catalog(self) -> dict:
        root = self.resolve(self.trailer.get("/Root"))
        return root if isinstance(root, dict) else {}

    def pages(self) -> list[int]:
        """Object numbers of the pages in order."""
        result: list[int] = []
        seen: set[int] = set()

        def walk(node_ref, depth):
            if depth > 64 or len(result) > 100000:
                return
            if isinstance(node_ref, Ref):
                if node_ref.num in seen:
                    return
                seen.add(node_ref.num)
            node = self.resolve(node_ref)
            if not isinstance(node, dict):
                return
            kids = self.resolve(node.get("/Kids"))
            if node.get("/Type") == "/Pages" or isinstance(kids, list):
                for kid in kids or []:
                    walk(kid, depth + 1)
            elif isinstance(node_ref, Ref):
                result.append(node_ref.num)

        walk(self.catalog().get("/Pages"), 0)
        return result

    def named_dests(self) -> dict:
        dests: dict = {}
        cat = self.catalog()
        old = self.resolve(cat.get("/Dests"))
        if isinstance(old, dict):
            for k, v in old.items():
                dests[k.lstrip("/")] = v
        names = self.resolve(cat.get("/Names"))
        tree = self.resolve(names.get("/Dests")) if isinstance(names, dict) else None
        seen: set[int] = set()

        def walk(node, depth):
            if depth > 32 or not isinstance(node, dict):
                return
            arr = self.resolve(node.get("/Names"))
            if isinstance(arr, list):
                for k in range(0, len(arr) - 1, 2):
                    key = self.resolve(arr[k])
                    if isinstance(key, bytes):
                        key = pdf_text(key)
                    dests[str(key)] = arr[k + 1]
            for kid in self.resolve(node.get("/Kids")) or []:
                if isinstance(kid, Ref):
                    if kid.num in seen:
                        continue
                    seen.add(kid.num)
                walk(self.resolve(kid), depth + 1)

        walk(tree, 0)
        return dests

    def outline(self) -> list[TocItem]:
        if "/Encrypt" in self.trailer:
            return []   # titles are encrypted
        page_index = {num: i + 1 for i, num in enumerate(self.pages())}
        named = None
        seen: set[int] = set()

        def dest_page(dest, depth=0):
            """(page number, top y or None) of a destination."""
            nonlocal named
            dest = self.resolve(dest)
            if depth > 8 or dest is None:
                return None, None
            if isinstance(dest, dict):
                if "/D" in dest:
                    return dest_page(dest["/D"], depth + 1)
                return None, None
            if isinstance(dest, (bytes, Name, str)):
                if named is None:
                    named = self.named_dests()
                key = pdf_text(dest) if isinstance(dest, bytes) else str(dest).lstrip("/")
                return dest_page(named.get(key), depth + 1)
            if isinstance(dest, list) and dest:
                target = dest[0]
                top = None
                kind = dest[1] if len(dest) > 1 else None
                if kind == "/XYZ" and len(dest) > 3:
                    top = self.resolve(dest[3])
                elif kind in ("/FitH", "/FitBH") and len(dest) > 2:
                    top = self.resolve(dest[2])
                elif kind in ("/FitR",) and len(dest) > 5:
                    top = self.resolve(dest[5])
                top = float(top) if isinstance(top, (int, float)) else None
                if isinstance(target, Ref):
                    return page_index.get(target.num), top
                if isinstance(target, int):
                    return target + 1, top
            return None, None

        def items(first_ref, depth) -> list[TocItem]:
            result = []
            ref = first_ref
            while isinstance(ref, Ref) and ref.num not in seen and depth < 32 and len(seen) < 20000:
                seen.add(ref.num)
                node = self.resolve(ref)
                if not isinstance(node, dict):
                    break
                title = self.resolve(node.get("/Title"))
                title = pdf_text(title) if isinstance(title, bytes) else str(title or "")
                dest = node.get("/Dest")
                if dest is None:
                    action = self.resolve(node.get("/A"))
                    if isinstance(action, dict) and action.get("/S") == "/GoTo":
                        dest = action.get("/D")
                page, top = dest_page(dest)
                item = TocItem(title=" ".join(title.split()) or "…", page=page, top=top)
                item.children = items(node.get("/First"), depth + 1)
                result.append(item)
                ref = node.get("/Next")
            return result

        outlines = self.resolve(self.catalog().get("/Outlines"))
        if not isinstance(outlines, dict):
            return []
        return items(outlines.get("/First"), 0)


# ---------------------------------------------------------------------------

def decode_stream(stm: Stream, reader: Optional[PdfReader] = None) -> bytes:
    res = (lambda x: reader.resolve(x)) if reader else (lambda x: x)
    filters = res(stm.dict.get("/Filter"))
    params = res(stm.dict.get("/DecodeParms"))
    if not isinstance(filters, list):
        filters = [filters] if filters else []
    if not isinstance(params, list):
        params = [params] * len(filters)
    data = stm.raw
    for f, p in zip(filters, params):
        f = res(f)
        p = res(p) or {}
        if f in ("/FlateDecode", "/Fl"):
            try:
                data = zlib.decompress(data)
            except zlib.error:
                data = zlib.decompressobj().decompress(data)
            pred = res(p.get("/Predictor", 1)) if isinstance(p, dict) else 1
            if isinstance(pred, int) and pred >= 10:
                data = _png_unpredict(data, res(p.get("/Columns", 1)), res(p.get("/Colors", 1)),
                                      res(p.get("/BitsPerComponent", 8)))
        elif f in ("/ASCIIHexDecode", "/AHx"):
            hexs = re.sub(rb"[^0-9a-fA-F]", b"", data.split(b">")[0])
            data = bytes.fromhex((hexs + b"0" * (len(hexs) % 2)).decode())
        elif f in ("/ASCII85Decode", "/A85"):
            body = data.strip()
            if body.startswith(b"<~"):
                body = body[2:]
            data = base64.a85decode(body.split(b"~>")[0], adobe=False, ignorechars=b" \t\n\r\x0b\x0c")
        else:
            break  # unsupported filter: leave as is
    return data


def _png_unpredict(data: bytes, columns: int, colors: int, bpc: int) -> bytes:
    bpp = max(1, colors * bpc // 8)
    row_len = (columns * colors * bpc + 7) // 8
    out = bytearray()
    prev = bytearray(row_len)
    pos = 0
    while pos + row_len + 1 <= len(data):
        ft = data[pos]
        row = bytearray(data[pos + 1:pos + 1 + row_len])
        pos += row_len + 1
        for i in range(row_len):
            left = row[i - bpp] if i >= bpp else 0
            up = prev[i]
            ul = prev[i - bpp] if i >= bpp else 0
            if ft == 1:
                row[i] = (row[i] + left) & 0xFF
            elif ft == 2:
                row[i] = (row[i] + up) & 0xFF
            elif ft == 3:
                row[i] = (row[i] + ((left + up) >> 1)) & 0xFF
            elif ft == 4:
                pa, pb, pc = abs(up - ul), abs(left - ul), abs(left + up - 2 * ul)
                pr = left if pa <= pb and pa <= pc else (up if pb <= pc else ul)
                row[i] = (row[i] + pr) & 0xFF
        out += row
        prev = row
    return bytes(out)


_PDFDOC = {0x80: "•", 0x81: "†", 0x82: "‡", 0x83: "…", 0x84: "—", 0x85: "–", 0x86: "ƒ", 0x87: "⁄",
           0x88: "‹", 0x89: "›", 0x8A: "−", 0x8B: "‰", 0x8C: "„", 0x8D: "“", 0x8E: "”", 0x8F: "‘",
           0x90: "’", 0x91: "‚", 0x92: "™", 0x93: "ﬁ", 0x94: "ﬂ", 0x95: "Ł", 0x96: "Œ", 0x97: "Š",
           0x98: "Ÿ", 0x99: "Ž", 0x9A: "ı", 0x9B: "ł", 0x9C: "œ", 0x9D: "š", 0x9E: "ž", 0xA0: "€"}


def pdf_text(b: bytes) -> str:
    if b.startswith(b"\xfe\xff"):
        return b[2:].decode("utf-16-be", errors="replace")
    if b.startswith(b"\xff\xfe"):
        return b[2:].decode("utf-16-le", errors="replace")
    if b.startswith(b"\xef\xbb\xbf"):
        return b[3:].decode("utf-8", errors="replace")
    return "".join(_PDFDOC.get(c, chr(c)) for c in b)


def read_structure(path: str) -> tuple[list[TocItem], int]:
    """(outline, page count). Never raises: a broken file just gives an empty outline."""
    try:
        with open(path, "rb") as f:
            reader = PdfReader(f.read())
        pages = len(reader.pages())
        try:
            toc = reader.outline()
        except Exception:
            toc = []
        return toc, pages
    except Exception:
        return [], 0
