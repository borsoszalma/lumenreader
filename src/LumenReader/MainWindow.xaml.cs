using System.Diagnostics;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using LumenReader.Documents;
using LumenReader.Localization;
using LumenReader.Services;
using LumenReader.Theming;
using LumenReader.Viewer;
using Microsoft.Web.WebView2.Core;
using Microsoft.Win32;

namespace LumenReader;

public partial class MainWindow : Window
{
    /// <summary>Virtual origin served entirely by the app (never reaches the network).</summary>
    private const string Host = "lumen.example";
    private const string Origin = "https://" + Host + "/";

    private readonly AppSettings _settings;
    private DocumentSession? _session;
    private string? _pendingPath;
    private string? _scriptId;
    private bool _webReady;
    private bool _updatingUi;
    private int _loadVersion;
    private int _chapterIndex = -1;
    private WindowState _stateBeforeFullscreen = WindowState.Normal;

    public sealed record LanguageOption(string Code, string Name);

    public MainWindow(AppSettings settings, string? startupPath)
    {
        _settings = settings;
        _pendingPath = startupPath;
        InitializeComponent();

        Width = _settings.WindowWidth;
        Height = _settings.WindowHeight;
        if (_settings.WindowMaximized) WindowState = WindowState.Maximized;

        SetTocVisible(_settings.TocVisible);
        SyncThemeButtons();
        UpdateTexts();

        SourceInitialized += (_, _) => ThemeManager.SetDarkTitleBar(this, ThemeManager.Current == ReaderTheme.Dark);
        Loaded += async (_, _) => await InitWebViewAsync();
        Closing += (_, _) => SaveWindowState();
        Closed += (_, _) => _session?.Dispose();
        PreviewKeyDown += OnPreviewKeyDown;
        DragOver += OnWindowDragOver;
        Drop += OnWindowDrop;
        Loc.I.LanguageChanged += (_, _) => OnLanguageChanged();
    }

    // =====================================================================
    //  WebView2 setup
    // =====================================================================

    private async Task InitWebViewAsync()
    {
        try
        {
            var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(AppSettings.DataFolder, "WebView2"));
            await Web.EnsureCoreWebView2Async(env);
        }
        catch (WebView2RuntimeNotFoundException)
        {
            MessageBox.Show(this, Loc.T("WebView2Missing"), "Lumen Reader", MessageBoxButton.OK, MessageBoxImage.Error);
            Close();
            return;
        }

        var core = Web.CoreWebView2;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.IsWebMessageEnabled = true;
        core.Settings.IsGeneralAutofillEnabled = false;
        core.Settings.IsPasswordAutosaveEnabled = false;
#if !DEBUG
        core.Settings.AreDevToolsEnabled = false;
#endif
        core.AddWebResourceRequestedFilter(Origin + "*", CoreWebView2WebResourceContext.All);
        core.WebResourceRequested += OnWebResourceRequested;
        core.WebMessageReceived += OnWebMessageReceived;
        core.NavigationStarting += OnNavigationStarting;
        core.NavigationCompleted += OnNavigationCompleted;
        core.NewWindowRequested += OnNewWindowRequested;

        Web.DefaultBackgroundColor = ThemeManager.PageColor(ThemeManager.Current);
        Web.ZoomFactor = Math.Clamp(_settings.Zoom, 0.25, 5);
        Web.ZoomFactorChanged += (_, _) =>
        {
            _settings.Zoom = Web.ZoomFactor;
            ZoomText.Text = $"{Math.Round(Web.ZoomFactor * 100)}%";
        };
        ZoomText.Text = $"{Math.Round(Web.ZoomFactor * 100)}%";

        _webReady = true;

