using System.Diagnostics;

namespace SoundvisionToRDShape;

internal sealed class OpenTopographyKeyForm : Form
{
    const string Variable = "LACONVERTEUR_OPENTOPOGRAPHY_API_KEY";
    const string OsmDonationUrl = "https://supporting.openstreetmap.org/donate/?utm_source=laconverteur";
    const string OpenTopographyDonationUrl = "https://www.opentopography.org/donate?utm_source=laconverteur";
    readonly TextBox apiKey = new() { Dock = DockStyle.Fill, UseSystemPasswordChar = true };

    public OpenTopographyKeyForm(string support, string currentKey)
    {
        Text = "OpenTopography API key";
        StartPosition = FormStartPosition.CenterParent;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MinimizeBox = MaximizeBox = false;
        ClientSize = new Size(680, 620);
        Font = new Font("Segoe UI", 10);
        AutoScaleMode = AutoScaleMode.Dpi;
        apiKey.Text = currentKey;

        var picture = new PictureBox { Dock = DockStyle.Fill, SizeMode = PictureBoxSizeMode.Zoom };
        var imagePath = Path.Combine(support, "assets", "donate.png");
        if (File.Exists(imagePath))
        {
            using var source = Image.FromFile(imagePath);
            picture.Image = new Bitmap(source);
        }

        var input = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, Padding = new Padding(18, 8, 18, 8) };
        input.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        input.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        input.Controls.Add(new Label { Text = "API key", AutoSize = true, Anchor = AnchorStyles.Left, Margin = new Padding(0, 7, 10, 0) }, 0, 0);
        input.Controls.Add(apiKey, 1, 0);

        var donateOsm = new Button { Text = "Donate OSM", AutoSize = true };
        var donateTopography = new Button { Text = "Donate Open Topography", AutoSize = true };
        var save = new Button { Text = "Save Apikey", AutoSize = true };
        var buttons = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.LeftToRight, WrapContents = false, Padding = new Padding(18, 8, 8, 8) };
        buttons.Controls.AddRange([donateOsm, donateTopography, save]);
        donateTopography.Margin = save.Margin = new Padding(14, 3, 3, 3);

        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 3, ColumnCount = 1 };
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 58));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 62));
        layout.Controls.Add(picture, 0, 0);
        layout.Controls.Add(input, 0, 1);
        layout.Controls.Add(buttons, 0, 2);
        Controls.Add(layout);
        AcceptButton = save;

        donateOsm.Click += (_, _) => OpenUrl(OsmDonationUrl);
        donateTopography.Click += (_, _) => OpenUrl(OpenTopographyDonationUrl);
        save.Click += (_, _) => SaveKey();
    }

    static void OpenUrl(string url) => Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });

    void SaveKey()
    {
        var value = apiKey.Text.Trim();
        if (value.Length == 0)
        {
            MessageBox.Show(this, "Please enter an OpenTopography API key.", "API key", MessageBoxButtons.OK, MessageBoxIcon.Information);
            apiKey.Focus();
            return;
        }
        try
        {
            Environment.SetEnvironmentVariable(Variable, value, EnvironmentVariableTarget.User);
            Environment.SetEnvironmentVariable(Variable, value);
            DialogResult = DialogResult.OK;
            Close();
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, "The API key could not be saved: " + ex.Message, "API key", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
            foreach (Control control in Controls) DisposeImages(control);
        base.Dispose(disposing);
    }

    static void DisposeImages(Control control)
    {
        if (control is PictureBox picture) picture.Image?.Dispose();
        foreach (Control child in control.Controls) DisposeImages(child);
    }
}
