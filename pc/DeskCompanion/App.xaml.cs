using System.IO;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using DeskCompanion.Services;
using Forms = System.Windows.Forms;

namespace DeskCompanion;

public partial class App : Application
{
    private Forms.NotifyIcon? _tray;
    private MainWindow? _window;
    private Settings _settings = new();

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        _settings = Settings.Load();
        // Язык — до создания окна: иначе оно на мгновение нарисовалось бы
        // на русском и перескочило бы.
        Lang.Use(Argument(e.Args, "--lang") ?? _settings.Language);

        // Режим снимка: собрать окно, отрисовать в файл и выйти, не
        // показывая ничего на экране. Нужен, чтобы проверять вёрстку, не
        // выбрасывая окно поверх того, чем человек занят прямо сейчас.
        var shot = Argument(e.Args, "--shot");
        if (shot is not null)
        {
            RenderToFile(shot, Argument(e.Args, "--page") ?? "0");
            Shutdown();
            return;
        }

        _window = new MainWindow(_settings);
        SetUpTray();

        var toTray = e.Args.Contains("--tray") && _settings.StartMinimized;
        if (!toTray) _window.Show();
    }

    private static string? Argument(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    /// <summary>
    /// Отрисовать окно в PNG, не показывая его.
    ///
    /// Окно нужно именно «собрать»: без Measure и Arrange у элементов нет
    /// размеров, и RenderTargetBitmap выдал бы пустой прямоугольник.
    /// </summary>
    private void RenderToFile(string path, string page)
    {
        var window = new MainWindow(_settings, passive: true)
        {
            Width = 1040,
            Height = 660,
            // За пределами экрана и прозрачное: даже мелькнуть не должно.
            WindowStartupLocation = WindowStartupLocation.Manual,
            Left = -4000,
            Top = -4000,
            ShowActivated = false,
            Opacity = 0,
            // Без этого на картинку попадает страница, не успевшая
            // проявиться: снимок делается сразу после переключения, а
            // переход идёт две десятых секунды.
            Animated = false,
        };
        window.Show();
        window.SelectPage(page);
        window.UpdateLayout();

        // Даём данным доехать: запросы к плате асинхронные, и снимок,
        // сделанный сразу, показал бы одни прочерки — то есть проверял бы
        // не то. Крутим очередь сообщений вместо сна: во сне окно ничего
        // не обрабатывает и ответ так и не будет разобран.
        var until = DateTime.UtcNow.AddSeconds(3);
        while (DateTime.UtcNow < until)
        {
            Forms.Application.DoEvents();
            Thread.Sleep(30);
        }
        window.UpdateLayout();
        Forms.Application.DoEvents();

        var width = (int)window.Width;
        var height = (int)window.Height;
        var target = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        var root = (System.Windows.Media.Visual)window.Content;
        ((FrameworkElement)root).Measure(new Size(width, height));
        ((FrameworkElement)root).Arrange(
            new Rect(0, 0, width, height));

        // Фон рисуем сами: содержимое окна прозрачно, а цвет живёт на самом
        // окне, в снимок он бы не попал.
        var visual = new DrawingVisual();
        using (var context = visual.RenderOpen())
        {
            context.DrawRectangle((Brush)Resources["Bg"], null,
                                  new Rect(0, 0, width, height));
            context.DrawRectangle(new VisualBrush(root), null,
                                  new Rect(0, 0, width, height));
        }
        target.Render(visual);

        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(target));
        using var stream = File.Create(path);
        encoder.Save(stream);
        window.Close();
    }

    private void SetUpTray()
    {
        _tray = new Forms.NotifyIcon
        {
            Icon = System.Drawing.SystemIcons.Application,
            Visible = true,
            Text = "Desk Companion",
        };
        _tray.DoubleClick += (_, _) => ShowWindow();
        var menu = new Forms.ContextMenuStrip();
        var open = menu.Items.Add(Lang.T("tray_open"), null, (_, _) => ShowWindow());
        menu.Items.Add(new Forms.ToolStripSeparator());
        var quit = menu.Items.Add(Lang.T("tray_quit"), null, (_, _) => Quit());
        _tray.ContextMenuStrip = menu;
        // Меню трея — WinForms, привязок к словарю у него нет: переименовываем
        // пункты сами, когда язык меняется.
        Lang.Changed += () =>
        {
            open.Text = Lang.T("tray_open");
            quit.Text = Lang.T("tray_quit");
        };

        if (_window is null) return;
        // Подсказка значка — единственное, что видно, пока окно закрыто.
        // Ограничение Windows: длиннее 63 знаков просто не покажет.
        _window.LinkStateChanged = text =>
        {
            if (_tray is not null)
                _tray.Text = text.Length <= 63 ? text : text[..63];
        };

        // Крестик прячет в трей, а не закрывает: приложение должно ждать
        // блок, даже когда окно человеку не нужно. Выход — из меню трея.
        _window.Closing += (_, args) =>
        {
            args.Cancel = true;
            _window.Hide();
        };
    }

    private void ShowWindow()
    {
        if (_window is null) return;
        _window.Show();
        _window.WindowState = WindowState.Normal;
        _window.Activate();
    }

    private void Quit()
    {
        if (_tray is not null) _tray.Visible = false;
        _tray?.Dispose();
        Shutdown();
    }
}