        if (_pendingPath != null)
        {
            var path = _pendingPath;
            _pendingPath = null;
            await OpenFileAsync(path);
        }
        else
        {
            await ShowWelcomeAsync();
        }
    }

    private async Task RegisterViewerScriptAsync()
    {
        var core = Web.CoreWebView2;
        if (_scriptId != null) core.RemoveScriptToExecuteOnDocumentCreated(_scriptId);
        _scriptId = await core.AddScriptToExecuteOnDocumentCreatedAsync(
            ViewerScript.Build(ThemeManager.Current, ViewerScript.KindName(_session?.Kind), _session?.CollectHeadings ?? false));
    }

    private string SessionUrl(DocumentSession session, string relative)
    {
        var fragment = "";
        var hash = relative.IndexOf('#');
        if (hash >= 0)
        {
            fragment = relative[hash..];
            relative = relative[..hash];
        }
        var escaped = string.Join('/', relative.Split('/').Select(Uri.EscapeDataString));
        return $"{Origin}doc/{session.Id}/{escaped}{fragment}";
    }

    /// <summary>Session-relative path of a viewer URL, or null when it belongs to another session.</summary>
    private string? RelativePath(string url)
    {
        if (_session == null || !Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Host != Host) return null;
        var prefix = $"/doc/{_session.Id}/";
        var path = uri.AbsolutePath;
        return path.StartsWith(prefix, StringComparison.Ordinal) ? Uri.UnescapeDataString(path[prefix.Length..]) : null;
    }

    private async void OnWebResourceRequested(object? sender, CoreWebView2WebResourceRequestedEventArgs e)
    {
        if (!Uri.TryCreate(e.Request.Uri, UriKind.Absolute, out var uri) || uri.Host != Host) return;

        var env = Web.CoreWebView2.Environment;
        var deferral = e.GetDeferral();
        try
        {
            var path = Uri.UnescapeDataString(uri.AbsolutePath.TrimStart('/'));
            ServedResource? resource = null;
            var cache = "Cache-Control: max-age=3600";

            if (path == "welcome.html")
            {
                var bytes = System.Text.Encoding.UTF8.GetBytes(WelcomePage.Build());
                resource = new ServedResource(new MemoryStream(bytes), "text/html; charset=utf-8");
                cache = "Cache-Control: no-store";
            }
            else if (_session != null && path.StartsWith($"doc/{_session.Id}/", StringComparison.Ordinal))
            {
                resource = await _session.ServeAsync(path[$"doc/{_session.Id}/".Length..]);
            }

            e.Response = resource == null
                ? env.CreateWebResourceResponse(null, 404, "Not Found", "")
                : env.CreateWebResourceResponse(resource.Content, 200, "OK", $"Content-Type: {resource.ContentType}\r\n{cache}");
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
            e.Response = env.CreateWebResourceResponse(null, 500, "Error", "");
        }
        finally
        {
            deferral.Complete();
        }
    }

    private void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (!e.Source.StartsWith(Origin, StringComparison.OrdinalIgnoreCase)) return;
        try
        {
            using var json = JsonDocument.Parse(e.WebMessageAsJson);
            var root = json.RootElement;
            switch (root.GetProperty("type").GetString())
            {
                case "drop":
                    var file = e.AdditionalObjects?.OfType<CoreWebView2File>().FirstOrDefault();
                    if (file != null) _ = OpenFileAsync(file.Path);
                    break;

                case "open":
                    _ = ChooseAndOpenAsync();
                    break;

                case "toc" when _session is { CollectHeadings: true }:
                    var headings = root.GetProperty("items").EnumerateArray()
                        .Select(i => (i.GetProperty("level").GetInt32(), i.GetProperty("id").GetString() ?? "",
                            i.GetProperty("text").GetString() ?? ""))
                        .ToList();
                    SetToc(TocItem.FromHeadings(headings));
                    break;
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
        }
    }

    private void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri)) return;
        if (uri.Host == Host && uri.Scheme == Uri.UriSchemeHttps) return; // our own content
        if (uri.Scheme is "about" or "data") return;

        e.Cancel = true;
        if (uri.IsFile)
        {
            // A file dropped where the page did not intercept it.
            _ = OpenFileAsync(uri.LocalPath);
        }
        else if (uri.Scheme is "http" or "https" or "mailto")
        {
            OpenExternal(uri.AbsoluteUri);
        }
    }

    private void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        e.Handled = true;
        if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri)) return;
        if (uri.Host == Host) Web.CoreWebView2.Navigate(e.Uri);
        else if (uri.Scheme is "http" or "https" or "mailto") OpenExternal(uri.AbsoluteUri);
    }

    private void OnNavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e)
    {
        ShowBusy(false);

        if (_session is EpubSession epub && RelativePath(Web.Source?.ToString() ?? "") is string rel)
        {
            var index = epub.IndexOf(rel);
            if (index >= 0) _chapterIndex = index;
            UpdateChapterButtons();
        }
        else if (_session is HtmlSession html && e.IsSuccess)
        {
            html.UpdateTitle(Web.CoreWebView2.DocumentTitle);
            UpdateTitle();
        }
    }

    private static void OpenExternal(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch
        {
            // ignore
        }
    }

    // =====================================================================
    //  Opening documents
    // =====================================================================

    public async Task OpenFileAsync(string path)
    {
        if (!_webReady)
        {
            _pendingPath = path;
            return;
        }
        if (!File.Exists(path)) return;
        if (!DocumentLoader.IsSupported(path))
        {
            ShowError(Loc.F("UnsupportedFormat", Path.GetExtension(path)));
            return;
        }

        var version = ++_loadVersion;
        ShowBusy(true);
        try
        {
            var session = await DocumentLoader.LoadAsync(path);
            if (version != _loadVersion)
            {
                session.Dispose();
                return;
            }

            var old = _session;
            _session = session;
            old?.Dispose();

            _chapterIndex = session is EpubSession ? 0 : -1;
            _settings.LastFolder = Path.GetDirectoryName(path);
            UpdateTitle();
            SetToc(session.Toc, waitForHeadings: session.CollectHeadings);
            UpdateChapterButtons();

            await RegisterViewerScriptAsync();
            Web.CoreWebView2.Navigate(SessionUrl(session, session.StartPath));
        }
        catch (DocumentLoadException ex)
        {
            ShowBusy(false);
            ShowError(ex.Message);
        }
        catch (Exception ex)
        {
            ShowBusy(false);
            ShowError(ex.Message);
        }
    }

    private async Task ChooseAndOpenAsync()
    {
        var patterns = string.Join(";", DocumentLoader.Extensions.Select(x => "*" + x));
        var dialog = new OpenFileDialog
        {
            Filter = $"{Loc.T("FileFilterAll")}|{patterns}|EPUB|*.epub|PDF|*.pdf|Word|*.docx;*.doc|RTF|*.rtf|" +
                     "Markdown|*.md;*.markdown;*.mdown;*.mkd|HTML|*.html;*.htm;*.xhtml|TXT|*.txt|*.*|*.*",
            InitialDirectory = Directory.Exists(_settings.LastFolder) ? _settings.LastFolder : "",
        };
        if (dialog.ShowDialog(this) == true)
            await OpenFileAsync(dialog.FileName);
    }

    private async Task ShowWelcomeAsync()
    {
        _session?.Dispose();
        _session = null;
        _chapterIndex = -1;
        UpdateTitle();
        SetToc([]);
        UpdateChapterButtons();
        await RegisterViewerScriptAsync();
        Web.CoreWebView2.Navigate($"{Origin}welcome.html?lang={Loc.I.Code}");
    }

    private void ShowError(string message) =>
        MessageBox.Show(this, $"{Loc.T("ErrorTitle")}\n\n{message}", "Lumen Reader", MessageBoxButton.OK, MessageBoxImage.Warning);

    private void ShowBusy(bool busy)
    {
        BusyText.Text = Loc.T("Loading");
        BusyOverlay.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        Web.Visibility = busy ? Visibility.Hidden : Visibility.Visible;
    }

    private void UpdateTitle()
    {
        TitleText.Text = _session?.Title ?? "";
        Title = _session == null ? "Lumen Reader" : $"{_session.Title} – Lumen Reader";
    }

    // =====================================================================
    //  Navigation panel (table of contents)
    // =====================================================================

    private void SetToc(List<TocItem> items, bool waitForHeadings = false)
    {
        TocTree.ItemsSource = items;
        NoTocText.Visibility = _session != null && items.Count == 0 && !waitForHeadings
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    private void TocTree_PreviewMouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        // Only react to clicks on the title, not on the expander arrow.
        if (e.OriginalSource is FrameworkElement { DataContext: TocItem item } fe && fe is not ToggleButton
            && FindParent<ToggleButton>(fe) == null)
            NavigateTo(item);
    }

    private void TocTree_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && TocTree.SelectedItem is TocItem item)
        {
            NavigateTo(item);
            e.Handled = true;
        }
    }

    private void NavigateTo(TocItem item)
    {
        if (_session == null || !_webReady) return;

        if (item.AnchorId != null)
        {
            ScrollToId(item.AnchorId);
            return;
        }
        if (item.Href == null) return;

        var hash = item.Href.IndexOf('#');
        var target = hash >= 0 ? item.Href[..hash] : item.Href;
        var fragment = hash >= 0 ? item.Href[(hash + 1)..] : null;

        var current = RelativePath(Web.Source?.ToString() ?? "");
        if (current != null && string.Equals(current, target, StringComparison.OrdinalIgnoreCase))
        {
            if (fragment != null) ScrollToId(Uri.UnescapeDataString(fragment));
            else _ = Web.CoreWebView2.ExecuteScriptAsync("window.__lumen && window.__lumen.scrollTop()");
        }
        else
        {
            Web.CoreWebView2.Navigate(SessionUrl(_session, item.Href));
        }
    }

    private void ScrollToId(string id) =>
        _ = Web.CoreWebView2.ExecuteScriptAsync($"window.__lumen && window.__lumen.scrollToId({JsonSerializer.Serialize(id)})");

    private void TocToggle_Click(object sender, RoutedEventArgs e) => SetTocVisible(TocToggle.IsChecked == true);

    private void SetTocVisible(bool visible)
    {
        _settings.TocVisible = visible;
        TocToggle.IsChecked = visible;
        TocPanel.Visibility = visible ? Visibility.Visible : Visibility.Collapsed;
        TocSplitter.Visibility = TocPanel.Visibility;
        TocColumn.Width = visible ? new GridLength(Math.Clamp(_settings.TocWidth, 160, 600)) : new GridLength(0);
    }

    private void TocSplitter_DragCompleted(object sender, DragCompletedEventArgs e)
    {
        if (TocColumn.ActualWidth > 0) _settings.TocWidth = TocColumn.ActualWidth;
    }

    private static T? FindParent<T>(DependencyObject child) where T : DependencyObject
    {
        for (var d = System.Windows.Media.VisualTreeHelper.GetParent(child); d != null; d = System.Windows.Media.VisualTreeHelper.GetParent(d))
        {
            if (d is T match) return match;
            if (d is TreeViewItem) return null;
        }
        return null;
    }

    // =====================================================================
    //  EPUB chapters
    // =====================================================================

    private void UpdateChapterButtons()
    {
        var epub = _session as EpubSession;
        PrevChapterButton.IsEnabled = epub != null && _chapterIndex > 0;
        NextChapterButton.IsEnabled = epub != null && _chapterIndex >= 0 && _chapterIndex < epub.Spine.Count - 1;
    }

    private void GoToChapter(int delta)
    {
        if (_session is not EpubSession epub) return;
        var target = _chapterIndex + delta;
        if (target < 0 || target >= epub.Spine.Count) return;
        _chapterIndex = target;
        Web.CoreWebView2.Navigate(SessionUrl(epub, epub.Spine[target]));
        UpdateChapterButtons();
    }

    private void PrevChapter_Click(object sender, RoutedEventArgs e) => GoToChapter(-1);
    private void NextChapter_Click(object sender, RoutedEventArgs e) => GoToChapter(1);

    // =====================================================================
    //  Themes, zoom, language
    // =====================================================================

    private async void ApplyTheme(ReaderTheme theme)
    {
        ThemeManager.Apply(theme, this);
        _settings.Theme = theme;
        SyncThemeButtons();

        if (!_webReady) return;
        Web.DefaultBackgroundColor = ThemeManager.PageColor(theme);
        await RegisterViewerScriptAsync(); // so the next page starts in the right theme
        await Web.CoreWebView2.ExecuteScriptAsync(
            $"window.__lumen && window.__lumen.setTheme('{ViewerScript.ThemeName(theme)}')");
    }

    private void SyncThemeButtons()
    {
        _updatingUi = true;
        ThemeLightButton.IsChecked = ThemeManager.Current == ReaderTheme.Light;
        ThemeDarkButton.IsChecked = ThemeManager.Current == ReaderTheme.Dark;
        ThemeReadingButton.IsChecked = ThemeManager.Current == ReaderTheme.Reading;
        _updatingUi = false;
    }

    private void Theme_Click(object sender, RoutedEventArgs e)
    {
        if (_updatingUi) return;
        if (sender == ThemeDarkButton) ApplyTheme(ReaderTheme.Dark);
        else if (sender == ThemeReadingButton) ApplyTheme(ReaderTheme.Reading);
        else ApplyTheme(ReaderTheme.Light);
    }

    private void CycleTheme() =>
        ApplyTheme(ThemeManager.Current switch
        {
            ReaderTheme.Light => ReaderTheme.Dark,
            ReaderTheme.Dark => ReaderTheme.Reading,
            _ => ReaderTheme.Light,
        });

    private void Zoom(double factor)
    {
        if (_webReady) Web.ZoomFactor = Math.Clamp(Math.Round(Web.ZoomFactor * factor, 2), 0.25, 5);
    }

    private void ZoomIn_Click(object sender, RoutedEventArgs e) => Zoom(1.1);
    private void ZoomOut_Click(object sender, RoutedEventArgs e) => Zoom(1 / 1.1);

    private void ZoomReset_Click(object sender, RoutedEventArgs e)
    {
        if (_webReady) Web.ZoomFactor = 1;
    }

    private void LanguageCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_updatingUi || LanguageCombo.SelectedItem is not LanguageOption option) return;
        _settings.Language = option.Code;
        _settings.Save();
        Loc.I.Apply(option.Code);
    }

    private async void OnLanguageChanged()
    {
        UpdateTexts();
        if (_webReady && _session == null) await ShowWelcomeAsync();
    }

    /// <summary>Sets every UI text from the current language.</summary>
    private void UpdateTexts()
    {
        OpenLabel.Text = Loc.T("Open");
        OpenButton.ToolTip = $"{Loc.T("Open")} (Ctrl+O)";
        TocToggle.ToolTip = $"{Loc.T("Contents")} (Ctrl+B)";
        PrevChapterButton.ToolTip = $"{Loc.T("PrevChapter")} (Alt+←)";
        NextChapterButton.ToolTip = $"{Loc.T("NextChapter")} (Alt+→)";
        ZoomInButton.ToolTip = $"{Loc.T("ZoomIn")} (Ctrl++)";
        ZoomOutButton.ToolTip = $"{Loc.T("ZoomOut")} (Ctrl+−)";
        ZoomResetButton.ToolTip = $"{Loc.T("ResetZoom")} (Ctrl+0)";
        ThemeLightButton.ToolTip = $"{Loc.T("ThemeLight")} (Ctrl+1)";
        ThemeDarkButton.ToolTip = $"{Loc.T("ThemeDark")} (Ctrl+2)";
        ThemeReadingButton.ToolTip = $"{Loc.T("ThemeReading")} (Ctrl+3)";
        LanguageCombo.ToolTip = Loc.T("Language");
        TocHeader.Text = Loc.T("Contents");
        NoTocText.Text = Loc.T("NoContents");
        BusyText.Text = Loc.T("Loading");

        _updatingUi = true;
        var options = new List<LanguageOption> { new("auto", Loc.T("LanguageAuto")) };
        options.AddRange(Loc.Languages.Select(l => new LanguageOption(l.Code, l.Name)));
        LanguageCombo.ItemsSource = options;
        LanguageCombo.SelectedItem = options.FirstOrDefault(o => o.Code == _settings.Language) ?? options[0];
        _updatingUi = false;
    }

    // =====================================================================
    //  Keyboard, drag & drop, full screen, window state
    // =====================================================================

    private void OnPreviewKeyDown(object sender, KeyEventArgs e)
    {
        var key = e.Key == Key.System ? e.SystemKey : e.Key;
        var ctrl = Keyboard.Modifiers.HasFlag(ModifierKeys.Control);
        var alt = Keyboard.Modifiers.HasFlag(ModifierKeys.Alt);
        var handled = true;

        if (ctrl && key == Key.O) _ = ChooseAndOpenAsync();
        else if (ctrl && key == Key.B) SetTocVisible(!_settings.TocVisible);
        else if (ctrl && key is Key.D1 or Key.NumPad1) ApplyTheme(ReaderTheme.Light);
        else if (ctrl && key is Key.D2 or Key.NumPad2) ApplyTheme(ReaderTheme.Dark);
        else if (ctrl && key is Key.D3 or Key.NumPad3) ApplyTheme(ReaderTheme.Reading);
        else if (ctrl && key == Key.T) CycleTheme();
        else if (ctrl && key is Key.OemPlus or Key.Add) Zoom(1.1);
        else if (ctrl && key is Key.OemMinus or Key.Subtract) Zoom(1 / 1.1);
        else if (ctrl && key is Key.D0 or Key.NumPad0) ZoomReset_Click(this, e);
        else if (alt && key == Key.Left) GoToChapter(-1);
        else if (alt && key == Key.Right) GoToChapter(1);
        else if (key == Key.F11) ToggleFullscreen();
        else if (key == Key.Escape && WindowStyle == WindowStyle.None) ToggleFullscreen();
        else handled = false;

        if (handled) e.Handled = true;
    }

    private void ToggleFullscreen()
    {
        if (WindowStyle == WindowStyle.None)
        {
            WindowStyle = WindowStyle.SingleBorderWindow;
            WindowState = _stateBeforeFullscreen;
        }
        else
        {
            _stateBeforeFullscreen = WindowState;
            WindowStyle = WindowStyle.None;
            WindowState = WindowState.Normal; // needed so Maximized covers the taskbar
            WindowState = WindowState.Maximized;
        }
    }

    private void OnWindowDragOver(object sender, DragEventArgs e)
    {
        e.Effects = e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None;
        e.Handled = true;
    }

    private void OnWindowDrop(object sender, DragEventArgs e)
    {
        if (e.Data.GetData(DataFormats.FileDrop) is string[] { Length: > 0 } files)
            _ = OpenFileAsync(files[0]);
    }

    private void OpenButton_Click(object sender, RoutedEventArgs e) => _ = ChooseAndOpenAsync();

    private void SaveWindowState()
    {
        if (WindowStyle == WindowStyle.None) WindowStyle = WindowStyle.SingleBorderWindow;
        _settings.WindowMaximized = WindowState == WindowState.Maximized;
        if (WindowState == WindowState.Normal)
        {
            _settings.WindowWidth = Width;
            _settings.WindowHeight = Height;
        }
        _settings.Save();
    }
}
