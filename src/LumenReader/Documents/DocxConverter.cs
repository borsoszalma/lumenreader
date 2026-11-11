using System.Text;
using System.Text.RegularExpressions;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;
using A = DocumentFormat.OpenXml.Drawing;
using Wp = DocumentFormat.OpenXml.Drawing.Wordprocessing;

namespace LumenReader.Documents;

/// <summary>
/// DOCX → HTML: headings (also localized style names like "Címsor 1"), bold/italic/underline/strike,
/// super/subscript, bullet and numbered lists, tables, hyperlinks, bookmarks and embedded images.
/// </summary>
internal sealed partial class DocxConverter
{
    private const string RelNs = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";

    private readonly MainDocumentPart _main;
    private readonly Dictionary<string, Style> _styles = new();
    private readonly Dictionary<(int, int), bool> _orderedCache = new();
    private readonly StringBuilder _sb = new();
    private readonly List<string> _lists = new();

    private DocxConverter(MainDocumentPart main)
    {
        _main = main;
        var styles = main.StyleDefinitionsPart?.Styles;
        if (styles != null)
            foreach (var s in styles.Elements<Style>())
                if (s.StyleId?.Value is string id) _styles.TryAdd(id, s);
    }

    public static string Convert(string path)
    {
        using var doc = WordprocessingDocument.Open(path, false);
        var main = doc.MainDocumentPart ?? throw new InvalidDataException("No main document part.");
        return new DocxConverter(main).Run();
    }

    private string Run()
    {
        var body = _main.Document?.Body;
        if (body == null) return "";
        RenderBlocks(body.ChildElements);
        CloseLists(0);
        return _sb.ToString();
    }

    // ---------- blocks ----------

    private void RenderBlocks(IEnumerable<OpenXmlElement> elements)
    {
        foreach (var el in elements)
        {
            switch (el)
            {
                case Paragraph p:
                    RenderParagraph(p);
                    break;
                case Table t:
                    CloseLists(0);
                    RenderTable(t);
                    break;
                case SdtBlock sdt when sdt.SdtContentBlock != null:
                    RenderBlocks(sdt.SdtContentBlock.ChildElements);
                    break;
                case CustomXmlBlock cx:
                    RenderBlocks(cx.ChildElements);
                    break;
            }
        }
    }

    private void RenderParagraph(Paragraph p)
    {
        var pPr = p.ParagraphProperties;
        var heading = HeadingLevel(pPr);
        var inner = RenderInlines(p);

        if (heading == 0 && GetNumbering(pPr) is { } num)
        {
            OpenListTo(num.Level + 1, IsOrdered(num.NumId, num.Level) ? "ol" : "ul");
            _sb.Append("<li>").Append(inner).Append("</li>");
            return;
        }

        CloseLists(0);
        var align = pPr?.Justification?.Val?.InnerText switch
        {
            "center" => " style=\"text-align:center\"",
            "right" or "end" => " style=\"text-align:right\"",
            "both" or "distribute" => " style=\"text-align:justify\"",
            _ => "",
        };

        if (heading > 0)
            _sb.Append($"<h{heading}{align}>{inner}</h{heading}>");
        else if (IsBlank(inner))
            _sb.Append("<p class=\"lumen-empty\"></p>");
        else
            _sb.Append($"<p{align}>{inner}</p>");
    }

    private void RenderTable(Table table)
    {
        _sb.Append("<table>");
        foreach (var row in table.Elements<TableRow>())
        {
            _sb.Append("<tr>");
            foreach (var cell in row.Elements<TableCell>())
            {
                var span = cell.TableCellProperties?.GridSpan?.Val?.Value ?? 1;
                _sb.Append(span > 1 ? $"<td colspan=\"{span}\">" : "<td>");

                var saved = new List<string>(_lists);
                _lists.Clear();
                RenderBlocks(cell.ChildElements);
                CloseLists(0);
                _lists.AddRange(saved);

                _sb.Append("</td>");
            }
            _sb.Append("</tr>");
        }
        _sb.Append("</table>");
    }

    // ---------- inlines ----------

