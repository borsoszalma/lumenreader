using System.Globalization;
using System.Text;
using LumenReader.Localization;
using UglyToad.PdfPig.Outline;
using Windows.Storage;
using Windows.Storage.Streams;
using PigDocument = UglyToad.PdfPig.PdfDocument;
using WinPdf = Windows.Data.Pdf;

namespace LumenReader.Documents;

/// <summary>
/// PDF via the PDF engine built into Windows (Windows.Data.Pdf). Pages are rendered lazily to PNG when the
/// viewer scrolls to them; this lets the light/dark/reading themes recolour pages consistently.
/// Bookmarks (outline) are read with PdfPig.
/// </summary>
public sealed class PdfSession : DocumentSession
{
    private const int RenderWidth = 1600;

    private readonly WinPdf.PdfDocument _doc;
    private readonly byte[] _index;
    private readonly SemaphoreSlim _gate = new(1, 1);

    public override DocumentKind Kind => DocumentKind.Pdf;
    public override string StartPath => "index.html";

    private PdfSession(string filePath, string title, WinPdf.PdfDocument doc, byte[] index, List<TocItem> toc)
        : base(filePath, title)
    {
        _doc = doc;
        _index = index;
        Toc = toc;
    }

    public static async Task<PdfSession> LoadAsync(string path)
    {
        var title = Path.GetFileNameWithoutExtension(path);
        var file = await StorageFile.GetFileFromPathAsync(path);
        WinPdf.PdfDocument doc;
        try
        {
            doc = await WinPdf.PdfDocument.LoadFromFileAsync(file);
        }
        catch (Exception ex) when (ex.HResult == unchecked((int)0x8007052B)) // ERROR_WRONG_PASSWORD
        {
            throw new DocumentLoadException(Loc.T("PasswordProtected"));
        }

        var count = (int)doc.PageCount;
        var html = new StringBuilder();
        html.Append("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>")
            .Append(HtmlUtil.Esc(title))
            .Append("</title></head><body class=\"lumen-kind-pdf\"><div class=\"lumen-pages\">");
        for (var i = 0; i < count; i++)
        {
            using var page = doc.GetPage((uint)i);
            var w = Math.Max(1, page.Size.Width);
            var h = Math.Max(1, page.Size.Height);
            var n = i + 1;
            html.Append(CultureInfo.InvariantCulture,
                $"<div class=\"lumen-page\" id=\"page-{n}\" style=\"aspect-ratio:{w:0.##}/{h:0.##}\">" +
                $"<img loading=\"lazy\" decoding=\"async\" alt=\"\" src=\"page/{n}.png\"></div>");
        }
        html.Append("</div></body></html>");

        var toc = await Task.Run(() => ReadOutline(path));
        if (toc.Count == 0)
            toc = Enumerable.Range(1, count).Select(n => new TocItem { Title = Loc.F("Page", n), AnchorId = $"page-{n}" }).ToList();
        TocItem.ExpandTop(toc);

        return new PdfSession(path, title, doc, Encoding.UTF8.GetBytes(html.ToString()), toc);
    }

    public override async Task<ServedResource?> ServeAsync(string path)
    {
        if (path == "index.html")
            return new ServedResource(new MemoryStream(_index, false), "text/html; charset=utf-8");

        if (!path.StartsWith("page/", StringComparison.Ordinal) || !path.EndsWith(".png", StringComparison.Ordinal))
            return null;
        if (!int.TryParse(path.AsSpan(5, path.Length - 9), NumberStyles.None, CultureInfo.InvariantCulture, out var n)
            || n < 1 || n > _doc.PageCount)
            return null;

        await _gate.WaitAsync();
        try
        {
            using var page = _doc.GetPage((uint)(n - 1));
            using var output = new InMemoryRandomAccessStream();
            var options = new WinPdf.PdfPageRenderOptions
            {
                DestinationWidth = RenderWidth,
                BitmapEncoderId = Windows.Graphics.Imaging.BitmapEncoder.PngEncoderId,
            };
            await page.RenderToStreamAsync(output, options);

            output.Seek(0);
            var ms = new MemoryStream();
            await output.AsStreamForRead().CopyToAsync(ms);
            ms.Position = 0;
            return new ServedResource(ms, "image/png");
        }
        finally
        {
            _gate.Release();
        }
    }

    private static List<TocItem> ReadOutline(string path)
    {
        try
        {
            using var pdf = PigDocument.Open(path);
            if (!pdf.TryGetBookmarks(out var bookmarks)) return new();
            return bookmarks.Roots.Select(Convert).ToList();
        }
        catch
        {
            return new();
        }
    }

    private static TocItem Convert(BookmarkNode node)
    {
        var item = new TocItem
        {
            Title = string.IsNullOrWhiteSpace(node.Title) ? "…" : node.Title.Trim(),
            AnchorId = node is DocumentBookmarkNode d ? $"page-{d.PageNumber}" : null,
        };
        foreach (var child in node.Children) item.Children.Add(Convert(child));
        return item;
    }

    public override void Dispose()
    {
        _gate.Dispose();
        base.Dispose();
    }
}
