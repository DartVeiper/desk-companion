using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;
using MQTTnet;
using MQTTnet.Client;

namespace DeskAgent;

/// <summary>
/// ПК-агент — шаг 6 плана. Собирает состояние игрового ПК и публикует его
/// в MQTT на Pi по контракту из п.5.
/// </summary>
/// <remarks>
/// Периоды взяты из п.3 плана и подобраны так, чтобы не топить ни сеть, ни
/// базу: агрегаты ввода раз в минуту, а не по событию; звук раз в секунду;
/// железо раз в две; heartbeat раз в тридцать.
///
/// Окна у агента нет. Раньше это было консольное приложение, и его окно
/// висело на экране с момента входа в систему. Теперь он пишет в журнал и в
/// файл состояния, а показывает их и управляет агентом приложение
/// DeskCompanion — для человека это одна программа.
/// </remarks>
internal static class Program
{
    private const string TopicActiveApp = "home/pc/active_app";
    private const string TopicActivity = "home/pc/activity_1min";
    private const string TopicAudio = "home/pc/audio";
    private const string TopicHeartbeat = "home/pc/heartbeat";
    private const string TopicHardware = "home/pc/hardware";
    private const string TopicMedia = "home/pc/media";

    private static readonly TimeSpan AfkAfter = TimeSpan.FromMinutes(5);
    private static readonly TimeSpan StatusEvery = TimeSpan.FromSeconds(10);

    private static async Task<int> Main(string[] args)
    {
        // Настройки приложения читаем первым делом: из них и адрес блока, и
        // язык, на котором писать журнал и разведку.
        var app = ReadAppSettings();
        Text.Use(app.Language);

        var diagnostics = args.Contains("--windows") || args.Contains("--list-sensors");
        if (diagnostics || args.Contains("--console"))
        {
            ConnectConsole();
        }

        if (args.Contains("--windows"))
        {
            // Разведка по окнам: показывает, к чему агент относит каждую
            // запущенную программу. Хуков не ставит и к брокеру не ходит,
            // поэтому запускать можно параллельно с работающим агентом.
            foreach (var line in ActiveWindow.Describe()) Console.WriteLine(line);

            // Отдельно — вердикт по тому окну, что впереди прямо сейчас.
            // Только для него известны два последних признака: занимает ли
            // оно весь экран и сколько при этом берёт видеокарта.
            using var gpu = new HardwareMonitor();
            var (_, load, _, _) = gpu.Read();
            var (name, _, verdict) = ActiveWindow.Current(load);
            Console.WriteLine();
            Console.WriteLine(Text.T("front_now", name, verdict, load?.ToString("0") ?? "?"));
            Console.WriteLine(Text.T("overrides_from", Overrides.Path));
            return 0;
        }

        if (args.Contains("--list-sensors"))
        {
            // Режим разведки: печатает всё, что видит LibreHardwareMonitor.
            // Нужен потому, что имена датчиков различаются между вендорами и
            // поколениями, и подбирать их вслепую — гарантированный способ
            // получить пустое поле на чужой машине.
            using var probe = new HardwareMonitor();
            foreach (var line in probe.Describe()) Console.WriteLine(line);
            Console.WriteLine(Text.T(probe.SensorsAvailable ? "temps_ok" : "temps_none"));
            return 0;
        }

        // Один агент на сеанс. Второй — например, запущенный руками, пока
        // работает поднятый планировщиком, — слал бы те же топики вперемешку
        // с первым, и блок показывал бы то одно, то другое число нажатий.
        Mutex? single;
        try
        {
            single = new Mutex(true, @"Local\DeskCompanion.Agent", out var first);
            if (!first)
            {
                Log.Info(Text.T("already_running"));
                return 0;
            }
        }
        catch (UnauthorizedAccessException)
        {
            // Мьютекс есть, но создан процессом с другими правами — значит
            // агент тоже уже работает.
            Log.Info(Text.T("already_running_rights"));
            return 0;
        }

        using var owned = single;

        // Адрес блока берём из настроек приложения, если его не дали явно.
        // Раньше планировщик запускал агента без адреса, и тот искал блок по
        // имени deskpi.local — а оно из Windows резолвится через раз. Теперь
        // приложение и агент смотрят в одно место и не могут разойтись.
        var host = Argument(args, "--host") ?? app.Host ?? "deskpi.local";
        var port = int.TryParse(Argument(args, "--port"), out var p) ? p : 1883;

        var status = new AgentStatus
        {
            Host = host,
            Port = port,
            Elevated = IsElevated(),
        };

        if (Argument(args, "--host") is null) app.Report();
        Log.Info(Text.T(status.Elevated ? "start_admin" : "start_user", host, port));

        using var input = new InputCounter();
        using var audio = new AudioMonitor();
        var media = new NowPlaying();
        using var hardware = new HardwareMonitor();

        status.InputHooks = input.Installed;
        if (!input.Installed)
        {
            Log.Error(Text.T("no_hooks"));
        }
        status.Save();

        var factory = new MqttFactory();
        using var client = factory.CreateMqttClient();
        var options = new MqttClientOptionsBuilder()
            .WithTcpServer(host, port)
            .WithClientId("desk-pc-agent")
            .WithCleanSession()
            // Пропажу агента Pi обязан замечать сам (п.5), но и брокеру
            // полезно закрыть сессию, если ПК выключили без предупреждения.
            .WithKeepAlivePeriod(TimeSpan.FromSeconds(30))
            .Build();

        using var stopping = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; stopping.Cancel(); };
        AppDomain.CurrentDomain.ProcessExit += (_, _) => stopping.Cancel();

