using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using LibreHardwareMonitor.Hardware;
using NAudio.CoreAudioApi;

namespace DeskAgent;

/// <summary>
/// Активное окно и его категория.
/// </summary>
public static class ActiveWindow
{
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hWnd, out Rect rect);
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);

    private struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr hWnd, char[] text, int count);

    // Категории по имени процесса. Список — только для того, что признаками
    // не ловится: у браузеров и сред разработки нет общего каталога или
    // суффикса, зато их немного и они меняются раз в несколько лет.
    private static readonly Dictionary<string, string> Known = new(StringComparer.OrdinalIgnoreCase)
    {
        ["rider64"] = "code", ["devenv"] = "code", ["code"] = "code",
        ["Code - Insiders"] = "code", ["cursor"] = "code", ["pycharm64"] = "code",
        ["idea64"] = "code", ["clion64"] = "code", ["webstorm64"] = "code",
        ["goland64"] = "code", ["sublime_text"] = "code", ["WindowsTerminal"] = "code",
        ["powershell"] = "code", ["pwsh"] = "code", ["mintty"] = "code",

        ["chrome"] = "browser", ["firefox"] = "browser", ["msedge"] = "browser",
        ["opera"] = "browser", ["opera_gx"] = "browser", ["brave"] = "browser",
        ["vivaldi"] = "browser", ["browser"] = "browser", ["zen"] = "browser",
        ["arc"] = "browser",

        ["steam"] = "game", ["EpicGamesLauncher"] = "game",
    };

    //: Каталоги игровых библиотек. Признак сильный: под steamapps\\common
    //: не лежит ничего, кроме игр.
    private static readonly string[] GameFolders =
    {
        @"\steamapps\common\", @"\Epic Games\", @"\GOG Galaxy\Games\",
        @"\Riot Games\", @"\Battle.net\", @"\Origin Games\",
        @"\EA Games\", @"\Ubisoft\", @"\XboxGames\",
    };

    /// <summary>
    /// Что за программа сейчас впереди и к чему её отнести.
    ///
    /// gpuLoad — загрузка видеокарты в этот момент. Нужна для последнего
    /// признака: см. Guess.
    /// </summary>
    public static (string Process, string Title, string Category) Current(
        double? gpuLoad = null)
    {
        var handle = GetForegroundWindow();
        if (handle == IntPtr.Zero) return ("", "", "other");

        GetWindowThreadProcessId(handle, out var pid);
        var buffer = new char[512];
        var length = GetWindowTextW(handle, buffer, buffer.Length);
        var title = length > 0 ? new string(buffer, 0, length) : "";

        Process handleProcess;
        try { handleProcess = Process.GetProcessById((int)pid); }
        catch (ArgumentException) { return ("", title, "other"); }

        var process = handleProcess.ProcessName;
        if (Overrides.For(process) is { } chosen) return (process, title, chosen);
        if (Known.TryGetValue(process, out var known)) return (process, title, known);
        return (process, title,
                Guess(process, PathOf(handleProcess), gpuLoad, Fullscreen(handle)));
    }

    /// <summary>
    /// Как агент видит каждое окно на экране.
    ///
    /// Нужно, чтобы вопрос «почему часы считают, что я занят прочим»
    /// решался за десять секунд, а не перебором догадок: видно и имя
    /// процесса, и путь, по которому принималось решение.
    /// </summary>
    public static IEnumerable<string> Describe()
    {
        yield return $"{"процесс",-34} {"занятие",-9} путь";
        yield return new string('-', 100);
        foreach (var process in Process.GetProcesses()
                     .Where(p => p.MainWindowHandle != IntPtr.Zero)
                     .OrderBy(p => p.ProcessName))
        {
            var path = PathOf(process);
            var category = Known.TryGetValue(process.ProcessName, out var known)
                ? known + " (по списку)"
                : Guess(process.ProcessName, path);
            yield return $"{process.ProcessName,-34} {category,-9} "
                         + (path.Length > 0 ? path : "путь не виден");
        }
    }

    /// <summary>Окно занимает весь экран целиком.</summary>
    private static bool Fullscreen(IntPtr handle)
    {
        if (!GetWindowRect(handle, out var rect)) return false;
        var width = GetSystemMetrics(0);
        var height = GetSystemMetrics(1);
        if (width <= 0 || height <= 0) return false;
        // С допуском: у игр в «оконном без рамки» края иногда на пиксель
        // выходят за экран, а у развёрнутого окна снизу остаётся панель.
        return rect.Right - rect.Left >= width - 2
            && rect.Bottom - rect.Top >= height - 2;
    }

    /// <summary>Путь к файлу процесса. Пусто — не дали посмотреть.</summary>
    private static string PathOf(Process process)
    {
        try { return process.MainModule?.FileName ?? ""; }
        catch (Exception)
        {
            // Процесс запущен от администратора, а мы нет — обычное дело,
            // и это не повод ничего не определить: суффикс имени остаётся.
            return "";
        }
    }

    /// <summary>
    /// Угадать занятие по признакам, а не по имени.
    ///
    /// Список игр вести руками бессмысленно: их у человека десятки, и
    /// каждая новая до правки кода будет считаться «прочим». Признаки же
    /// покрывают сразу целые классы:
    ///
    ///   • каталог игровой библиотеки — под steamapps\common и подобными
    ///     не лежит ничего, кроме игр;
    ///   • суффикс -Win64-Shipping — так называется собранная под релиз
    ///     игра на Unreal Engine, любая;
    ///   • папка «Имя_Data» рядом с файлом — так устроена любая сборка Unity.
    /// </summary>
    //: Ниже этой загрузки видеокарты полноэкранное окно игрой не считаем.
    //: Сорок процентов выбраны так, чтобы не поймать видео на весь экран:
    //: аппаратное декодирование редко забирает больше двадцати.
    private const double GameGpuLoad = 40;

    internal static string Guess(string process, string path,
                                 double? gpuLoad = null, bool fullscreen = false)
    {
        if (process.EndsWith("-Win64-Shipping", StringComparison.OrdinalIgnoreCase) ||
            process.EndsWith("-WinGDK-Shipping", StringComparison.OrdinalIgnoreCase))
            return "game";

        if (path.Length == 0) return "other";

        foreach (var folder in GameFolders)
            if (path.Contains(folder, StringComparison.OrdinalIgnoreCase))
                return "game";

        try
        {
            var folder = Path.GetDirectoryName(path);
            var name = Path.GetFileNameWithoutExtension(path);
            if (folder is not null && name.Length > 0 &&
                Directory.Exists(Path.Combine(folder, name + "_Data")))
                return "game";
        }
        catch (Exception)
        {
            // Путь с недопустимыми символами — просто не угадали.
        }

        // Последний признак — по поведению, а не по месту на диске. Он
        // единственный работает для игры, поставленной куда попало: сборка
        // модов Skyrim живёт в E:\OmenRim\Stock Game, ни Steam, ни Unreal,
        // ни Unity её не выдают. Зато игра всегда занимает весь экран и
        // всегда грузит видеокарту — этим она от всего остального и
        // отличается.
        if (fullscreen && gpuLoad >= GameGpuLoad) return "game";

        return "other";
    }
}

