"""Small Markdown → HTML converter (CommonMark/GFM subset, standard library only).

Supports: ATX/setext headings, paragraphs, emphasis, strong, strikethrough, inline code, fenced and
indented code, block quotes, nested ordered/unordered lists, task list boxes, GFM tables, links,
images, autolinks, hard line breaks, horizontal rules, raw HTML blocks and YAML front matter.
"""
from __future__ import annotations

import re
from .common import esc

_HR = re.compile(r"^ {0,3}([-*_])( *\1){2,} *$")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})\s*([^`\s]*)")
_LIST = re.compile(r"^( {0,3})([-+*]|\d{1,9}[.)])([ \t]+|$)(.*)$")
_TABLE_SEP = re.compile(r"^ *\|? *:?-{1,}:? *(\| *:?-{1,}:? *)*\|? *$")
_HTML_BLOCK = re.compile(r"^ {0,3}<(/?[a-zA-Z][a-zA-Z0-9-]*|!--)")


def to_html(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    lines = text.split("\n")
    # YAML front matter
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 200)):
            if lines[i].strip() in ("---", "..."):
                lines = lines[i + 1:]
                break
    return _blocks(lines)


# ---------------------------------------------------------------- blocks

def _blocks(lines: list[str]) -> str:
    out: list[str] = []
    i = 0
    n = len(lines)
    para: list[str] = []

    def flush():
        if para:
            out.append("<p>" + _inline("\n".join(para).strip()) + "</p>")
            para.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush()
            i += 1
            continue

        m = _FENCE.match(line)
        if m:
            flush()
            fence = m.group(2)
            lang = m.group(3)
            code = []
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                code.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{esc(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{esc(chr(10).join(code))}</code></pre>")
            continue

        m = _ATX.match(line)
        if m:
            flush()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2) or '')}</h{level}>")
            i += 1
            continue

        # setext heading (underline on the next line) – only for a single-line paragraph
        if para and len(para) == 1 and re.match(r"^ {0,3}(=+|-+) *$", line):
            level = 1 if stripped.startswith("=") else 2
            out.append(f"<h{level}>{_inline(para[0].strip())}</h{level}>")
            para.clear()
            i += 1
            continue

        if _HR.match(line):
            flush()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith(">"):
            flush()
            quote = []
            while i < n and lines[i].strip() and (lines[i].lstrip().startswith(">") or quote):
                q = lines[i].lstrip()
                if q.startswith(">"):
                    q = q[1:]
                    if q.startswith(" "):
                        q = q[1:]
                quote.append(q)
                i += 1
            out.append("<blockquote>" + _blocks(quote) + "</blockquote>")
            continue

        if not para and line.startswith("    "):
            code = []
            while i < n and (lines[i].startswith("    ") or not lines[i].strip()):
                code.append(lines[i][4:])
                i += 1
            while code and not code[-1].strip():
                code.pop()
            out.append(f"<pre><code>{esc(chr(10).join(code))}</code></pre>")
            continue

        if _LIST.match(line) and (not para or not _LIST.match(line).group(2)[0].isdigit()
                                  or _LIST.match(line).group(2) in ("1.", "1)")):
            flush()
            html_list, i = _list(lines, i)
            out.append(html_list)
            continue

        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]) and not para:
            html_table, i = _table(lines, i)
            out.append(html_table)
            continue

        if not para and _HTML_BLOCK.match(line):
            block = []
            while i < n and lines[i].strip():
                block.append(lines[i])
                i += 1
            out.append("\n".join(block))
            continue

        para.append(line)
        i += 1

    flush()
    return "".join(out)


def _list(lines: list[str], i: int) -> tuple[str, int]:
    first = _LIST.match(lines[i])
    assert first
    indent = len(first.group(1))
    ordered = first.group(2)[0].isdigit()
    tag = "ol" if ordered else "ul"
    start = ""
    if ordered:
        num = int(first.group(2)[:-1])
        if num != 1:
            start = f' start="{num}"'
    items: list[list[str]] = []
    loose = False
    n = len(lines)
    blank_before = False

    while i < n:
        line = lines[i]
        m = _LIST.match(line)
        if m and len(m.group(1)) == indent and m.group(2)[0].isdigit() == ordered:
            if blank_before and items:
                loose = True
            content_indent = len(m.group(1)) + len(m.group(2)) + max(1, len(m.group(3)))
            items.append([m.group(4)])
            cur_indent = content_indent
            blank_before = False
            i += 1
            continue
        if not line.strip():
            blank_before = True
            i += 1
            continue
        lead = len(line) - len(line.lstrip(" "))
        if items and (lead >= indent + 2 or (not blank_before and not m and not _starts_block(line))):
            items[-1].append(line[min(lead, cur_indent):] if lead >= indent + 2 else line.strip())
            if blank_before:
                items[-1].insert(len(items[-1]) - 1, "")
                loose = True
            blank_before = False
            i += 1
            continue
        break

    parts = []
    for item in items:
        body = _blocks(item)
        if not loose and body.startswith("<p>"):
            # tight list: unwrap the first paragraph
            end = body.find("</p>")
            body = body[3:end] + body[end + 4:]
        body = re.sub(r"^\[( |x|X)\] ",
                      lambda mm: '<input type="checkbox" disabled' + (" checked" if mm.group(1) != " " else "") + "> ",
                      body)
        parts.append(f"<li>{body}</li>")
    return f"<{tag}{start}>{''.join(parts)}</{tag}>", i


def _starts_block(line: str) -> bool:
    return bool(_ATX.match(line) or _FENCE.match(line) or _HR.match(line) or line.lstrip().startswith(">"))


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, k = [], "", 0
    while k < len(s):
        if s[k] == "\\" and k + 1 < len(s) and s[k + 1] == "|":
            cur += "|"
            k += 2
            continue
        if s[k] == "|":
            cells.append(cur.strip())
            cur = ""
        else:
            cur += s[k]
        k += 1
    cells.append(cur.strip())
    return cells


def _table(lines: list[str], i: int) -> tuple[str, int]:
    head = _split_row(lines[i])
    aligns = []
    for c in _split_row(lines[i + 1]):
        c = c.strip()
        aligns.append("center" if c.startswith(":") and c.endswith(":") else
                      "right" if c.endswith(":") else "left" if c.startswith(":") else "")
    i += 2

    def cell(tag, text, k):
        a = aligns[k] if k < len(aligns) and aligns[k] else ""
        style = f' style="text-align:{a}"' if a else ""
        return f"<{tag}{style}>{_inline(text)}</{tag}>"

    rows = ["<tr>" + "".join(cell("th", c, k) for k, c in enumerate(head)) + "</tr>"]
    while i < len(lines) and lines[i].strip() and "|" in lines[i]:
        cells = _split_row(lines[i])
        rows.append("<tr>" + "".join(cell("td", c, k) for k, c in enumerate(cells)) + "</tr>")
        i += 1
    return "<table>" + "".join(rows) + "</table>", i


# ---------------------------------------------------------------- inline

_CODE = re.compile(r"(`+)(.+?)\1", re.S)
_IMG = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"([^\"]*)\")?\s*\)")
_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*<?([^)\s>]*)>?(?:\s+\"([^\"]*)\")?\s*\)")
_AUTO = re.compile(r"<((?:https?|mailto|ftp):[^>\s]+)>")
_BARE = re.compile(r"(?<![\"'=>\w])(https?://[^\s<]+[^\s<.,;:!?)\]'\"])")
_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.S)
_EM = re.compile(r"(?<![\w*])(\*|_)(?=\S)(.+?)(?<=\S)\1(?![\w*])", re.S)
_DEL = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S)
_INLINE_HTML = re.compile(r"</?[a-zA-Z][^>]*>|<!--.*?-->", re.S)


