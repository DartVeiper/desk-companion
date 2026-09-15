using System.ComponentModel;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
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

        _touchSave.Tick += (_, _) =>
        {
            _touchSave.Stop();
            SaveTouch();
        };

        Loaded += async (_, _) =>
        {
            await RefreshScreensAsync();
            await RefreshTouchAsync();
            await RefreshCityAsync();
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

    /// <summary>
    /// Анимировать ли переходы. Режим снимка выключает: он рисует окно
    /// сразу после переключения страницы, и на картинку попала бы
    /// наполовину проявившаяся страница.
    /// </summary>
    public bool Animated { get; set; } = true;

    private void Nav_Checked(object sender, RoutedEventArgs e)
    {
        if (PageOverview is null) return;  // ещё не собрано окно
        var tag = (sender as FrameworkElement)?.Tag as string ?? "0";
        PageOverview.Visibility = tag == "0" ? Visibility.Visible : Visibility.Collapsed;
        PageScreens.Visibility = tag == "1" ? Visibility.Visible : Visibility.Collapsed;
        PageStats.Visibility = tag == "2" ? Visibility.Visible : Visibility.Collapsed;
        PageSettings.Visibility = tag == "3" ? Visibility.Visible : Visibility.Collapsed;

        FrameworkElement shown = tag switch
        {
            "1" => PageScreens,
            "2" => PageStats,
            "3" => PageSettings,
            _ => PageOverview,
        };
        Appear(shown);
    }

    /// <summary>
    /// Проявить страницу: короткое всплытие снизу вверх.
    ///
    /// Смысл не в украшении. Мгновенная подмена содержимого не говорит, что
    /// произошло: страницы похожи по строению, и на полкадра непонятно,
    /// сменилась она или просто перерисовалась. Движение снизу отвечает на
    /// это без единого слова.
    /// </summary>
    private void Appear(FrameworkElement page)
    {
        if (!Animated)
        {
            page.Opacity = 1;
            page.RenderTransform = Transform.Identity;
            return;
        }

        var shift = new TranslateTransform();
        page.RenderTransform = shift;
        page.BeginAnimation(OpacityProperty,
            new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(130)));
        shift.BeginAnimation(TranslateTransform.YProperty,
            new DoubleAnimation(10, 0, TimeSpan.FromMilliseconds(190))
            {
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
            });
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

    // ------------------------------------------------ перетаскивание строк

    /// <summary>
    /// Перетаскивание сделано вручную, а не через DragDrop.DoDragDrop.
    ///
    /// Системное перетаскивание не анимируется в принципе: оно рисует свой
    /// курсор, а список под ним стоит неподвижно и перестраивается разом в
    /// момент отпускания. Строка не едет за мышью, соседи не расступаются,
    /// и понять, куда именно встанет строка, можно только отпустив её.
    ///
    /// Здесь строка поднимается, едет ровно за курсором, а остальные
    /// разъезжаются на её место по ходу — то есть видно результат до того,
    /// как отпустил.
    /// </summary>
    private ScreenRow? _dragRow;

    //: За какое место строки взялись. Без этого строка прыгала бы верхним
    //: краем под курсор в момент захвата.
    private double _grabOffset;
    private bool _dragging;

    private const double SlideMs = 150;

    private ListBoxItem? Container(ScreenRow row)
        => ScreenList.ItemContainerGenerator.ContainerFromItem(row) as ListBoxItem;

    private static TranslateTransform Shift(ListBoxItem item)
    {
        if (item.RenderTransform is TranslateTransform existing) return existing;
        var made = new TranslateTransform();
        item.RenderTransform = made;
        return made;
    }

    /// <summary>Где строка лежала бы без сдвига — то есть по вёрстке.</summary>
    private double LayoutTop(ListBoxItem item)
    {
        var y = item.TranslatePoint(new Point(0, 0), ScreenList).Y;
        return item.RenderTransform is TranslateTransform t ? y - t.Y : y;
    }

    private static void SlideTo(ListBoxItem item, double from, double to)
    {
        var shift = Shift(item);
        shift.Y = from;
        shift.BeginAnimation(TranslateTransform.YProperty,
            new DoubleAnimation(from, to, TimeSpan.FromMilliseconds(SlideMs))
            {
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
            });
    }

    private void ScreenList_MouseDown(object sender, MouseButtonEventArgs e)
    {
        _dragStart = e.GetPosition(ScreenList);
        _dragRow = FindRow(e.OriginalSource as DependencyObject);
    }

    private void ScreenList_MouseMove(object sender, MouseEventArgs e)
    {
        if (e.LeftButton != MouseButtonState.Pressed) { EndDrag(); return; }
        if (_dragRow is null) return;

        var point = e.GetPosition(ScreenList);

        if (!_dragging)
        {
            // Порог обязателен: без него клик по галочке превращался бы в
            // перетаскивание, и галочку стало бы не поставить.
            if (Math.Abs(point.Y - _dragStart.Y) < SystemParameters.MinimumVerticalDragDistance)
                return;
            if (Container(_dragRow) is not { } start) return;
            _grabOffset = _dragStart.Y - LayoutTop(start);
            _dragging = true;
            // Поверх остальных и чуть прозрачнее — чтобы было видно, что
            // строку держат в руке, а не что список просто перерисовался.
            Panel.SetZIndex(start, 1);
            start.Opacity = 0.9;
            // Захват мыши обязателен: без него, уведя курсор за край списка,
            // человек теряет строку на полпути — события просто перестают
            // приходить, а строка остаётся висеть поднятой.
            ScreenList.CaptureMouse();
        }

        if (Container(_dragRow) is not { } item) return;

        // Едет точно за курсором: анимацию сдвига снимаем, иначе она будет
        // спорить с мышью за одно и то же свойство.
        var shift = Shift(item);
        shift.BeginAnimation(TranslateTransform.YProperty, null);
        shift.Y = point.Y - _grabOffset - LayoutTop(item);

        var over = IndexAt(point.Y);
        var here = _screens.IndexOf(_dragRow);
        if (over >= 0 && over != here) Reorder(here, over);
    }

    /// <summary>Над какой строкой курсор. -1 — ни над какой.</summary>
    private int IndexAt(double y)
    {
        for (var i = 0; i < _screens.Count; i++)
        {
            if (ReferenceEquals(_screens[i], _dragRow)) continue;
            if (Container(_screens[i]) is not { } item) continue;
            var top = LayoutTop(item);
            if (y >= top && y <= top + item.ActualHeight) return i;
        }
        return -1;
    }

    /// <summary>
    /// Переставить строку и развезти остальные по новым местам плавно.
    ///
    /// Приём известный: запоминаем, где строки были, переставляем, меряем,
    /// где стали, и сдвигаем каждую обратно на разницу — а потом гасим этот
    /// сдвиг анимацией. Вёрстка при этом мгновенная и настоящая, едет
    /// только картинка.
    /// </summary>
    private void Reorder(int from, int to)
    {
        var was = new Dictionary<ScreenRow, double>();
        foreach (var row in _screens)
            if (Container(row) is { } item) was[row] = LayoutTop(item);

        _screens.Move(from, to);
        Renumber();
        ScreenList.UpdateLayout();

        foreach (var row in _screens)
        {
            // Перетаскиваемой правит мышь, её трогать нельзя.
            if (ReferenceEquals(row, _dragRow)) continue;
            if (Container(row) is not { } item) continue;
            if (!was.TryGetValue(row, out var before)) continue;
            var delta = before - LayoutTop(item);
            if (Math.Abs(delta) > 0.5) SlideTo(item, delta, 0);
        }

        SaveScreens.IsEnabled = true;
        ScreensNote.Text = "порядок изменён — нажми «Применить на блоке»";
    }

    private void ScreenList_MouseUp(object sender, MouseButtonEventArgs e) => EndDrag();

    private void ScreenList_LostCapture(object sender, MouseEventArgs e) => EndDrag();

    private void EndDrag()
    {
        var row = _dragRow;
        _dragRow = null;
        if (!_dragging) return;
        _dragging = false;

        if (row is not null && Container(row) is { } item)
        {
            // Доезжает до места, а не телепортируется: отпущенная строка
            // должна догнать своих, иначе конец жеста выглядит обрывом.
            var shift = Shift(item);
            SlideTo(item, shift.Y, 0);
            item.Opacity = 1.0;
            Panel.SetZIndex(item, 0);
        }
        if (ScreenList.IsMouseCaptured) ScreenList.ReleaseMouseCapture();
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

    // ----------------------------------------------------- город погоды

    private readonly Geocoder _geocoder = new();

    private async Task RefreshCityAsync()
    {
        var place = await _board.CityAsync();
        CityNow.Text = place is null
            ? _board.LastError ?? "блок не ответил"
            : string.IsNullOrEmpty(place.Name)
                ? $"{place.Latitude:0.00}, {place.Longitude:0.00} — город не назван"
                : place.Name;
    }

    private void CityBox_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) CityFind_Click(sender, e);
    }

    private async void CityFind_Click(object sender, RoutedEventArgs e)
    {
        var query = CityBox.Text.Trim();
        if (query.Length < 2)
        {
            CityNote.Text = "нужно хотя бы две буквы";
            return;
        }

        CityNote.Text = "ищу…";
        CityList.Visibility = Visibility.Collapsed;
        CityApply.IsEnabled = false;

        var found = await _geocoder.SearchAsync(query);
        if (found.Count == 0)
        {
            CityNote.Text = _geocoder.LastError ?? $"ничего не нашлось по запросу «{query}»";
            return;
        }

        CityList.ItemsSource = found;
        CityList.SelectedIndex = 0;
        CityList.Visibility = Visibility.Visible;
        CityApply.IsEnabled = true;
        // Тёзок много: без страны и области выбор был бы гаданием, поэтому
        // и говорим, сколько их, а не молча подставляем первый.
        CityNote.Text = found.Count == 1
            ? "нашёлся один — проверь и поставь на блок"
            : $"нашлось {found.Count}: выбери нужный, они бывают тёзками";
    }

    private async void CityPick_Click(object sender, RoutedEventArgs e)
    {
        if (CityList.SelectedItem is not Place place) return;

        CityNote.Text = $"ставлю {place.Full}…";
        CityApply.IsEnabled = false;
        var ok = await _board.SaveCityAsync(place);
        if (!ok)
        {
            CityNote.Text = _board.LastError ?? "не сохранилось";
            CityApply.IsEnabled = true;
            return;
        }

        CityNow.Text = place.Name;
        CityList.Visibility = Visibility.Collapsed;
        CityBox.Text = "";
        CityNote.Text = "готово — блок запросит погоду сразу, не дожидаясь своего часа";
    }

    // ------------------------------------------- чувствительность экрана

    //: Пока значение не приехало с блока, ползунок двигать нечему: любое
    //: его положение было бы выдумкой, и первое же касание мышью отправило
    //: бы эту выдумку на плату.
    private bool _touchKnown;

    /// <summary>
    /// Отложенное сохранение. Ползунок за одно перетаскивание проходит
    /// десятки значений, и слать каждое — значит завалить плату запросами
    /// ради единственного, которое человек имел в виду: последнего.
    /// </summary>
    private readonly System.Windows.Threading.DispatcherTimer _touchSave = new()
    {
        Interval = TimeSpan.FromMilliseconds(400),
    };

    private async Task RefreshTouchAsync()
    {
        var value = await _board.TouchSensitivityAsync();
        if (value is null)
        {
            TouchState.Text = _board.LastError ?? "блок не ответил";
            return;
        }
        // Ставим значение, не считая это правкой человека.
        _touchKnown = false;
        TouchSlider.Value = Math.Clamp(value.Value, TouchSlider.Minimum, TouchSlider.Maximum);
        _touchKnown = true;
        TouchSlider.IsEnabled = true;
        TouchState.Text = Sensitivity(TouchSlider.Value);
    }

    private void TouchSlider_Changed(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (TouchState is null || !_touchKnown) return;
        TouchState.Text = Sensitivity(e.NewValue);
        // Отсчёт начинается заново с каждым движением: сохранится то, на
        // чём человек остановился, а не то, через что он проехал.
        _touchSave.Stop();
        _touchSave.Start();
    }

    /// <summary>Отпустили мышь — ждать нечего, шлём сразу.</summary>
    private void TouchSlider_Done(object sender, System.Windows.Controls.Primitives.DragCompletedEventArgs e)
    {
        if (!_touchKnown) return;
        _touchSave.Stop();
        SaveTouch();
    }

    private async void SaveTouch()
    {
        var value = (int)Math.Round(TouchSlider.Value);
        TouchState.Text = $"{Sensitivity(value)} — сохраняю";
        var ok = await _board.SaveTouchSensitivityAsync(value);
        TouchState.Text = ok
            ? $"{Sensitivity(value)} — готово, попробуй нажать"
            : _board.LastError ?? "не сохранилось";
    }

    /// <summary>
    /// Словами, а не числом. Число здесь — порог сопротивления в омах, и
    /// человеку оно не говорит ничего: важно, сильно ли надо давить.
    /// </summary>
    private static string Sensitivity(double value) => value switch
    {
        < 4000 => "нажимать туго",
        < 7000 => "обычное нажатие",
        < 11000 => "лёгкое нажатие",
        _ => "самое лёгкое",
    };

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
