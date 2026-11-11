using System.Text.Json;
using System.Text.Json.Serialization;
using LumenReader.Theming;

namespace LumenReader.Services;

/// <summary>User preferences, stored as JSON in %LOCALAPPDATA%\LumenReader (virtualized when packaged as MSIX).</summary>
public sealed class AppSettings
{
    public ReaderTheme? Theme { get; set; }
    public string Language { get; set; } = "auto";
    public double Zoom { get; set; } = 1.0;
    public bool TocVisible { get; set; } = true;
    public double TocWidth { get; set; } = 290;
    public string? LastFolder { get; set; }
    public double WindowWidth { get; set; } = 1200;
    public double WindowHeight { get; set; } = 800;
    public bool WindowMaximized { get; set; }

    public static string DataFolder { get; } =
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LumenReader");

    private static string FilePath => Path.Combine(DataFolder, "settings.json");

    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        Converters = { new JsonStringEnumConverter() },
    };

    public static AppSettings Load()
    {
        try
        {
            if (File.Exists(FilePath))
                return JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(FilePath), Options) ?? new AppSettings();
        }
        catch
        {
            // Corrupt settings file: start with defaults.
        }
        return new AppSettings();
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(DataFolder);
            File.WriteAllText(FilePath, JsonSerializer.Serialize(this, Options));
        }
        catch
        {
            // Settings are best-effort.
        }
    }
}