    private string RenderInlines(OpenXmlElement container)
    {
        var sb = new StringBuilder();
        foreach (var child in container.ChildElements)
        {
            switch (child)
            {
                case ParagraphProperties:
                case DeletedRun:
                    break;
                case DocumentFormat.OpenXml.Wordprocessing.Run r:
                    sb.Append(RenderRun(r));
                    break;
                case Hyperlink h:
                {
                    string? href = null;
                    if (h.Id?.Value is string rid)
                        href = _main.HyperlinkRelationships.FirstOrDefault(x => x.Id == rid)?.Uri.ToString();
                    else if (h.Anchor?.Value is string anchor)
                        href = "#" + anchor;
                    var inner = RenderInlines(h);
                    sb.Append(href != null ? $"<a href=\"{HtmlUtil.Esc(href)}\">{inner}</a>" : inner);
                    break;
                }
                case BookmarkStart bs when bs.Name?.Value is string name && !name.StartsWith("_GoBack", StringComparison.Ordinal):
                    sb.Append($"<a id=\"{HtmlUtil.Esc(name)}\"></a>");
                    break;
                default:
                    if (child.HasChildren) sb.Append(RenderInlines(child));
                    break;
            }
        }
        return sb.ToString();
    }

    private string RenderRun(DocumentFormat.OpenXml.Wordprocessing.Run run)
    {
        var rPr = run.RunProperties;
        if (IsOn(rPr?.Vanish)) return "";

        var text = new StringBuilder();
        foreach (var c in run.ChildElements)
        {
            switch (c)
            {
                case Text t: text.Append(HtmlUtil.Esc(t.Text)); break;
                case TabChar: text.Append("&emsp;"); break;
                case Break b: text.Append(b.Type?.InnerText == "page" ? "" : "<br>"); break;
                case CarriageReturn: text.Append("<br>"); break;
                case NoBreakHyphen: text.Append("&#8209;"); break;
                case Drawing or Picture or AlternateContent: text.Append(RenderImage(c)); break;
            }
        }
        if (text.Length == 0) return "";

        var s = text.ToString();
        if (rPr != null)
        {
            if (IsOn(rPr.Bold)) s = $"<strong>{s}</strong>";
            if (IsOn(rPr.Italic)) s = $"<em>{s}</em>";
            if (rPr.Underline?.Val?.InnerText is string u && u != "none") s = $"<u>{s}</u>";
            if (IsOn(rPr.Strike) || IsOn(rPr.DoubleStrike)) s = $"<s>{s}</s>";
            switch (rPr.VerticalTextAlignment?.Val?.InnerText)
            {
                case "superscript": s = $"<sup>{s}</sup>"; break;
                case "subscript": s = $"<sub>{s}</sub>"; break;
            }
        }
        return s;
    }

    private string RenderImage(OpenXmlElement el)
    {
        // DrawingML (w:drawing) – only the first blip, AlternateContent repeats the image in its fallback.
        var embed = el.Descendants<A.Blip>().Select(b => b.Embed?.Value).FirstOrDefault(v => v != null);
        // Legacy VML (v:imagedata r:id="...")
        embed ??= el.Descendants().Where(d => d.LocalName == "imagedata")
            .Select(d => d.GetAttributes().FirstOrDefault(a => a.LocalName == "id" && a.NamespaceUri == RelNs).Value)
            .FirstOrDefault(v => !string.IsNullOrEmpty(v));
        if (embed == null) return "";

        try
        {
            if (_main.GetPartById(embed) is not ImagePart part) return "";
            var type = part.ContentType;
            if (type.Contains("emf") || type.Contains("wmf")) return ""; // not renderable in a browser
            using var stream = part.GetStream();
            using var ms = new MemoryStream();
            stream.CopyTo(ms);

            var width = "";
            var extent = el.Descendants<Wp.Extent>().FirstOrDefault();
            if (extent?.Cx?.Value is long cx && cx > 0) width = $" width=\"{cx / 9525}\""; // EMU -> px

            return $"<img src=\"data:{type};base64,{System.Convert.ToBase64String(ms.ToArray())}\"{width} alt=\"\">";
        }
        catch
        {
            return "";
        }
    }

