using System.ComponentModel;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using DeskCompanion.Services;

namespace DeskCompanion;

/// <summary>Строка в списке экранов. Порядок в списке — порядок в карусели.</summary>
public sealed class ScreenRow : INotifyPropertyChanged
{
    private bool _on;
    private int _position;

    public string Key { get; init; } = "";
    public string Label { get; init; } = "";

    public bool On
    {
        get => _on;
        set { _on = value; Changed(nameof(On)); }
    }

    /// <summary>Номер в карусели или прочерк у выключенного.</summary>
    public string Position => _on ? _position.ToString() : "выключен";

    public void SetPosition(int value)
    {
        _position = value;
        Changed(nameof(Position));
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Changed(string name)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        if (name == nameof(On)) PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Position)));
    }
}

public sealed class HourBar
{
    public double Height { get; init; }
}

public sealed class CategoryRow
{
    public string Label { get; init; } = "";
    public string Value { get; init; } = "";
    public double BarWidth { get; init; }
}

public partial class MainWindow : Window
{
    private readonly Board _board = new();
    private readonly Backup _backup;
    private readonly Settings _settings;
    private readonly System.Collections.ObjectModel.ObservableCollection<ScreenRow> _screens = new();
    private readonly System.Windows.Threading.DispatcherTimer _timer = new();
    private Point _dragStart;

    private const double VisibleSeconds = 1;
    private const double HiddenSeconds = 10;

    /// <summary>Куда написать состояние связи — подсказка значка в трее.</summary>
    public Action<string>? LinkStateChanged;

    public MainWindow(Settings settings)
    {
        InitializeComponent();
        _settings = settings;
        _backup = new Backup(_board);
        _board.Host = settings.Host;
        _board.Port = settings.Port;

        HostBox.Text = settings.Host;
        PortBox.Text = settings.Port.ToString();
        AutostartBox.IsChecked = Settings.IsAutostartOn();
        var agentTask = Settings.IsAgentAutostartOn();
        AgentAutostart.Text = agentTask
            ? "агент: автозапуск заведён"
            : "агент: автозапуска нет — данные с компьютера пропадут после перезагрузки";
        AgentAutostart.Foreground = (Brush)FindResource(agentTask ? "Ok" : "Warn");
        MinimizedBox.IsChecked = settings.StartMinimized;
        ScreenList.ItemsSource = _screens;

        // Опрос идёт всегда, а не с момента показа окна. Раньше таймер
        // заводился в Loaded, и запущенное в трей приложение не делало
        // ровно ничего: ноль процессорного времени за шесть минут. А
        // обещано было обратное — что оно ждёт плату само и подхватит её,
        // как только та появится в сети.
        _timer.Interval = TimeSpan.FromSeconds(HiddenSeconds);
        _timer.Tick += (_, _) => Refresh();
        _timer.Start();
        Refresh();

        // Со скрытым окном частить незачем: никто не смотрит, а разница
        // между «плата появилась» и «мы это заметили» в десять секунд
        // человеку не видна — он ещё окно открыть не успеет.
        IsVisibleChanged += (_, _) =>
        {
            _timer.Interval = TimeSpan.FromSeconds(
                IsVisible ? VisibleSeconds : HiddenSeconds);
            if (IsVisible) Refresh();
        };

        Loaded += async (_, _) =>
        {
            await RefreshScreensAsync();
            ShowBackupState();
        };
    }

    /// <summary>Переключить страницу снаружи — нужно режиму снимка.</summary>
    public void SelectPage(string tag)
    {
        var buttons = new[] { NavOverview, NavScreens, NavStats, NavSettings };
        var index = int.TryParse(tag, out var value) ? value : 0;
        buttons[Math.Clamp(index, 0, buttons.Length - 1)].IsChecked = true;
    }

    // --------------------------------------------------------- навигация

    private void Nav_Checked(object sender, RoutedEventArgs e)
    {
        if (PageOverview is null) return;  // ещё не собрано окно
        var tag = (sender as FrameworkElement)?.Tag as string ?? "0";
        PageOverview.Visibility = tag == "0" ? Visibility.Visible : Visibility.Collapsed;
        PageScreens.Visibility = tag == "1" ? Visibility.Visible : Visibility.Collapsed;
        PageStats.Visibility = tag == "2" ? Visibility.Visible : Visibility.Collapsed;
        PageSettings.Visibility = tag == "3" ? Visibility.Visible : Visibility.Collapsed;
    }

