using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace LumenReader.Localization;

/// <summary>
/// Minimal JSON-based localization. Strings live in Localization/Strings/{code}.json (embedded).
/// Adding a language = adding one JSON file + one line in <see cref="Languages"/>.
/// </summary>
public sealed class Loc
{
    public static Loc I { get; } = new();

    /// <summary>Supported UI languages (code, native name).</summary>
    public static readonly IReadOnlyList<(string Code, string Name)> Languages =
    [
        ("en", "English"),
        ("de", "Deutsch"),
        ("fr", "Français"),
        ("es", "Español"),
        ("it", "Italiano"),
        ("pt", "Português"),
        ("nl", "Nederlands"),
        ("sv", "Svenska"),
        ("pl", "Polski"),
        ("cs", "Čeština"),
        ("sk", "Slovenčina"),
        ("hu", "Magyar"),
        ("ro", "Română"),
        ("hr", "Hrvatski"),
        ("sl", "Slovenščina"),
        ("sr-Latn", "Srpski (latinica)"),
        ("lt", "Lietuvių"),
        ("lv", "Latviešu"),
        ("et", "Eesti"),
    ];

    private readonly Dictionary<string, string> _fallback;
    private Dictionary<string, string> _strings;

    /// <summary>Currently active language code, e.g. "hu".</summary>
    public string Code { get; private set; } = "en";

    public event EventHandler? LanguageChanged;

    private Loc()
    {
        _fallback = LoadStrings("en");
        _strings = _fallback;
    }

    public string this[string key] =>
        _strings.TryGetValue(key, out var v) ? v :
        _fallback.TryGetValue(key, out var f) ? f : key;

    public static string T(string key) => I[key];

    public static string F(string key, params object[] args) => string.Format(CultureInfo.CurrentCulture, I[key], args);

    /// <summary>Applies a language setting: "auto" (follow Windows) or an explicit code.</summary>
    public void Apply(string setting)
    {
        var code = setting == "auto" || !IsSupported(setting) ? DetectSystemLanguage() : setting;
        Code = code;
        _strings = code == "en" ? _fallback : LoadStrings(code);
        LanguageChanged?.Invoke(this, EventArgs.Empty);
    }

    public static bool IsSupported(string code) => Languages.Any(l => l.Code == code);

    /// <summary>Windows display language, mapped to a supported language; English if unavailable.</summary>
    public static string DetectSystemLanguage()
    {
        CultureInfo culture;
        try
        {
            culture = new CultureInfo(GetUserDefaultUILanguage());
        }
        catch
        {
            culture = CultureInfo.CurrentUICulture;
        }
        return Match(culture) ?? "en";
    }

    private static string? Match(CultureInfo culture)
    {
        for (var c = culture; !string.IsNullOrEmpty(c.Name); c = c.Parent)
        {
            // Serbian (either script) -> Latin Serbian, the only Serbian variant we ship.
            if (c.TwoLetterISOLanguageName == "sr") return "sr-Latn";
            if (IsSupported(c.Name)) return c.Name;
            if (IsSupported(c.TwoLetterISOLanguageName)) return c.TwoLetterISOLanguageName;
        }
        return null;
    }

    private static Dictionary<string, string> LoadStrings(string code)
    {
        try
        {
            using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream($"strings.{code}.json");
            if (stream == null) return new();
            return JsonSerializer.Deserialize<Dictionary<string, string>>(stream) ?? new();
        }
        catch
        {
            return new();
        }
    }

    [DllImport("kernel32.dll")]
    private static extern ushort GetUserDefaultUILanguage();
}
