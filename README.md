# Lumen Reader

Windowsos e-könyv- és dokumentumolvasó, két változatban:

| Változat | Mappa | Kinek |
|---|---|---|
| **C# / WPF** (.NET 10, WebView2) | `src/` | Microsoft Store-ba szánt, telepíthető alkalmazás (MSIX) |
| **Python** (csak beépített könyvtárak) | `python/` | letöltött exe nélkül, a gépre telepített Pythonnal futtatható – lásd [python/README.md](python/README.md) |

A leírás további része a C#-os változatról szól.

## Funkciók

| | |
|---|---|
| **Formátumok** | EPUB (2 és 3), PDF, DOCX, DOC, RTF, Markdown, HTML/XHTML, TXT |
| **Navigációs panel** | A tartalomjegyzék bal oldali fában: EPUB-nál a könyv saját tartalomjegyzéke, PDF-nél a könyvjelzők (ha nincs ilyen, akkor az oldalak listája), a többi formátumnál a címsorok. Ki-be kapcsolható (Ctrl+B), a szélessége húzható. |
| **Témák** | Világos, sötét és szembarát olvasó mód (szépia háttér, talpas betű, keskenyebb hasáb). Egy kattintással vagy Ctrl+1/2/3-mal, illetve Ctrl+T-vel körbe lehet váltani. A PDF-oldalak is átszíneződnek. Első indításkor a Windows világos vagy sötét beállítását követi. |
| **Drag & drop** | A fájlt az ablak bármely részére rá lehet húzni, az olvasófelületre is. |
| **Nyelvek** | 19 nyelv: angol, német, francia, spanyol, olasz, portugál, holland, svéd, lengyel, cseh, szlovák, **magyar**, román, horvát, szlovén, szerb (latin betűs), litván, lett, észt. Alapból a Windows megjelenítési nyelvét használja; ha az nem érhető el, angolra vált. A nyelv a jobb felső sarokban átállítható. |
| **Egyéb** | Nagyítás (Ctrl +/−/0), teljes képernyő (F11), EPUB-fejezetek között Alt+←/→, fájltársítás („Megnyitás ezzel”), a beállításokat megjegyzi. |

## Felépítés

```
src/LumenReader/
  App.xaml(.cs)            induló logika, téma és nyelv betöltése
  MainWindow.xaml(.cs)     eszköztár, navigációs panel, WebView2, billentyűk, drag & drop
  Documents/               formátumonkénti betöltők
    EpubSession.cs         EPUB közvetlenül a zipből (nav.xhtml / toc.ncx)
    PdfSession.cs          Windows beépített PDF-motorja (Windows.Data.Pdf) + PdfPig a könyvjelzőkhöz
    DocxConverter.cs       DOCX → HTML (OpenXML SDK)
    LegacyDocLoader.cs     DOC: Word COM → LibreOffice → beépített szövegkinyerő
    RtfConverter.cs        RTF → HTML (WPF FlowDocument)
    DocumentLoader.cs      kiterjesztés szerinti szétosztás, Markdown (Markdig), TXT
  Viewer/                  a WebView2-be injektált JS/CSS (témák, címsorgyűjtés, drop), kezdőlap
  Localization/            Loc.cs + Strings/<nyelvkód>.json
  Theming/, Services/      témakezelés, beállítások (JSON a %LOCALAPPDATA%\LumenReader mappában)
packaging/                 MSIX-manifest és logók
build/pack-msix.ps1        MSIX / .msixbundle / .msixupload készítése
.github/workflows/         CI: fordítás + csomagolás + release
```

Minden dokumentum egy WebView2-ben jelenik meg egy virtuális `https://lumen.example/` címről. Ezt teljes egészében az alkalmazás szolgálja ki, a hálózatra nem megy ki semmi. Így minden formátumra ugyanaz a téma-, nagyítás- és navigációs logika működik.

**Tudnivalók a mostani állapotról**
- **PDF:** az oldalak képként jelennek meg (ezért működik rajtuk a sötét és a szépia mód), így szöveget kijelölni és keresni egyelőre nem lehet. Ha ez fontos, később a PDF.js-re érdemes átállni.
- **DOC:** a teljes formázáshoz Microsoft Word vagy LibreOffice kell a gépen. Ha egyik sincs, az app csak a szöveget mutatja, és ezt jelzi is.
- **DOCX:** a lábjegyzetek, az élőfej és -láb, valamint a szövegdobozok kimaradnak.

## Fordítás és futtatás

