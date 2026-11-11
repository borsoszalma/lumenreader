using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using LumenReader.Localization;
using OpenMcdf;

namespace LumenReader.Documents;

/// <summary>
/// Legacy Word 97-2003 (.doc). Strategy, best quality first:
///  1. Microsoft Word installed → convert to .docx via COM automation
///  2. LibreOffice installed   → convert to .docx headless
///  3. Built-in fallback       → extract the plain text from the binary file (piece table)
/// Files that only carry a .doc extension (RTF, HTML or DOCX inside) are detected first.
/// </summary>
internal static class LegacyDocLoader
{
    public static async Task<DocumentSession> LoadAsync(string path, string title)
    {
        var folder = Path.GetDirectoryName(path);
        switch (Sniff(path))
        {
            case "rtf":
                return new FlowSession(path, title, HtmlUtil.Wrap(title, RtfConverter.ToHtml(path)), folder);
            case "zip":
                return new FlowSession(path, title, HtmlUtil.Wrap(title, await Task.Run(() => DocxConverter.Convert(path))), folder);
            case "html":
                return new HtmlSession(path, title);
        }

        var temp = Path.Combine(Path.GetTempPath(), "LumenReader", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(temp);
        try
        {
            var docx = await ConvertWithWordAsync(path, temp) ?? await ConvertWithLibreOfficeAsync(path, temp);
            if (docx != null)
            {
                var html = await Task.Run(() => DocxConverter.Convert(docx));
                return new FlowSession(path, title, HtmlUtil.Wrap(title, html), folder);
            }
        }
        catch (DocumentLoadException)
        {
            throw;
        }
        catch
        {
            // fall through to the text extractor
        }
        finally
        {
            try { Directory.Delete(temp, true); } catch { /* ignore */ }
        }

        var text = await Task.Run(() => ExtractText(path));
        var body = $"<div class=\"lumen-note\">{HtmlUtil.Esc(Loc.T("DocLimitedPreview"))}</div>" + HtmlUtil.TextToHtml(text);
        return new FlowSession(path, title, HtmlUtil.Wrap(title, body), folder);
    }

    private static string Sniff(string path)
    {
        var head = new byte[8];
        using (var fs = File.OpenRead(path))
            _ = fs.Read(head, 0, head.Length);

        if (head[0] == 0xD0 && head[1] == 0xCF && head[2] == 0x11 && head[3] == 0xE0) return "ole";
        if (head[0] == (byte)'P' && head[1] == (byte)'K') return "zip";
        var start = Encoding.Latin1.GetString(head).TrimStart('﻿', 'ï', '»', '¿', ' ', '\r', '\n', '\t');
        if (start.StartsWith("{\\rtf", StringComparison.Ordinal)) return "rtf";
        if (start.StartsWith('<')) return "html";
        return "ole";
    }

    // ---------- 1. Microsoft Word (COM) ----------

    private static async Task<string?> ConvertWithWordAsync(string path, string outDir)
    {
        var type = Type.GetTypeFromProgID("Word.Application");
        if (type == null) return null;

        var tcs = new TaskCompletionSource<string?>();
        var thread = new Thread(() =>
        {
            dynamic? word = null;
            try
            {
                word = Activator.CreateInstance(type)!;
                word.Visible = false;
                word.DisplayAlerts = 0; // wdAlertsNone
                // FileName, ConfirmConversions, ReadOnly, AddToRecentFiles, PasswordDocument
                dynamic doc = word.Documents.Open(path, false, true, false, "\u0001");
                var output = Path.Combine(outDir, "converted.docx");
                doc.SaveAs2(output, 16); // wdFormatDocumentDefault
                doc.Close(false);
                tcs.TrySetResult(output);
            }
            catch
            {
                tcs.TrySetResult(null);
            }
            finally
            {
                try { word?.Quit(false); } catch { /* ignore */ }
                if (word != null) Marshal.FinalReleaseComObject((object)word);
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.IsBackground = true;
        thread.Start();

        var finished = await Task.WhenAny(tcs.Task, Task.Delay(TimeSpan.FromSeconds(60)));
        return finished == tcs.Task ? await tcs.Task : null;
    }

    // ---------- 2. LibreOffice ----------

    private static async Task<string?> ConvertWithLibreOfficeAsync(string path, string outDir)
    {
        var candidates = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "LibreOffice", "program", "soffice.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "LibreOffice", "program", "soffice.exe"),
        };
        var exe = candidates.FirstOrDefault(File.Exists);
        if (exe == null) return null;

        var psi = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true };
        // Separate profile so a running LibreOffice instance does not interfere.
        psi.ArgumentList.Add("-env:UserInstallation=" + new Uri(Path.Combine(outDir, "lo-profile")).AbsoluteUri);
        psi.ArgumentList.Add("--headless");
        psi.ArgumentList.Add("--norestore");
        psi.ArgumentList.Add("--convert-to");
        psi.ArgumentList.Add("docx");
        psi.ArgumentList.Add("--outdir");
        psi.ArgumentList.Add(outDir);
        psi.ArgumentList.Add(path);

        using var process = Process.Start(psi);
        if (process == null) return null;
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(90));
        try
        {
            await process.WaitForExitAsync(cts.Token);
        }
        catch (OperationCanceledException)
        {
            try { process.Kill(true); } catch { /* ignore */ }
            return null;
        }

        var result = Path.Combine(outDir, Path.GetFileNameWithoutExtension(path) + ".docx");
        return File.Exists(result) ? result : null;
    }

    // ---------- 3. Built-in plain-text extraction ([MS-DOC] piece table) ----------

    internal static string ExtractText(string path)
    {
        using var cf = new CompoundFile(path);
        var wd = cf.RootStorage.GetStream("WordDocument").GetData();
        if (BitConverter.ToUInt16(wd, 0) != 0xA5EC) throw new DocumentLoadException(Loc.T("ErrorTitle"));

        var flags = BitConverter.ToUInt16(wd, 0x0A);
        if ((flags & 0x0100) != 0) throw new DocumentLoadException(Loc.T("PasswordProtected")); // fEncrypted
        var tableName = (flags & 0x0200) != 0 ? "1Table" : "0Table";                        // fWhichTblStm
        var table = cf.RootStorage.GetStream(tableName).GetData();

        var ccpText = BitConverter.ToInt32(wd, 0x4C);  // FibRgLw97.ccpText (main document only)
        var fcClx = BitConverter.ToInt32(wd, 0x1A2);   // FibRgFcLcb97.fcClx
        var lcbClx = BitConverter.ToInt32(wd, 0x1A6);

        var pos = fcClx;
        var end = fcClx + lcbClx;
        while (pos < end && table[pos] == 0x01) // skip Prc (property modifiers)
            pos += 3 + BitConverter.ToInt16(table, pos + 1);
        if (pos >= end || table[pos] != 0x02) throw new DocumentLoadException(Loc.T("ErrorTitle"));

        var lcb = BitConverter.ToInt32(table, pos + 1);
        var plc = pos + 5;
        var pieces = (lcb - 4) / 12;
        var cp1252 = Encoding.GetEncoding(1252);
        var raw = new StringBuilder();

        for (var i = 0; i < pieces; i++)
        {
            var cpStart = BitConverter.ToInt32(table, plc + i * 4);
            var cpEnd = Math.Min(BitConverter.ToInt32(table, plc + (i + 1) * 4), ccpText);
            if (cpStart >= ccpText) break;
            var count = cpEnd - cpStart;
            if (count <= 0) continue;

            var pcd = plc + (pieces + 1) * 4 + i * 8;
            var fcRaw = BitConverter.ToUInt32(table, pcd + 2);
            var compressed = (fcRaw & 0x40000000) != 0;
            var fc = fcRaw & 0x3FFFFFFF;

            if (compressed)
            {
                var offset = (int)(fc / 2);
                raw.Append(cp1252.GetString(wd, offset, Math.Max(0, Math.Min(count, wd.Length - offset))));
            }
            else
            {
                var offset = (int)fc;
                raw.Append(Encoding.Unicode.GetString(wd, offset, Math.Max(0, Math.Min(count * 2, wd.Length - offset))));
            }
        }

        return CleanFieldsAndControls(raw.ToString());
    }

    /// <summary>Hides field codes (0x13 code 0x14 result 0x15) and maps Word control characters.</summary>
    private static string CleanFieldsAndControls(string s)
    {
        var sb = new StringBuilder(s.Length);
        var fields = new Stack<bool>(); // true = currently inside the field *code*
        foreach (var ch in s)
        {
            switch (ch)
            {
                case '\u0013': fields.Push(true); continue;
                case '\u0014': if (fields.Count > 0) { fields.Pop(); fields.Push(false); } continue;
                case '\u0015': if (fields.Count > 0) fields.Pop(); continue;
            }
            if (fields.Contains(true)) continue;

            switch (ch)
            {
                case '\r': case '\u000B': case '\u000C': case '\u0007': sb.Append('\n'); break;
                case '\u001E': sb.Append('-'); break;
                case '\t': sb.Append('\t'); break;
                default:
                    if (ch >= ' ') sb.Append(ch);
                    break;
            }
        }
        return sb.ToString();
    }
}
