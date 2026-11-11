using System.Text;
using LumenReader.Documents;
using LumenReader.Localization;

namespace LumenReader.Viewer;

/// <summary>Start page shown when no document is open. It is also a drop target.</summary>
public static class WelcomePage
{
    public static string Build()
    {
        string T(string key) => HtmlUtil.Esc(Loc.T(key));
        static string K(params string[] keys) => string.Join("+", keys.Select(k => $"<kbd>{k}</kbd>"));

        var shortcuts = new (string Keys, string Label)[]
        {
            (K("Ctrl", "O"), T("Open")),
            (K("Ctrl", "B"), T("Contents")),
            (K("Ctrl", "1") + " / " + K("2") + " / " + K("3"), $"{T("ThemeLight")} / {T("ThemeDark")} / {T("ThemeReading")}"),
            (K("Ctrl", "T"), T("NextTheme")),
            (K("Ctrl", "+") + " / " + K("Ctrl", "−") + " / " + K("Ctrl", "0"), T("Zoom")),
            (K("Alt", "←") + " / " + K("Alt", "→"), T("Chapters")),
            (K("F11"), T("Fullscreen")),
        };

        var sb = new StringBuilder();
        sb.Append($"<!DOCTYPE html><html lang=\"{Loc.I.Code}\"><head><meta charset=\"utf-8\"><title>Lumen Reader</title><style>");
        sb.Append(Style);
        sb.Append("</style></head><body class=\"lumen-kind-welcome\"><main>");
        sb.Append(Logo);
        sb.Append($"<h1>{T("WelcomeTitle")}</h1><p class=\"muted\">{T("WelcomeText")}</p>");
        sb.Append($"<button type=\"button\" data-lumen-open>{T("Open")}</button>");
        sb.Append($"<h2>{T("SupportedFormats")}</h2><div class=\"formats\">");
        foreach (var f in new[] { "EPUB", "PDF", "DOCX", "DOC", "RTF", "Markdown", "HTML", "TXT" })
            sb.Append($"<span>{f}</span>");
        sb.Append($"</div><h2>{T("Shortcuts")}</h2><table class=\"keys\">");
        foreach (var (keys, label) in shortcuts)
            sb.Append($"<tr><td>{keys}</td><td>{label}</td></tr>");
        sb.Append("</table></main></body></html>");
        return sb.ToString();
    }

    private const string Logo = """
<svg class="logo" viewBox="0 0 64 64" aria-hidden="true">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#5C7CFA"/><stop offset="1" stop-color="#15AABF"/></linearGradient></defs>
  <rect width="64" height="64" rx="14" fill="url(#g)"/>
  <path d="M12 19c7-2 14-1 20 3v26c-6-4-13-5-20-3z" fill="#fff"/>
  <path d="M52 19c-7-2-14-1-20 3v26c6-4 13-5 20-3z" fill="#fff" opacity=".82"/>
</svg>
""";

    private const string Style = """
body.lumen-kind-welcome {
  --bg: #ffffff; --fg: #1f2328; --muted: #667085; --chip: #eef1f6; --border: #d0d5dd; --accent: #3b5bdb; --accent-fg: #fff;
  margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
  font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif; background: var(--bg) !important; color: var(--fg) !important;
}
:root[data-lumen-theme="dark"] body.lumen-kind-welcome { --bg: #1b1d21; --fg: #e6e8eb; --muted: #9aa1ab; --chip: #262a31; --border: #3b4048; --accent: #748ffc; --accent-fg: #10131a; }
:root[data-lumen-theme="reading"] body.lumen-kind-welcome { --bg: #f4ecd8; --fg: #3d3123; --muted: #7a6a52; --chip: #ebe0c6; --border: #d6c5a3; --accent: #8b4513; --accent-fg: #fff8ea; }
main { max-width: 560px; margin: 32px; padding: 44px 48px; text-align: center; border: 2px dashed var(--border); border-radius: 20px; }
.logo { width: 72px; height: 72px; }
h1 { font-family: "Segoe UI Variable Display", "Segoe UI", sans-serif; font-weight: 600; font-size: 26px; margin: 18px 0 6px; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 32px 0 10px; font-weight: 600; }
.muted { color: var(--muted); margin: 0 0 22px; }
button { font: inherit; font-weight: 600; padding: 9px 26px; border: 0; border-radius: 8px; background: var(--accent); color: var(--accent-fg); cursor: pointer; }
button:hover { filter: brightness(1.08); }
.formats { display: flex; flex-wrap: wrap; justify-content: center; gap: 6px; }
.formats span { background: var(--chip); padding: 4px 11px; border-radius: 999px; font-size: 13px; }
.keys { margin: 0 auto; border-collapse: collapse; font-size: 13px; text-align: left; }
.keys td { padding: 4px 10px; color: var(--muted); }
.keys td:first-child { text-align: right; white-space: nowrap; color: var(--fg); }
kbd { font-family: inherit; font-size: 12px; background: var(--chip); border: 1px solid var(--border); border-bottom-width: 2px; border-radius: 5px; padding: 1px 6px; }
""";
}
