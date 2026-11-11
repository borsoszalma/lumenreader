using System.Text;
using System.Windows;
using LumenReader.Localization;
using LumenReader.Services;
using LumenReader.Theming;

namespace LumenReader;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // Needed for legacy code pages (RTF \ansicpg1250, old .doc files, ...).
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);

        DispatcherUnhandledException += (_, args) =>
        {
            MessageBox.Show(args.Exception.Message, "Lumen Reader", MessageBoxButton.OK, MessageBoxImage.Error);
            args.Handled = true;
        };

        var settings = AppSettings.Load();
        settings.Theme ??= ThemeManager.SystemPrefersDark() ? ReaderTheme.Dark : ReaderTheme.Light;

        Loc.I.Apply(settings.Language);
        ThemeManager.Apply(settings.Theme.Value, null);

        // File passed by Explorer / file association ("Open with").
        var startupFile = e.Args.FirstOrDefault(File.Exists);

        var window = new MainWindow(settings, startupFile);
        MainWindow = window;
        window.Show();
    }
}
