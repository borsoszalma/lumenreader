using System.IO.Compression;
using System.Xml;
using System.Xml.Linq;
using LumenReader.Localization;

namespace LumenReader.Documents;

/// <summary>
/// EPUB 2/3 reader. The archive is served directly from the zip (no extraction), chapter by chapter,
/// so the book's own CSS, fonts and images work. TOC comes from nav.xhtml (EPUB 3) or toc.ncx (EPUB 2).
/// </summary>
public sealed class EpubSession : DocumentSession
{
    private const string OpsNs = "http://www.idpf.org/2007/ops";
    private const string DcNs = "http://purl.org/dc/elements/1.1/";

    private readonly FileStream _stream;
    private readonly ZipArchive _zip;
    private readonly Dictionary<string, ZipArchiveEntry> _entries;
    private readonly object _zipLock = new();

    public IReadOnlyList<string> Spine { get; }

    public override DocumentKind Kind => DocumentKind.Epub;
    public override string StartPath => Spine.Count > 0 ? Spine[0] : "";

    private EpubSession(string filePath, string title, FileStream stream, ZipArchive zip,
        Dictionary<string, ZipArchiveEntry> entries, List<string> spine, List<TocItem> toc) : base(filePath, title)
    {
        _stream = stream;
        _zip = zip;
        _entries = entries;
        Spine = spine;
        Toc = toc;
    }

