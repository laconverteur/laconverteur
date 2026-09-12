using System.Diagnostics;
using System.Globalization;
using System.Net.Http.Headers;
using System.Text.Json;

namespace SoundvisionToRDShape;

internal sealed class GeoSelectionForm : Form
{
    readonly string support;
    readonly Action<string> mainLog;
    readonly ComboBox address = new() { Width = 620, DropDownStyle = ComboBoxStyle.DropDown };
    readonly Button searchButton = new() { Text = "Search address", AutoSize = true };
    readonly TextBox latitude = Coordinate("50.941066");
    readonly TextBox longitude = Coordinate("6.957008");
    readonly NumericUpDown width = Number(200, 50, 2000, 0);
    readonly NumericUpDown height = Number(200, 50, 2000, 0);
    readonly NumericUpDown heading = Number(0, 0, 359, 0);
    readonly CheckBox terrain = new() { Text = "Include topography", Checked = true, AutoSize = true };
    readonly CheckBox smoothTerrain = new() { Text = "Smooth topography", Checked = false, AutoSize = true };
    readonly CheckBox buildings = new() { Text = "Include buildings", Checked = true, AutoSize = true };
    readonly PictureBox preview = new() { Dock = DockStyle.Fill, SizeMode = PictureBoxSizeMode.Zoom, BackColor = Color.FromArgb(45, 45, 45), Cursor = Cursors.Cross };
    readonly Label status = new() { AutoSize = false, TextAlign = ContentAlignment.MiddleLeft, Dock = DockStyle.Fill, Text = "Enter coordinates and load the preview." };
    readonly Button previewButton = new() { Text = "Load preview", AutoSize = true };
    readonly Button createButton = new() { Text = "Create venue", AutoSize = true };
    double? previewLatitude, previewLongitude;
    double previewExtentWidth, previewExtentHeight;
    double pinX = 0.5, pinY = 0.5;
    bool draggingDirection, draggingBox;
    double dragOffsetX, dragOffsetY;
    bool busy;

