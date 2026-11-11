# Lumen Reader – Python-változat

A C#-os Lumen Reader párja, amely **csak a Python beépített könyvtárait** használja (a PDF-megjelenítéshez mellékelve van a Mozilla pdf.js, ami a böngészőben fut). Nincs `pip install`, nincs letöltött exe: a programot a python.org-ról telepített, aláírt `python.exe` futtatja. Ezt a víruskeresők és a céges tűzfalak jellemzően nem kifogásolják.

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
  - PDF, a mellékelt pdf.js-sel (a szöveg kijelölhető)
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
- **Hol tartottál:** minden dokumentumnál megjegyzi az olvasási helyet (PDF-nél az oldalt, EPUB-nál a fejezetet is), és indításkor a legutóbbi dokumentumot ott nyitja meg, ahol abbahagytad.
- **Legutóbbi dokumentumok:** a kezdőlapon (🏠 gomb) láthatók, olvasottsági százalékkal és a jegyzetek számával. Innen egy kattintással újranyithatók.
- **Haladás:** az alsó sávon a százalék, PDF-nél az oldalszám („12. oldal / 340”), EPUB-nál a fejezet is.
- **Kiemelés és megjegyzés:** jelölj ki szöveget, és válassz színt (sárga, zöld, kék, rózsaszín), vagy a ✎ gombbal írj hozzá megjegyzést. A kiemelésre kattintva szerkeszthető vagy törölhető. Az oldalsó panel *Jegyzetek* fülén az összes jegyzet listája látható, kattintásra odaugrik.
- **Word-dokumentumoknál** (.docx, és .doc is, ha a gépen van Word vagy LibreOffice) a kiemelések és megjegyzések egy külön másolatba is mentődnek, például `Dokumentum (megjegyzésekkel).docx`. Ebben a megjegyzések valódi Word-megjegyzésbuborékként, a kiemelések Word-kiemelésként jelennek meg. Az eredeti fájl érintetlen marad. A másolat az eredeti mellé kerül; ráhúzott fájlnál, vagy ha oda nem lehet írni, a `Dokumentumok\Lumen Reader` mappába.
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
lumen/library.py       legutóbbi dokumentumok, olvasási hely, jegyzetek
lumen/formats/docx_export.py  jegyzetek → Word-megjegyzések egy .docx-másolatban
lumen/web/             felület (HTML/CSS/JS), témák, PDF-néző
lumen/web/pdfjs/       Mozilla pdf.js 5.7 (Apache-2.0), változtatás nélkül
lumen/strings/         fordítások (ugyanazok a fájlok, mint a C#-os változatnál)
tests/                 tesztek valódi fájlokkal
```

A beállítások itt vannak: `%LOCALAPPDATA%\LumenReaderPy\settings.json`. Az olvasási helyek, a legutóbbi dokumentumok és a jegyzetek a `library.json` fájlba kerülnek. A ráhúzott fájlokról a program egy másolatot is eltesz a `files` mappába, hogy a listából újra megnyithatók legyenek. Hiba esetén a naplót a `log.txt` tartalmazza ugyanebben a mappában.

## Korlátok

- **PDF:** a jegyzetek a programban mentődnek, a PDF-fájlba nem.
- **Jegyzetek EPUB-ban, Markdownban, HTML-ben és RTF-ben:** ezek is csak a programban mentődnek. Word-másolat csak Word-dokumentumokhoz készül.
- **DOC:** a teljes formázáshoz Microsoft Word vagy LibreOffice kell a gépen. Ha egyik sincs, a program csak a szöveget mutatja.
- **Feltöltött (ráhúzott) Markdown és HTML:** ráhúzáskor a böngésző nem adja át a fájl helyét, ezért az ezekben relatív hivatkozással szereplő képek nem jelennek meg. A *Megnyitás* gombbal megnyitva igen.

## Tesztek

```
python -m unittest discover -s python/tests -v
```