/// <summary>
/// Подсчёт ввода низкоуровневыми хуками.
/// </summary>
/// <remarks>
/// Считаем агрегаты за минуту, а не отдельные события: п.4 плана прямо
/// запрещает строку в базе на каждое нажатие — это утопит базу и сожжёт
/// microSD. Наружу отдаётся только количество, ни один код клавиши не
/// покидает этот класс и никуда не пишется.
/// </remarks>
public sealed class InputCounter : IDisposable
{
    private const int WH_KEYBOARD_LL = 13, WH_MOUSE_LL = 14;
    private const int WM_KEYDOWN = 0x0100, WM_SYSKEYDOWN = 0x0104;
    private const int WM_LBUTTONDOWN = 0x0201, WM_RBUTTONDOWN = 0x0204, WM_MBUTTONDOWN = 0x0207;

    private delegate IntPtr HookProc(int code, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    private static extern IntPtr SetWindowsHookEx(int idHook, HookProc fn, IntPtr module, uint threadId);
    [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    private static extern bool UnhookWindowsHookEx(IntPtr hook);
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    private static extern IntPtr CallNextHookEx(IntPtr hook, int code, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    private static extern IntPtr GetModuleHandle(string? name);

    private const uint WM_QUIT = 0x0012;

    [StructLayout(LayoutKind.Sequential)]
    private struct MSG
    {
        public IntPtr Hwnd;
        public uint Message;
        public IntPtr WParam;
        public IntPtr LParam;
        public uint Time;
        public int X;
        public int Y;
    }

    [DllImport("user32.dll")]
    private static extern int GetMessage(out MSG message, IntPtr hwnd, uint filterMin, uint filterMax);

    [DllImport("user32.dll")]
    private static extern bool TranslateMessage(ref MSG message);

    [DllImport("user32.dll")]
    private static extern IntPtr DispatchMessage(ref MSG message);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool PostThreadMessage(uint threadId, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();

    // Ссылки на делегаты держим полями: если их соберёт сборщик мусора,
    // Windows позовёт освобождённую память и процесс упадёт. Это классическая
    // ошибка при работе с хуками, и проявляется она не сразу.
    private readonly HookProc _keyboardProc;
    private readonly HookProc _mouseProc;
    private IntPtr _keyboardHook, _mouseHook;

    private long _keys, _clicks;
    private DateTime _lastInput = DateTime.UtcNow;

    // Хуки живут на своём потоке, и у этого потока есть цикл разбора
    // сообщений. Причина важная. Низкоуровневые хуки Windows доставляет
    // через очередь сообщений потока, который их поставил. Если очередь
    // никто не разбирает, система на КАЖДОМ событии ввода ждёт таймаут
    // (по умолчанию 300 мс) и только потом пропускает его дальше.
    //
    // Раньше хуки ставились на главном потоке, а он уходил в асинхронный
    // цикл без разбора сообщений — и весь ввод в системе начинал тормозить:
    // мышь двигалась рывками, курсор шёл кадра по три в секунду. Выглядело
    // как «агент повесил компьютер», и по сути так и было.
    private Thread? _pump;
    private uint _pumpThread;
    private readonly ManualResetEventSlim _ready = new(false);

    public InputCounter()
    {
        _keyboardProc = KeyboardCallback;
        _mouseProc = MouseCallback;

        _pump = new Thread(PumpLoop)
        {
            IsBackground = true,
            Name = "desk-agent-input-hooks",
        };
        _pump.Start();
        _ready.Wait(TimeSpan.FromSeconds(5));
    }

    private void PumpLoop()
    {
        _pumpThread = GetCurrentThreadId();
        var module = GetModuleHandle(null);
        _keyboardHook = SetWindowsHookEx(WH_KEYBOARD_LL, _keyboardProc, module, 0);
        _mouseHook = SetWindowsHookEx(WH_MOUSE_LL, _mouseProc, module, 0);
        _ready.Set();

        // Сам цикл ничего не делает: он нужен, чтобы очередь сообщений
        // разбиралась и Windows не ждала таймаут на каждом событии.
        while (GetMessage(out var message, IntPtr.Zero, 0, 0) > 0)
        {
            TranslateMessage(ref message);
            DispatchMessage(ref message);
        }

        if (_keyboardHook != IntPtr.Zero) { UnhookWindowsHookEx(_keyboardHook); _keyboardHook = IntPtr.Zero; }
        if (_mouseHook != IntPtr.Zero) { UnhookWindowsHookEx(_mouseHook); _mouseHook = IntPtr.Zero; }
    }

    public bool Installed => _keyboardHook != IntPtr.Zero && _mouseHook != IntPtr.Zero;
    public TimeSpan IdleFor => DateTime.UtcNow - _lastInput;

    private IntPtr KeyboardCallback(int code, IntPtr wParam, IntPtr lParam)
    {
        if (code >= 0)
        {
            var message = (int)wParam;
            if (message is WM_KEYDOWN or WM_SYSKEYDOWN)
            {
                Interlocked.Increment(ref _keys);
                _lastInput = DateTime.UtcNow;
            }
        }
        return CallNextHookEx(_keyboardHook, code, wParam, lParam);
    }

    private IntPtr MouseCallback(int code, IntPtr wParam, IntPtr lParam)
    {
        if (code >= 0)
        {
            var message = (int)wParam;
            if (message is WM_LBUTTONDOWN or WM_RBUTTONDOWN or WM_MBUTTONDOWN)
                Interlocked.Increment(ref _clicks);
            _lastInput = DateTime.UtcNow;
        }
        return CallNextHookEx(_mouseHook, code, wParam, lParam);
    }

    /// <summary>Забрать накопленное и обнулить счётчики.</summary>
    public (long Keys, long Clicks) Drain() =>
        (Interlocked.Exchange(ref _keys, 0), Interlocked.Exchange(ref _clicks, 0));

    public void Dispose()
    {
        // Снимать хуки обязан тот же поток, что их ставил, поэтому просим
        // цикл завершиться, а он снимет их сам на выходе.
        if (_pumpThread != 0)
        {
            PostThreadMessage(_pumpThread, WM_QUIT, IntPtr.Zero, IntPtr.Zero);
            _pump?.Join(TimeSpan.FromSeconds(2));
            _pumpThread = 0;
        }
        _ready.Dispose();
    }
}

/// <summary>
/// Играет ли что-нибудь звук прямо сейчас.
/// </summary>
public sealed class AudioMonitor : IDisposable
{
    private readonly MMDeviceEnumerator _enumerator = new();

    // Порог по пику на выходе. Полная тишина даёт ровно ноль, поэтому
    // достаточно отсечь околонулевой шум.
    private const float Threshold = 0.002f;

    public bool IsPlaying()
    {
        try
        {
            // Устройство запрашиваем каждый раз, а не кэшируем: наушники
            // втыкают и вынимают, и старая ссылка станет недействительной.
            var device = _enumerator.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia);
            return device.AudioMeterInformation.MasterPeakValue > Threshold;
        }
        catch (Exception)
        {
            return false;  // устройства нет или его меняют прямо сейчас
        }
    }

    public void Dispose() => _enumerator.Dispose();
}

/// <summary>
/// Температуры и загрузка через LibreHardwareMonitor.
/// </summary>
/// <remarks>
/// Ради этого агент и написан на C#: LHM — .NET-библиотека, и здесь она
/// работает внутри процесса. Из Python пришлось бы держать запущенным само
/// приложение LHM и парсить его веб-сервер.
///
/// Без прав администратора датчики температуры молча отдают пустоту (п.6
/// плана). Поэтому <see cref="SensorsAvailable"/> проверяется явно и
/// сообщается наружу, а не выясняется по пустому Режиму 5.
/// </remarks>
public sealed class HardwareMonitor : IDisposable
{
    private sealed class UpdateVisitor : IVisitor
    {
        public void VisitComputer(IComputer computer) => computer.Traverse(this);
        public void VisitHardware(IHardware hardware)
        {
            hardware.Update();
            foreach (var sub in hardware.SubHardware) sub.Accept(this);
        }
        public void VisitSensor(ISensor sensor) { }
        public void VisitParameter(IParameter parameter) { }
    }

    private readonly Computer _computer;
    private readonly UpdateVisitor _visitor = new();

    public HardwareMonitor()
    {
        _computer = new Computer
        {
            IsCpuEnabled = true,
            IsGpuEnabled = true,
        };
        _computer.Open();
    }

    public bool SensorsAvailable { get; private set; } = true;

    /// <summary>Все найденные датчики — чтобы не угадывать их имена.</summary>
    public IEnumerable<string> Describe()
    {
        _computer.Accept(_visitor);
        foreach (var hardware in _computer.Hardware)
        {
            yield return $"{hardware.HardwareType}  {hardware.Name}";
            foreach (var sensor in hardware.Sensors)
            {
                var value = sensor.Value is { } v ? v.ToString("0.##") : "—";
                yield return $"    {sensor.SensorType,-12} {sensor.Name,-28} {value}";
            }
        }
    }

    public (double? GpuTemp, double? GpuLoad, double? CpuTemp, double? CpuLoad) Read()
    {
        double? gpuTemp = null, gpuLoad = null, cpuTemp = null, cpuLoad = null;
        var gpuIsDiscrete = false;

        try
        {
            _computer.Accept(_visitor);
            foreach (var hardware in _computer.Hardware)
            {
                var discrete = hardware.HardwareType is HardwareType.GpuNvidia
                    or HardwareType.GpuAmd;
                var isGpu = discrete || hardware.HardwareType == HardwareType.GpuIntel;
                var isCpu = hardware.HardwareType == HardwareType.Cpu;
                if (!isGpu && !isCpu) continue;

                // На машине с дискретной картой видеоядро процессора тоже
                // числится видеокартой, и показания встроенного затирали бы
                // настоящие. Дискретная всегда важнее; встроенную берём,
                // только если другой нет вовсе.
                if (isGpu && gpuIsDiscrete && !discrete) continue;
                if (isGpu && discrete && !gpuIsDiscrete)
                {
                    gpuIsDiscrete = true;
                    gpuTemp = gpuLoad = null;  // сбрасываем показания встроенного
                }

                foreach (var sensor in hardware.Sensors)
                {
                    if (sensor.Value is not { } value) continue;

                    if (sensor.SensorType == SensorType.Temperature)
                    {
                        // У видеокарты нужный датчик так и зовётся — GPU Core.
                        if (isGpu && sensor.Name.Contains("Core")) gpuTemp = value;
                        else if (isGpu) gpuTemp ??= value;
                        else if (sensor.Name.Contains("Package") || sensor.Name.Contains("Core"))
                            cpuTemp ??= value;
                    }
                    else if (sensor.SensorType == SensorType.Load)
                    {
                        // Имена различаются: у процессора это «CPU Total», у
                        // видеокарты «GPU Core». Раньше и там и там искалось
                        // «Total», и загрузка видеокарты не находилась никогда.
                        if (isGpu && sensor.Name == "GPU Core") gpuLoad = value;
                        else if (!isGpu && sensor.Name.Contains("Total")) cpuLoad ??= value;
                    }
                }
            }
        }
        catch (Exception)
        {
            SensorsAvailable = false;
            return (null, null, null, null);
        }

        // Загрузка читается и без прав, а температура — не всегда. Пустая
        // температура при живой загрузке означает именно нехватку прав.
        SensorsAvailable = cpuTemp is not null || gpuTemp is not null;
        return (gpuTemp, gpuLoad, cpuTemp, cpuLoad);
    }

    public void Dispose() => _computer.Close();
}