    public GeoSelectionForm(string applicationRoot, Action<string> log, bool showApiKeyDialog = false)
    {
        support = Path.Combine(applicationRoot, "converteur");
        mainLog = log;
        Text = "Create venue from map";
        StartPosition = FormStartPosition.CenterParent;
        MinimumSize = new Size(800, 650);
        Size = new Size(1050, 820);
        Font = new Font("Segoe UI", 10);
        AutoScaleMode = AutoScaleMode.Dpi;

        var searchRow = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Padding = new Padding(8, 12, 8, 8) };
        searchRow.Controls.Add(new Label { Text = "Address", AutoSize = true, Margin = new Padding(8, 7, 3, 0) });
        searchRow.Controls.Add(address);
        searchRow.Controls.Add(searchButton);
        var controls = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = false, WrapContents = false, FlowDirection = FlowDirection.LeftToRight, Padding = new Padding(8, 10, 8, 8) };
        AddField(controls, "Latitude", latitude);
        AddField(controls, "Longitude", longitude);
        AddField(controls, "Width (m)", width);
        AddField(controls, "Height (m)", height);
        AddField(controls, "Direction (°)", heading);
        var optionRow = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Padding = new Padding(16, 10, 8, 8) };
        optionRow.Controls.Add(terrain);
        optionRow.Controls.Add(smoothTerrain);
        optionRow.Controls.Add(buildings);
        smoothTerrain.Margin = new Padding(28, 3, 3, 3);
        buildings.Margin = new Padding(28, 3, 3, 3);
        var actionRow = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Padding = new Padding(16, 10, 8, 8) };
        actionRow.Controls.Add(previewButton);
        actionRow.Controls.Add(createButton);
        createButton.Margin = new Padding(18, 3, 3, 3);

        var hint = new Label { Dock = DockStyle.Fill, Text = "Click the preview to move its centre, then reload it.", TextAlign = ContentAlignment.MiddleLeft, Padding = new Padding(10, 0, 0, 0) };
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 7 };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 64));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 58));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 52));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 60));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
        layout.Controls.Add(searchRow, 0, 0);
        layout.Controls.Add(controls, 0, 1);
        layout.Controls.Add(optionRow, 0, 2);
        layout.Controls.Add(actionRow, 0, 3);
        layout.Controls.Add(hint, 0, 4);
        layout.Controls.Add(preview, 0, 5);
        layout.Controls.Add(status, 0, 6);
        Controls.Add(layout);

        previewButton.Click += async (_, _) => await LoadPreviewAsync();
        createButton.Click += async (_, _) => await CreateVenueAsync();
        searchButton.Click += async (_, _) => await SearchAddressAsync();
        terrain.CheckedChanged += (_, _) => smoothTerrain.Enabled = terrain.Checked && !busy;
        width.ValueChanged += (_, _) => preview.Invalidate();
        height.ValueChanged += (_, _) => preview.Invalidate();
        heading.ValueChanged += (_, _) => preview.Invalidate();
        address.SelectedIndexChanged += async (_, _) =>
        {
            if (address.SelectedItem is not AddressResult result) return;
            latitude.Text = Invariant(result.Latitude);
            longitude.Text = Invariant(result.Longitude);
            await LoadPreviewAsync();
        };
        preview.MouseDown += PreviewMouseDown;
        preview.MouseMove += PreviewMouseMove;
        preview.MouseUp += PreviewMouseUp;
        preview.MouseLeave += (_, _) => { if (!busy && !draggingDirection && !draggingBox) preview.Cursor = Cursors.Cross; };
        preview.Paint += PreviewPaint;
        Shown += (_, _) => BeginInvoke(() => EnsureOpenTopographyKey(showApiKeyDialog));
    }

    static NumericUpDown Number(decimal value, decimal minimum, decimal maximum, int decimals) => new NumericUpDown
    {
        Minimum = minimum, Maximum = maximum, DecimalPlaces = decimals, Value = value,
        Increment = decimals == 0 ? 25 : 0.0001m, Width = decimals == 0 ? 72 : 112,
    };

    static TextBox Coordinate(string value) => new() { Text = value, Width = 112 };

    static void AddField(FlowLayoutPanel row, string label, Control control)
    {
        row.Controls.Add(new Label { Text = label, AutoSize = true, Margin = new Padding(8, 7, 3, 0) });
        row.Controls.Add(control);
    }

    async Task LoadPreviewAsync()
    {
        try
        {
            var package = Path.Combine(Path.GetTempPath(), "LaConverteur", "geo-preview");
            await AcquireAsync(package, false, false, true);
            var imagePath = Path.Combine(package, "imagery.png");
            if (!File.Exists(imagePath)) return;
            using var source = Image.FromFile(imagePath);
            var replacement = new Bitmap(source);
            previewExtentWidth = (double)width.Value * 3.0;
            previewExtentHeight = (double)height.Value * 3.0;
            var old = preview.Image;
            preview.Image = replacement;
            old?.Dispose();
            previewLatitude = ReadCoordinate(latitude, "Latitude", -84.9, 84.9);
            previewLongitude = ReadCoordinate(longitude, "Longitude", -179.9, 179.9);
            pinX = pinY = 0.5;
            preview.Invalidate();
            SetStatus("Preview loaded. Click the image to choose a new centre.", true);
        }
        catch (Exception ex) { ShowError(ex); }
        finally { SetBusy(false); }
    }

    async Task SearchAddressAsync()
    {
        var query = address.Text.Trim();
        if (query.Length < 3)
        {
            MessageBox.Show(this, "Please enter at least three characters.", "Address search", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        SetBusy(true);
        SetStatus("Searching address…", true);
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
            client.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("La-Converteur", "0.1"));
            client.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("(contact: laconverteur@proton.me)"));
            var url = "https://nominatim.openstreetmap.org/search?format=jsonv2&addressdetails=1&limit=8&q=" + Uri.EscapeDataString(query);
            using var response = await client.GetAsync(url);
            response.EnsureSuccessStatusCode();
            using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
            var results = document.RootElement.EnumerateArray().Select(item => new AddressResult(
                item.GetProperty("display_name").GetString() ?? query,
                double.Parse(item.GetProperty("lat").GetString()!, CultureInfo.InvariantCulture),
                double.Parse(item.GetProperty("lon").GetString()!, CultureInfo.InvariantCulture)
            )).ToArray();
            address.Items.Clear();
            address.Items.AddRange(results);
            if (results.Length == 0) SetStatus("No address matches found.", true);
            else
            {
                SetStatus($"{results.Length} address matches found. Select one from the list.", true);
                SetBusy(false);
                address.DroppedDown = true;
            }
        }
        catch (Exception ex) { ShowError(ex); }
        finally { SetBusy(false); }
    }

    async Task CreateVenueAsync()
    {
        if (terrain.Checked && !EnsureOpenTopographyKey(false)) return;
        var package = Path.Combine(Path.GetTempPath(), "LaConverteur", "geo-" + Guid.NewGuid().ToString("N"));
        var venueDirectory = Path.Combine(Path.GetTempPath(), "LaConverteur", "venues");
        Directory.CreateDirectory(venueDirectory);
        var output = Path.Combine(venueDirectory, $"map-venue-{DateTime.Now:yyyyMMdd-HHmmss}.s3d");
        try
        {
            await AcquireAsync(package, terrain.Checked, buildings.Checked);
            SetStatus("Creating RDShape venue…", true);
            var python = Path.Combine(support, "runtime", "python", "python.exe");
            var writer = Path.Combine(support, "geo", "geo_to_s3d.py");
            var terrainStep = smoothTerrain.Checked ? "12.5" : "25";
            var arguments = new[] { writer, package, output, "--occt", Path.Combine(support, "runtime", "occt"), "--templates", Path.Combine(support, "rdshape"), "--limit", "0", "--terrain-step", terrainStep, "--heading", Invariant(heading.Value) };
            await RunAsync(python, arguments);
            SetStatus("Opening map venue in RDShape…", true);
            mainLog("Finished map venue: " + output);
            Process.Start(new ProcessStartInfo(output) { UseShellExecute = true });
        }
        catch (Exception ex) { ShowError(ex); }
        finally { SetBusy(false); }
    }

    async Task AcquireAsync(string package, bool includeTerrain, bool includeBuildings = false, bool previewOnly = false)
    {
        SetBusy(true);
        var parts = new List<string> { "imagery" };
        if (includeBuildings) parts.Add("buildings");
        if (includeTerrain) parts.Add(smoothTerrain.Checked ? "smooth topography" : "topography");
        SetStatus(previewOnly ? "Downloading satellite preview…" : $"Downloading {string.Join(", ", parts)}…", true);
        try
        {
            var python = Path.Combine(support, "runtime", "python", "python.exe");
            var acquisition = Path.Combine(support, "geo", "geo_phase0.py");
            var lat = ReadCoordinate(latitude, "Latitude", -84.9, 84.9);
            var lon = ReadCoordinate(longitude, "Longitude", -179.9, 179.9);
            var requestWidth = previewOnly ? width.Value * 3 : width.Value;
            var requestHeight = previewOnly ? height.Value * 3 : height.Value;
            var arguments = new List<string> { acquisition, "--lat", Invariant(lat), "--lon", Invariant(lon), "--width", Invariant(requestWidth), "--height", Invariant(requestHeight), "--output", package };
            if (previewOnly) arguments.Add("--imagery-only");
            else if (!includeBuildings) arguments.Add("--skip-buildings");
            if (includeTerrain) { arguments.Add("--elevation"); arguments.Add("GEDTM30"); }
            await RunAsync(python, arguments);
        }
        catch { throw; }
    }

    async Task RunAsync(string executable, IEnumerable<string> arguments)
    {
        if (!File.Exists(executable)) throw new FileNotFoundException("Portable Python runtime not found.", executable);
        var psi = new ProcessStartInfo(executable) { WorkingDirectory = support, UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
        foreach (var argument in arguments) psi.ArgumentList.Add(argument);
        psi.Environment["PYTHONUNBUFFERED"] = "1";
        using var process = Process.Start(psi) ?? throw new InvalidOperationException("Geo process could not be started.");
        var stdout = PumpGeoOutputAsync(process.StandardOutput);
        var stderr = PumpGeoOutputAsync(process.StandardError);
        await process.WaitForExitAsync();
        var output = (await stdout).Trim();
        var error = (await stderr).Trim();
        if (process.ExitCode != 0)
        {
            var details = string.IsNullOrWhiteSpace(error) ? output : error;
            mainLog("Map venue error details: " + details);
            var useful = details.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries).LastOrDefault() ?? "Geo operation failed.";
            throw new InvalidOperationException(useful);
        }
    }

    RectangleF PreviewImageRectangle()
    {
        if (preview.Image is null) return RectangleF.Empty;
        var imageRatio = (double)preview.Image.Width / preview.Image.Height;
        var boxRatio = (double)preview.ClientSize.Width / preview.ClientSize.Height;
        var shownWidth = boxRatio > imageRatio ? preview.ClientSize.Height * imageRatio : preview.ClientSize.Width;
        var shownHeight = boxRatio > imageRatio ? preview.ClientSize.Height : preview.ClientSize.Width / imageRatio;
        var left = (preview.ClientSize.Width - shownWidth) / 2;
        var top = (preview.ClientSize.Height - shownHeight) / 2;
        return new RectangleF((float)left, (float)top, (float)shownWidth, (float)shownHeight);
    }

    PointF DirectionEndpoint(RectangleF imageRectangle)
    {
        var centre = new PointF((float)(imageRectangle.Left + pinX * imageRectangle.Width), (float)(imageRectangle.Top + pinY * imageRectangle.Height));
        var extentWidth = previewExtentWidth > 0 ? previewExtentWidth : (double)width.Value * 3;
        var extentHeight = previewExtentHeight > 0 ? previewExtentHeight : (double)height.Value * 3;
        var boxWidth = imageRectangle.Width * (float)((double)width.Value / extentWidth);
        var boxHeight = imageRectangle.Height * (float)((double)height.Value / extentHeight);
        var length = Math.Max(35f, Math.Min(boxWidth, boxHeight) * 0.38f);
        var radians = (double)heading.Value * Math.PI / 180.0;
        return new PointF(centre.X + (float)(Math.Sin(radians) * length), centre.Y - (float)(Math.Cos(radians) * length));
    }

    RectangleF SelectionRectangle(RectangleF imageRectangle)
    {
        var extentWidth = previewExtentWidth > 0 ? previewExtentWidth : (double)width.Value * 3;
        var extentHeight = previewExtentHeight > 0 ? previewExtentHeight : (double)height.Value * 3;
        var selectionWidth = imageRectangle.Width * (float)((double)width.Value / extentWidth);
        var selectionHeight = imageRectangle.Height * (float)((double)height.Value / extentHeight);
        var x = (float)(imageRectangle.Left + pinX * imageRectangle.Width);
        var y = (float)(imageRectangle.Top + pinY * imageRectangle.Height);
        return new RectangleF(x - selectionWidth / 2, y - selectionHeight / 2, selectionWidth, selectionHeight);
    }

    static double DistanceToSegment(PointF point, PointF start, PointF end)
    {
        var dx = end.X - start.X;
        var dy = end.Y - start.Y;
        var lengthSquared = dx * dx + dy * dy;
        if (lengthSquared <= 0.001) return Math.Sqrt(Math.Pow(point.X - start.X, 2) + Math.Pow(point.Y - start.Y, 2));
        var t = Math.Max(0, Math.Min(1, ((point.X - start.X) * dx + (point.Y - start.Y) * dy) / lengthSquared));
        var nearestX = start.X + t * dx;
        var nearestY = start.Y + t * dy;
        return Math.Sqrt(Math.Pow(point.X - nearestX, 2) + Math.Pow(point.Y - nearestY, 2));
    }

    bool IsDirectionHit(Point location, RectangleF imageRectangle)
    {
        var centre = new PointF((float)(imageRectangle.Left + pinX * imageRectangle.Width), (float)(imageRectangle.Top + pinY * imageRectangle.Height));
        var arrow = DirectionEndpoint(imageRectangle);
        return DistanceToSegment(location, centre, arrow) <= 20 ||
               Math.Sqrt(Math.Pow(location.X - arrow.X, 2) + Math.Pow(location.Y - arrow.Y, 2)) <= 42;
    }

    void PreviewMouseDown(object? sender, MouseEventArgs e)
    {
        if (busy || preview.Image is null || previewLatitude is null || previewLongitude is null) return;
        var imageRectangle = PreviewImageRectangle();
        if (IsDirectionHit(e.Location, imageRectangle))
        {
            draggingDirection = true;
            preview.Cursor = Cursors.SizeAll;
            return;
        }
        if (!imageRectangle.Contains(e.Location)) return;
        var selection = SelectionRectangle(imageRectangle);
        var normalizedX = (e.X - imageRectangle.Left) / imageRectangle.Width;
        var normalizedY = (e.Y - imageRectangle.Top) / imageRectangle.Height;
        draggingBox = true;
        if (selection.Contains(e.Location))
        {
            dragOffsetX = normalizedX - pinX;
            dragOffsetY = normalizedY - pinY;
        }
        else
        {
            dragOffsetX = dragOffsetY = 0;
            MoveSelection(normalizedX, normalizedY);
        }
        preview.Cursor = Cursors.Hand;
    }

    void MoveSelection(double normalizedX, double normalizedY)
    {
        if (previewLatitude is not double lat || previewLongitude is not double lon) return;
        var halfWidth = (double)width.Value / previewExtentWidth / 2;
        var halfHeight = (double)height.Value / previewExtentHeight / 2;
        normalizedX = Math.Clamp(normalizedX - dragOffsetX, halfWidth, 1 - halfWidth);
        normalizedY = Math.Clamp(normalizedY - dragOffsetY, halfHeight, 1 - halfHeight);
        var east = (normalizedX - 0.5) * previewExtentWidth;
        var north = (0.5 - normalizedY) * previewExtentHeight;
        var nextLatitude = lat + north / 111_320.0;
        var nextLongitude = lon + east / (111_320.0 * Math.Cos(lat * Math.PI / 180));
        if (!double.IsFinite(nextLatitude) || !double.IsFinite(nextLongitude) || Math.Abs(nextLatitude) >= 84.9 || Math.Abs(nextLongitude) >= 179.9) return;
        latitude.Text = Invariant(nextLatitude);
        longitude.Text = Invariant(nextLongitude);
        pinX = normalizedX;
        pinY = normalizedY;
        preview.Invalidate();
    }

    void PreviewMouseMove(object? sender, MouseEventArgs e)
    {
        if (preview.Image is null || busy) return;
        var imageRectangle = PreviewImageRectangle();
        if (draggingDirection)
        {
            var centreX = imageRectangle.Left + pinX * imageRectangle.Width;
            var centreY = imageRectangle.Top + pinY * imageRectangle.Height;
            var angle = Math.Atan2(e.X - centreX, centreY - e.Y) * 180.0 / Math.PI;
            if (angle < 0) angle += 360;
            heading.Value = Math.Min(heading.Maximum, Math.Max(heading.Minimum, (decimal)Math.Round(angle)));
            preview.Cursor = Cursors.SizeAll;
        }
        else if (draggingBox)
        {
            MoveSelection((e.X - imageRectangle.Left) / imageRectangle.Width, (e.Y - imageRectangle.Top) / imageRectangle.Height);
            preview.Cursor = Cursors.Hand;
        }
        else if (IsDirectionHit(e.Location, imageRectangle)) preview.Cursor = Cursors.SizeAll;
        else if (SelectionRectangle(imageRectangle).Contains(e.Location)) preview.Cursor = Cursors.Hand;
        else preview.Cursor = Cursors.Cross;
    }

    void PreviewMouseUp(object? sender, MouseEventArgs e)
    {
        var moved = draggingBox;
        draggingDirection = draggingBox = false;
        if (moved) SetStatus("Selection moved. Reload preview for a newly centred context, or create the venue directly.");
        PreviewMouseMove(sender, e);
    }

    static string Invariant(decimal value) => value.ToString(CultureInfo.InvariantCulture);
    static string Invariant(double value) => value.ToString("0.######", CultureInfo.InvariantCulture);
    static double ReadCoordinate(TextBox field, string name, double minimum, double maximum)
    {
        var normalized = field.Text.Trim().Replace(',', '.');
        if (!double.TryParse(normalized, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) || !double.IsFinite(value) || value < minimum || value > maximum)
            throw new InvalidOperationException($"{name} must be a number between {minimum} and {maximum}. Both decimal point and comma are accepted.");
        return value;
    }

    void PreviewPaint(object? sender, PaintEventArgs e)
    {
        if (preview.Image is null) return;
        var imageRatio = (double)preview.Image.Width / preview.Image.Height;
        var boxRatio = (double)preview.ClientSize.Width / preview.ClientSize.Height;
        var shownWidth = boxRatio > imageRatio ? preview.ClientSize.Height * imageRatio : preview.ClientSize.Width;
        var shownHeight = boxRatio > imageRatio ? preview.ClientSize.Height : preview.ClientSize.Width / imageRatio;
        var left = (preview.ClientSize.Width - shownWidth) / 2;
        var top = (preview.ClientSize.Height - shownHeight) / 2;
        var x = (float)(left + pinX * shownWidth);
        var y = (float)(top + pinY * shownHeight);
        e.Graphics.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        var imageRectangle = new RectangleF((float)left, (float)top, (float)shownWidth, (float)shownHeight);
        var selection = SelectionRectangle(imageRectangle);
        using var outside = new Region(imageRectangle);
        outside.Exclude(selection);
        using var shade = new SolidBrush(Color.FromArgb(145, 65, 68, 72));
        e.Graphics.FillRegion(shade, outside);
        using var selectionOutline = new Pen(Color.Gold, 3);
        e.Graphics.DrawRectangle(selectionOutline, selection.X, selection.Y, selection.Width, selection.Height);
        var arrow = DirectionEndpoint(imageRectangle);
        using var arrowPen = new Pen(Color.FromArgb(245, 220, 35, 35), 3);
        using var arrowHead = new System.Drawing.Drawing2D.AdjustableArrowCap(10, 13, true);
        arrowPen.CustomEndCap = arrowHead;
        e.Graphics.DrawLine(arrowPen, x, y, arrow.X, arrow.Y);
        using var fill = new SolidBrush(Color.FromArgb(235, 220, 35, 35));
        using var outline = new Pen(Color.White, 2);
        using var stemOutline = new Pen(Color.White, 3);
        using var stem = new Pen(Color.FromArgb(235, 220, 35, 35), 2);
        e.Graphics.FillEllipse(fill, x - 8, y - 18, 16, 16);
        e.Graphics.DrawEllipse(outline, x - 8, y - 18, 16, 16);
        e.Graphics.DrawLine(stemOutline, x, y - 2, x, y + 7);
        e.Graphics.DrawLine(stem, x, y - 3, x, y + 8);
    }

    bool EnsureOpenTopographyKey(bool force)
    {
        const string variable = "LACONVERTEUR_OPENTOPOGRAPHY_API_KEY";
        var key = Environment.GetEnvironmentVariable(variable)
            ?? Environment.GetEnvironmentVariable(variable, EnvironmentVariableTarget.User)
            ?? "";
        if (!force && !string.IsNullOrWhiteSpace(key)) return true;
        using var dialog = new OpenTopographyKeyForm(support, key);
        return dialog.ShowDialog(this) == DialogResult.OK;
    }
    void SetBusy(bool value)
    {
        busy = value;
        previewButton.Enabled = createButton.Enabled = searchButton.Enabled = address.Enabled = !value;
        latitude.Enabled = longitude.Enabled = width.Enabled = height.Enabled = heading.Enabled = !value;
        terrain.Enabled = buildings.Enabled = !value;
        smoothTerrain.Enabled = !value && terrain.Checked;
        Application.UseWaitCursor = value;
        UseWaitCursor = value;
        Cursor = value ? Cursors.WaitCursor : Cursors.Default;
        preview.Cursor = value ? Cursors.WaitCursor : Cursors.Cross;
        if (!value) Cursor.Current = Cursors.Default;
    }
    async Task<string> PumpGeoOutputAsync(StreamReader reader)
    {
        var lines = new List<string>();
        while (await reader.ReadLineAsync() is { } line)
        {
            line = line.Trim();
            if (line.Length == 0) continue;
            lines.Add(line);
            mainLog("[Map] " + line);
        }
        return string.Join(Environment.NewLine, lines);
    }

    void SetStatus(string value, bool log = false)
    {
        status.Text = value;
        if (log) mainLog("[Map] " + value);
    }

    void ShowError(Exception ex) { SetStatus("Error: " + ex.Message, true); MessageBox.Show(this, ex.Message, "Map venue error", MessageBoxButtons.OK, MessageBoxIcon.Error); SetBusy(false); }
}

internal sealed record AddressResult(string Label, double Latitude, double Longitude)
{
    public override string ToString() => Label;
}
