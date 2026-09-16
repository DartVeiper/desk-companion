using System.Text.Json;
using DeskCompanion.Services;
using MQTTnet;
using MQTTnet.Client;

namespace DeskCompanion.Collection;

/// <summary>Что сбор знает о себе — для страницы «Компьютер».</summary>
public sealed record CollectorState
{
    public bool Enabled { get; init; }
    public bool Running { get; init; }
    public DateTime? Started { get; init; }
    public string Host { get; init; } = "";
    public int Port { get; init; } = Collector.MqttPort;
    public bool Connected { get; init; }
    public bool Elevated { get; init; }
    public bool Sensors { get; init; }
    public bool InputHooks { get; init; }
    public string? LastError { get; init; }
    public string ActiveApp { get; init; } = "";
    public string Category { get; init; } = "";
}

/// <summary>
/// Сбор данных с компьютера и отправка их блоку по MQTT.
/// </summary>
/// <remarks>
/// Раньше это была отдельная программа — агент. Две программы на одном
/// компьютере раздражали: два файла, два автозапуска, два окна в диспетчере
/// задач, и агент со своей консолью висел на экране. Теперь сбор — часть
/// приложения и идёт в фоне, пока оно запущено, в том числе свёрнутым в
/// трей.
///
/// Права администратора нужны только температурам: LibreHardwareMonitor без
/// них молчит. Их приложение получает от задачи планировщика, которой само
/// себя и заводит. Запущенное без прав, оно собирает всё остальное и прямо
/// говорит, чего не хватает.
///
/// Периоды — из п.3 плана и подобраны так, чтобы не топить ни сеть, ни
/// базу: агрегаты ввода раз в минуту, а не по событию; звук раз в секунду;
/// железо раз в две; heartbeat раз в тридцать.
/// </remarks>
public sealed class Collector
{
    public const int MqttPort = 1883;

    private const string TopicActiveApp = "home/pc/active_app";
    private const string TopicActivity = "home/pc/activity_1min";
    private const string TopicAudio = "home/pc/audio";
    private const string TopicHeartbeat = "home/pc/heartbeat";
    private const string TopicHardware = "home/pc/hardware";
    private const string TopicMedia = "home/pc/media";

    private static readonly TimeSpan AfkAfter = TimeSpan.FromMinutes(5);

    private readonly Func<string> _host;
    private readonly object _gate = new();
    private CollectorState _state = new();
    private CancellationTokenSource? _stop;
    private Task? _loop;
    private volatile bool _reconnect;
    private volatile HardwareMonitor? _hardware;

    /// <param name="host">Адрес блока. Спрашивается при каждом подключении,
    /// так что смена адреса в настройках подхватывается без перезапуска.</param>
    public Collector(Func<string> host) => _host = host;

    /// <summary>Состояние изменилось. Зовётся не из потока окна.</summary>
    public event Action? Changed;

    public CollectorState State
    {
        get { lock (_gate) return _state; }
    }

    public void Start()
    {
        lock (_gate)
        {
            if (_loop is { IsCompleted: false }) return;
            _stop = new CancellationTokenSource();
            var token = _stop.Token;
            // Отдельный поток пула, а не поток окна: COM-объекты звука и
            // медиа живут в многопоточном апартаменте, а поток окна — нет.
            _loop = Task.Run(() => RunAsync(token));
        }
        Update(s => s with { Enabled = true });
    }

    public async Task StopAsync()
    {
        Task? loop;
        lock (_gate)
        {
            loop = _loop;
            _stop?.Cancel();
        }
        if (loop is not null)
        {
            try { await loop.WaitAsync(TimeSpan.FromSeconds(5)); }
            catch (Exception) { /* остановка — не место для исключений */ }
        }
        Update(s => s with { Enabled = false, Running = false, Connected = false });
    }

    /// <summary>
    /// Переподключиться — адрес блока сменился. Соединение рвётся, и цикл
    /// подключается уже по новому адресу.
    /// </summary>
    public void Reconnect() => _reconnect = true;