    // ---------- styles, headings, lists ----------

    private int HeadingLevel(ParagraphProperties? pPr)
    {
        if (pPr == null) return 0;
        if (pPr.OutlineLevel?.Val?.Value is int direct && direct < 6) return direct + 1;
        var styleId = pPr.ParagraphStyleId?.Val?.Value;
        return styleId == null ? 0 : StyleHeading(styleId, 0);
    }

    private int StyleHeading(string styleId, int depth)
    {
        if (depth > 10 || !_styles.TryGetValue(styleId, out var style))
        {
            var m = HeadingIdRegex().Match(styleId);
            return m.Success ? Math.Clamp(int.Parse(m.Groups[1].Value), 1, 6) : 0;
        }

        // Built-in style *names* are always English ("heading 1", "Title") even in localized Word.
        var name = style.StyleName?.Val?.Value?.ToLowerInvariant() ?? "";
        if (name == "title") return 1;
        if (name.StartsWith("heading ", StringComparison.Ordinal) && int.TryParse(name.AsSpan(8), out var lvl))
            return Math.Clamp(lvl, 1, 6);
        if (style.StyleParagraphProperties?.OutlineLevel?.Val?.Value is int ol && ol < 6) return ol + 1;

        var basedOn = style.BasedOn?.Val?.Value;
        return basedOn == null ? 0 : StyleHeading(basedOn, depth + 1);
    }

    private (int NumId, int Level)? GetNumbering(ParagraphProperties? pPr)
    {
        var numPr = pPr?.NumberingProperties;
        var numId = numPr?.NumberingId?.Val?.Value;
        var level = numPr?.NumberingLevelReference?.Val?.Value ?? 0;

        if (numId == null && pPr?.ParagraphStyleId?.Val?.Value is string sid && _styles.TryGetValue(sid, out var st))
        {
            var snp = st.StyleParagraphProperties?.NumberingProperties;
            numId = snp?.NumberingId?.Val?.Value;
            level = snp?.NumberingLevelReference?.Val?.Value ?? 0;
        }

        if (numId is null or 0) return null;
        return (numId.Value, Math.Clamp(level, 0, 8));
    }

    private bool IsOrdered(int numId, int level)
    {
        if (_orderedCache.TryGetValue((numId, level), out var cached)) return cached;

        var numbering = _main.NumberingDefinitionsPart?.Numbering;
        var instance = numbering?.Elements<NumberingInstance>().FirstOrDefault(n => n.NumberID?.Value == numId);
        var abstractId = instance?.AbstractNumId?.Val?.Value;
        var abs = numbering?.Elements<AbstractNum>().FirstOrDefault(a => a.AbstractNumberId?.Value == abstractId);
        var lvl = abs?.Elements<Level>().FirstOrDefault(l => l.LevelIndex?.Value == level);
        var format = lvl?.NumberingFormat?.Val?.InnerText;

        var ordered = format != null && format != "bullet" && format != "none";
        _orderedCache[(numId, level)] = ordered;
        return ordered;
    }

    private void OpenListTo(int depth, string tag)
    {
        CloseLists(depth);
        if (_lists.Count == depth && depth > 0 && _lists[^1] != tag) CloseLists(depth - 1);
        while (_lists.Count < depth)
        {
            _sb.Append('<').Append(tag).Append('>');
            _lists.Add(tag);
        }
    }

    private void CloseLists(int depth)
    {
        while (_lists.Count > depth)
        {
            _sb.Append("</").Append(_lists[^1]).Append('>');
            _lists.RemoveAt(_lists.Count - 1);
        }
    }

    private static bool IsOn(OnOffType? t) => t != null && (t.Val == null || t.Val.Value);

    private static bool IsBlank(string html) =>
        !html.Contains("<img", StringComparison.Ordinal) &&
        string.IsNullOrWhiteSpace(TagRegex().Replace(html, "").Replace("&emsp;", ""));

    [GeneratedRegex(@"^heading\s*(\d)$", RegexOptions.IgnoreCase)]
    private static partial Regex HeadingIdRegex();

    [GeneratedRegex("<[^>]+>")]
    private static partial Regex TagRegex();
}