        var lastMinute = DateTime.UtcNow;
        var lastHardware = DateTime.MinValue;
        var lastHeartbeat = DateTime.MinValue;
        var lastStatus = DateTime.MinValue;
        var lastApp = "";
        // Последняя известная загрузка видеокарты. Нужна разбору окна:
        // полноэкранное окно с загруженной видеокартой — это игра, и это
        // единственный признак, работающий для игры, поставленной куда
        // попало. Берём последнее значение, а не свежее: читать датчики
        // ради каждой проверки окна дорого, а за пару секунд загрузка
        // видеокарты не меняется настолько, чтобы это меняло вывод.
        double? lastGpuLoad = null;
        var lastTrack = "";
        var lastAudio = (bool?)null;
        var warnedAboutRights = false;
        var reportedFailure = "";

        while (!stopping.IsCancellationRequested)
        {
            try
            {
                if (!client.IsConnected)
                {
                    if (status.Connected)
                    {
                        status.Connected = false;
                        status.Save();
                    }
                    try
                    {
                        await client.ConnectAsync(options, stopping.Token);
                        Log.Info(Text.T("connected", host, port));
                        status.Connected = true;
                        status.LastError = null;
                        reportedFailure = "";
                        status.Save();
                    }
                    catch (Exception ex) when (ex is not OperationCanceledException)
                    {
                        // Одна и та же ошибка повторяется каждые пять секунд,
                        // пока блок недоступен. В журнал — только первая: иначе
                        // за ночь без блока он состоял бы из неё одной.
                        var failure = Text.T("connect_failed", host, port, ex.Message);
                        if (failure != reportedFailure)
                        {
                            reportedFailure = failure;
                            Log.Error(failure);
                            if (host.EndsWith(".local", StringComparison.OrdinalIgnoreCase))
                            {
                                // Самая частая причина — не брокер, а имя:
                                // *.local из Windows резолвится через раз.
                                Log.Error(Text.T("local_names"));
                            }
                        }
                        status.LastError = failure;
                        status.Save();
                        throw new ConnectFailed();
                    }
                }

                var now = DateTime.UtcNow;

                // Активное окно — только на смену, а не по таймеру: иначе
                // один и тот же топик перепубликовывался бы сотни раз в час.
                var (process, _, category) = ActiveWindow.Current(lastGpuLoad);
                var appKey = $"{process}|{category}";
                if (appKey != lastApp)
                {
                    lastApp = appKey;
                    await Publish(client, TopicActiveApp,
                        JsonSerializer.Serialize(new { app = process, category }), true, stopping.Token);
                }

                // Трек — на смену, а не по таймеру: песня живёт минуты,
                // и перепубликовывать её каждую секунду незачем.
                var track = await media.CurrentAsync();
                if (track.Key != lastTrack)
                {
                    lastTrack = track.Key;
                    await Publish(client, TopicMedia, JsonSerializer.Serialize(new
                    {
                        artist = track.Artist,
                        title = track.Title,
                        playing = track.Playing,
                    }), true, stopping.Token);
                }

                var playing = audio.IsPlaying();
                if (playing != lastAudio)
                {
                    lastAudio = playing;
                    await Publish(client, TopicAudio, playing ? "1" : "0", true, stopping.Token);
                }

                if (now - lastMinute >= TimeSpan.FromMinutes(1))
                {
                    lastMinute = now;
                    var (keys, clicks) = input.Drain();
                    await Publish(client, TopicActivity, JsonSerializer.Serialize(new
                    {
                        keys,
                        clicks,
                        mouse_px = 0,
                        afk = input.IdleFor > AfkAfter,
                    }), true, stopping.Token);
                }

                if (now - lastHardware >= TimeSpan.FromSeconds(2))
                {
                    lastHardware = now;
                    var (gpuTemp, gpuLoad, cpuTemp, cpuLoad) = hardware.Read();
                    lastGpuLoad = gpuLoad;
                    await Publish(client, TopicHardware, JsonSerializer.Serialize(new
                    {
                        gpu_temp = gpuTemp,
                        gpu_load = gpuLoad,
                        cpu_temp = cpuTemp,
                        cpu_load = cpuLoad,
                    }), true, stopping.Token);

                    if (status.Sensors != hardware.SensorsAvailable)
                    {
                        status.Sensors = hardware.SensorsAvailable;
                        status.Save();
                    }
                    if (!hardware.SensorsAvailable && !warnedAboutRights)
                    {
                        warnedAboutRights = true;
                        // П.6 плана: без прав администратора Режим 5 остаётся
                        // пустым молча. Пусть хотя бы здесь будет сказано.
                        Log.Error(Text.T("no_temps"));
                    }
                }

                if (now - lastHeartbeat >= TimeSpan.FromSeconds(30))
                {
                    lastHeartbeat = now;
                    // Heartbeat без retain: сохранённая метка времени после
                    // выключения ПК врала бы, что агент жив.
                    await Publish(client, TopicHeartbeat,
                        DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString(), false, stopping.Token);
                }

                // Метка «жив» для приложения. По ней оно отличает работающего
                // агента от зависшего: процесс может существовать и ничего не
                // делать, а обновляемая метка — нет.
                if (now - lastStatus >= StatusEvery)
                {
                    lastStatus = now;
                    status.Save();
                }

                await Task.Delay(TimeSpan.FromSeconds(1), stopping.Token);
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex)
            {
                // Обрыв сети или перезагрузка Pi не должны ронять агента:
                // п.9 требует, чтобы после выключения роутера на минуту всё
                // вернулось само.
                //
                // Неудачное подключение уже записано там, где случилось, и с
                // понятным текстом, — второй раз его не пишем.
                if (ex is not ConnectFailed)
                {
                    var failure = $"{ex.GetType().Name}: {ex.Message}";
                    if (failure != reportedFailure)
                    {
                        reportedFailure = failure;
                        Log.Error(failure);
                    }
                    // Публикация упала — значит, связь с брокером порвалась,
                    // даже если клиент ещё об этом не знает.
                    status.Connected = client.IsConnected;
                    status.LastError = failure;
                    status.Save();
                }
                try { await Task.Delay(TimeSpan.FromSeconds(5), stopping.Token); }
                catch (OperationCanceledException) { break; }
            }
        }

