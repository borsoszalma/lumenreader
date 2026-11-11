using System.Text;

namespace LumenReader.Documents;

public enum DocumentKind
{
    /// <summary>Converted to HTML by the app (Markdown, DOCX, DOC, RTF, TXT).</summary>
    Flow,
    /// <summary>Original HTML file shown as-is.</summary>
    Html,
    Epub,
    Pdf,
}

/// <summary>A node of the navigation panel (table of contents).</summary>
public sealed class TocItem
{
    public string Title { get; init; } = "";

    /// <summary>Session-relative document path, optionally with #fragment (EPUB chapters).</summary>
    public string? Href { get; init; }

    /// <summary>Element id inside the current page (headings, PDF pages).</summary>
    public string? AnchorId { get; init; }

    public bool IsExpanded { get; set; }

    public List<TocItem> Children { get; } = new();

    /// <summary>Builds a tree from a flat, ordered list of headings (level 1..6).</summary>
    public static List<TocItem> FromHeadings(IEnumerable<(int Level, string Id, string Text)> headings)
    {
        var roots = new List<TocItem>();
        var stack = new List<(int Level, TocItem Item)>();
        foreach (var (level, id, text) in headings)
        {
            var node = new TocItem { Title = text, AnchorId = id };
            while (stack.Count > 0 && stack[^1].Level >= level) stack.RemoveAt(stack.Count - 1);
            if (stack.Count == 0) roots.Add(node);
            else stack[^1].Item.Children.Add(node);
            stack.Add((level, node));
        }
        ExpandTop(roots);
        return roots;
    }

    /// <summary>Expands the first level when the tree is reasonably small.</summary>
    public static void ExpandTop(List<TocItem> roots)
    {
        if (roots.Count <= 60)
            foreach (var r in roots) r.IsExpanded = true;
    }
}

public sealed record ServedResource(Stream Content, string ContentType);

/// <summary>Error with a user-facing (localized) message.</summary>
public sealed class DocumentLoadException(string message) : Exception(message);

/// <summary>
/// An opened document. The viewer (WebView2) requests everything from https://lumen.example/doc/{Id}/...
/// and the session answers with in-memory or on-disk content.
/// </summary>
public abstract class DocumentSession : IDisposable
{
    protected DocumentSession(string filePath, string title)
    {
        FilePath = filePath;
        Title = title;
    }

    public string Id { get; } = Guid.NewGuid().ToString("N")[..12];
    public string FilePath { get; }
    public string Title { get; protected set; }
    public abstract DocumentKind Kind { get; }

    /// <summary>Path (relative to the session root) of the first page to show.</summary>
    public abstract string StartPath { get; }

    /// <summary>Table of contents known up-front (EPUB, PDF). Empty for heading-based documents.</summary>
    public List<TocItem> Toc { get; protected set; } = new();

    /// <summary>True when the TOC should be built from the headings of the rendered page.</summary>
    public virtual bool CollectHeadings => false;

    public abstract Task<ServedResource?> ServeAsync(string path);

    public virtual void Dispose() => GC.SuppressFinalize(this);
}

internal static class FolderServer
{
    /// <summary>Serves a file from <paramref name="root"/> without allowing path traversal outside it.</summary>
    public static ServedResource? Serve(string root, string relativePath)
    {
        try
        {
            var rootFull = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            var full = Path.GetFullPath(Path.Combine(rootFull, relativePath.Replace('/', Path.DirectorySeparatorChar)));
            if (!full.StartsWith(rootFull, StringComparison.OrdinalIgnoreCase) || !File.Exists(full)) return null;
            return new ServedResource(new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.ReadWrite), Mime.FromPath(full));
        }
        catch
        {
            return null;
        }
    }
}

internal static class Mime
{
    private static readonly Dictionary<string, string> Map = new(StringComparer.OrdinalIgnoreCase)
    {
        [".html"] = "text/html", [".htm"] = "text/html", [".xhtml"] = "application/xhtml+xml",
        [".xml"] = "application/xml", [".ncx"] = "application/x-dtbncx+xml", [".opf"] = "application/oebps-package+xml",
        [".css"] = "text/css", [".js"] = "text/javascript", [".json"] = "application/json", [".txt"] = "text/plain",
        [".svg"] = "image/svg+xml", [".png"] = "image/png", [".jpg"] = "image/jpeg", [".jpeg"] = "image/jpeg",
        [".gif"] = "image/gif", [".webp"] = "image/webp", [".bmp"] = "image/bmp", [".ico"] = "image/x-icon",
        [".avif"] = "image/avif", [".ttf"] = "font/ttf", [".otf"] = "font/otf", [".woff"] = "font/woff",
        [".woff2"] = "font/woff2", [".mp3"] = "audio/mpeg", [".m4a"] = "audio/mp4", [".mp4"] = "video/mp4",
        [".webm"] = "video/webm", [".ogg"] = "audio/ogg",
    };

    public static string FromPath(string path) =>
        Map.TryGetValue(Path.GetExtension(path), out var m) ? m : "application/octet-stream";
}

internal static class HtmlUtil
{
    public static string Esc(string? s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        var sb = new StringBuilder(s.Length + 16);
        foreach (var ch in s)
        {
            sb.Append(ch switch
            {
                '&' => "&amp;",
                '<' => "&lt;",
                '>' => "&gt;",
                '"' => "&quot;",
                _ => ch.ToString(),
            });
        }
        return sb.ToString();
    }

    /// <summary>Wraps converted body HTML into a full page using the reader's own typography.</summary>
    public static string Wrap(string title, string body) =>
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">" +
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
        $"<title>{Esc(title)}</title></head><body class=\"lumen-doc lumen-kind-flow\">{body}</body></html>";

    /// <summary>Plain text to paragraphs.</summary>
    public static string TextToHtml(string text)
    {
        var sb = new StringBuilder();
        foreach (var line in text.Replace("\r\n", "\n").Replace('\r', '\n').Split('\n'))
        {
            if (string.IsNullOrWhiteSpace(line)) sb.Append("<p class=\"lumen-empty\"></p>");
            else sb.Append("<p>").Append(Esc(line).Replace("\t", "&emsp;")).Append("</p>");
        }
        return sb.ToString();
    }
}
