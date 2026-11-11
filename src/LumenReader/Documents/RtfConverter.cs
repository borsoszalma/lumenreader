using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media.Imaging;

namespace LumenReader.Documents;

/// <summary>
/// RTF → HTML using WPF's built-in RTF reader (FlowDocument), then walking the document tree.
/// Must run on the UI (STA) thread.
/// </summary>
internal static class RtfConverter
{
    public static string ToHtml(string path)
    {
        var doc = new FlowDocument();
        var range = new TextRange(doc.ContentStart, doc.ContentEnd);
        using (var fs = File.OpenRead(path))
            range.Load(fs, DataFormats.Rtf);

        var sb = new StringBuilder();
        RenderBlocks(doc.Blocks, sb);
        return sb.ToString();
    }

    private static void RenderBlocks(IEnumerable<Block> blocks, StringBuilder sb)
    {
        foreach (var block in blocks) RenderBlock(block, sb);
    }

    private static void RenderBlock(Block block, StringBuilder sb)
    {
        switch (block)
        {
            case System.Windows.Documents.Paragraph p:
            {
                var inner = RenderInlines(p.Inlines);
                var text = new TextRange(p.ContentStart, p.ContentEnd).Text.Trim();
                var align = p.TextAlignment switch
                {
                    TextAlignment.Center => " style=\"text-align:center\"",
                    TextAlignment.Right => " style=\"text-align:right\"",
                    TextAlignment.Justify => " style=\"text-align:justify\"",
                    _ => "",
                };
                var heading = GuessHeading(p, text);
                if (text.Length == 0 && !inner.Contains("<img", StringComparison.Ordinal))
                    sb.Append("<p class=\"lumen-empty\"></p>");
                else if (heading > 0)
                    sb.Append($"<h{heading}{align}>{inner}</h{heading}>");
                else
                    sb.Append($"<p{align}>{inner}</p>");
                break;
            }
            case List list:
            {
                var tag = list.MarkerStyle switch
                {
                    TextMarkerStyle.Disc or TextMarkerStyle.Circle or TextMarkerStyle.Square
                        or TextMarkerStyle.Box or TextMarkerStyle.None => "ul",
                    _ => "ol",
                };
                sb.Append('<').Append(tag).Append('>');
                foreach (var item in list.ListItems)
                {
                    sb.Append("<li>");
                    RenderBlocks(item.Blocks, sb);
                    sb.Append("</li>");
                }
                sb.Append("</").Append(tag).Append('>');
                break;
            }
            case System.Windows.Documents.Table table:
            {
                sb.Append("<table>");
                foreach (var group in table.RowGroups)
                foreach (var row in group.Rows)
                {
                    sb.Append("<tr>");
                    foreach (var cell in row.Cells)
                    {
                        sb.Append(cell.ColumnSpan > 1 ? $"<td colspan=\"{cell.ColumnSpan}\">" : "<td>");
                        RenderBlocks(cell.Blocks, sb);
                        sb.Append("</td>");
                    }
                    sb.Append("</tr>");
                }
                sb.Append("</table>");
                break;
            }
            case Section section:
                RenderBlocks(section.Blocks, sb);
                break;
            case BlockUIContainer { Child: Image img }:
                sb.Append("<p>").Append(ImageTag(img)).Append("</p>");
                break;
        }
    }

    private static string RenderInlines(InlineCollection inlines)
    {
        var sb = new StringBuilder();
        foreach (var inline in inlines)
        {
            switch (inline)
            {
                case System.Windows.Documents.Run run:
                    sb.Append(Format(run, HtmlUtil.Esc(run.Text).Replace("\t", "&emsp;")));
                    break;
                case LineBreak:
                    sb.Append("<br>");
                    break;
                case Hyperlink link:
                {
                    var inner = RenderInlines(link.Inlines);
                    var href = link.NavigateUri?.OriginalString;
                    sb.Append(string.IsNullOrEmpty(href) ? inner : $"<a href=\"{HtmlUtil.Esc(href)}\">{inner}</a>");
                    break;
                }
                case Span span: // Bold, Italic, Underline are Spans; Runs inherit their formatting.
                    sb.Append(RenderInlines(span.Inlines));
                    break;
                case InlineUIContainer { Child: Image img }:
                    sb.Append(ImageTag(img));
                    break;
            }
        }
        return sb.ToString();
    }

    private static string Format(System.Windows.Documents.Run run, string s)
    {
        if (s.Length == 0) return s;
        if (run.FontWeight.ToOpenTypeWeight() >= 600) s = $"<strong>{s}</strong>";
        if (run.FontStyle == FontStyles.Italic || run.FontStyle == FontStyles.Oblique) s = $"<em>{s}</em>";
        if (HasDecoration(run, TextDecorationLocation.Underline)) s = $"<u>{s}</u>";
        if (HasDecoration(run, TextDecorationLocation.Strikethrough)) s = $"<s>{s}</s>";
        if (run.BaselineAlignment == BaselineAlignment.Superscript) s = $"<sup>{s}</sup>";
        else if (run.BaselineAlignment == BaselineAlignment.Subscript) s = $"<sub>{s}</sub>";
        return s;
    }

    /// <summary>TextDecorations are not inherited, so walk up through enclosing spans.</summary>
    private static bool HasDecoration(Inline inline, TextDecorationLocation location)
    {
        for (DependencyObject? d = inline; d is Inline i; d = i.Parent)
            if (i.TextDecorations?.Any(x => x.Location == location) == true) return true;
        return false;
    }

    /// <summary>RTF has no reliable heading markup; use font size (default text is 16px = 12pt).</summary>
    private static int GuessHeading(System.Windows.Documents.Paragraph p, string text)
    {
        if (text.Length == 0 || text.Length > 160) return 0;
        var runs = Runs(p.Inlines).Where(r => !string.IsNullOrWhiteSpace(r.Text)).ToList();
        if (runs.Count == 0) return 0;
        var size = runs.Max(r => r.FontSize);
        var bold = runs.All(r => r.FontWeight.ToOpenTypeWeight() >= 600);
        if (size >= 26) return 1;
        if (size >= 21) return 2;
        if (size >= 18.5 && bold) return 3;
        return 0;
    }

    private static IEnumerable<System.Windows.Documents.Run> Runs(InlineCollection inlines)
    {
        foreach (var inline in inlines)
        {
            if (inline is System.Windows.Documents.Run r) yield return r;
            else if (inline is Span s)
                foreach (var inner in Runs(s.Inlines)) yield return inner;
        }
    }

    private static string ImageTag(Image img)
    {
        if (img.Source is not BitmapSource bitmap) return "";
        try
        {
            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using var ms = new MemoryStream();
            encoder.Save(ms);
            return $"<img src=\"data:image/png;base64,{Convert.ToBase64String(ms.ToArray())}\" alt=\"\">";
        }
        catch
        {
            return "";
        }
    }
}