    public static EpubSession Load(string path)
    {
        var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        try
        {
            var zip = new ZipArchive(stream, ZipArchiveMode.Read);
            var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.OrdinalIgnoreCase);
            foreach (var e in zip.Entries)
                entries.TryAdd(e.FullName.Replace('\\', '/'), e);

            var container = ReadXml(entries, "META-INF/container.xml")
                ?? throw new DocumentLoadException(Loc.T("ErrorTitle"));
            var opfPath = container.Descendants().FirstOrDefault(x => x.Name.LocalName == "rootfile")
                ?.Attribute("full-path")?.Value ?? throw new DocumentLoadException(Loc.T("ErrorTitle"));
            var opf = ReadXml(entries, opfPath) ?? throw new DocumentLoadException(Loc.T("ErrorTitle"));
            var opfDir = DirOf(opfPath);

            var title = opf.Descendants(XName.Get("title", DcNs)).FirstOrDefault()?.Value.Trim();
            if (string.IsNullOrEmpty(title)) title = Path.GetFileNameWithoutExtension(path);

            // Manifest: id -> (path, media-type, properties)
            var manifest = new Dictionary<string, (string Path, string Media, string Props)>();
            foreach (var item in opf.Descendants().Where(x => x.Name.LocalName == "item"))
            {
                var id = item.Attribute("id")?.Value;
                var href = item.Attribute("href")?.Value;
                if (id == null || href == null) continue;
                manifest[id] = (Resolve(opfDir, href).Path, item.Attribute("media-type")?.Value ?? "",
                    item.Attribute("properties")?.Value ?? "");
            }

            var spineEl = opf.Descendants().FirstOrDefault(x => x.Name.LocalName == "spine");
            var spine = new List<string>();
            foreach (var itemref in spineEl?.Elements().Where(x => x.Name.LocalName == "itemref") ?? [])
            {
                var idref = itemref.Attribute("idref")?.Value;
                if (idref != null && manifest.TryGetValue(idref, out var m) && entries.ContainsKey(m.Path))
                    spine.Add(m.Path);
            }

            // Table of contents
            var toc = new List<TocItem>();
            var nav = manifest.Values.FirstOrDefault(m => m.Props.Split(' ').Contains("nav"));
            if (nav.Path != null) toc = ParseNav(entries, nav.Path);

            if (toc.Count == 0)
            {
                var ncxId = spineEl?.Attribute("toc")?.Value;
                var ncx = ncxId != null && manifest.TryGetValue(ncxId, out var n) ? n.Path
                    : manifest.Values.FirstOrDefault(m => m.Media == "application/x-dtbncx+xml").Path;
                if (ncx != null) toc = ParseNcx(entries, ncx);
            }

            if (toc.Count == 0)
                toc = spine.Select((p, i) => new TocItem { Title = Loc.F("Chapter", i + 1), Href = p }).ToList();

            TocItem.ExpandTop(toc);
            return new EpubSession(path, title, stream, zip, entries, spine, toc);
        }
        catch
        {
            stream.Dispose();
            throw;
        }
    }

    public override Task<ServedResource?> ServeAsync(string path)
    {
        if (!_entries.TryGetValue(path, out var entry)) return Task.FromResult<ServedResource?>(null);
        var ms = new MemoryStream();
        lock (_zipLock)
        {
            using var s = entry.Open();
            s.CopyTo(ms);
        }
        ms.Position = 0;
        return Task.FromResult<ServedResource?>(new ServedResource(ms, Mime.FromPath(path)));
    }

    /// <summary>Index of a spine item by path, or -1.</summary>
    public int IndexOf(string path) =>
        Spine.ToList().FindIndex(s => string.Equals(s, path, StringComparison.OrdinalIgnoreCase));

    public override void Dispose()
    {
        _zip.Dispose();
        _stream.Dispose();
        base.Dispose();
    }

    // ---------- TOC parsing ----------

    private static List<TocItem> ParseNav(Dictionary<string, ZipArchiveEntry> entries, string navPath)
    {
        var doc = ReadXml(entries, navPath);
        if (doc == null) return new();
        var navs = doc.Descendants().Where(x => x.Name.LocalName == "nav").ToList();
        var tocNav = navs.FirstOrDefault(n => (n.Attribute(XName.Get("type", OpsNs))?.Value ?? "").Split(' ').Contains("toc"))
                     ?? navs.FirstOrDefault();
        var ol = tocNav?.Elements().FirstOrDefault(x => x.Name.LocalName == "ol");
        return ol == null ? new() : ParseNavList(ol, DirOf(navPath));
    }

    private static List<TocItem> ParseNavList(XElement ol, string baseDir)
    {
        var result = new List<TocItem>();
        foreach (var li in ol.Elements().Where(x => x.Name.LocalName == "li"))
        {
            var label = li.Elements().FirstOrDefault(x => x.Name.LocalName is "a" or "span");
            var href = label?.Attribute("href")?.Value;
            var item = new TocItem
            {
                Title = Clean(label?.Value),
                Href = href == null ? null : Resolve(baseDir, href).Full,
            };
            var childList = li.Elements().FirstOrDefault(x => x.Name.LocalName == "ol");
            if (childList != null) item.Children.AddRange(ParseNavList(childList, baseDir));
            if (item.Title.Length > 0 || item.Children.Count > 0) result.Add(item);
        }
        return result;
    }

    private static List<TocItem> ParseNcx(Dictionary<string, ZipArchiveEntry> entries, string ncxPath)
    {
        var doc = ReadXml(entries, ncxPath);
        var navMap = doc?.Descendants().FirstOrDefault(x => x.Name.LocalName == "navMap");
        return navMap == null ? new() : ParseNavPoints(navMap, DirOf(ncxPath));
    }

    private static List<TocItem> ParseNavPoints(XElement parent, string baseDir)
    {
        var result = new List<TocItem>();
        foreach (var np in parent.Elements().Where(x => x.Name.LocalName == "navPoint"))
        {
            var text = np.Elements().FirstOrDefault(x => x.Name.LocalName == "navLabel")
                ?.Descendants().FirstOrDefault(x => x.Name.LocalName == "text")?.Value;
            var src = np.Elements().FirstOrDefault(x => x.Name.LocalName == "content")?.Attribute("src")?.Value;
            var item = new TocItem { Title = Clean(text), Href = src == null ? null : Resolve(baseDir, src).Full };
            item.Children.AddRange(ParseNavPoints(np, baseDir));
            result.Add(item);
        }
        return result;
    }

    // ---------- helpers ----------

    private static XDocument? ReadXml(Dictionary<string, ZipArchiveEntry> entries, string path)
    {
        if (!entries.TryGetValue(path, out var entry)) return null;
        try
        {
            using var s = entry.Open();
            var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Ignore, XmlResolver = null };
            using var reader = XmlReader.Create(s, settings);
            return XDocument.Load(reader);
        }
        catch
        {
            return null;
        }
    }

    private static string DirOf(string path)
    {
        var i = path.LastIndexOf('/');
        return i < 0 ? "" : path[..(i + 1)];
    }

    /// <summary>Resolves an href relative to a folder inside the archive. Returns path and path+fragment.</summary>
    private static (string Path, string Full) Resolve(string baseDir, string href)
    {
        var fragment = "";
        var hash = href.IndexOf('#');
        if (hash >= 0)
        {
            fragment = href[hash..];
            href = href[..hash];
        }
        href = Uri.UnescapeDataString(href);

        var parts = new List<string>();
        foreach (var seg in (href.Length == 0 ? "" : baseDir + href).Split('/'))
        {
            if (seg is "" or ".") continue;
            if (seg == "..") { if (parts.Count > 0) parts.RemoveAt(parts.Count - 1); }
            else parts.Add(seg);
        }
        var p = string.Join('/', parts);
        return (p, p + fragment);
    }

    private static string Clean(string? s) =>
        string.Join(' ', (s ?? "").Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
}
