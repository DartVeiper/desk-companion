using System.Text.Json;
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
/// </remarks>
internal static class Program
{
    private const string TopicActiveApp = "home/pc/active_app";
    private const string TopicActivity = "home/pc/activity_1min";
    private const string TopicAudio = "home/pc/audio";
    private const string TopicHeartbeat = "home/pc/heartbeat";
    private const string TopicHardware = "home/pc/hardware";

    private static readonly TimeSpan AfkAfter = TimeSpan.FromMinutes(5);

    private static async Task<int> Main(string[] args)
    {
        // Русский вывод и буферизация. Двух строк тут мало, нужны обе:
        //
        // OutputEncoding переключает кодовую страницу самого окна консоли —
        // без этого текст в окне превращается в крякозябры.
        //
        // SetOut нужен отдельно, потому что при перенаправлении в файл или в
        // конвейер окна нет вовсе, и кодировку приходится задавать самому
        // потоку. AutoFlush там же: без него последние строки остаются в
        // буфере и пропадают, если агента закрыли — а закрывают его как раз
        // тогда, когда эти строки и надо прочитать.
        try { Console.OutputEncoding = new System.Text.UTF8Encoding(false); } catch { }
        var stdout = new StreamWriter(Console.OpenStandardOutput(),
                                      new System.Text.UTF8Encoding(false)) { AutoFlush = true };
        var stderr = new StreamWriter(Console.OpenStandardError(),
                                      new System.Text.UTF8Encoding(false)) { AutoFlush = true };
        Console.SetOut(stdout);
        Console.SetError(stderr);

        if (args.Contains("--windows"))
        {
            // Разведка по окнам: показывает, к чему агент относит каждую
            // запущенную программу. Хуков не ставит и к брокеру не ходит,
            // поэтому запускать можно параллельно с работающим агентом.
            foreach (var line in ActiveWindow.Describe()) Console.WriteLine(line);
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
            Console.WriteLine(probe.SensorsAvailable
                ? "\nтемпературы доступны"
                : "\nтемператур нет — нужен запуск от администратора");
            return 0;
        }

        var host = Argument(args, "--host") ?? "deskpi.local";
        var port = int.TryParse(Argument(args, "--port"), out var p) ? p : 1883;

        Console.WriteLine($"Desk Companion — агент ПК");
        Console.WriteLine($"  брокер: {host}:{port}");

        using var input = new InputCounter();
        using var audio = new AudioMonitor();
        using var hardware = new HardwareMonitor();

        if (!input.Installed)
        {
            Console.Error.WriteLine("  не удалось поставить хуки ввода — нажатия считаться не будут");
        }

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

        var lastMinute = DateTime.UtcNow;
        var lastHardware = DateTime.MinValue;
        var lastHeartbeat = DateTime.MinValue;
        var lastApp = "";
        var lastAudio = (bool?)null;
        var warnedAboutRights = false;

        while (!stopping.IsCancellationRequested)
        {
            try
            {
                if (!client.IsConnected)
                {
                    try
                    {
                        await client.ConnectAsync(options, stopping.Token);
                        Console.WriteLine("  подключено");
                    }
                    catch (Exception ex) when (ex is not OperationCanceledException)
                    {
                        // Самая частая причина — не брокер, а имя: deskpi.local
                        // из Windows резолвится через раз. Сказать об этом
                        // здесь дешевле, чем дать человеку искать самому.
                        Console.Error.WriteLine($"  не подключиться к {host}:{port} — {ex.Message}");
                        Console.Error.WriteLine("  если это deskpi.local, попробуй адрес: " +
                                                "DeskAgent.exe --host 192.168.1.205");
                        throw;
                    }
                }

                var now = DateTime.UtcNow;

                // Активное окно — только на смену, а не по таймеру: иначе
                // один и тот же топик перепубликовывался бы сотни раз в час.
                var (process, _, category) = ActiveWindow.Current();
                var appKey = $"{process}|{category}";
                if (appKey != lastApp)
                {
                    lastApp = appKey;
                    await Publish(client, TopicActiveApp,
                        JsonSerializer.Serialize(new { app = process, category }), true, stopping.Token);
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
                    await Publish(client, TopicHardware, JsonSerializer.Serialize(new
                    {
                        gpu_temp = gpuTemp,
                        gpu_load = gpuLoad,
                        cpu_temp = cpuTemp,
                        cpu_load = cpuLoad,
                    }), true, stopping.Token);

                    if (!hardware.SensorsAvailable && !warnedAboutRights)
                    {
                        warnedAboutRights = true;
                        // П.6 плана: без прав администратора Режим 5 остаётся
                        // пустым молча. Пусть хотя бы здесь будет сказано.
                        Console.Error.WriteLine(
                            "  датчики температуры недоступны — запусти от администратора, " +
                            "иначе Режим 5 на блоке будет пустым");
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
                Console.Error.WriteLine($"  {ex.GetType().Name}: {ex.Message}");
                try { await Task.Delay(TimeSpan.FromSeconds(5), stopping.Token); }
                catch (OperationCanceledException) { break; }
            }
        }

        if (client.IsConnected) await client.DisconnectAsync();
        Console.WriteLine("  остановлен");
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

    private static string? Argument(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }
}