        if (client.IsConnected)
        {
            try { await client.DisconnectAsync(); } catch { }
        }
        status.Connected = false;
        status.Save();
        Log.Info(Text.T("stopped"));
        return 0;
    }

    private static Task Publish(IMqttClient client, string topic, string payload,
                                bool retain, CancellationToken token)
    {
        // retain на топиках состояния — требование п.5: после перезапуска
        // сервиса на Pi экран должен сразу получить последнее известное
        // значение, а не показывать пустоту.
        var message = new MqttApplicationMessageBuilder()
            .WithTopic(topic)
            .WithPayload(payload)
            .WithRetainFlag(retain)
            .Build();
        return client.PublishAsync(message, token);
    }

    /// <summary>Подключение не удалось; причина уже в журнале.</summary>
    private sealed class ConnectFailed : Exception
    {
    }

    private static string? Argument(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    /// <summary>Что агенту нужно из настроек приложения.</summary>
    private sealed record AppSettings(string? Host, string? Language, string Path, Exception? Error)
    {
        /// <summary>
        /// Сказать в журнал, почему адреса нет. Молча откатываться на
        /// deskpi.local нельзя: именно так агент однажды и уехал на имя,
        /// которое из Windows находится через раз, хотя адрес цифрами лежал в
        /// настройках, — и понять это было не по чему.
        /// </summary>
        public void Report()
        {
            if (Host is not null) return;
            if (Error is null or FileNotFoundException or DirectoryNotFoundException)
            {
                // Обычное дело для свежей установки: адрес ещё не задавали.
                Log.Info(Text.T("no_host"));
            }
            else
            {
                Log.Error(Text.T("settings_unreadable", Path, Error.Message));
            }
        }
    }

    private static AppSettings ReadAppSettings()
    {
        var path = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData,
                                      Environment.SpecialFolderOption.DoNotVerify),
            "DeskCompanion", "settings.json");
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(path));
            return new AppSettings(Field(doc.RootElement, "Host"),
                                   Field(doc.RootElement, "Language"), path, null);
        }
        catch (Exception error)
        {
            return new AppSettings(null, null, path, error);
        }
    }

    private static string? Field(JsonElement root, string name)
    {
        if (!root.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.String)
            return null;
        var text = value.GetString()?.Trim();
        return string.IsNullOrEmpty(text) ? null : text;
    }

    private static bool IsElevated()
    {
        try
        {
            using var identity = WindowsIdentity.GetCurrent();
            return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
        }
        catch
        {
            return false;
        }
    }

    // --------------------------------------------------------- консоль

    private const int AttachParentProcess = -1;
    private const int StdOutputHandle = -11;
    private const uint GenericWrite = 0x40000000;
    private const uint FileShareWrite = 0x2;
    private const uint OpenExisting = 3;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AttachConsole(int processId);

    [DllImport("kernel32.dll")]
    private static extern IntPtr GetStdHandle(int handle);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFileW(string name, uint access, uint share,
                                             IntPtr security, uint disposition,
                                             uint flags, IntPtr template);

    /// <summary>
    /// Вывод для разведки и отладки.
    /// </summary>
    /// <remarks>
    /// У программы без окна консоли своей нет, и печать уходит в никуда. Два
    /// случая, когда вывод всё-таки нужен.
    ///
    /// Выход перенаправлен — приложение запустило разведку и читает ответ,
    /// или человек написал <c>| more</c>. Тогда поток уже есть, пишем в него.
    ///
    /// Агента запустили из терминала руками. Тогда пристраиваемся к консоли
    /// этого терминала. Стандартные потоки при этом не появляются сами —
    /// Windows не выдаёт их программам без окна, — и консоль приходится
    /// открывать по имени.
    /// </remarks>
    private static void ConnectConsole()
    {
        var utf8 = new UTF8Encoding(false);
        var handle = GetStdHandle(StdOutputHandle);
        var redirected = handle != IntPtr.Zero && handle != new IntPtr(-1);

        if (redirected)
        {
            Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), utf8) { AutoFlush = true });
            Console.SetError(new StreamWriter(Console.OpenStandardError(), utf8) { AutoFlush = true });
            return;
        }

        if (!AttachConsole(AttachParentProcess)) return;

        var console = CreateFileW("CONOUT$", GenericWrite, FileShareWrite,
                                  IntPtr.Zero, OpenExisting, 0, IntPtr.Zero);
        if (console == IntPtr.Zero || console == new IntPtr(-1)) return;

        try { Console.OutputEncoding = utf8; } catch { }
        var writer = new StreamWriter(
            new FileStream(new SafeFileHandle(console, ownsHandle: true), FileAccess.Write), utf8)
        {
            AutoFlush = true,
        };
        Console.SetOut(writer);
        Console.SetError(writer);
        // Терминал не ждёт программу без окна и уже напечатал приглашение —
        // начинаем с новой строки, чтобы не писать поверх него.
        Console.WriteLine();
    }
}