    /// <summary>
    /// Датчики для разведки: список, читаются ли температуры и загрузка
    /// видеокарты. Долгая операция — звать не из потока окна.
    /// </summary>
    /// <remarks>
    /// Работающий сбор отдаёт свой монитор: второй экземпляр закрыл бы
    /// драйвер и сбору (см. HardwareMonitor). Свой заводится, только когда
    /// сбор выключен.
    /// </remarks>
    public (IReadOnlyList<string> Lines, bool Temps, double? GpuLoad) ProbeHardware()
    {
        if (_hardware?.Probe() is { } shared) return shared;
        using var own = new HardwareMonitor();
        return own.Probe() ?? (Array.Empty<string>(), false, null);
    }

    private void Update(Func<CollectorState, CollectorState> change)
    {
        lock (_gate) _state = change(_state);
        Changed?.Invoke();
    }

    private async Task RunAsync(CancellationToken stopping)
    {
        Update(s => s with
        {
            Running = true,
            Started = DateTime.Now,
            Host = _host(),
            Elevated = Elevation.IsElevated,
            Port = MqttPort,
            LastError = null,
        });
        ComputerLog.Info(Lang.T(Elevation.IsElevated ? "log_start_admin" : "log_start_user",
                                _host(), MqttPort));

        using var input = new InputCounter();
        using var audio = new AudioMonitor();
        var media = new NowPlaying();
        using var hardware = new HardwareMonitor();
        _hardware = hardware;

        // Датчики — сразу, а не после подключения: права и температуры от
        // связи с блоком не зависят, и страница «Компьютер» не должна
        // говорить «температур нет», пока блок просто не отвечает.
        var (_, firstGpuLoad, _, _) = hardware.Read();
        Update(s => s with { InputHooks = input.Installed, Sensors = hardware.SensorsAvailable });
        if (!input.Installed) ComputerLog.Error(Lang.T("log_no_hooks"));

        var factory = new MqttFactory();
        using var client = factory.CreateMqttClient();

        var lastMinute = DateTime.UtcNow;
        var lastHardware = DateTime.MinValue;
        var lastHeartbeat = DateTime.MinValue;
        var lastApp = "";
        // Последняя известная загрузка видеокарты. Нужна разбору окна:
        // полноэкранное окно с загруженной видеокартой — это игра, и это
        // единственный признак, работающий для игры, поставленной куда
        // попало. Берём последнее значение, а не свежее: читать датчики
        // ради каждой проверки окна дорого, а за пару секунд загрузка
        // видеокарты не меняется настолько, чтобы это меняло вывод.
        double? lastGpuLoad = firstGpuLoad;
        var lastTrack = "";
        var lastAudio = (bool?)null;
        var warnedAboutRights = false;
        if (!hardware.SensorsAvailable)
        {
            warnedAboutRights = true;
            // П.6 плана: без прав администратора экран GPU / CPU остаётся
            // пустым молча. Пусть хотя бы здесь будет сказано.
            ComputerLog.Error(Lang.T("log_no_temps"));
        }
        var reportedFailure = "";
        var host = _host();

        while (!stopping.IsCancellationRequested)
        {
            try
            {
                if (_reconnect)
                {
                    _reconnect = false;
                    if (client.IsConnected)
                    {
                        try { await client.DisconnectAsync(); } catch (Exception) { }
                    }
                }

                if (!client.IsConnected)
                {
                    if (State.Connected) Update(s => s with { Connected = false });

                    host = _host();
                    var options = new MqttClientOptionsBuilder()
                        .WithTcpServer(host, MqttPort)
                        .WithClientId("desk-pc-agent")
                        .WithCleanSession()
                        // Пропажу компьютера Pi обязан замечать сам (п.5), но и
                        // брокеру полезно закрыть сессию, если ПК выключили без
                        // предупреждения.
                        .WithKeepAlivePeriod(TimeSpan.FromSeconds(30))
                        .Build();
                    try
                    {
                        using var attempt = CancellationTokenSource.CreateLinkedTokenSource(stopping);
                        attempt.CancelAfter(TimeSpan.FromSeconds(8));
                        await client.ConnectAsync(options, attempt.Token);
                        ComputerLog.Info(Lang.T("log_connected", host, MqttPort));
                        reportedFailure = "";
                        Update(s => s with { Connected = true, Host = host, LastError = null });
                    }
                    catch (Exception ex) when (!stopping.IsCancellationRequested)
                    {
                        // Одна и та же ошибка повторяется каждые пять секунд,
                        // пока блок недоступен. В журнал — только первая: иначе
                        // за ночь без блока он состоял бы из неё одной.
                        var failure = Lang.T("log_connect_failed", host, MqttPort, ex.Message);
                        if (failure != reportedFailure)
                        {
                            reportedFailure = failure;
                            ComputerLog.Error(failure);
                            if (host.EndsWith(".local", StringComparison.OrdinalIgnoreCase))
                                ComputerLog.Error(Lang.T("log_local_names"));
                        }
                        Update(s => s with { Connected = false, Host = host, LastError = failure });
                        // Нажатия, накопленные без связи, отправить некуда. Если
                        // их не сбрасывать, после восстановления связи десять
                        // минут простоя уйдут одной минутой, и в истории
                        // появится минута с десятикратным числом нажатий.
                        if (DateTime.UtcNow - lastMinute >= TimeSpan.FromMinutes(1))
                        {
                            lastMinute = DateTime.UtcNow;
                            input.Drain();
                        }
                        await Task.Delay(TimeSpan.FromSeconds(5), stopping);
                        continue;
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
                        JsonSerializer.Serialize(new { app = process, category }), true, stopping);
                    Update(s => s with { ActiveApp = process, Category = category });
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
                    }), true, stopping);
                }

