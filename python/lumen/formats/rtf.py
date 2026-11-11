"""RTF → HTML (standard library only).

Handles code pages (\\ansicpg, per-font \\fcharset), \\uN Unicode escapes, bold/italic/underline/strike,
super/subscript, alignment, headings (\\outlinelevel or large font size), simple tables, line breaks,
hyperlinks (HYPERLINK fields) and PNG/JPEG pictures.
"""
from __future__ import annotations

import base64
import re
from typing import Optional

from .common import esc

_TOKEN = re.compile(
    rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|(\r\n|\r|\n)|([^\\{}\r\n]+)",
    re.S,
)

_CHARSET_CP = {0: "cp1252", 1: None, 2: "symbol", 77: "mac_roman", 128: "cp932", 129: "cp949", 134: "cp936",
               136: "cp950", 161: "cp1253", 162: "cp1254", 163: "cp1258", 177: "cp1255", 178: "cp1256",
               186: "cp1257", 204: "cp1251", 222: "cp874", 238: "cp1250", 255: "cp437"}

# Destinations whose text is never shown
_SKIP = {
    "fonttbl", "colortbl", "info", "listtable", "listoverridetable", "rsidtbl", "generator",
    "header", "headerl", "headerr", "headerf", "footer", "footerl", "footerr", "footerf", "footnote",
    "themedata", "colorschememapping", "latentstyles", "datastore", "xmlnstbl", "mmathPr", "object",
    "shp", "shpinst", "nonshppict", "pgdsctbl", "revtbl", "filetbl", "operator", "author", "title",
    "company", "comment", "doccomm", "keywords", "subject", "template", "pntxta", "pntxtb", "bkmkend",
    "wgrffmtfilter", "xmlopen", "fldinst", "annotation", "atnid", "atnauthor", "protusertbl", "pn",
}


class _State:
    __slots__ = ("bold", "italic", "underline", "strike", "valign", "fs", "skip", "uc", "cp",
                 "dest", "font")

    def __init__(self):
        self.bold = self.italic = self.underline = self.strike = False
        self.valign = ""
        self.fs = 24
        self.skip = False
        self.uc = 1
        self.cp = "cp1252"
        self.dest = ""
        self.font = -1

    def copy(self) -> "_State":
        s = _State()
        for k in self.__slots__:
            setattr(s, k, getattr(self, k))
        return s


def convert(path: str) -> str:
    with open(path, "rb") as f:
        return to_html(f.read())


def to_html(data: bytes) -> str:
    return _Parser(data).run()


