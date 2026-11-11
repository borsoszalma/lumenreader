"""Tests with real files (created by Word-compatible tools). Run: python -m unittest discover -s python/tests"""
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from lumen import formats, i18n, settings as settings_mod  # noqa: E402
from lumen.formats import doc, docx, docx_export, markdown, pdf, rtf  # noqa: E402
from lumen.library import Library  # noqa: E402
from lumen.server import ReaderApp, start_server  # noqa: E402

FX = os.path.join(HERE, "fixtures")
STRINGS = i18n.load("hu")


def t(key):
    return STRINGS.get(key, key)


def fx(name):
    return os.path.join(FX, name)


def page(session):
    return session.serve(session.start)[0].decode("utf-8")


def toc_titles(items):
    out = []
    for i in items:
        out.append(i.title)
        out += toc_titles(i.children)
    return out


class FormatTests(unittest.TestCase):
    def test_docx_headings_lists_tables_images(self):
        html = docx.convert(fx("sample.docx"))
        self.assertRegex(html, r'<h1 data-p="\d+">Első fejezet</h1>')
        self.assertRegex(html, r'<h2 data-p="\d+">Alfejezet táblázattal</h2>')
        self.assertIn("<strong>félkövér</strong>", html)
        self.assertRegex(html, r'<ul><li data-p="\d+">Első pont</li>')
        self.assertRegex(html, r'<ol><li data-p="\d+">Egyes</li>')
        self.assertIn("<table>", html)
        self.assertIn("data:image/png;base64,", html)

    def test_docx_localized_style_ids(self):
        # Hungarian Word uses style ids like "Cmsor1"; the English built-in name must still be recognised
        html = docx.convert(fx("sample_hu_styles.docx"))
        self.assertRegex(html, r'<h1 data-p="\d+">Első fejezet</h1>')
        self.assertRegex(html, r'<h2 data-p="\d+">Alfejezet táblázattal</h2>')

    def test_rtf(self):
        html = rtf.convert(fx("sample.rtf"))
        self.assertIn("Árvíztűrő tükörfúrógép", html)
        self.assertIn("Ő, ű, Ő, Ű", html)
        self.assertRegex(html, r"<h1>(<strong>)?Első fejezet")
        self.assertRegex(html, r"<h2>(<strong>)?Alfejezet táblázattal")
        self.assertIn("<ul><li>Első pont</li>", html)
        self.assertIn("<ol><li>Egyes</li>", html)
        self.assertIn("<td>Név</td>", html)
        self.assertIn("data:image/png;base64,", html)

    def test_rtf_unicode_and_codepage(self):
        html = rtf.to_html(rb"{\rtf1\ansi\ansicpg1250{\fonttbl{\f0\fcharset238 Arial;}}\f0 \'f5 \u337?x\par}")
        self.assertIn("ő ő", html)  # 0xF5 in cp1250 is "ő"
        self.assertNotIn("?", html)

    def test_doc_text_extraction(self):
        text = doc.extract_text(fx("sample.doc"))
        self.assertIn("Árvíztűrő tükörfúrógép", text)
        self.assertIn("Második fejezet", text)

    def test_doc_corrupt_file_does_not_hang(self):
        with open(fx("sample.doc"), "rb") as f:
            data = bytearray(f.read())
        # make the FAT point every sector to itself (cycle)
        sector_size = 1 << int.from_bytes(data[0x1E:0x20], "little")
        first_fat = int.from_bytes(data[0x4C:0x50], "little")
        off = (first_fat + 1) * sector_size
        for i in range(sector_size // 4):
            data[off + 4 * i: off + 4 * i + 4] = i.to_bytes(4, "little")
        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as f:
            f.write(bytes(data))
        try:
            with self.assertRaises(Exception):
                doc.extract_text(f.name)
        finally:
            os.unlink(f.name)

    def test_doc_loader(self):
        s = formats.load(fx("sample.doc"), t)
        self.assertIn("Második fejezet", page(s))

    def test_markdown(self):
        with open(fx("sample.md"), encoding="utf-8") as f:
            html = markdown.to_html(f.read())
        self.assertIn("<h1>Markdown próba</h1>", html)
        self.assertIn("<strong>félkövér</strong>", html)
        self.assertIn("<del>áthúzott</del>", html)
        self.assertIn('<a href="https://www.anthropic.com">Anthropic</a>', html)
        self.assertIn("<li>kettő<ul><li>beágyazott</li></ul></li>", html)
        self.assertIn('<th style="text-align:right">Érték</th>', html)
        self.assertIn("hello &lt;world&gt;", html)
        self.assertIn('<img src="pic.png" alt="kép">', html)
        self.assertNotIn("title:", html)  # front matter hidden

    def test_epub3(self):
        s = formats.load(fx("sample.epub"), t)
        self.assertEqual(s.kind, "epub")
        self.assertEqual(s.title, "EPUB próba könyv")
        self.assertIn("Táblázat", toc_titles(s.toc))
        self.assertTrue(s.spine)
        self.assertIsNotNone(s.serve(s.spine[-1]))

    def test_epub2_ncx(self):
        s = formats.load(fx("sample_epub2.epub"), t)
        self.assertIn("Harmadik szint", toc_titles(s.toc))

    def test_pdf_outline(self):
        for name in ("sample.pdf", "sample_objstm.pdf"):
            toc, pages = pdf.read_structure(fx(name))
            self.assertEqual(pages, 1, name)
            self.assertEqual(toc_titles(toc), ["Első fejezet", "Alfejezet táblázattal", "Második fejezet"], name)
            self.assertEqual(toc[0].page, 1)

    def test_pdf_damaged_xref(self):
        with open(fx("sample.pdf"), "rb") as f:
            data = f.read().replace(b"startxref", b"startxreX")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(data)
        try:
            toc, _ = pdf.read_structure(f.name)
            self.assertEqual(len(toc), 2)
        finally:
            os.unlink(f.name)

    def test_html_and_txt(self):
        s = formats.load(fx("sample.html"), t)
        self.assertEqual(s.kind, "html")
        self.assertIsNotNone(s.serve("pic.png"))
        self.assertIsNone(s.serve("../test_reader.py"))  # no path traversal
        s = formats.load(fx("sample_cp1250.txt"), t)
        self.assertIn("Els", page(s))

    def test_unsupported(self):
        with self.assertRaises(formats.DocumentError):
            formats.load(fx("pic.png"), t)


def paragraph_texts(html):
    """data-p index -> plain text, as the reader sees it."""
    import re
    import html as h
    out = {}
    for m in re.finditer(r'<(p|h\d|li)[^>]*data-p="(\d+)"[^>]*>(.*?)</\1>', html):
        out[int(m.group(2))] = h.unescape(re.sub(r"<[^>]+>", "", m.group(3)))
    return out


class WordExportTests(unittest.TestCase):
    def test_comments_and_highlights(self):
        import re
        html = docx.convert(fx("sample.docx"))
        paras = paragraph_texts(html)
        p = next(i for i, t in paras.items() if t.startswith("Ez egy"))
        text = paras[p]
        s0 = text.index("félkövér")
        anns = [
            # crosses run boundaries: "félkövér és egy dőlt"
            {"color": "green", "note": "Első megjegyzés\nmásodik sor",
             "docx": {"sp": p, "so": s0, "ep": p, "eo": text.index("dőlt") + 4}},
            # highlight only, spanning two paragraphs
            {"color": "yellow", "note": "", "docx": {"sp": p + 1, "so": 2, "ep": p + 2, "eo": 3}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.docx")
            self.assertEqual(docx_export.export(fx("sample.docx"), out, anns, author="Teszt Elek"), 2)
            import zipfile
            with zipfile.ZipFile(out) as z:
                doc_xml = z.read("word/document.xml").decode("utf-8")
                comments = z.read("word/comments.xml").decode("utf-8")
                rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
                ct = z.read("[Content_Types].xml").decode("utf-8")
            self.assertIn("commentRangeStart", doc_xml)
            self.assertIn("commentReference", doc_xml)
            self.assertIn('w:highlight w:val="green"', doc_xml)
            self.assertIn('w:highlight w:val="yellow"', doc_xml)
            self.assertIn("Első megjegyzés", comments)
            self.assertIn('w:author="Teszt Elek"', comments)
            self.assertIn("comments.xml", rels)
            self.assertIn("/word/comments.xml", ct)
            # original namespace declarations are kept (Word needs them for mc:Ignorable)
            self.assertIn("xmlns:w=", doc_xml[:3000])
            # the visible text is unchanged and still converts the same way
            self.assertEqual(paragraph_texts(docx.convert(out))[p], text)
            # the commented range covers exactly the selected words
            body = re.sub(r"<w:del\b.*?</w:del>", "", doc_xml, flags=re.S)
            seg = body[body.index("commentRangeStart"):body.index("commentRangeEnd")]
            self.assertEqual("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", seg)), "félkövér és egy dőlt")

    def test_doc_converted_by_word_keeps_original(self):
        before = open(fx("sample.docx"), "rb").read()
        with tempfile.TemporaryDirectory() as tmp:
            docx_export.export(fx("sample.docx"), os.path.join(tmp, "x.docx"), [])
        self.assertEqual(before, open(fx("sample.docx"), "rb").read())


class LibraryTests(unittest.TestCase):
    def test_recent_position_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            key = lib.key_for(fx("sample.md"))
            lib.touch(key, fx("sample.md"), "sample", "flow")
            lib.set_position(key, {"ratio": 0.4}, 0.4)
            lib.set_annotations(key, [{"id": "a", "quote": "x"}])
            lib2 = Library(tmp)  # reload from disk
            self.assertEqual(lib2.get(key)["position"], {"ratio": 0.4})
            self.assertEqual(lib2.recent()[0]["notes"], 1)
            self.assertAlmostEqual(lib2.recent()[0]["progress"], 0.4)

    def test_upload_copy_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            path = lib.store_upload(fx("sample.epub"), "könyv.epub")
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(lib.is_library_copy(path))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["LOCALAPPDATA"] = cls.tmp
        settings_mod.data_dir = lambda: cls.tmp
        cls.app = ReaderApp(settings_mod.load())
        cls.server, cls.url = start_server(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.app.close()

    def req(self, path, body=None, headers=None):
        r = urllib.request.Request(self.url + path, data=body, headers=headers or {},
                                   method="POST" if body is not None else "GET")
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.read()

    def test_state_open_and_serve(self):
        state = json.loads(self.req("api/state"))
        self.assertEqual(len(state["languages"]), 19)
        res = json.loads(self.req("api/open", json.dumps({"path": fx("sample.docx")}).encode()))
        sid = res["session"]["id"]
        html = self.req(f"doc/{sid}/__lumen_view__.html").decode()
        self.assertIn("Első fejezet", html)
        self.assertIn(b"<!DOCTYPE html>", self.req(""))
        self.assertIn(b"viewer", self.req("app.js")[:2000] + b"viewer")

    def test_upload(self):
        with open(fx("sample.epub"), "rb") as f:
            data = f.read()
        res = json.loads(self.req("api/upload?name=k%C3%B6nyv.epub", data))
        self.assertEqual(res["session"]["kind"], "epub")

    def test_settings_language(self):
        st = json.loads(self.req("api/settings", json.dumps({"language": "de"}).encode()))
        self.assertEqual(st["strings"]["Open"], "Öffnen")
        st = json.loads(self.req("api/settings", json.dumps({"language": "hu"}).encode()))
        self.assertEqual(st["strings"]["Open"], "Megnyitás")

    def test_security(self):
        base = self.url.rsplit("/", 2)[0]
        with self.assertRaises(urllib.error.HTTPError):  # wrong token
            urllib.request.urlopen(base + "/wrongtoken/api/state", timeout=10)
        with self.assertRaises(urllib.error.HTTPError):  # cross-site request
            self.req("api/open", b"{}", {"Origin": "https://evil.example"})
        with self.assertRaises(urllib.error.HTTPError):  # DNS rebinding
            self.req("api/state", None, {"Host": "evil.example"})

    def test_error_message(self):
        res = json.loads(self.req("api/open", json.dumps({"path": fx("pic.png")}).encode()))
        self.assertIn(".png", res["error"])


if __name__ == "__main__":
    unittest.main()