                var playing = audio.IsPlaying();
                if (playing != lastAudio)
                {
                    lastAudio = playing;
                    await Publish(client, TopicAudio, playing ? "1" : "0", true, stopping);
                }

                if (now - lastMinute >= TimeSpan.FromMinutes(1))
                {
                    lastMinute = now;
                    var (keys, clicks) = input.Drain();
                    // Метка минуты — чтобы блок не засчитал одно сообщение
                    // дважды. Сообщение сохраняется у брокера, и после
                    // перезапуска блок получает его снова.
                    await Publish(client, TopicActivity, JsonSerializer.Serialize(new
                    {
                        keys,
                        clicks,
                        mouse_px = 0,
                        afk = input.IdleFor > AfkAfter,
                        ts = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                    }), true, stopping);
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
                    }), true, stopping);

                    if (State.Sensors != hardware.SensorsAvailable)
                        Update(s => s with { Sensors = hardware.SensorsAvailable });
                    if (!hardware.SensorsAvailable && !warnedAboutRights)
                    {
                        warnedAboutRights = true;
                        ComputerLog.Error(Lang.T("log_no_temps"));
                    }
                }

                if (now - lastHeartbeat >= TimeSpan.FromSeconds(30))
                {
                    lastHeartbeat = now;
                    // Heartbeat без retain: сохранённая метка времени после
                    // выключения ПК врала бы, что компьютер на связи.
                    await Publish(client, TopicHeartbeat,
                        DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString(), false, stopping);
                }

                await Task.Delay(TimeSpan.FromSeconds(1), stopping);
            }
            catch (OperationCanceledException) when (stopping.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                // Обрыв сети или перезагрузка Pi не должны ронять сбор:
                // п.9 требует, чтобы после выключения роутера на минуту всё
                // вернулось само.
                var failure = $"{ex.GetType().Name}: {ex.Message}";
                if (failure != reportedFailure)
                {
                    reportedFailure = failure;
                    ComputerLog.Error(failure);
                }
                Update(s => s with { Connected = client.IsConnected, LastError = failure });
                try { await Task.Delay(TimeSpan.FromSeconds(5), stopping); }
                catch (OperationCanceledException) { break; }
            }
        }

        _hardware = null;
        if (client.IsConnected)
        {
            try { await client.DisconnectAsync(); } catch (Exception) { }
        }
        Update(s => s with { Running = false, Connected = false });
        ComputerLog.Info(Lang.T("log_stopped"));
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
}