def _inline(text: str) -> str:
    stash: list[str] = []

    def keep(s: str) -> str:
        stash.append(s)
        return f"\x00{len(stash) - 1}\x00"

    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|~<>])", lambda m: keep(esc(m.group(1))), text)
    text = _CODE.sub(lambda m: keep(f"<code>{esc(m.group(2).strip())}</code>"), text)
    text = _IMG.sub(lambda m: keep(
        f'<img src="{esc(m.group(2))}" alt="{esc(m.group(1))}"'
        + (f' title="{esc(m.group(3))}"' if m.group(3) else "") + ">"), text)
    text = _LINK.sub(lambda m: keep(
        f'<a href="{esc(m.group(2))}"' + (f' title="{esc(m.group(3))}"' if m.group(3) else "") + ">")
        + m.group(1) + keep("</a>"), text)
    text = _AUTO.sub(lambda m: keep(f'<a href="{esc(m.group(1))}">{esc(m.group(1))}</a>'), text)
    text = _INLINE_HTML.sub(lambda m: keep(m.group(0)), text)
    text = _BARE.sub(lambda m: keep(f'<a href="{esc(m.group(1))}">{esc(m.group(1))}</a>'), text)

    text = esc(text).replace("&quot;", '"').replace("&#x27;", "'")
    text = _STRONG.sub(r"<strong>\2</strong>", text)
    text = _EM.sub(r"<em>\2</em>", text)
    text = _DEL.sub(r"<del>\1</del>", text)
    text = re.sub(r"( {2,}|\\)\n", "<br>", text)

    for _ in range(3):  # stashed items can contain other stashed items (link text)
        text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
    return text