class _Parser:
    def __init__(self, data: bytes):
        self.data = data
        self.state = _State()
        self.stack: list[_State] = []
        self.fonts: dict[int, str] = {}
        self.default_cp = "cp1252"
        self.default_font = 0
        self.out: list[str] = []
        self.runs: list[tuple[str, tuple]] = []   # current paragraph (text, format)
        self.max_fs = 0
        self.para_align = ""
        self.outline = -1
        self.in_table = False
        self.row: list[str] = []
        self.table_rows: list[list[str]] = []
        self.pending_bytes = bytearray()
        self.skip_chars = 0
        # font table parsing
        self.font_num = -1
        self.font_charset: Optional[int] = None
        # pictures and fields
        self.pict_hex: list[str] = []
        self.pict_kind = ""
        self.pict_depth = -1
        self.fld_href: Optional[str] = None
        self.fldinst: list[str] = []
        self.fldinst_depth = -1
        self.fldrslt_depth = -1
        # styles and lists
        self.styles: dict[int, tuple[str, int]] = {}   # \sN -> (name, outline level)
        self.ss_num = -1
        self.ss_outline = -1
        self.ss_name: list[str] = []
        self.para_style = 0
        self.list_ls = -1
        self.list_lvl = 0
        self.listtext: list[str] = []
        self.lists: list[str] = []

    # ------------------------------------------------------------ main loop
    def run(self) -> str:
        for m in _TOKEN.finditer(self.data):
            word, param, hexb, sym, brace, nl, text = m.groups()
            if text is not None:
                self.text_bytes(text)
            elif word is not None:
                self.flush_bytes()
                self.control(word.decode("ascii"), int(param) if param is not None else None)
            elif hexb is not None:
                if self.skip_chars > 0:
                    self.skip_chars -= 1
                    continue
                self.pending_bytes.append(int(hexb, 16))
            elif brace is not None:
                self.flush_bytes()
                if brace == b"{":
                    self.stack.append(self.state.copy())
                else:
                    self.end_group()
            elif sym is not None:
                self.flush_bytes()
                self.symbol(sym.decode("latin-1"))
            # bare newlines are ignored in RTF
        self.flush_bytes()
        self.end_paragraph()
        self.close_table()
        self.close_lists(0)
        return "".join(self.out)

    def end_group(self):
        depth = len(self.stack)
        if self.pict_depth == depth:
            self.emit_picture()
            self.pict_depth = -1
        if self.fldinst_depth == depth:
            m = re.search(r'HYPERLINK\s+(?:\\l\s+)?"([^"]+)"', "".join(self.fldinst))
            self.fld_href = m.group(1) if m else None
            self.fldinst_depth = -1
        if self.fldrslt_depth == depth:
            self.fld_href = None
            self.fldrslt_depth = -1
        if self.state.dest == "stylesheet" and self.ss_num >= 0:
            name = "".join(self.ss_name).strip().rstrip(";").strip().lower()
            self.styles[self.ss_num] = (name, self.ss_outline)
            self.ss_num = -1
        if self.state.dest in ("fonttbl_entry", "fonttbl") and self.font_num >= 0:
            self.register_font()
        if self.stack:
            self.state = self.stack.pop()

    # ------------------------------------------------------------ text
    def text_bytes(self, raw: bytes):
        if self.skip_chars > 0:
            n = min(self.skip_chars, len(raw))
            raw = raw[n:]
            self.skip_chars -= n
        if raw:
            self.pending_bytes.extend(raw)

    def flush_bytes(self):
        if not self.pending_bytes:
            return
        raw = bytes(self.pending_bytes)
        self.pending_bytes.clear()
        cp = self.state.cp or self.default_cp
        try:
            s = raw.decode(cp, errors="replace") if cp != "symbol" else raw.decode("latin-1")
        except LookupError:
            s = raw.decode("cp1252", errors="replace")
        self.text(s)

    def text(self, s: str):
        st = self.state
        if st.dest == "fonttbl_entry":
            return
        if st.dest == "fldinst":
            self.fldinst.append(s)
            return
        if st.dest == "pict":
            self.pict_hex.append(s)
            return
        if st.dest == "stylesheet":
            self.ss_name.append(s)
            return
        if st.dest == "listtext":
            self.listtext.append(s)
            return
        if st.skip or not s:
            return
        fmt = (st.bold, st.italic, st.underline, st.strike, st.valign, self.fld_href)
        self.runs.append((s, fmt))
        if s.strip():
            self.max_fs = max(self.max_fs, st.fs)

    # ------------------------------------------------------------ control words
    def control(self, word: str, param: Optional[int]):
        st = self.state
        if self.skip_chars > 0 and word not in ("par", "line", "tab", "cell", "row"):
            self.skip_chars -= 1
            return
        on = param is None or param != 0

        if word == "shppict":          # modern picture container: keep it (its twin nonshppict is skipped)
            st.dest = ""
            return

        if word == "stylesheet":
            st.dest = "stylesheet"
            return
        if word in ("listtext", "pntext"):
            st.dest = "listtext"
            self.listtext = []
            return
        if st.dest == "stylesheet":
            if word == "s":
                self.ss_num, self.ss_outline, self.ss_name = param or 0, -1, []
            elif word in ("cs", "ds", "ts", "tsrowd"):
                self.ss_num = -1
            elif word == "outlinelevel" and param is not None:
                self.ss_outline = param
            return

        if word in _SKIP or (st.dest == "ignorable"):
            if word == "fonttbl":
                st.dest = "fonttbl"
                st.skip = True
            elif word == "fldinst":
                st.dest = "fldinst"
                self.fldinst = []
                self.fldinst_depth = len(self.stack)
            else:
                st.skip = True
                if st.dest == "ignorable":
                    st.dest = "skip"
            return

        if st.dest == "fonttbl" or st.dest == "fonttbl_entry":
            if word == "f":
                if self.font_num >= 0:
                    self.register_font()
                st.dest = "fonttbl_entry"
                self.font_num = param or 0
                self.font_charset = None
            elif word == "fcharset":
                self.font_charset = param
            return

        if word == "ansicpg" and param:
            self.default_cp = f"cp{param}"
            st.cp = self.default_cp
        elif word == "deff" and param is not None:
            self.default_font = param
        elif word == "f" and param is not None:
            st.font = param
            st.cp = self.fonts.get(param, self.default_cp)
        elif word == "uc" and param is not None:
            st.uc = param
        elif word == "u" and param is not None:
            self.text(chr(param + 65536 if param < 0 else param))
            self.skip_chars = st.uc
        elif word == "par":
            self.end_paragraph()
        elif word == "line":
            self.runs.append(("\n", (st.bold, st.italic, st.underline, st.strike, st.valign, self.fld_href)))
        elif word == "tab":
            self.text("\t")
        elif word == "pard":
            self.para_align = ""
            self.outline = -1
            self.in_table = False
            self.para_style = 0
            self.list_ls = -1
            self.list_lvl = 0
        elif word == "s" and param is not None:
            self.para_style = param
        elif word == "ls" and param is not None:
            self.list_ls = param
        elif word == "ilvl" and param is not None:
            self.list_lvl = max(0, min(param, 8))
        elif word == "plain":
            st.bold = st.italic = st.underline = st.strike = False
            st.valign = ""
            st.fs = 24
        elif word == "b":
            st.bold = on
        elif word == "i":
            st.italic = on
        elif word in ("ul", "uld", "uldb", "ulw", "uldash", "ulth", "ulwave"):
            st.underline = on
        elif word == "ulnone":
            st.underline = False
        elif word in ("strike", "striked"):
            st.strike = on
        elif word == "super":
            st.valign = "sup"
        elif word == "sub":
            st.valign = "sub"
        elif word == "nosupersub":
            st.valign = ""
        elif word == "fs" and param:
            st.fs = param
        elif word == "qc":
            self.para_align = "center"
        elif word == "qr":
            self.para_align = "right"
        elif word == "qj":
            self.para_align = "justify"
        elif word == "ql":
            self.para_align = ""
        elif word == "outlinelevel" and param is not None:
            self.outline = param
        elif word == "intbl":
            self.in_table = True
        elif word == "cell":
            self.end_cell()
        elif word == "row":
            self.end_row()
        elif word == "pict" and not st.skip:
            st.dest = "pict"
            self.pict_hex = []
            self.pict_kind = ""
            self.pict_depth = len(self.stack)
        elif word in ("pngblip", "jpegblip") and st.dest == "pict":
            self.pict_kind = "png" if word == "pngblip" else "jpeg"
        elif word == "fldrslt":
            self.fldrslt_depth = len(self.stack)
        elif word in ("emdash", "endash", "bullet", "lquote", "rquote", "ldblquote", "rdblquote", "emspace",
                      "enspace"):
            self.text({"emdash": "—", "endash": "–", "bullet": "•", "lquote": "‘", "rquote": "’",
                       "ldblquote": "“", "rdblquote": "”", "emspace": "\u2003", "enspace": "\u2002"}[word])
        elif word == "sect" or word == "page":
            self.end_paragraph()

    def symbol(self, ch: str):
        if self.skip_chars > 0:
            self.skip_chars -= 1
            return
        if ch == "*":
            self.state.dest = "ignorable"
        elif ch in "\\{}":
            self.text(ch)
        elif ch == "~":
            self.text("\u00a0")
        elif ch == "_":
            self.text("\u2011")
        elif ch in "\r\n":
            self.end_paragraph()

    def register_font(self):
        cp = _CHARSET_CP.get(self.font_charset if self.font_charset is not None else 0, None)
        if self.font_charset in (None, 1):
            cp = self.default_cp
        self.fonts[self.font_num] = cp or self.default_cp
        self.font_num = -1

    # ------------------------------------------------------------ output
    def paragraph_html(self) -> tuple[str, str]:
        parts: list[str] = []
        plain: list[str] = []
        for s, (b, i, u, k, va, href) in self.runs:
            plain.append(s)
            h = esc(s).replace("\t", "&emsp;").replace("\n", "<br>")
            if b:
                h = f"<strong>{h}</strong>"
            if i:
                h = f"<em>{h}</em>"
            if u:
                h = f"<u>{h}</u>"
            if k:
                h = f"<s>{h}</s>"
            if va:
                h = f"<{va}>{h}</{va}>"
            if href:
                h = f'<a href="{esc(href)}">{h}</a>'
            parts.append(h)
        html_text = re.sub(r"</(strong|em|u|s|sup|sub)><\1>", "", "".join(parts))
        html_text = re.sub(r'</a><a href="[^"]*">', "", html_text)
        return html_text, "".join(plain).strip()

    def end_paragraph(self):
        if self.in_table:
            # paragraph inside a cell: keep content, separated by a line break
            inner, _ = self.paragraph_html()
            if inner:
                self.row.append(("<br>" if self.row and not self.row[-1].endswith("</td>") else "") + inner)
            self.runs.clear()
            return
        self.close_table()
        inner, text = self.paragraph_html()
        self.runs.clear()
        fs = self.max_fs
        self.max_fs = 0
        style = f' style="text-align:{self.para_align}"' if self.para_align else ""
        listtext = "".join(self.listtext)
        self.listtext = []
        if self.list_ls >= 0 and (text or "<img" in inner):
            ordered = re.search(r"[0-9A-Za-z][.)]", listtext) is not None
            self.open_list(self.list_lvl + 1, "ol" if ordered else "ul")
            self.out.append(f"<li>{inner.lstrip()}</li>")
            return
        self.close_lists(0)
        if not text and "<img" not in inner:
            self.out.append('<p class="lumen-empty"></p>')
            return
        level = 0
        sname, soutline = self.styles.get(self.para_style, ("", -1))
        m = re.match(r"^heading (\d)$", sname)
        if 0 <= self.outline <= 5:
            level = self.outline + 1
        elif 0 <= soutline <= 5:
            level = soutline + 1
        elif m:
            level = min(max(int(m.group(1)), 1), 6)
        elif sname == "title":
            level = 1
        elif len(text) <= 160:
            if fs >= 40:
                level = 1
            elif fs >= 32:
                level = 2
            elif fs >= 28 and "<strong>" in inner:
                level = 3
        if level:
            self.out.append(f"<h{level}{style}>{inner}</h{level}>")
        else:
            self.out.append(f"<p{style}>{inner}</p>")

    def open_list(self, depth: int, tag: str):
        self.close_lists(depth)
        if len(self.lists) == depth and depth > 0 and self.lists[-1] != tag:
            self.close_lists(depth - 1)
        while len(self.lists) < depth:
            self.out.append(f"<{tag}>")
            self.lists.append(tag)

    def close_lists(self, depth: int):
        while len(self.lists) > depth:
            self.out.append(f"</{self.lists.pop()}>")

    def end_cell(self):
        inner, _ = self.paragraph_html()
        self.runs.clear()
        self.max_fs = 0
        cell = "".join(c for c in self.row if not c.endswith("</td>")) + inner
        self.row = [c for c in self.row if c.endswith("</td>")]
        self.row.append(f"<td>{cell}</td>")

    def end_row(self):
        cells = [c for c in self.row if c.endswith("</td>")]
        self.row = []
        self.runs.clear()
        if cells:
            self.table_rows.append(cells)
        self.in_table = False

    def close_table(self):
        if self.table_rows:
            self.close_lists(0)
            self.out.append("<table>" + "".join("<tr>" + "".join(r) + "</tr>" for r in self.table_rows) + "</table>")
            self.table_rows = []

    def emit_picture(self):
        if self.pict_kind not in ("png", "jpeg"):
            return
        hex_data = re.sub(r"[^0-9a-fA-F]", "", "".join(self.pict_hex))
        try:
            data = bytes.fromhex(hex_data)
        except ValueError:
            return
        self.runs.append(("", (False, False, False, False, "", None)))
        img = f'<img src="data:image/{self.pict_kind};base64,{base64.b64encode(data).decode()}" alt="">'
        # pictures are inserted as raw HTML into the paragraph
        self.runs.append((_RawHtml(img), (False, False, False, False, "", None)))


class _RawHtml(str):
    """Marker type: already-HTML content inside a run."""


_orig_esc = esc


def esc(s):  # noqa: F811 – keep raw HTML runs (pictures) unescaped
    return s if isinstance(s, _RawHtml) else _orig_esc(s)
