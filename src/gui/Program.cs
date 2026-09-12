using System.Diagnostics;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace SoundvisionToRDShape;

internal static class Program
{
    [STAThread] static void Main() { ApplicationConfiguration.Initialize(); Application.Run(new MainForm()); }
}

internal sealed class MainForm : Form
{
    readonly Dictionary<string, string> strings;
    readonly Panel dropZone = new();
    readonly PictureBox logo = new(), convertButton = new(), mapButton = new();
    readonly Label dropLabel = new(), fileLabel = new();
    readonly CheckBox openResult = new(), batchProcess = new();
    readonly ProgressBar progress = new();
    readonly TextBox log = new();
    readonly Image normal, hover, clicked, mapNormal, mapHover, mapClicked;
    string? inputPath;
    bool busy;

    string S(string key, params object[] args)
    {
        var value = strings.TryGetValue(key, out var found) ? found : key;
        return args.Length == 0 ? value : string.Format(value, args);
    }

    public MainForm()
    {
        var root = AppContext.BaseDirectory;
        var support = Path.Combine(root, "converteur");
        strings = LoadStrings(Path.Combine(support, "strings.en.json"));
        normal = Image.FromFile(Path.Combine(support, "assets", "baguette_normal_alpha.png"));
        hover = Image.FromFile(Path.Combine(support, "assets", "baguette_hover_alpha.png"));
        clicked = Image.FromFile(Path.Combine(support, "assets", "baguette_clicked_alpha.png"));
        mapNormal = Image.FromFile(Path.Combine(support, "assets", "croissant_alpha.png"));
        mapHover = Image.FromFile(Path.Combine(support, "assets", "croissant_hover_alpha.png"));
        mapClicked = Image.FromFile(Path.Combine(support, "assets", "croissant_clicked_alpha.png"));
        var canvas = Color.FromArgb(253, 252, 248);
        Text = S("windowTitle"); MinimumSize = new Size(820, 1050); Size = new Size(900, 1250);
        StartPosition = FormStartPosition.CenterScreen; BackColor = canvas;
        ForeColor = Color.Black; Font = new Font("Segoe UI", 10); AllowDrop = true;

        var heading = new Label { Text = S("windowTitle"), AutoSize = true, BackColor = canvas, Font = new Font("Segoe UI Semibold", 22), Location = new Point(28, 16) };
        dropZone.SetBounds(30, 65, 824, 790); dropZone.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        dropZone.BackColor = canvas; dropZone.BorderStyle = BorderStyle.None; dropZone.AllowDrop = true;
        logo.Image = Image.FromFile(Path.Combine(support, "assets", "laconverteur3.png")); logo.SizeMode = PictureBoxSizeMode.Zoom;
        logo.Dock = DockStyle.Fill; logo.BackColor = canvas; logo.AllowDrop = true;
        dropLabel.Text = S("dropPrompt"); dropLabel.Font = new Font("Segoe UI Semibold", 14); dropLabel.TextAlign = ContentAlignment.MiddleCenter;
        // Leave enough vertical room for high-DPI font metrics; the previous
        // 34 px box clipped the prompt at common Windows scaling factors.
        dropLabel.AutoSize = false; dropLabel.SetBounds(25, 606, 774, 54); dropLabel.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Bottom; dropLabel.BackColor = Color.Transparent;
        fileLabel.Text = S("noFile"); fileLabel.ForeColor = Color.Black; fileLabel.BackColor = Color.Transparent; fileLabel.AutoEllipsis = true; fileLabel.TextAlign = ContentAlignment.MiddleCenter;
        fileLabel.Font = new Font("Segoe UI Semibold", 12); fileLabel.SetBounds(25, 672, 774, 54); fileLabel.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Bottom;
        dropZone.Controls.AddRange([logo, dropLabel, fileLabel]);
        logo.SendToBack();

        openResult.Text = S("openResult"); openResult.Checked = true; openResult.AutoSize = true; openResult.BackColor = canvas; openResult.Anchor = AnchorStyles.Right;
        batchProcess.Text = S("batchProcess"); batchProcess.AutoSize = true; batchProcess.BackColor = canvas; batchProcess.Anchor = AnchorStyles.Left;
        var checkRow = new TableLayoutPanel { BackColor = canvas, ColumnCount = 2, RowCount = 1, Location = new Point(30, 858), Size = new Size(824, 36), Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right };
        checkRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50)); checkRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
        openResult.Margin = new Padding(0, 7, 28, 0); batchProcess.Margin = new Padding(28, 7, 0, 0);
        checkRow.Controls.Add(openResult, 0, 0); checkRow.Controls.Add(batchProcess, 1, 0);
        convertButton.Image = normal; convertButton.SizeMode = PictureBoxSizeMode.Zoom; convertButton.Cursor = Cursors.Hand; convertButton.BackColor = Color.Transparent;
        convertButton.Enabled = false; convertButton.SetBounds(45, 900, 390, 120); convertButton.Anchor = AnchorStyles.Top;
        mapButton.Image = mapNormal; mapButton.SizeMode = PictureBoxSizeMode.Zoom; mapButton.Cursor = Cursors.Hand; mapButton.BackColor = Color.Transparent;
        mapButton.SetBounds(465, 900, 390, 120); mapButton.Anchor = AnchorStyles.Top;
        progress.SetBounds(30, 1035, 824, 8); progress.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        log.Multiline = true; log.ReadOnly = true; log.ScrollBars = ScrollBars.Vertical; log.BackColor = canvas;
        log.ForeColor = Color.FromArgb(35, 35, 35); log.BorderStyle = BorderStyle.FixedSingle; log.SetBounds(30, 1057, 824, 135);
        log.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
        Controls.AddRange([heading, mapButton, dropZone, checkRow, convertButton, progress, log]);

        foreach (Control target in new Control[] { this, dropZone, logo, dropLabel, fileLabel }) { target.DragEnter += OnDragEnter; target.DragDrop += OnDragDrop; target.AllowDrop = true; }
        convertButton.Click += async (_, _) => { if (!busy && inputPath is not null) await ConvertAsync(); };
        convertButton.MouseEnter += (_, _) => { if (convertButton.Enabled && !busy) convertButton.Image = hover; };
        convertButton.MouseLeave += (_, _) => { if (!busy) convertButton.Image = normal; };
        convertButton.MouseDown += (_, _) => { if (convertButton.Enabled && !busy) convertButton.Image = clicked; };
        convertButton.MouseUp += (_, _) => { if (convertButton.Enabled && !busy) convertButton.Image = hover; };
        batchProcess.CheckedChanged += (_, _) => { openResult.Enabled = !batchProcess.Checked; if (batchProcess.Checked) openResult.Checked = false; };
        mapButton.Click += (_, _) =>
        {
            var showApiKeyDialog = (ModifierKeys & Keys.Shift) == Keys.Shift;
            new GeoSelectionForm(AppContext.BaseDirectory, AppendLog, showApiKeyDialog).ShowDialog(this);
        };
        mapButton.MouseEnter += (_, _) => { if (!busy) mapButton.Image = mapHover; };
        mapButton.MouseLeave += (_, _) => { if (!busy) mapButton.Image = mapNormal; };
        mapButton.MouseDown += (_, _) => { if (!busy) mapButton.Image = mapClicked; };
        mapButton.MouseUp += (_, _) => { if (!busy) mapButton.Image = mapHover; };
        Shown += (_, _) => { if (Environment.GetCommandLineArgs().Length > 1) SelectFile(Environment.GetCommandLineArgs()[1]); };
    }

    static Dictionary<string, string> LoadStrings(string path)
    { try { return JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(path)) ?? []; } catch { return []; } }

    void OnDragEnter(object? sender, DragEventArgs e) => e.Effect = !busy && e.Data?.GetDataPresent(DataFormats.FileDrop) == true ? DragDropEffects.Copy : DragDropEffects.None;
    void OnDragDrop(object? sender, DragEventArgs e) { if (!busy && e.Data?.GetData(DataFormats.FileDrop) is string[] files && files.Length > 0) SelectFile(files[0]); }

    void SelectFile(string path)
    {
        if (!File.Exists(path) || !new[] { ".xmlp", ".xmls", ".dbacv", ".dbpr", ".fc3", ".eprj" }.Contains(Path.GetExtension(path).ToLowerInvariant()))
        { MessageBox.Show(this, S("invalidFile"), S("invalidFileTitle"), MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
        inputPath = Path.GetFullPath(path); fileLabel.Text = Path.GetFileName(inputPath); fileLabel.ForeColor = Color.Black;
        dropLabel.Text = S("dropPrompt"); convertButton.Enabled = true; log.Clear(); AppendLog(S("ready"));
    }

    async Task ConvertAsync()
    {
        if (inputPath is null) return;
        var root = AppContext.BaseDirectory;
        var support = Path.Combine(root, "converteur");
        var python = Path.Combine(support, "runtime", "python", "python.exe");
        var converter = Path.Combine(support, "converteur.py");
        var occt = Path.Combine(support, "runtime", "occt");
        if (!File.Exists(python) || !File.Exists(converter) || !Directory.Exists(occt))
        { MessageBox.Show(this, S("runtimeMissing"), S("runtimeMissingTitle"), MessageBoxButtons.OK, MessageBoxIcon.Error); return; }
        var extensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { ".xmlp", ".xmls", ".dbacv", ".dbpr", ".fc3", ".eprj" };
        var inputs = batchProcess.Checked
            ? Directory.EnumerateFiles(Path.GetDirectoryName(inputPath)!)
                .Where(path => extensions.Contains(Path.GetExtension(path)))
                .GroupBy(path => Path.ChangeExtension(path, null), StringComparer.OrdinalIgnoreCase)
                .Select(group => group.OrderBy(path => Array.IndexOf(new[] { ".dbpr", ".dbacv", ".xmlp", ".xmls", ".fc3", ".eprj" }, Path.GetExtension(path).ToLowerInvariant())).First())
                .OrderBy(path => path).ToArray()
            : new[] { inputPath };
        var singleOutput = Path.ChangeExtension(inputPath, ".s3d");
        if (!batchProcess.Checked && File.Exists(singleOutput) && MessageBox.Show(this, S("overwrite", singleOutput), S("overwriteTitle"), MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
        SetBusy(true);
        if (batchProcess.Checked) AppendLog(S("batchStart", inputs.Length, Path.GetDirectoryName(inputPath)!));
        var succeeded = 0; var failed = 0;
        try
        {
            foreach (var input in inputs)
            {
                var output = Path.ChangeExtension(input, ".s3d");
                AppendLog(S("converting", Path.GetFileName(input)));
                try { await ConvertFileAsync(support, python, converter, occt, input, output); succeeded++; }
                catch (Exception ex)
                {
                    failed++; AppendLog(S("error", $"{Path.GetFileName(input)}: {ex.Message}"));
                    if (!batchProcess.Checked) throw;
                }
            }
            if (batchProcess.Checked) AppendLog(S("batchFinished", succeeded, failed));
            else if (openResult.Checked) { AppendLog(S("opening")); Process.Start(new ProcessStartInfo(singleOutput) { UseShellExecute = true }); }
        }
        catch (Exception ex) { MessageBox.Show(this, ex.Message, S("errorTitle"), MessageBoxButtons.OK, MessageBoxIcon.Error); }
        finally { SetBusy(false); }
    }

    async Task ConvertFileAsync(string support, string python, string converter, string occt, string input, string output)
    {
            var psi = new ProcessStartInfo(python) { WorkingDirectory = support, UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
            foreach (var value in new[] { converter, input, output, "--occt", occt }) psi.ArgumentList.Add(value);
            psi.Environment["PATH"] = Path.Combine(support, "runtime", "openssl") + Path.PathSeparator + (psi.Environment["PATH"] ?? "");
            psi.Environment["PYTHONUNBUFFERED"] = "1";
            using var process = Process.Start(psi) ?? throw new InvalidOperationException(S("converterStartFailed"));
            var stdoutTask = PumpAsync(process.StandardOutput);
            var stderrTask = PumpAsync(process.StandardError);
            await process.WaitForExitAsync();
            await Task.WhenAll(stdoutTask, stderrTask);
            if (process.ExitCode != 0 || !File.Exists(output)) throw new InvalidOperationException(S("conversionFailed", process.ExitCode));
            progress.Style = ProgressBarStyle.Continuous; progress.Value = 100; AppendLog(S("finished", output));
    }

    void SetBusy(bool value)
    {
        busy = value; convertButton.Enabled = !busy && inputPath is not null; convertButton.Image = normal;
        batchProcess.Enabled = !busy; if (!batchProcess.Checked) openResult.Enabled = !busy;
        mapButton.Enabled = !busy; mapButton.Image = mapNormal;
        AllowDrop = dropZone.AllowDrop = logo.AllowDrop = !busy; progress.Value = 0;
        progress.Style = busy ? ProgressBarStyle.Marquee : ProgressBarStyle.Continuous;
    }
    async Task PumpAsync(StreamReader reader)
    {
        while (await reader.ReadLineAsync() is { } line)
        {
            line = Regex.Replace(line, @"(?:Draw\[\d+\]>\s*)+", "").Trim();
            if (!string.IsNullOrWhiteSpace(line)) AppendLog(line);
        }
    }

    void AppendLog(string value)
    {
        if (InvokeRequired) { BeginInvoke(() => AppendLog(value)); return; }
        log.AppendText($"[{DateTime.Now:HH:mm:ss}] {value.Replace("\n", Environment.NewLine)}{Environment.NewLine}");
        log.SelectionStart = log.TextLength;
        log.ScrollToCaret();
    }
}
