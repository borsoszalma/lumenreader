using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using Microsoft.Win32;

namespace LumenReader.Theming;

public enum ReaderTheme
{
    Light,
    Dark,
    /// <summary>Eye-friendly sepia reading mode with serif typography.</summary>
    Reading,
}

public static class ThemeManager
{
    private sealed record Palette(
        string ChromeBackground, string PanelBackground, string Foreground, string Border,
        string Hover, string Selection, string Accent, string Page);

    private static readonly Palette Light = new("#F3F3F3", "#FAFAFA", "#1F1F1F", "#E1E1E1", "#14000000", "#263B5BDB", "#3B5BDB", "#FFFFFF");
    private static readonly Palette Dark = new("#202124", "#1A1B1E", "#E6E6E6", "#34363A", "#1FFFFFFF", "#405C7CFA", "#8EA6FF", "#1B1D21");
    private static readonly Palette Reading = new("#EADFC7", "#F0E6CF", "#3B2F20", "#D6C5A3", "#1A5B4636", "#33A0682A", "#8B4513", "#F4ECD8");

    public static ReaderTheme Current { get; private set; } = ReaderTheme.Light;

    public static void Apply(ReaderTheme theme, Window? window)
    {
        Current = theme;
        var p = theme switch { ReaderTheme.Dark => Dark, ReaderTheme.Reading => Reading, _ => Light };

        var app = Application.Current;
        // Fluent (Windows 11 style) control theme for the standard controls (ComboBox, TreeView, scroll bars...).
        app.ThemeMode = theme == ReaderTheme.Dark ? ThemeMode.Dark : ThemeMode.Light;

        Set(app, "ChromeBackground", p.ChromeBackground);
        Set(app, "PanelBackground", p.PanelBackground);
        Set(app, "ChromeForeground", p.Foreground);
        Set(app, "ChromeBorder", p.Border);
        Set(app, "HoverBrush", p.Hover);
        Set(app, "SelectionBrush", p.Selection);
        Set(app, "AccentBrush", p.Accent);

        if (window != null)
            SetDarkTitleBar(window, theme == ReaderTheme.Dark);
    }

    /// <summary>Page background used by WebView2 before the document paints (avoids white flashes).</summary>
    public static System.Drawing.Color PageColor(ReaderTheme theme)
    {
        var hex = (theme switch { ReaderTheme.Dark => Dark, ReaderTheme.Reading => Reading, _ => Light }).Page;
        var c = (Color)ColorConverter.ConvertFromString(hex);
        return System.Drawing.Color.FromArgb(255, c.R, c.G, c.B);
    }

    public static bool SystemPrefersDark()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            return key?.GetValue("AppsUseLightTheme") is int v && v == 0;
        }
        catch
        {
            return false;
        }
    }

    private static void Set(Application app, string key, string hex)
    {
        var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
        brush.Freeze();
        app.Resources[key] = brush;
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    public static void SetDarkTitleBar(Window window, bool dark)
    {
        var hwnd = new WindowInteropHelper(window).Handle;
        if (hwnd == IntPtr.Zero) return;
        int value = dark ? 1 : 0;
        const int DWMWA_USE_IMMERSIVE_DARK_MODE = 20;
        _ = DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ref value, sizeof(int));
    }
}