Ezekre van szükség: Windows 10 1809 vagy újabb, [.NET 10 SDK](https://dotnet.microsoft.com/download), és a Visual Studio 2022 (17.14+) vagy 2026 „.NET desktop development” munkaterhelése (vagy csak a CLI).

```powershell
dotnet run --project src/LumenReader
```

Visual Studióban elég megnyitni a `LumenReader.slnx` fájlt, majd F5.

### MSIX-csomag helyben

```powershell
./build/pack-msix.ps1 -Version 1.0.0.0
```

Az `artifacts/` mappába kerül a `.msixupload` (ezt kell a Store-ba feltölteni) és a `.msixbundle`. Ha helyben akarod telepíteni a bundle-t, előbb alá kell írni egy tesztes tanúsítvánnyal (`-CertificatePath`, `-CertificatePassword`). A tanúsítvány Subject mezőjének egyeznie kell a `-Publisher` értékével.

## Verziókezelés GitHubon

1. Hozz létre egy (akár privát) GitHub-repót, és töltsd fel ezt a mappát:
   ```powershell
   git init -b main; git add .; git commit -m "Initial commit"
   git remote add origin https://github.com/<felhasznalo>/lumen-reader.git
   git push -u origin main
   ```
2. Minden push és pull request után a **GitHub Actions** lefordítja az appot. A `main` ágon MSIX-csomag is készül, ez letölthető artifactként.
3. **Kiadás:** tegyél fel egy taget, például `git tag v1.0.0 && git push --tags`. Ebből `1.0.0.0` verziójú csomag lesz, és létrejön egy GitHub Release, amelyhez a `.msixupload` is csatolva van.
4. Javasolt munkamenet: a `main` mindig kiadható állapotban legyen, a fejlesztés feature-ágakon és PR-eken menjen, a hibákat és ötleteket Issue-kban kövesd.

## Microsoft Store

1. **Fejlesztői fiók:** <https://storedeveloper.microsoft.com>. Magánszemélyként ingyenes, személyi igazolvánnyal és szelfivel kell igazolni magad.
2. **Partner Center → Apps and games → New product → MSIX or PWA app**, és foglald le az app nevét. Ha a „Lumen Reader” foglalt, válassz másikat, és írd át a `DisplayName` mezőket a `packaging/Package.appxmanifest` fájlban.
3. **Product identity** oldal: másold ki a `Package/Identity/Name`, `Package/Identity/Publisher` és `Package/Properties/PublisherDisplayName` értékeket. A GitHub-repóban a *Settings → Secrets and variables → Actions → Variables* alatt vedd fel őket így:
   - `MSIX_IDENTITY_NAME`
   - `MSIX_PUBLISHER`
   - `MSIX_PUBLISHER_DISPLAY_NAME`
4. Tagelj (`v1.0.0`), majd töltsd le a release-ből a `.msixupload`-ot, és töltsd fel a beadásnál a *Packages* részhez. A Store maga írja alá a csomagot.
5. A beadáshoz még ezek kellenek: leírás nyelvenként, képernyőképek, kategória (*Books & reference*), korhatár-besorolási kérdőív és adatvédelmi nyilatkozat URL (használhatod a repó `PRIVACY.md` fájljának linkjét). A `runFullTrust` képesség miatt rövid indoklást kérnek, például: „Desktop app (WPF) that opens local documents.” A `packaging/store/AppIcon-300.png` a Store-logóhoz jó.
6. **Később automatizálható:** a [Microsoft Store Developer CLI](https://learn.microsoft.com/windows/apps/publish/msstore-dev-cli/overview) (`msstore publish`) GitHub Actionsből is be tud adni, ehhez egy Entra ID alkalmazás adatait kell titkos változóként beállítani.

## Új nyelv hozzáadása

1. Másold le a `src/LumenReader/Localization/Strings/en.json` fájlt `<kód>.json` néven, és fordítsd le.
2. Vegyél fel egy sort a `Loc.Languages` listába (`Localization/Loc.cs`).
3. Opcionálisan add hozzá a nyelvet a `<Resources>` részhez a `packaging/Package.appxmanifest` fájlban.

## Licenc

Még nincs megadva. Ha nyílt forrásúvá teszed, érdemes egy `LICENSE` fájlt felvenni (például MIT-et). A felhasznált csomagok licencei: Markdig (BSD-2), Open XML SDK (MIT), PdfPig (Apache-2.0), OpenMcdf (MPL-2.0), WebView2 SDK (Microsoft).
