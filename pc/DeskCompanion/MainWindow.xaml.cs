using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using DeskCompanion.Collection;
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

    /// <summary>Номер в карусели или «выключен».</summary>
    public string Position => _on ? _position.ToString() : Lang.T("screens_off");

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

/// <summary>
/// Полоска зоны радара: заливка по энергии и черта порога.
/// </summary>
/// <remarks>
/// Длины заданы долями, а не пикселями: колонки со звёздочкой делят ширину
/// сами, и полоске не нужно знать свою ширину — а её до вёрстки и не знает
/// никто.
/// </remarks>
public sealed class BarView
{
    public GridLength Fill { get; init; }
    public GridLength Rest { get; init; } = new(1, GridUnitType.Star);
    public GridLength Mark { get; init; }
    public GridLength MarkRest { get; init; } = new(1, GridUnitType.Star);
    public Visibility MarkShown { get; init; } = Visibility.Collapsed;
    public Brush? Brush { get; init; }
    public string Text { get; init; } = "";

    /// <param name="threshold">Порог зоны: null — неизвестен, 100 и выше —
    /// зона не смотрит.</param>
    public static BarView Of(int value, int? threshold, Brush on, Brush off)
    {
        value = Math.Clamp(value, 0, 100);
        var mark = threshold is >= 0 and < 100 ? threshold.Value : -1;
        return new BarView
        {
            Fill = new GridLength(value, GridUnitType.Star),
            Rest = new GridLength(100 - value, GridUnitType.Star),
            Mark = new GridLength(Math.Max(mark, 0), GridUnitType.Star),
            MarkRest = new GridLength(100 - Math.Max(mark, 0), GridUnitType.Star),
            MarkShown = mark >= 0 ? Visibility.Visible : Visibility.Collapsed,
            // Ярко — сигнал перешёл черту и зона сработала. Без известного
            // порога судить не о чем, и полоска просто показывает уровень.
            Brush = threshold switch
            {
                null => on,
                >= 100 => off,
                _ => value >= threshold ? on : off,
            },
            Text = value.ToString(),
        };
    }
}

/// <summary>Строка зоны на странице «Радар».</summary>
public sealed class ZoneView : INotifyPropertyChanged
{
    public string Label { get; private set; } = "";
    public string Role { get; private set; } = "";
    public Brush? RoleBrush { get; private set; }
    public double Opacity { get; private set; } = 1;
    public BarView Moving { get; private set; } = new();
    public BarView Static { get; private set; } = new();

    public event PropertyChangedEventHandler? PropertyChanged;

    public void Set(string label, string role, Brush? roleBrush, double opacity,
                    BarView moving, BarView still)
    {
        Label = label;
        Role = role;
        RoleBrush = roleBrush;
        Opacity = opacity;
        Moving = moving;
        Static = still;
        // Пустое имя значит «поменялось всё»: одно событие вместо шести.
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(string.Empty));
    }
}

public partial class MainWindow : Window
{
    private readonly Board _board = new();
    private readonly Backup _backup;
    private readonly Settings _settings;
    private readonly Collector? _collector;
    private readonly System.Collections.ObjectModel.ObservableCollection<ScreenRow> _screens = new();
    private readonly System.Windows.Threading.DispatcherTimer _timer = new();
    private Point _dragStart;

    /// <summary>
    /// Только смотреть: не искать блок, не снимать копий базы и не трогать
    /// автозапуск. Нужно режиму снимка — окно живёт в нём три секунды, и за
    /// это время оно не должно ничего менять на компьютере.
    /// </summary>
    private readonly bool _passive;

    private const double VisibleSeconds = 1;
    private const double HiddenSeconds = 10;

    /// <summary>Куда написать состояние связи — подсказка значка в трее.</summary>
    public Action<string>? LinkStateChanged;

    /// <param name="collector">Сбор данных с компьютера; null — окно без
    /// него, как в режиме снимка.</param>
    public MainWindow(Settings settings, Collector? collector, bool passive = false)
    {
        InitializeComponent();
        // Версия — в заголовке: по ней понятно, какая сборка запущена, когда
        // что-то пошло не так.
        var version = typeof(MainWindow).Assembly.GetName().Version;
        if (version is not null) Title = $"Desk Companion {version.Major}.{version.Minor}.{version.Build}";
        _settings = settings;
        _collector = collector;
        _passive = passive;
        _backup = new Backup(_board);
        _board.Host = settings.Host;
        _board.Port = settings.Port;

        HostBox.Text = settings.Host;
        PortBox.Text = settings.Port.ToString();
        MinimizedBox.IsChecked = settings.StartMinimized;
        CollectBox.IsChecked = settings.CollectorEnabled;
        ScreenList.ItemsSource = _screens;
        ZoneList.ItemsSource = _zones;
        FillLanguageBox();

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
        _calibrationPoll.Tick += async (_, _) => await PollCalibrationAsync();

        Lang.Changed += ApplyLanguage;
        if (_collector is not null) _collector.Changed += OnCollectorChanged;
        ComputerLog.Written += OnLogWritten;
        Closed += (_, _) =>
        {
            Lang.Changed -= ApplyLanguage;
            if (_collector is not null) _collector.Changed -= OnCollectorChanged;
            ComputerLog.Written -= OnLogWritten;
        };

        Loaded += async (_, _) =>
        {
            RefreshAutostart();
            RefreshComputer();
            await RefreshScreensAsync();
            await RefreshTouchAsync();
            await RefreshLanguageAsync();
            await RefreshCityAsync();
            ShowBackupState();
        };
    }

    private RadioButton[] NavButtons =>
        new[] { NavOverview, NavScreens, NavStats, NavRadar, NavComputer, NavSettings };

    private FrameworkElement[] Pages =>
        new FrameworkElement[] { PageOverview, PageScreens, PageStats, PageRadar, PageComputer, PageSettings };

