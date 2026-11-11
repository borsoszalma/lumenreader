# Lumen Reader – Python-változat

A C#-os Lumen Reader párja, amely **csak a Python beépített könyvtárait** használja. Nincs `pip install`, nincs letöltött exe: a programot a python.org-ról telepített, aláírt `python.exe` futtatja. Ezt a víruskeresők és a céges tűzfalak jellemzően nem kifogásolják.

## Indítás

1. Ha még nincs a gépen, telepítsd a **Python 3.9-es vagy újabb** verzióját a <https://www.python.org/downloads/> oldalról, vagy a Microsoft Store-ból („Python 3.13”). A python.org-os telepítőnél hagyd bekapcsolva a „tcl/tk” és a „py launcher” lehetőséget, ezek alapból be vannak jelölve.
2. Tedd a `python` mappát egy tetszőleges helyre, például `C:\LumenReader`.
3. Kattints duplán a **`LumenReader.pyw`** fájlra.
   - Ha nem indul el, nyiss egy parancssort a mappában, és futtasd: `pyw LumenReader.pyw` vagy `python LumenReader.pyw`.
   - Fájllal együtt is indítható: `pyw LumenReader.pyw "C:\Könyvek\regény.epub"`.

A program a Windowsba épített **Microsoft Edge** böngészőt használja megjelenítésre, egy külön „alkalmazásablakban” (címsor és fülek nélkül). Ha bezárod az ablakot, néhány másodperc múlva a háttérben futó Python is kilép.

## Mit tud

- **Formátumok:**
  - EPUB 2 és 3
  - PDF, az Edge beépített PDF-nézőjével
  - DOCX
  - DOC
  - RTF
  - Markdown
  - HTML/XHTML
  - TXT
- **Navigációs panel:**
  - EPUB-nál a könyv tartalomjegyzéke,
  - PDF-nél a könyvjelzők (ha nincsenek, akkor az oldalak listája),
  - a többi formátumnál a címsorok. Ki-be kapcsolható (Ctrl+B), a szélessége húzható.
- **Témák:** világos, sötét és szembarát olvasó mód (Alt+1/2/3). A PDF-re is hatnak.
- **Drag & drop:** a fájlt az ablakra húzva nyílik meg.
- **Nyelvek:** 19 nyelv, alapból a Windows nyelvét használja, ha az nem érhető el, angolt. A jobb felső sarokban átállítható.

## Hogyan működik

```
LumenReader.pyw        indító
lumen/app.py           helyi kiszolgáló indítása + Edge-ablak megnyitása
lumen/server.py        kiszolgáló: kizárólag 127.0.0.1, véletlen kulcs az URL-ben,
                       más weboldalak kéréseit elutasítja
lumen/formats/         formátumok (saját kód, csak beépített könyvtárakkal):
  docx.py              DOCX → HTML (zipfile + ElementTree)
  doc.py               DOC: Word → LibreOffice → beépített szövegkinyerő (saját OLE-olvasó)
  rtf.py               RTF → HTML (kódlapok, \u, stílusok, listák, táblázatok, képek)
  markdown.py          Markdown → HTML
  epub.py              EPUB közvetlenül a zipből (nav.xhtml / toc.ncx)
  pdf.py               PDF-könyvjelzők és oldalszám (saját PDF-objektumolvasó)
lumen/web/             felület (HTML/CSS/JS) és a témák
lumen/strings/         fordítások (ugyanazok a fájlok, mint a C#-os változatnál)
tests/                 tesztek valódi fájlokkal
```

A beállítások itt vannak: `%LOCALAPPDATA%\LumenReaderPy\settings.json`. Hiba esetén a naplót a `log.txt` tartalmazza ugyanebben a mappában.

## Korlátok

- **PDF:** a megjelenítést az Edge PDF-nézője végzi. A szöveg így kijelölhető és kereshető, a sötét és a szépia mód pedig színszűrőként hat rá. Ha a fájlt közvetlenül a PDF-oldalra húzod, előfordulhat, hogy az Edge nyitja meg magának. Ezért inkább az eszköztárra vagy a bal oldali panelre húzd.
- **DOC:** a teljes formázáshoz Microsoft Word vagy LibreOffice kell a gépen. Ha egyik sincs, a program csak a szöveget mutatja.
- **Feltöltött (ráhúzott) Markdown és HTML:** ráhúzáskor a böngésző nem adja át a fájl helyét, ezért az ezekben relatív hivatkozással szereplő képek nem jelennek meg. A *Megnyitás* gombbal megnyitva igen.

## Tesztek

```
python -m unittest discover -s python/tests -v
```
