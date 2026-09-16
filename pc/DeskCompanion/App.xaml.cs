using System.IO;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using DeskCompanion.Collection;
using DeskCompanion.Services;
using Forms = System.Windows.Forms;

namespace DeskCompanion;

public partial class App : Application
{
    private Forms.NotifyIcon? _tray;
    private MainWindow? _window;
    private Settings _settings = new();
    private SingleInstance? _instance;
    private Collector? _collector;
    private bool _quitting;

    /// <summary>
    /// Сколько ждать экземпляр с правами, поднятый задачей. Обычно он
    /// появляется за секунду; первый запуск однофайлового exe распаковывает
    /// себя и бывает втрое дольше.
    /// </summary>
    private static readonly TimeSpan HandOffWait = TimeSpan.FromSeconds(10);

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        DispatcherUnhandledException += OnWindowError;
        _settings = Settings.Load();
        // Язык — до создания окна: иначе оно на мгновение нарисовалось бы
        // на русском и перескочило бы.
        Lang.Use(Argument(e.Args, "--lang") ?? _settings.Language);

        // Помощник: завести или снять автозапуск с правами. Его запускает
        // само приложение, когда прав у него нет, и ждёт код выхода — окна
        // здесь не бывает.
        if (Argument(e.Args, "--autostart") is { } mode)
        {
            Shutdown(Autostart.HandleHelper(mode));
            return;
        }

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

        var toTray = e.Args.Contains("--tray");

        // Приложение уже работает — показывается оно, второе не нужно: два
        // сбора слали бы блоку одно и то же вперемешку.
        if (SingleInstance.IsRunning())
        {
            if (!toTray) SingleInstance.SignalShow(TimeSpan.FromSeconds(3));
            Shutdown();
            return;
        }

        // Запустили без прав, а автозапуск с правами заведён — поднимаемся
        // через его задачу. Так температуры читаются и у приложения,
        // открытого двойным щелчком, и без окна UAC. Задачу, смотрящую на
        // другой exe, не трогаем: она подняла бы не эту версию.
        if (!Elevation.IsElevated && Autostart.HasTask && !Autostart.IsStale
            && HandOff(page: -1, show: !toTray))
        {
            Shutdown();
            return;
        }

        _instance = SingleInstance.TryClaim();
        if (_instance is null)
        {
            // Второй запуск успел раньше — уступаем ему.
            if (!toTray) SingleInstance.SignalShow(TimeSpan.FromSeconds(3));
            Shutdown();
            return;
        }

        // До сбора: старый агент должен уйти раньше, чем сбор подключится
        // к брокеру под тем же именем.
        var oldAgent = Autostart.Migrate();
        _collector = new Collector(() => _settings.Host);
        if (_settings.CollectorEnabled && !oldAgent) _collector.Start();

        _window = new MainWindow(_settings, _collector)
        {
            OldAgentInTheWay = oldAgent,
            RestartWithRights = RestartWithRightsAsync,
        };
        _instance.Listen(OnShowRequest);
        SetUpTray();