    // ------------------------------------------------------- обновление

    private int _ticks;

    private async void Refresh()
    {
        var live = await _board.LiveAsync();
        ApplyLive(live);

        // Сводка за день меняется минутами, а не секундами: дёргать её
        // каждую секунду — зря будить и сеть, и базу на блоке.
        if (_ticks++ % 30 == 0)
            ApplyToday(await _board.TodayAsync());

        // Копию базы снимаем раз в сутки и только когда блок на связи.
        // Проверка дешёвая — время правки файла, — поэтому спрашиваем на
        // каждом круге, а не по расписанию: расписание пришлось бы чинить
        // после каждого сна компьютера.
        if (live is not null && await _backup.EnsureTodayAsync())
            ShowBackupState();
    }

    private void ApplyLive(Live? live)
    {
        if (live is null)
        {
            LinkDot.Fill = (Brush)FindResource("Alert");
            LinkText.Text = "нет связи";
            LinkText.Foreground = (Brush)FindResource("Alert");
            LinkDetail.Text = _board.LastError ?? "";
            SubTitle.Text = "жду блок";
            LinkStateChanged?.Invoke($"Desk Companion — {_board.LastError ?? "нет связи"}");
            return;
        }

        LinkDot.Fill = (Brush)FindResource("Ok");
        LinkText.Text = "блок на связи";
        LinkText.Foreground = (Brush)FindResource("Ok");
        LinkDetail.Text = live.Ip ?? "";
        SubTitle.Text = live.Version ?? "";
        LinkStateChanged?.Invoke(live.Co2 is null
            ? "Desk Companion — блок на связи"
            : $"Desk Companion — CO₂ {live.Co2} ppm, "
              + (live.Presence ? "за столом" : "никого"));

        Co2Value.Text = live.Co2?.ToString() ?? "—";
        Co2Value.Foreground = (Brush)FindResource(
            live.Co2 is null ? "Dim" : live.Co2 < 800 ? "Ok" : live.Co2 < 1400 ? "Warn" : "Alert");
        AirCaption.Text = live.Temperature is null
            ? "датчик молчит"
            : $"{live.Temperature:0.0} °C · влажность {live.Humidity:0} %";

        PresenceValue.Text = live.Presence ? "на месте" : "пусто";
        PresenceValue.Foreground = (Brush)FindResource(live.Presence ? "Ok" : "Dim");
        PresenceCaption.Text = live.DistanceCm is > 0
            ? $"радар видит в {live.DistanceCm} см"
            : live.Ld2410Ok ? "радар на связи" : "радар молчит";

        WeatherValue.Text = live.WeatherTemp is null ? "—" : $"{live.WeatherTemp:0.#} °C";
        WeatherCaption.Text = live.WeatherCond ?? "прогноза нет";

        ActiveApp.Text = live.PcOnline
            ? string.IsNullOrEmpty(live.ActiveApp) ? "не видно активного окна" : live.ActiveApp
            : "агент не на связи — данные с компьютера не приходят";
        ActiveApp.Foreground = (Brush)FindResource(live.PcOnline ? "Fg" : "Dim");
        CpuLoad.Text = Percent(live.CpuLoad);
        CpuTemp.Text = Degrees(live.CpuTemp);
        GpuLoad.Text = Percent(live.GpuLoad);
        GpuTemp.Text = Degrees(live.GpuTemp);

        AgentState.Text = live.PcOnline
            ? "данные с компьютера приходят"
            : "агент не запущен или не достучался до блока";
        AgentState.Foreground = (Brush)FindResource(live.PcOnline ? "Ok" : "Warn");

        BoardGrid.Children.Clear();
        AddFact("адрес", live.Ip ?? "—");
        AddFact("Wi-Fi", live.WifiDbm is null ? (live.WifiOk ? "есть" : "нет") : $"{live.WifiDbm} дБм");
        AddFact("память", live.RamUsedMb is null ? "—" : $"{live.RamUsedMb} из {live.RamTotalMb} МБ");
        AddFact("температура платы", Degrees(live.BoardTemp));
        AddFact("в работе", Uptime(live.UptimeSeconds));
        AddFact("датчик воздуха", live.Scd41Ok ? "в строю" : "молчит");
        AddFact("радар", live.Ld2410Ok ? "в строю" : "молчит");
        AddFact("версия", live.Version ?? "—");

        ProblemList.ItemsSource = live.Problems
            .Select(p => new { p.Label, p.Detail }).ToList();
    }

