using System.Text;

namespace LumenReader.Documents;

/// <summary>A document converted to a single HTML page (Markdown, DOCX, DOC, RTF, TXT).</summary>
public sealed class FlowSession : DocumentSession
{
    public const string IndexName = "__lumen_view__.html";

    private readonly byte[] _html;
    private readonly string? _folder;

    /// <param name="folder">Folder of the source file, so relative images (e.g. in Markdown) resolve.</param>
    public FlowSession(string filePath, string title, string html, string? folder) : base(filePath, title)
    {
        _html = Encoding.UTF8.GetBytes(html);
        _folder = folder;
    }

    public override DocumentKind Kind => DocumentKind.Flow;
    public override string StartPath => IndexName;
    public override bool CollectHeadings => true;

    public override Task<ServedResource?> ServeAsync(string path)
    {
        if (path == IndexName)
            return Task.FromResult<ServedResource?>(new ServedResource(new MemoryStream(_html, false), "text/html; charset=utf-8"));
        return Task.FromResult(_folder == null ? null : FolderServer.Serve(_folder, path));
    }
}

/// <summary>An HTML file shown as-is; its folder is served so CSS/images keep working.</summary>
public sealed class HtmlSession : DocumentSession
{
    private readonly string _folder;
    private readonly string _fileName;

    public HtmlSession(string filePath, string title) : base(filePath, title)
    {
        _folder = Path.GetDirectoryName(filePath)!;
        _fileName = Path.GetFileName(filePath);
    }

    public override DocumentKind Kind => DocumentKind.Html;
    public override string StartPath => _fileName;
    public override bool CollectHeadings => true;

    public override Task<ServedResource?> ServeAsync(string path) => Task.FromResult(FolderServer.Serve(_folder, path));

    public void UpdateTitle(string title)
    {
        if (!string.IsNullOrWhiteSpace(title)) Title = title.Trim();
    }
}