        if (!(toTray && _settings.StartMinimized)) _window.Show();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _instance?.Dispose();
        _instance = null;
        base.OnExit(e);
    }

    private string _lastWindowError = "";
    private DateTime _lastWindowErrorAt = DateTime.MinValue;

    /// <summary>
    /// Ошибка в окне — в журнал, а не конец приложения.
    /// </summary>
    /// <remarks>
    /// Приложение живёт неделями в трее и собирает данные для блока. Упасть
    /// из-за одного странного ответа — значит оставить блок без данных до
    /// следующего входа в систему, и никто этого не заметит: 17.09 так и
    /// вышло, стоило перезапустить сервис на блоке. Журнал делает ошибку
    /// видимой на странице «Компьютер»; одна и та же ошибка пишется не чаще
    /// раза в минуту — окно опрашивает блок каждую секунду.
    /// </remarks>
    private void OnWindowError(object sender, System.Windows.Threading.DispatcherUnhandledExceptionEventArgs e)
    {
        var text = $"{e.Exception.GetType().Name}: {e.Exception.Message}";
        if (text != _lastWindowError || DateTime.Now - _lastWindowErrorAt > TimeSpan.FromMinutes(1))
        {
            _lastWindowError = text;
            _lastWindowErrorAt = DateTime.Now;
            ComputerLog.Error(Lang.T("log_window_error", text));
        }
        e.Handled = true;
    }

    private static string? Argument(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    private void OnShowRequest(int page) => Dispatcher.InvokeAsync(() => ShowWindow(page));

    /// <summary>
    /// Поднять экземпляр с правами задачей автозапуска и дождаться его.
    /// </summary>
    /// <param name="page">Какую страницу ему открыть; -1 — любую.</param>
    /// <param name="show">Попросить его показать окно.</param>
    /// <returns>Он работает — этому экземпляру пора уходить.</returns>
    private static bool HandOff(int page, bool show)
    {
        if (!Autostart.RunNow()) return false;
        var until = DateTime.UtcNow + HandOffWait;
        while (DateTime.UtcNow < until)
        {
            if (SingleInstance.IsRunning())
            {
                // Место он занимает раньше, чем начинает слушать просьбы, —
                // поэтому просим, пока не услышит или не выйдет время.
                while (show && DateTime.UtcNow < until)
                {
                    if (SingleInstance.SignalShow(TimeSpan.FromMilliseconds(500), page)) break;
                }
                return true;
            }
            Thread.Sleep(100);
        }
        return false;
    }

    /// <summary>
    /// Уступить место экземпляру с правами: освободить брокер и имя, поднять
    /// задачу и выйти. Не вышло — вернуть всё как было.
    /// </summary>
    private async Task<bool> RestartWithRightsAsync(int page)
    {
        if (_collector is not null) await _collector.StopAsync();
        // Освобождать место можно только из потока, который его занял, —
        // а это поток окна, и await выше возвращает именно в него.
        _instance?.Dispose();
        _instance = null;

        if (await Task.Run(() => HandOff(page, show: true)))
        {
            Quit();
            return true;
        }

        _instance = SingleInstance.TryClaim();
        _instance?.Listen(OnShowRequest);
        if (_settings.CollectorEnabled && _window?.OldAgentInTheWay != true) _collector?.Start();
        return false;
    }

    /// <summary>
    /// Отрисовать окно в PNG, не показывая его.
    ///
    /// Окно нужно именно «собрать»: без Measure и Arrange у элементов нет
    /// размеров, и RenderTargetBitmap выдал бы пустой прямоугольник.
    /// </summary>
    private void RenderToFile(string path, string page)
    {
        // Без сбора: снимок не должен ни ставить перехват ввода, ни слать
        // что-то блоку от имени компьютера.
        var window = new MainWindow(_settings, collector: null, passive: true)
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
        // блок и собирать данные, даже когда окно человеку не нужно. Выход —
        // из меню трея.
        _window.Closing += (_, args) =>
        {
            if (_quitting) return;
            args.Cancel = true;
            _window.Hide();
        };
    }

    private void ShowWindow(int page = -1)
    {
        if (_window is null || _quitting) return;
        if (page >= 0) _window.SelectPage(page);
        _window.Show();
        if (_window.WindowState == WindowState.Minimized) _window.WindowState = WindowState.Normal;
        _window.Activate();
    }

    private async void Quit()
    {
        if (_quitting) return;
        _quitting = true;
        if (_tray is not null)
        {
            _tray.Visible = false;
            _tray.Dispose();
            _tray = null;
        }
        _window?.Hide();
        // Сбор закрываем по-честному: отключение от брокера — сигнал блоку,
        // что компьютер ушёл, а не пропал.
        if (_collector is not null) await _collector.StopAsync();
        Shutdown();
    }
}