    private void AddFact(string caption, string value)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 12, 12) };
        panel.Children.Add(new TextBlock
        {
            Text = value,
            Foreground = (Brush)FindResource("Fg"),
            FontSize = 15,
            TextTrimming = TextTrimming.CharacterEllipsis,
        });
        panel.Children.Add(new TextBlock
        {
            Text = caption,
            Foreground = (Brush)FindResource("Dim"),
            FontSize = 11,
            Margin = new Thickness(0, 2, 0, 0),
        });
        BoardGrid.Children.Add(panel);
    }

    private void ApplyToday(Today? today)
    {
        if (today is null) return;

        AtDesk.Text = Minutes(today.AtDeskMinutes);
        Longest.Text = Minutes(today.LongestSitting);
        Longest.Foreground = (Brush)FindResource(
            today.LongestSitting >= today.SittingLimit ? "Warn" : "Fg");
        Keys.Text = today.Keystrokes.ToString("N0", CultureInfo.CurrentCulture);
        Clicks.Text = today.Clicks.ToString("N0", CultureInfo.CurrentCulture);

        // Столбики масштабируем по самому высокому часу, а не по суткам:
        // иначе в спокойный день график выглядит пустым полем.
        var peak = Math.Max(1, today.Hours.Max());
        HourBars.ItemsSource = today.Hours
            .Select(h => new HourBar { Height = 110.0 * h / peak })
            .ToList();

        var names = new Dictionary<string, string>
        {
            ["code"] = "код", ["browser"] = "браузер",
            ["game"] = "игры", ["other"] = "прочее",
        };
        var top = Math.Max(1, today.ByCategory.Values.DefaultIfEmpty(0).Max());
        CategoryList.ItemsSource = today.ByCategory
            .OrderByDescending(p => p.Value)
            .Select(p => new CategoryRow
            {
                Label = names.TryGetValue(p.Key, out var name) ? name : p.Key,
                Value = Minutes(p.Value),
                BarWidth = 420.0 * p.Value / top,
            })
            .ToList();
    }

    // ----------------------------------------------------------- экраны

    private async Task RefreshScreensAsync()
    {
        var screens = await _board.ScreensAsync();
        if (screens is null)
        {
            ScreensNote.Text = _board.LastError ?? "блок не ответил";
            return;
        }

        _screens.Clear();
        foreach (var entry in screens)
            _screens.Add(new ScreenRow { Key = entry.Key, Label = entry.Label, On = entry.On });
        Renumber();
        SaveScreens.IsEnabled = false;
        ScreensNote.Text = "";
    }

    /// <summary>Проставить номера в карусели: выключенные в счёте не участвуют.</summary>
    private void Renumber()
    {
        var number = 1;
        foreach (var row in _screens)
            row.SetPosition(row.On ? number++ : 0);
    }

    private async void SaveScreens_Click(object sender, RoutedEventArgs e)
    {
        ScreensNote.Text = "сохраняю...";
        var ok = await _board.SaveScreensAsync(
            _screens.Select(r => new ScreenEntry(r.Key, r.Label, r.On)));
        ScreensNote.Text = ok ? "готово, блок перестроит карусель сам"
                              : _board.LastError ?? "не сохранилось";
        if (ok)
        {
            SaveScreens.IsEnabled = false;
        }
    }

    private async void ReloadScreens_Click(object sender, RoutedEventArgs e)
        => await RefreshScreensAsync();

    // Перетаскивание строк. Жест начинается не по нажатию, а после того как
    // палец увёл мышь дальше системного порога, — иначе обычный клик по
    // галочке превращался бы в перетаскивание.
    private void ScreenList_MouseDown(object sender, MouseButtonEventArgs e)
        => _dragStart = e.GetPosition(null);

    private void ScreenList_MouseMove(object sender, MouseEventArgs e)
    {
        if (e.LeftButton != MouseButtonState.Pressed) return;
        var moved = e.GetPosition(null) - _dragStart;
        if (Math.Abs(moved.X) < SystemParameters.MinimumHorizontalDragDistance &&
            Math.Abs(moved.Y) < SystemParameters.MinimumVerticalDragDistance) return;

        if (FindRow(e.OriginalSource as DependencyObject) is not { } row) return;
        DragDrop.DoDragDrop(ScreenList, row, DragDropEffects.Move);
    }

    private void ScreenList_DragOver(object sender, DragEventArgs e)
    {
        e.Effects = e.Data.GetDataPresent(typeof(ScreenRow))
            ? DragDropEffects.Move : DragDropEffects.None;
        e.Handled = true;
    }

    private void ScreenList_Drop(object sender, DragEventArgs e)
    {
        if (e.Data.GetData(typeof(ScreenRow)) is not ScreenRow dragged) return;
        var target = FindRow(e.OriginalSource as DependencyObject);
        if (target is null || ReferenceEquals(target, dragged)) return;

        _screens.Move(_screens.IndexOf(dragged), _screens.IndexOf(target));
        Renumber();
        SaveScreens.IsEnabled = true;
        ScreensNote.Text = "порядок изменён — нажми «Применить на блоке»";
    }

    private static ScreenRow? FindRow(DependencyObject? source)
    {
        while (source is not null and not ListBoxItem)
            source = VisualTreeHelper.GetParent(source);
        return (source as ListBoxItem)?.DataContext as ScreenRow;
    }

    // -------------------------------------------------------- настройки

    private async void TestHost_Click(object sender, RoutedEventArgs e)
    {
        _settings.Host = _board.Host = HostBox.Text.Trim();
        _settings.Port = _board.Port = int.TryParse(PortBox.Text, out var port) ? port : 843;
        _settings.Save();

        TestResult.Text = "проверяю...";
        var live = await _board.LiveAsync();
        TestResult.Text = live is null
            ? $"не отвечает: {_board.LastError}"
            : $"отвечает, версия {live.Version}";
        TestResult.Foreground = (Brush)FindResource(live is null ? "Alert" : "Ok");
        if (live is not null) await RefreshScreensAsync();
    }

    private void ShowBackupState()
    {
        var at = _backup.LastAt();
        BackupState.Text = at is null
            ? "копий ещё нет"
            : $"последняя копия: {at:dd.MM HH:mm}";
        if (_backup.LastError is not null)
            BackupState.Text += $" · не удалось обновить: {_backup.LastError}";
        BackupState.Foreground = (Brush)FindResource(at is null ? "Warn" : "Ok");
    }

    private async void BackupNow_Click(object sender, RoutedEventArgs e)
    {
        BackupNow.IsEnabled = false;
        BackupState.Text = "снимаю копию...";
        BackupState.Foreground = (Brush)FindResource("Dim");
        await _backup.RunAsync();
        ShowBackupState();
        BackupNow.IsEnabled = true;
    }

    private void OpenBackups_Click(object sender, RoutedEventArgs e)
    {
        System.IO.Directory.CreateDirectory(Backup.Folder);
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
        {
            FileName = Backup.Folder,
            UseShellExecute = true,
        });
    }

    private void Autostart_Click(object sender, RoutedEventArgs e)
    {
        var want = AutostartBox.IsChecked == true;
        if (!Settings.SetAutostart(want))
        {
            AutostartBox.IsChecked = Settings.IsAutostartOn();
            TestResult.Text = "не удалось изменить автозапуск";
            return;
        }
        _settings.Autostart = want;
        _settings.Save();
    }

    private void Minimized_Click(object sender, RoutedEventArgs e)
    {
        _settings.StartMinimized = MinimizedBox.IsChecked == true;
        _settings.Save();
    }

    // ------------------------------------------------------------ мелочи

    private static string Percent(double? value) => value is null ? "—" : $"{value:0} %";
    private static string Degrees(double? value) => value is null ? "—" : $"{value:0} °";

    private static string Minutes(int minutes) =>
        minutes >= 60 ? $"{minutes / 60} ч {minutes % 60:00} м" : $"{minutes} м";

    private static string Uptime(long seconds)
    {
        if (seconds <= 0) return "—";
        var span = TimeSpan.FromSeconds(seconds);
        if (span.TotalDays >= 1) return $"{(int)span.TotalDays} д {span.Hours} ч";
        if (span.TotalHours >= 1) return $"{(int)span.TotalHours} ч {span.Minutes} мин";
        return $"{span.Minutes} мин";
    }
}
