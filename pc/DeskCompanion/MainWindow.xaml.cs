using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
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

public partial class MainWindow : Window
{
    private readonly Board _board = new();
    private readonly Backup _backup;
    private readonly Settings _settings;
    private readonly System.Collections.ObjectModel.ObservableCollection<ScreenRow> _screens = new();
    private readonly System.Windows.Threading.DispatcherTimer _timer = new();
    private Point _dragStart;

    /// <summary>
    /// Только смотреть: не поднимать агента и не снимать копий базы. Нужно
    /// режиму снимка — окно живёт в нём три секунды, и за это время оно не
    /// должно ничего менять на компьютере.
    /// </summary>
    private readonly bool _passive;

    private const double VisibleSeconds = 1;
    private const double HiddenSeconds = 10;

    /// <summary>Куда написать состояние связи — подсказка значка в трее.</summary>
    public Action<string>? LinkStateChanged;

    public MainWindow(Settings settings, bool passive = false)
    {
        InitializeComponent();
        _settings = settings;
        _passive = passive;
        _backup = new Backup(_board);
        _board.Host = settings.Host;
        _board.Port = settings.Port;

        HostBox.Text = settings.Host;
        PortBox.Text = settings.Port.ToString();
        AutostartBox.IsChecked = Settings.IsAutostartOn();
        MinimizedBox.IsChecked = settings.StartMinimized;
        AgentKeep.IsChecked = settings.AgentManaged;
        ScreenList.ItemsSource = _screens;
        FillLanguageBox();

        // Опрос идёт всегда, а не с момента показа окна. Раньше таймер
        // заводился в Loaded, и запущенное в трей приложение не делало
        // ровно ничего: ноль процессорного времени за шесть минут. А
        // обещано было обратное — что оно ждёт плату само и подхватит её,
        // как только та появится в сети.
        _timer.Interval = TimeSpan.FromSeconds(HiddenSeconds);
        _timer.Tick += (_, _) =>
        {
            Refresh();
            KeepAgentAlive();
        };
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
        Closed += (_, _) => Lang.Changed -= ApplyLanguage;

        Loaded += async (_, _) =>
        {
            _agentHasTask = Agent.HasTask;
            RefreshAgent();
            KeepAgentAlive();
            await RefreshScreensAsync();
            await RefreshTouchAsync();
            await RefreshLanguageAsync();
            await RefreshCityAsync();
            ShowBackupState();
        };
    }

    /// <summary>Переключить страницу снаружи — нужно режиму снимка.</summary>
    public void SelectPage(string tag)
    {
        var buttons = new[] { NavOverview, NavScreens, NavStats, NavAgent, NavSettings };
        var index = int.TryParse(tag, out var value) ? value : 0;
        buttons[Math.Clamp(index, 0, buttons.Length - 1)].IsChecked = true;
    }

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
        RefreshAgent();
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
        var pages = new FrameworkElement[] { PageOverview, PageScreens, PageStats, PageAgent, PageSettings };
        var index = int.TryParse(tag, out var value) ? Math.Clamp(value, 0, pages.Length - 1) : 0;
        for (var i = 0; i < pages.Length; i++)
            pages[i].Visibility = i == index ? Visibility.Visible : Visibility.Collapsed;

        if (pages[index] == PageAgent)
        {
            // Задачу планировщика спрашиваем не каждую секунду, а когда на
            // неё смотрят: это запуск отдельной программы.
            _agentHasTask = Agent.HasTask;
            RefreshAgent();
        }
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