    /// <summary>Переключить страницу снаружи — нужно режиму снимка.</summary>
    public void SelectPage(string tag) => SelectPage(int.TryParse(tag, out var value) ? value : 0);

    /// <summary>
    /// Переключить страницу снаружи — нужно режиму снимка и второму запуску,
    /// который просит окно показаться на определённой странице.
    /// </summary>
    public void SelectPage(int index)
    {
        var buttons = NavButtons;
        buttons[Math.Clamp(index, 0, buttons.Length - 1)].IsChecked = true;
    }

    /// <summary>Какая страница открыта.</summary>
    public int CurrentPage => Array.FindIndex(NavButtons, b => b.IsChecked == true);

    /// <summary>
    /// Перерисовать всё, что собирает код, на новом языке.
    ///
    /// Надписи из разметки меняются сами — они привязаны к словарю. А
    /// текст, который код складывает из данных, WPF перечитать не может: он
    /// не знает, откуда тот взялся. Поэтому каждый такой кусок здесь
    /// собирается заново.
    /// </summary>
    private async void ApplyLanguage()
    {
        FillLanguageBox();
        Refresh();
        if (_today is not null) ApplyToday(_today);
        ShowBackupState();
        ApplyRadar(_live);
        RefreshComputer();
        RefreshAutostart();
        if (_touchKnown) TouchState.Text = Sensitivity(TouchSlider.Value);
        if (_calibration is not null) ShowCalibration(_calibration);

        // Подписи экранов приходят с блока уже переведёнными — за новыми
        // надо сходить. Но если человек переставил строки и ещё не нажал
        // «Применить», перечитывать нельзя: пропала бы его перестановка.
        if (SaveScreens.IsEnabled)
        {
            // Номера перерисуются на новом языке — «выключен» там словом.
            Renumber();
        }
        else
        {
            await RefreshScreensAsync();
        }
        await RefreshLanguageAsync();
        await RefreshCityAsync();
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
        var pages = Pages;
        var index = int.TryParse(tag, out var value) ? Math.Clamp(value, 0, pages.Length - 1) : 0;
        for (var i = 0; i < pages.Length; i++)
            pages[i].Visibility = i == index ? Visibility.Visible : Visibility.Collapsed;

        // Скрытые страницы на опросе не перерисовываются — догоняем их при
        // открытии, чтобы не показать на мгновение прошлое состояние.
        if (pages[index] == PageRadar) ApplyRadar(_live);
        if (pages[index] == PageComputer) RefreshComputer();
        // Задачу планировщика спрашиваем не каждую секунду, а когда на неё
        // смотрят: это обращение к службе, а не к памяти.
        if (pages[index] == PageSettings) RefreshAutostart();
        Appear(pages[index]);
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
    private Today? _today;
    private Live? _live;

    private async void Refresh()
    {
        var live = await _board.LiveAsync();
        _live = live;
        ApplyLive(live);
        if (PageRadar.IsVisible) ApplyRadar(live);
        if (live is null) MaybeFindBoard();
        else _misses = 0;

        // Сводка за день меняется минутами, а не секундами: дёргать её
        // каждую секунду — зря будить и сеть, и базу на блоке.
        if (_ticks++ % 30 == 0)
        {
            _today = await _board.TodayAsync() ?? _today;
            if (_today is not null) ApplyToday(_today);
        }

        // Копию базы снимаем раз в сутки и только когда блок на связи.
        // Проверка дешёвая — время правки файла, — поэтому спрашиваем на
        // каждом круге, а не по расписанию: расписание пришлось бы чинить
        // после каждого сна компьютера.
        if (!_passive && live is not null && await _backup.EnsureTodayAsync())
            ShowBackupState();
    }

    private void ApplyLive(Live? live)
    {
        if (live is null)
        {
            LinkDot.Fill = Brush("Alert");
            LinkText.Text = Lang.T("link_none");
            LinkText.Foreground = Brush("Alert");
            LinkDetail.Text = _board.LastError ?? "";
            SubTitle.Text = Lang.T("link_waiting");
            LinkStateChanged?.Invoke(Lang.T("tray_state", _board.LastError ?? Lang.T("link_none")));
            return;
        }

        LinkDot.Fill = Brush("Ok");
        LinkText.Text = Lang.T("link_ok");
        LinkText.Foreground = Brush("Ok");
        LinkDetail.Text = live.Ip ?? "";
        SubTitle.Text = live.Version ?? "";
        LinkStateChanged?.Invoke(live.Co2 is null
            ? Lang.T("tray_state", Lang.T("link_ok"))
            : Lang.T("tray_air", live.Co2,
                     Lang.T(live.Presence ? "tray_at_desk" : "tray_nobody")));

        Co2Value.Text = live.Co2?.ToString() ?? "—";
        Co2Value.Foreground = Brush(
            live.Co2 is null ? "Dim" : live.Co2 < 800 ? "Ok" : live.Co2 < 1400 ? "Warn" : "Alert");
        AirCaption.Text = live.Temperature is null
            ? Lang.T("air_silent")
            : Lang.T("air_caption", live.Temperature, live.Humidity);

        PresenceValue.Text = Lang.T(live.Presence ? "desk_present" : "desk_empty");
        PresenceValue.Foreground = Brush(live.Presence ? "Ok" : "Dim");
        PresenceCaption.Text = live.DistanceCm is > 0
            ? Lang.T("radar_distance", live.DistanceCm)
            : Lang.T(live.Ld2410Ok ? "radar_online" : "radar_silent");

        WeatherValue.Text = live.WeatherTemp is null
            ? "—"
            : string.Format(Lang.Culture, "{0:0.#} °C", live.WeatherTemp);
        WeatherCaption.Text = WeatherName(live) ?? Lang.T("weather_none");

        ActiveApp.Text = live.PcOnline
            ? string.IsNullOrEmpty(live.ActiveApp) ? Lang.T("pc_no_window") : live.ActiveApp
            : Lang.T("pc_offline");
        ActiveApp.Foreground = Brush(live.PcOnline ? "Fg" : "Dim");
        CpuLoad.Text = Percent(live.CpuLoad);
        CpuTemp.Text = Degrees(live.CpuTemp);
        GpuLoad.Text = Percent(live.GpuLoad);
        GpuTemp.Text = Degrees(live.GpuTemp);

        BoardGrid.Children.Clear();
        AddFact(BoardGrid, Lang.T("fact_address"), live.Ip ?? "—");
        AddFact(BoardGrid, Lang.T("fact_wifi"), live.WifiDbm is null
            ? Lang.T(live.WifiOk ? "fact_yes" : "fact_no")
            : Lang.T("fact_dbm", live.WifiDbm));
        AddFact(BoardGrid, Lang.T("fact_memory"), live.RamUsedMb is null
            ? "—"
            : Lang.T("fact_memory_value", live.RamUsedMb, live.RamTotalMb));
        AddFact(BoardGrid, Lang.T("fact_board_temp"), Degrees(live.BoardTemp));
        AddFact(BoardGrid, Lang.T("fact_uptime"), Uptime(live.UptimeSeconds));
        AddFact(BoardGrid, Lang.T("fact_air_sensor"), Lang.T(live.Scd41Ok ? "fact_ok" : "fact_silent"));
        AddFact(BoardGrid, Lang.T("fact_radar"), Lang.T(live.Ld2410Ok ? "fact_ok" : "fact_silent"));
        AddFact(BoardGrid, Lang.T("fact_version"), live.Version ?? "—");

        ProblemList.ItemsSource = live.Problems
            .Select(p => new { p.Label, p.Detail }).ToList();
    }

    /// <summary>
    /// Название погоды на языке окна.
    ///
    /// Если этот язык знает блок, берём его название — оно подробнее
    /// («небольшой дождь», а не просто «дождь») и совпадает с тем, что на
    /// экране. Иначе называем сами, по коду WMO: блок прислал бы английское.
    /// </summary>
    private static string? WeatherName(Live live)
    {
        if (Lang.DeviceLanguages.Contains(Lang.Code) && !string.IsNullOrEmpty(live.WeatherCond))
            return live.WeatherCond;
        var key = live.WeatherCode switch
        {
            0 => "wmo_0",
            1 => "wmo_1",
            2 => "wmo_2",
            3 => "wmo_3",
            45 or 48 => "wmo_fog",
            51 or 53 or 55 => "wmo_drizzle",
            56 or 57 or 66 or 67 => "wmo_freezing",
            61 or 63 or 65 => "wmo_rain",
            71 or 73 or 75 or 77 => "wmo_snow",
            80 or 81 or 82 => "wmo_showers",
            85 or 86 => "wmo_snow_showers",
            95 or 96 or 99 => "wmo_storm",
            _ => null,
        };
        return key is null ? live.WeatherCond : Lang.T(key);
    }

    private void AddFact(UniformGrid grid, string caption, string value, string brush = "Fg")
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 12, 12) };
        panel.Children.Add(new TextBlock
        {
            Text = value,
            Foreground = Brush(brush),
            FontSize = 15,
            TextTrimming = TextTrimming.CharacterEllipsis,
        });
        panel.Children.Add(new TextBlock
        {
            Text = caption,
            Foreground = Brush("Dim"),
            FontSize = 11,
            Margin = new Thickness(0, 2, 0, 0),
        });
        grid.Children.Add(panel);
    }

    private void ApplyToday(Today today)
    {
        AtDesk.Text = Minutes(today.AtDeskMinutes);
        Longest.Text = Minutes(today.LongestSitting);
        Longest.Foreground = Brush(today.LongestSitting >= today.SittingLimit ? "Warn" : "Fg");
        Keys.Text = today.Keystrokes.ToString("N0", Lang.Culture);
        Clicks.Text = today.Clicks.ToString("N0", Lang.Culture);

        // Столбики масштабируем по самому высокому часу, а не по суткам:
        // иначе в спокойный день график выглядит пустым полем.
        var peak = Math.Max(1, today.Hours.Max());
        HourBars.ItemsSource = today.Hours
            .Select(h => new HourBar { Height = 110.0 * h / peak })
            .ToList();

        var top = Math.Max(1, today.ByCategory.Values.DefaultIfEmpty(0).Max());
        CategoryList.ItemsSource = today.ByCategory
            .OrderByDescending(p => p.Value)
            .Select(p => new CategoryRow
            {
                Label = Strings.Ru.ContainsKey($"category_{p.Key}")
                    ? Lang.T($"category_{p.Key}")
                    : p.Key,
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
            ScreensNote.Text = _board.LastError ?? Lang.T("board_no_answer");
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
        ScreensNote.Text = Lang.T("screens_saving");
        var ok = await _board.SaveScreensAsync(
            _screens.Select(r => new ScreenEntry(r.Key, r.Label, r.On)));
        ScreensNote.Text = ok
            ? Lang.T("screens_saved")
            : _board.LastError ?? Lang.T("not_saved");
        if (ok) SaveScreens.IsEnabled = false;
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
        ScreensNote.Text = Lang.T("screens_moved");
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

    // ------------------------------------------------------------ радар

    private const int GateCm = 75;

    //: Ворота 0 и 1 статику не поддерживают — у них слишком малая дальность,
    //: и порог статики модуль игнорирует (см. pi/app/drivers/ld2410.py).
    //: Черта там врала бы, будто зона что-то ждёт.
    private const int NoStaticGates = 2;
    private readonly System.Collections.ObjectModel.ObservableCollection<ZoneView> _zones = new();

    /// <summary>
    /// Нарисовать страницу радара. На опросе зовётся, только когда страница
    /// открыта: девять зон по две полоски раз в секунду — работа, которую
    /// незачем делать для скрытой страницы.
    /// </summary>
    private void ApplyRadar(Live? live)
    {
        var radar = live?.Radar;
        if (live is null || radar is null || !live.Ld2410Ok)
        {
            RadarDot.Fill = Brush(live is null ? "Alert" : "Dim");
            RadarVerdict.Text = Lang.T(live is null ? "link_none" : "radar_silent");
            RadarVerdict.Foreground = Brush(live is null ? "Alert" : "Fg");
            RadarReason.Text = live is null ? _board.LastError ?? "" : "";
            RadarFacts.Children.Clear();
            // Старые полоски не оставляем: застывшая картинка выглядела бы
            // живой, а это хуже, чем пустая.
            _zones.Clear();
            RadarLevels.Visibility = Visibility.Collapsed;
            return;
        }

        RadarDot.Fill = Brush(live.Presence ? "Ok" : "Dim");
        RadarVerdict.Text = Lang.T(live.Presence ? "radar_at_desk" : "radar_away");
        RadarVerdict.Foreground = Brush(live.Presence ? "Ok" : "Fg");
        var (reason, tone) = ExplainPresence(live, radar);
        RadarReason.Text = reason;
        RadarReason.Foreground = Brush(tone);

        RadarFacts.Children.Clear();
        AddFact(RadarFacts, Lang.T("radar_fact_module"),
                Lang.T(radar.ModulePresent ? "radar_fact_target" : "radar_fact_nobody"));
        AddFact(RadarFacts, Lang.T("radar_fact_distance"),
                radar.ModulePresent && live.DistanceCm is > 0
                    ? Lang.T("radar_cm", live.DistanceCm)
                    : "—");
        AddFact(RadarFacts, Lang.T("radar_fact_quiet"),
                radar.NearMotionAgo is { } quiet && !NearMotionNow(radar)
                    ? Ago(TimeSpan.FromSeconds(quiet))
                    : "—");

        var gates = Math.Max(radar.Moving.Length, radar.Static.Length);
        while (_zones.Count < gates) _zones.Add(new ZoneView());
        while (_zones.Count > gates) _zones.RemoveAt(_zones.Count - 1);
        for (var i = 0; i < gates; i++)
        {
            var movingLimit = At(radar.MovingThresholds, i);
            var staticLimit = i < NoStaticGates ? 100 : At(radar.StaticThresholds, i);
            var ignored = movingLimit >= 100 && staticLimit >= 100;
            var near = i < radar.NearGates;
            _zones[i].Set(
                Lang.T("radar_zone", i * GateCm, (i + 1) * GateCm),
                near ? Lang.T("radar_near") : ignored ? Lang.T("radar_ignored") : "",
                Brush(near ? "Accent" : "Dim"),
                ignored ? 0.45 : 1,
                BarView.Of(At(radar.Moving, i) ?? 0, movingLimit, Brush("Accent"), Brush("AccentDim")),
                BarView.Of(At(radar.Static, i) ?? 0, staticLimit, Brush("Still"), Brush("StillDim")));
        }

        RadarLevels.Text = radar.LevelsHours is { } hours ? Lang.T("radar_levels", hours) : "";
        RadarLevels.Visibility = radar.LevelsHours is null ? Visibility.Collapsed : Visibility.Visible;
    }

    private static int? At(int[] values, int index) => index < values.Length ? values[index] : null;

    /// <summary>Есть ли движение у стола в этом отсчёте.</summary>
    private static bool NearMotionNow(Radar radar)
    {
        for (var i = 0; i < radar.NearGates; i++)
        {
            if (At(radar.Moving, i) is { } energy
                && At(radar.MovingThresholds, i) is { } limit
                && energy >= limit)
                return true;
        }
        return false;
    }

    /// <summary>
    /// Почему блок решил так, а не иначе.
    /// </summary>
    /// <remarks>
    /// Повторяет правило Ld2410Source._at_desk на блоке и расходиться с ним
    /// не должно: иначе страница объясняла бы решение, которого блок не
    /// принимал.
    /// </remarks>
    private static (string Text, string Tone) ExplainPresence(Live live, Radar radar)
    {
        if (radar.NearMotionAgo is not { } quiet)
            return (Lang.T("radar_reason_no_rule"), "Warn");
        if (!radar.ModulePresent)
            return (Lang.T("radar_reason_empty"), "Dim");
        if (NearMotionNow(radar))
            return (Lang.T("radar_reason_motion_now"), "Ok");

        var since = TimeSpan.FromSeconds(quiet);
        if (live.Presence)
        {
            // Пара секунд тишины — это человек, который замер, а не повод
            // рассказывать про удержание.
            if (quiet < 10) return (Lang.T("radar_reason_motion", Ago(since)), "Ok");
            var left = TimeSpan.FromSeconds(Math.Max(0, radar.HoldSeconds - quiet));
            return (Lang.T("radar_reason_still", Ago(since), Ago(left)), "Fg");
        }

        // Модуль цель видит, а блок человека не засчитал — значит, держит
        // дальняя зона. Называем самую громкую: это первое, что захочется
        // знать, чтобы найти виновника.
        var gates = Math.Max(radar.Moving.Length, radar.Static.Length);
        var far = Enumerable.Range(radar.NearGates, Math.Max(0, gates - radar.NearGates))
            .OrderByDescending(g => Math.Max(At(radar.Moving, g) ?? 0, At(radar.Static, g) ?? 0))
            .Select(g => (int?)g)
            .FirstOrDefault();
        var zone = far is { } g ? Lang.T("radar_zone", g * GateCm, (g + 1) * GateCm) : "—";
        return (Lang.T("radar_reason_ghost", Ago(since), zone), "Warn");
    }

    // -------------------------------------------------------- компьютер

    private static readonly string OwnProcess =
        System.IO.Path.GetFileNameWithoutExtension(Environment.ProcessPath ?? "DeskCompanion");

    private string _logShown = "";
    private bool _granting;

    //: Последнее окно, кроме нашего. Пока человек смотрит на эту страницу,
    //: впереди всегда само приложение, и показывать это бессмысленно.
    private CollectorState? _lastWindow;

    /// <summary>
    /// Прежний агент работает и остановить его не вышло. Пока так, сбор не
    /// запускается: у агента и сбора один клиент на брокере, и они выбивали
    /// бы друг друга каждые несколько секунд.
    /// </summary>
    public bool OldAgentInTheWay { get; set; }

    /// <summary>
    /// Уступить место экземпляру с правами, который поднимает задача
    /// автозапуска. Параметр — какую страницу открыть в новом окне; true —
    /// уступили, и это приложение закрывается.
    /// </summary>
    public Func<int, Task<bool>>? RestartWithRights;

    private void OnCollectorChanged()
    {
        // Снимок — здесь, в потоке сбора: к тому времени, как очередь окна
        // до него дойдёт, окно впереди может смениться ещё раз.
        var state = _collector?.State;
        if (state is null) return;
        Dispatcher.InvokeAsync(() =>
        {
            if (!string.IsNullOrEmpty(state.ActiveApp)
                && !string.Equals(state.ActiveApp, OwnProcess, StringComparison.OrdinalIgnoreCase))
                _lastWindow = state;
            if (PageComputer.IsVisible) RefreshComputer();
        });
    }

    private void OnLogWritten() => Dispatcher.InvokeAsync(() =>
    {
        if (PageComputer.IsVisible) ShowComputerLog();
    });

    private void RefreshComputer()
    {
        var state = _collector?.State ?? new CollectorState();

        var (status, dot) =
            OldAgentInTheWay ? ("computer_blocked", "Alert")
            : _collector is null || !_settings.CollectorEnabled ? ("computer_off", "Dim")
            : !state.Running ? ("computer_starting", "Dim")
            : !state.Connected ? ("computer_offline", "Warn")
            : ("computer_running", "Ok");
        ComputerStatus.Text = Lang.T(status);
        ComputerStatus.Foreground = Brush(dot == "Dim" ? "Fg" : dot);
        ComputerDot.Fill = Brush(dot);

        var error = state.Running ? state.LastError : null;
        ShowNote(ComputerError, string.IsNullOrEmpty(error) ? null : Lang.T("computer_last_error", error), "Alert");

        ComputerFacts.Children.Clear();
        if (state.Running)
        {
            AddFact(ComputerFacts, Lang.T("computer_fact_board"), $"{state.Host}:{state.Port}");
            AddFact(ComputerFacts, Lang.T("computer_fact_link"),
                    Lang.T(state.Connected ? "fact_yes" : "fact_no"),
                    state.Connected ? "Ok" : "Warn");
            AddFact(ComputerFacts, Lang.T("computer_fact_temps"),
                    Lang.T(state.Sensors ? "computer_temps_ok" : "computer_temps_no"),
                    state.Sensors ? "Fg" : "Warn");
            AddFact(ComputerFacts, Lang.T("computer_fact_input"),
                    Lang.T(state.InputHooks ? "computer_input_ok" : "computer_input_no"),
                    state.InputHooks ? "Fg" : "Warn");
            AddFact(ComputerFacts, Lang.T("computer_fact_rights"),
                    Lang.T(state.Elevated ? "computer_rights_admin" : "computer_rights_user"));
            AddFact(ComputerFacts, Lang.T("computer_fact_window"), WindowLabel(_lastWindow));
        }
        ComputerFacts.Visibility = state.Running ? Visibility.Visible : Visibility.Collapsed;

        // Про права — только когда их действительно не хватает. С правами и
        // без температур кнопка не поможет: там виноват не запуск.
        TempsHint.Visibility = state.Running && !state.Sensors && !state.Elevated
            ? Visibility.Visible
            : Visibility.Collapsed;
        OldAgentCard.Visibility = OldAgentInTheWay ? Visibility.Visible : Visibility.Collapsed;
        GrantRights.IsEnabled = OldAgentFix.IsEnabled = !_granting && !_passive;

        CollectBox.IsChecked = _settings.CollectorEnabled;
        CollectBox.IsEnabled = _collector is not null && !OldAgentInTheWay;

        ShowComputerLog();
    }

    private static string WindowLabel(CollectorState? state)
    {
        if (state is null || string.IsNullOrEmpty(state.ActiveApp)) return "—";
        var key = $"category_{state.Category}";
        var category = Strings.Ru.ContainsKey(key) ? Lang.T(key) : state.Category;
        return $"{state.ActiveApp} · {category}";
    }

    private void ShowComputerLog()
    {
        var lines = ComputerLog.Lines();
        var text = lines.Count == 0
            ? Lang.T("computer_log_empty")
            : string.Join(Environment.NewLine, lines.TakeLast(80));
        // Перерисовываем, только если журнал изменился: иначе каждую
        // секунду слетало бы выделение, а прокрутка прыгала бы в конец,
        // пока человек читает середину.
        if (text == _logShown) return;
        _logShown = text;
        ComputerLogBox.Text = text;
        ComputerLogBox.ScrollToEnd();
    }

    private async void Collect_Click(object sender, RoutedEventArgs e)
    {
        if (_passive || _collector is null) return;
        var on = CollectBox.IsChecked == true;
        _settings.CollectorEnabled = on;
        _settings.Save();
        if (on && !OldAgentInTheWay) _collector.Start();
        else if (!on) await _collector.StopAsync();
        RefreshComputer();
    }

    private async void Grant_Click(object sender, RoutedEventArgs e)
    {
        if (_passive || _granting) return;
        _granting = true;
        RefreshComputer();
        try
        {
            await EnableRightsAsync(GrantState);
        }
        finally
        {
            _granting = false;
            RefreshComputer();
        }
    }

    /// <summary>
    /// Завести автозапуск с правами и, если приложение само работает без
    /// них, уступить место экземпляру, которого поднимет задача.
    /// </summary>
    /// <param name="note">Куда писать, как идут дела.</param>
    private async Task EnableRightsAsync(TextBlock note)
    {
        ShowNote(note, Lang.T("autostart_waiting"), "Dim");
        var error = await Autostart.SetAsync(true);
        // Помощник с правами писал в журнал из своего процесса.
        ComputerLog.Reload();
        RefreshAutostart();
        if (error is not null)
        {
            ShowNote(note, error, "Alert");
            return;
        }

        if (Elevation.IsElevated)
        {
            // Права уже есть — уступать некому. Задача заведена, старый
            // агент убран вместе с ней; осталось запустить сбор, если он
            // ждал, пока агент уйдёт.
            ShowNote(note, null, "Dim");
            if (!OldAgentInTheWay) return;
            OldAgentInTheWay = Autostart.CleanUpOldSetup(removeRunKey: true);
            if (!OldAgentInTheWay && _settings.CollectorEnabled) _collector?.Start();
            RefreshComputer();
            return;
        }

        ShowNote(note, Lang.T("computer_restarting"), "Dim");
        if (RestartWithRights is not null && await RestartWithRights(CurrentPage)) return;
        ShowNote(note, Lang.T("computer_restart_failed"), "Warn");
    }

    private void ComputerLogOpen_Click(object sender, RoutedEventArgs e)
    {
        if (!System.IO.File.Exists(ComputerLog.FilePath)) return;
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
        {
            FileName = ComputerLog.FilePath,
            UseShellExecute = true,
        });
    }

    private async void ProbeWindows_Click(object sender, RoutedEventArgs e)
        => await RunProbeAsync(() =>
        {
            var lines = ActiveWindow.Describe().ToList();
            lines.Add("");
            lines.Add(Lang.T("probe_overrides_from", Overrides.Path));
            return lines;
        });

    private async void ProbeSensors_Click(object sender, RoutedEventArgs e)
        => await RunProbeAsync(() =>
        {
            var (found, temps, _) = ProbeHardware();
            var lines = found.ToList();
            lines.Add("");
            lines.Add(Lang.T(temps ? "probe_temps_ok"
                             : Elevation.IsElevated ? "probe_temps_blocked"
                             : "probe_temps_none"));
            return lines;
        });

    private (IReadOnlyList<string> Lines, bool Temps, double? GpuLoad) ProbeHardware()
    {
        if (_collector is not null) return _collector.ProbeHardware();
        using var own = new HardwareMonitor();
        return own.Probe() ?? (Array.Empty<string>(), false, null);
    }

    private async Task RunProbeAsync(Func<List<string>> probe)
    {
        ProbeWindows.IsEnabled = ProbeSensors.IsEnabled = false;
        ProbeOutput.Visibility = Visibility.Visible;
        ProbeOutput.Text = Lang.T("computer_probe_running");
        try
        {
            // Обход процессов и датчиков — секунды работы, и окно на это
            // время замирать не должно.
            var lines = await Task.Run(probe);
            ProbeOutput.Text = string.Join(Environment.NewLine, lines);
        }
        catch (Exception error)
        {
            ProbeOutput.Text = error.Message;
        }
        finally
        {
            ProbeWindows.IsEnabled = ProbeSensors.IsEnabled = true;
        }
    }

    // ------------------------------------------------------ поиск блока

    private bool _finding;
    private int _misses;
    private DateTime _lastAutoFind = DateTime.MinValue;

    /// <summary>Как часто искать блок самому, пока он не отвечает.</summary>
    private static readonly TimeSpan AutoFindEvery = TimeSpan.FromMinutes(10);

    /// <summary>
    /// Поискать блок, если он молчит. Не с первого промаха: блок мог просто
    /// перезагружаться, и обходить ради этого сеть — лишний шум. Три промаха
    /// подряд — уже повод.
    /// </summary>
    private async void MaybeFindBoard()
    {
        if (_passive || _finding) return;
        if (++_misses < 3) return;
        if (DateTime.Now - _lastAutoFind < AutoFindEvery) return;
        _lastAutoFind = DateTime.Now;
        await FindBoardAsync(manual: false);
    }

    private async void FindBoard_Click(object sender, RoutedEventArgs e)
    {
        if (_passive || _finding) return;
        await FindBoardAsync(manual: true);
    }

    /// <param name="manual">Искать попросил человек — тогда ход поиска
    /// виден под полем адреса. Сам по себе поиск идёт молча.</param>
    private async Task FindBoardAsync(bool manual)
    {
        _finding = true;
        FindBoard.IsEnabled = false;
        try
        {
            if (manual) ShowNote(TestResult, Lang.T("host_testing"), "Dim");
            var progress = new Progress<(int Done, int Total)>(p =>
            {
                if (manual) ShowNote(TestResult, Lang.T("host_finding", p.Done, p.Total), "Dim");
            });
            var found = await Discovery.FindAsync(_settings.Host, _settings.Port, progress);
            if (found is null)
            {
                if (manual) ShowNote(TestResult, Lang.T("host_not_found"), "Alert");
                return;
            }

            if (!string.Equals(found, _settings.Host, StringComparison.OrdinalIgnoreCase))
            {
                _settings.Host = _board.Host = found;
                _settings.Save();
                HostBox.Text = found;
                _collector?.Reconnect();
                ComputerLog.Info(Lang.T("log_board_found", found));
            }
            _misses = 0;
            if (manual) ShowNote(TestResult, Lang.T("host_found", found), "Ok");
            Refresh();
        }
        finally
        {
            _finding = false;
            FindBoard.IsEnabled = true;
        }
    }

    // -------------------------------------------------------- настройки

    private async void TestHost_Click(object sender, RoutedEventArgs e)
    {
        var host = HostBox.Text.Trim();
        if (host.Length == 0) return;
        var moved = !string.Equals(host, _settings.Host, StringComparison.OrdinalIgnoreCase);
        _settings.Host = _board.Host = host;
        _settings.Port = _board.Port = int.TryParse(PortBox.Text, out var port) ? port : 843;
        _settings.Save();
        // Сбор берёт адрес из тех же настроек, но подключён по старому —
        // пусть переподключится, иначе будет стучаться туда, где блока нет.
        if (moved) _collector?.Reconnect();

        ShowNote(TestResult, Lang.T("host_testing"), "Dim");
        var live = await _board.LiveAsync();
        ShowNote(TestResult,
                 live is null ? Lang.T("host_bad", _board.LastError) : Lang.T("host_good", live.Version),
                 live is null ? "Alert" : "Ok");
        if (live is not null) await RefreshScreensAsync();
    }

    private void ShowBackupState()
    {
        var at = _backup.LastAt();
        BackupState.Text = at is null
            ? Lang.T("backup_none")
            : Lang.T("backup_last", at);
        if (_backup.LastError is not null)
            BackupState.Text += Lang.T("backup_failed", _backup.LastError);
        BackupState.Foreground = Brush(at is null ? "Warn" : "Ok");
    }

    private async void BackupNow_Click(object sender, RoutedEventArgs e)
    {
        BackupNow.IsEnabled = false;
        BackupState.Text = Lang.T("backup_running");
        BackupState.Foreground = Brush("Dim");
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

    private void RefreshAutostart()
    {
        AutostartBox.IsChecked = Autostart.IsOn;
        ShowNote(AutostartState, Autostart.IsStale ? Lang.T("startup_stale") : null, "Warn");
    }

    private async void Autostart_Click(object sender, RoutedEventArgs e)
    {
        if (_passive) return;
        var want = AutostartBox.IsChecked == true;
        AutostartBox.IsEnabled = false;
        try
        {
            if (want)
            {
                await EnableRightsAsync(AutostartState);
                return;
            }
            ShowNote(AutostartState, Lang.T("autostart_waiting"), "Dim");
            var error = await Autostart.SetAsync(false);
            ComputerLog.Reload();
            RefreshAutostart();
            if (error is not null) ShowNote(AutostartState, error, "Alert");
        }
        finally
        {
            AutostartBox.IsEnabled = true;
        }
    }

    private void Minimized_Click(object sender, RoutedEventArgs e)
    {
        _settings.StartMinimized = MinimizedBox.IsChecked == true;
        _settings.Save();
    }

    // ------------------------------------------------------------- язык

    //: Пока список заполняется кодом, его изменения — не выбор человека, и
    //: слать их на блок нельзя.
    private bool _langReady;

    private void FillLanguageBox()
    {
        _langReady = false;
        if (LangBox.ItemsSource is null) LangBox.ItemsSource = Lang.Available;
        LangBox.SelectedItem = Lang.Available.FirstOrDefault(c => c.Code == Lang.Code);
        _langReady = true;
    }

    private async void LangBox_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (!_langReady) return;
        if (LangBox.SelectedItem is not Lang.Choice choice) return;
        if (choice.Code == Lang.Code) return;

        _settings.Language = choice.Code;
        _settings.Save();
        // Окно переключается сразу и само, по событию языка. Блок — вслед,
        // по сети: он может быть и недоступен, и окно ждать его не должно.
        Lang.Use(choice.Code);

        LangState.Text = Lang.T("lang_device_saving");
        LangState.Foreground = Brush("Dim");
        if (!await _board.SaveLanguageAsync(Lang.DeviceCode))
        {
            LangState.Text = Lang.T("lang_device_offline");
            LangState.Foreground = Brush("Warn");
            return;
        }
        await RefreshLanguageAsync();
    }

    private async Task RefreshLanguageAsync()
    {
        var code = await _board.LanguageAsync();
        if (code is null)
        {
            LangState.Text = Lang.T("lang_device_offline");
            LangState.Foreground = Brush("Warn");
            return;
        }
        var name = Lang.Available.FirstOrDefault(c => c.Code == code)?.Name ?? code;
        LangState.Text = Lang.T("lang_device_now", name);
        LangState.Foreground = Brush(code == Lang.DeviceCode ? "Fg" : "Warn");
    }

    // ----------------------------------------------------- город погоды

    private readonly Geocoder _geocoder = new();

    private async Task RefreshCityAsync()
    {
        var place = await _board.CityAsync();
        CityNow.Text = place is null
            ? _board.LastError ?? Lang.T("board_no_answer")
            : string.IsNullOrEmpty(place.Name)
                ? Lang.T("city_unnamed", place.Latitude, place.Longitude)
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
            CityNote.Text = Lang.T("city_too_short");
            return;
        }

        CityNote.Text = Lang.T("city_searching");
        CityList.Visibility = Visibility.Collapsed;
        CityApply.IsEnabled = false;

        var found = await _geocoder.SearchAsync(query);
        if (found.Count == 0)
        {
            CityNote.Text = _geocoder.LastError ?? Lang.T("city_nothing", query);
            return;
        }

        CityList.ItemsSource = found;
        CityList.SelectedIndex = 0;
        CityList.Visibility = Visibility.Visible;
        CityApply.IsEnabled = true;
        // Тёзок много: без страны и области выбор был бы гаданием, поэтому
        // и говорим, сколько их, а не молча подставляем первый.
        CityNote.Text = found.Count == 1
            ? Lang.T("city_one")
            : Lang.T("city_many", found.Count);
    }

    private async void CityPick_Click(object sender, RoutedEventArgs e)
    {
        if (CityList.SelectedItem is not Place place) return;

        CityNote.Text = Lang.T("city_setting", place.Full);
        CityApply.IsEnabled = false;
        var ok = await _board.SaveCityAsync(place);
        if (!ok)
        {
            CityNote.Text = _board.LastError ?? Lang.T("not_saved");
            CityApply.IsEnabled = true;
            return;
        }

        CityNow.Text = place.Name;
        CityList.Visibility = Visibility.Collapsed;
        CityBox.Text = "";
        CityNote.Text = Lang.T("city_done");
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
            TouchState.Text = _board.LastError ?? Lang.T("board_no_answer");
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
        TouchState.Text = Lang.T("touch_saving", Sensitivity(value));
        var ok = await _board.SaveTouchSensitivityAsync(value);
        TouchState.Text = ok
            ? Lang.T("touch_saved", Sensitivity(value))
            : _board.LastError ?? Lang.T("not_saved");
    }

    /// <summary>
    /// Словами, а не числом. Число здесь — порог сопротивления в омах, и
    /// человеку оно не говорит ничего: важно, сильно ли надо давить.
    /// </summary>
    private static string Sensitivity(double value) => Lang.T(value switch
    {
        < 4000 => "touch_very_hard",
        < 7000 => "touch_normal",
        < 11000 => "touch_light",
        _ => "touch_lightest",
    });

    // ------------------------------------------------ калибровка экрана

    /// <summary>
    /// Опрос хода калибровки. Сама она идёт на блоке — человек жмёт в
    /// крестики там, — а окно только показывает, на каком он шаге.
    /// </summary>
    private readonly System.Windows.Threading.DispatcherTimer _calibrationPoll = new()
    {
        Interval = TimeSpan.FromMilliseconds(500),
    };

    private string _calibrationId = "";
    private CalibrationState? _calibration;
    private DateTime _calibrationAsked;

    /// <summary>
    /// Сколько ждать, пока сервис на блоке заметит просьбу. Обычно — доли
    /// секунды; если дольше, значит, на блоке старый код без калибровки.
    /// </summary>
    private static readonly TimeSpan CalibrationPickup = TimeSpan.FromSeconds(10);

    private async void CalibStart_Click(object sender, RoutedEventArgs e)
    {
        CalibStart.IsEnabled = false;
        CalibState.Foreground = Brush("Dim");
        CalibState.Text = Lang.T("calib_starting");
        var state = await _board.RequestCalibrationAsync("start");
        if (state is null)
        {
            CalibState.Text = Lang.T("calib_failed", _board.LastError ?? Lang.T("board_no_answer"));
            CalibState.Foreground = Brush("Alert");
            CalibStart.IsEnabled = true;
            return;
        }
        _calibrationId = state.Id;
        _calibrationAsked = DateTime.Now;
        _calibration = null;
        CalibCancel.Visibility = Visibility.Visible;
        _calibrationPoll.Start();
    }

    private async void CalibCancel_Click(object sender, RoutedEventArgs e)
        => await _board.RequestCalibrationAsync("cancel");

    private async Task PollCalibrationAsync()
    {
        var state = await _board.CalibrationAsync();
        if (state is null) return;   // блок моргнул — спросим ещё раз

        if (state.Id != _calibrationId)
        {
            if (DateTime.Now - _calibrationAsked > CalibrationPickup)
            {
                StopCalibration();
                CalibState.Text = Lang.T("calib_failed", Lang.T("calib_no_service"));
                CalibState.Foreground = Brush("Alert");
            }
            return;
        }

        _calibration = state;
        ShowCalibration(state);
        if (state.State is "done" or "failed" or "cancelled") StopCalibration();
    }

    private void ShowCalibration(CalibrationState state)
    {
        (CalibState.Text, var brush) = state.State switch
        {
            "running" => (Lang.T("calib_waiting", state.Step, state.Total), "Accent"),
            "done" => (Lang.T("calib_done"), "Ok"),
            "failed" => (Lang.T("calib_failed", state.Detail), "Alert"),
            "cancelled" => (string.IsNullOrEmpty(state.Detail)
                ? Lang.T("calib_cancelled")
                : $"{Lang.T("calib_cancelled")} · {state.Detail}", "Dim"),
            _ => (Lang.T("calib_starting"), "Dim"),
        };
        CalibState.Foreground = Brush(brush);
    }

    private void StopCalibration()
    {
        _calibrationPoll.Stop();
        CalibCancel.Visibility = Visibility.Collapsed;
        CalibStart.IsEnabled = true;
    }

    // ------------------------------------------------------------ мелочи

    private Brush Brush(string key) => (Brush)FindResource(key);

    /// <summary>Строка-пояснение: пустая прячется, чтобы не держать отступ.</summary>
    private void ShowNote(TextBlock block, string? text, string brush)
    {
        block.Text = text ?? "";
        block.Foreground = Brush(brush);
        block.Visibility = string.IsNullOrEmpty(text) ? Visibility.Collapsed : Visibility.Visible;
    }

    private static string Percent(double? value)
        => value is null ? "—" : string.Format(Lang.Culture, "{0:0} %", value);

    private static string Degrees(double? value)
        => value is null ? "—" : string.Format(Lang.Culture, "{0:0} °", value);

    private static string Minutes(int minutes) =>
        minutes >= 60
            ? Lang.T("time_hm", minutes / 60, minutes % 60)
            : Lang.T("time_m", minutes);

    private static string Uptime(long seconds)
    {
        if (seconds <= 0) return "—";
        var span = TimeSpan.FromSeconds(seconds);
        if (span.TotalDays >= 1) return Lang.T("uptime_dh", (int)span.TotalDays, span.Hours);
        if (span.TotalHours >= 1) return Lang.T("uptime_hm", (int)span.TotalHours, span.Minutes);
        return Lang.T("uptime_m", span.Minutes);
    }

    private static string Ago(TimeSpan span)
        => span.TotalMinutes >= 1
            ? Lang.T("ago_m", (int)span.TotalMinutes)
            : Lang.T("ago_s", (int)span.TotalSeconds);
}
