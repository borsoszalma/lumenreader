using Markdig;
using LumenReader.Localization;

namespace LumenReader.Documents;

public static class DocumentLoader
{
    /// <summary>File extensions the reader opens.</summary>
    public static readonly string[] Extensions =
    [
        ".epub", ".pdf", ".docx", ".doc", ".rtf",
        ".md", ".markdown", ".mdown", ".mkd",
        ".html", ".htm", ".xhtml", ".txt",
    ];

    public static bool IsSupported(string path) =>
        Extensions.Contains(Path.GetExtension(path), StringComparer.OrdinalIgnoreCase);

    private static readonly MarkdownPipeline Pipeline = new MarkdownPipelineBuilder()
        .UseAdvancedExtensions()
        .UseYamlFrontMatter()
        .Build();

    /// <summary>Opens a document. Call from the UI thread (RTF needs it); heavy work is offloaded.</summary>
    public static async Task<DocumentSession> LoadAsync(string path)
    {
        var ext = Path.GetExtension(path).ToLowerInvariant();
        var title = Path.GetFileNameWithoutExtension(path);
        var folder = Path.GetDirectoryName(path);

        switch (ext)
        {
            case ".epub":
                return await Task.Run(() => EpubSession.Load(path));

            case ".pdf":
                return await PdfSession.LoadAsync(path);

            case ".docx":
            {
                var html = await Task.Run(() => DocxConverter.Convert(path));
                return new FlowSession(path, title, HtmlUtil.Wrap(title, html), folder);
            }

            case ".doc":
                return await LegacyDocLoader.LoadAsync(path, title);

            case ".rtf":
                return new FlowSession(path, title, HtmlUtil.Wrap(title, RtfConverter.ToHtml(path)), folder);

            case ".md" or ".markdown" or ".mdown" or ".mkd":
            {
                var markdown = await File.ReadAllTextAsync(path);
                var html = await Task.Run(() => Markdown.ToHtml(markdown, Pipeline));
                return new FlowSession(path, title, HtmlUtil.Wrap(title, html), folder);
            }

            case ".html" or ".htm" or ".xhtml":
                return new HtmlSession(path, title);

            case ".txt":
            {
                var text = await File.ReadAllTextAsync(path);
                return new FlowSession(path, title, HtmlUtil.Wrap(title, HtmlUtil.TextToHtml(text)), folder);
            }

            default:
                throw new DocumentLoadException(Loc.F("UnsupportedFormat", ext));
        }
    }
}