    private async void Refresh()
    {
        var live = await _board.LiveAsync();
        ApplyLive(live);
        if (PageAgent.Visibility == Visibility.Visible) RefreshAgent();

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

    // ------------------------------------------------------------ агент

    //: Есть ли задача планировщика. Спрашивать её — значит запускать
    //: schtasks, поэтому ответ помним и обновляем, когда страница открыта
    //: или агента запускали.
    private bool _agentHasTask;
    private bool _agentBusy;
    private DateTime _agentChecked = DateTime.MinValue;
    private string _agentLog = "";

    /// <summary>Как часто проверять, что агент жив, если за ним следим.</summary>
    private static readonly TimeSpan KeepAliveEvery = TimeSpan.FromSeconds(30);

    /// <summary>
    /// Поднять агента, если он не работает, а слежение включено.
    ///
    /// Планировщик поднимает агента при входе в систему, но не после того,
    /// как тот упал посреди дня, — а упавший агент значит пустые данные с
    /// компьютера до следующей перезагрузки. Раз в полминуты — чаще незачем:
    /// минутный счётчик нажатий за это время почти ничего не теряет.
    /// </summary>
    private async void KeepAgentAlive()
    {
        if (_passive || _agentBusy || !_settings.AgentManaged) return;
        if (DateTime.Now - _agentChecked < KeepAliveEvery) return;
        _agentChecked = DateTime.Now;
        if (Agent.IsRunning) return;

        _agentBusy = true;
        try
        {
            await Agent.StartAsync();
        }
        finally
        {
            _agentBusy = false;
        }
        RefreshAgent();
    }

    private void RefreshAgent()
    {
        var running = Agent.IsRunning;
        var report = Agent.ReadReport();
        var age = report is null ? TimeSpan.MaxValue : DateTime.Now - report.Updated;
        var fresh = running && report is not null && age < Agent.StaleAfter;

        string status, dot, detail = "";
        if (!running)
        {
            status = Lang.T("agent_stopped");
            dot = "Dim";
        }
        else if (!fresh)
        {
            status = Lang.T("agent_hung");
            dot = "Alert";
            detail = report is null ? "" : Lang.T("agent_hung_detail", Ago(age));
        }
        else if (!report!.Connected)
        {
            status = Lang.T("agent_running_offline");
            dot = "Warn";
        }
        else
        {
            status = Lang.T("agent_running");
            dot = "Ok";
        }
        AgentStatus.Text = status;
        AgentStatus.Foreground = Brush(dot == "Dim" ? "Fg" : dot);
        AgentDot.Fill = Brush(dot);
        AgentDetail.Text = detail;
        AgentDetail.Visibility = detail.Length > 0 ? Visibility.Visible : Visibility.Collapsed;

        AgentFacts.Children.Clear();
        if (fresh)
        {
            AddFact(AgentFacts, Lang.T("agent_fact_board"), $"{report!.Host}:{report.Port}");
            AddFact(AgentFacts, Lang.T("agent_fact_link"),
                    Lang.T(report.Connected ? "fact_yes" : "fact_no"),
                    report.Connected ? "Ok" : "Warn");
            AddFact(AgentFacts, Lang.T("agent_fact_temps"),
                    Lang.T(report.Sensors ? "agent_temps_ok" : "agent_temps_no"),
                    report.Sensors ? "Fg" : "Warn");
            AddFact(AgentFacts, Lang.T("agent_fact_input"),
                    Lang.T(report.InputHooks ? "agent_input_ok" : "agent_input_no"),
                    report.InputHooks ? "Fg" : "Warn");
            AddFact(AgentFacts, Lang.T("agent_fact_rights"),
                    Lang.T(report.Elevated ? "agent_rights_admin" : "agent_rights_user"));
            AddFact(AgentFacts, Lang.T("agent_fact_started"),
                    report.Started.ToString("HH:mm", Lang.Culture));
        }
        AgentFacts.Visibility = fresh ? Visibility.Visible : Visibility.Collapsed;
        AgentTempsHint.Visibility = fresh && !report!.Sensors
            ? Visibility.Visible
            : Visibility.Collapsed;

        var error = fresh ? report!.LastError : null;
        AgentError.Text = string.IsNullOrEmpty(error) ? "" : Lang.T("agent_last_error", error);
        AgentError.Visibility = string.IsNullOrEmpty(error) ? Visibility.Collapsed : Visibility.Visible;

        AgentStart.IsEnabled = !running && !_agentBusy;
        AgentRestart.IsEnabled = running && !_agentBusy;
        AgentStop.IsEnabled = running && !_agentBusy;
        // Кнопка с правами нужна, только когда их не хватает: без задачи
        // планировщика или когда агент уже работает без них.
        AgentAdmin.Visibility = !_agentHasTask || (fresh && !report!.Elevated)
            ? Visibility.Visible
            : Visibility.Collapsed;
        AgentAdmin.IsEnabled = !_agentBusy;

        AgentAutostart.Text = Lang.T(_agentHasTask ? "agent_autostart_on" : "agent_autostart_off");
        AgentAutostart.Foreground = Brush(_agentHasTask ? "Ok" : "Warn");

        var lines = Agent.Tail(60);
        var text = lines.Count == 0 ? Lang.T("agent_log_empty") : string.Join(Environment.NewLine, lines);
        // Перерисовываем, только если журнал изменился: иначе каждую
        // секунду слетало бы выделение, а прокрутка прыгала бы в конец,
        // пока человек читает середину.
        if (text != _agentLog)
        {
            _agentLog = text;
            AgentLog.Text = text;
            AgentLog.ScrollToEnd();
        }
    }

    private async Task RunAgentAction(Func<Task<string?>> action, string progressKey)
    {
        _agentBusy = true;
        AgentResult.Text = Lang.T(progressKey);
        AgentResult.Foreground = Brush("Dim");
        RefreshAgent();
        string? error;
        try
        {
            error = await action();
        }
        finally
        {
            _agentBusy = false;
        }
        _agentHasTask = Agent.HasTask;
        AgentResult.Text = error ?? Lang.T("agent_done");
        AgentResult.Foreground = Brush(error is null ? "Ok" : "Alert");
        RefreshAgent();
    }

    private async void AgentStart_Click(object sender, RoutedEventArgs e)
    {
        SetAgentManaged(true);
        await RunAgentAction(Agent.StartAsync, "agent_starting");
    }

    private async void AgentRestart_Click(object sender, RoutedEventArgs e)
        => await RunAgentAction(Agent.RestartAsync, "agent_starting");

    private async void AgentStop_Click(object sender, RoutedEventArgs e)
    {
        // Иначе слежение подняло бы остановленного агента через полминуты,
        // и кнопка выглядела бы сломанной.
        SetAgentManaged(false);
        await RunAgentAction(Agent.StopAsync, "agent_stopping");
    }

    private async void AgentAdmin_Click(object sender, RoutedEventArgs e)
    {
        SetAgentManaged(true);
        await RunAgentAction(Agent.StartElevatedAsync, "agent_starting");
    }

    private void AgentKeep_Click(object sender, RoutedEventArgs e)
        => SetAgentManaged(AgentKeep.IsChecked == true);

    private void SetAgentManaged(bool on)
    {
        _settings.AgentManaged = on;
        _settings.Save();
        AgentKeep.IsChecked = on;
        // Включили слежение — проверить сразу, а не через полминуты.
        if (on) _agentChecked = DateTime.MinValue;
    }

    private void AgentLogOpen_Click(object sender, RoutedEventArgs e)
    {
        if (!System.IO.File.Exists(Agent.LogPath)) return;
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
        {
            FileName = Agent.LogPath,
            UseShellExecute = true,
        });
    }

    private async void Probe_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.Tag as string is not { } key) return;
        ProbeWindows.IsEnabled = ProbeSensors.IsEnabled = false;
        AgentProbe.Visibility = Visibility.Visible;
        AgentProbe.Text = Lang.T("agent_probe_running");
        try
        {
            AgentProbe.Text = await Agent.DiagnoseAsync(key);
        }
        finally
        {
            ProbeWindows.IsEnabled = ProbeSensors.IsEnabled = true;
        }
    }

    // -------------------------------------------------------- настройки

    private async void TestHost_Click(object sender, RoutedEventArgs e)
    {
        var host = HostBox.Text.Trim();
        var moved = !string.Equals(host, _settings.Host, StringComparison.OrdinalIgnoreCase);
        _settings.Host = _board.Host = host;
        _settings.Port = _board.Port = int.TryParse(PortBox.Text, out var port) ? port : 843;
        _settings.Save();

        TestResult.Text = Lang.T("host_testing");
        TestResult.Foreground = Brush("Dim");
        var live = await _board.LiveAsync();
        TestResult.Text = live is null
            ? Lang.T("host_bad", _board.LastError)
            : Lang.T("host_good", live.Version);
        TestResult.Foreground = Brush(live is null ? "Alert" : "Ok");
        if (live is not null) await RefreshScreensAsync();

        // Агент берёт адрес из тех же настроек, но читает его при запуске.
        // Сменили адрес — перезапускаем, иначе он продолжит стучаться туда,
        // где блока больше нет.
        if (moved && Agent.IsRunning && !_passive)
            await RunAgentAction(Agent.RestartAsync, "agent_starting");
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

    private void Autostart_Click(object sender, RoutedEventArgs e)
    {
        var want = AutostartBox.IsChecked == true;
        if (!Settings.SetAutostart(want))
        {
            AutostartBox.IsChecked = Settings.IsAutostartOn();
            TestResult.Text = Lang.T("startup_failed");
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
        CalibState.Text = Lang.T("agent_starting");
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
            _ => (Lang.T("agent_starting"), "Dim"),
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
