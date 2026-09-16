using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace DeskCompanion.Services;

/// <summary>Что агент сам сообщает о себе.</summary>
public sealed class AgentReport
{
    public int Pid { get; set; }
    public DateTime Started { get; set; }
    public DateTime Updated { get; set; }
    public string Host { get; set; } = "";
    public int Port { get; set; }
    public bool Connected { get; set; }
    public bool Elevated { get; set; }
    public bool Sensors { get; set; }
    public bool InputHooks { get; set; }
    public string? LastError { get; set; }
}

/// <summary>
/// Агент глазами приложения: жив ли, что с ним, и рычаги управления.
/// </summary>
/// <remarks>
/// Агент — отдельный процесс, и так и останется: ему нужны права
/// администратора ради температур, а окну они не нужны, и поднимать окно с
/// ними означало бы запрос UAC при каждом входе в систему. Но для человека
/// это одна программа: окна у агента нет, а всё, что раньше печаталось в
/// его консоль, показывается здесь.
///
/// Запускаем через задачу планировщика, а не напрямую. Задача заведена «с
/// наивысшими правами» и повышает агента без запроса — даже когда само
/// приложение работает без прав. Прямой запуск остаётся запасным путём: агент
/// поднимется, но без температур.
/// </remarks>
public static class Agent
{
    public const string ProcessName = "DeskAgent";

    /// <summary>
    /// Через сколько метку «жив» считать протухшей. Агент обновляет её раз в
    /// десять секунд; тройной запас — чтобы не пугать человека из-за одного
    /// медленного прохода.
    /// </summary>
    public static readonly TimeSpan StaleAfter = TimeSpan.FromSeconds(30);

    private static string Folder => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "DeskCompanion");

    public static string LogPath => Path.Combine(Folder, "agent.log");

    private static string StatusPath => Path.Combine(Folder, "agent-status.json");

    /// <summary>
    /// Где лежит агент. Рядом с приложением — так их кладёт build.ps1 и так
    /// их качают с релиза. Иначе — там, куда смотрит задача планировщика.
    /// </summary>
    public static string? ExePath
    {
        get
        {
            var beside = Path.Combine(AppContext.BaseDirectory, ProcessName + ".exe");
            if (File.Exists(beside)) return beside;
            var fromTask = TaskExecutable();
            return fromTask is not null && File.Exists(fromTask) ? fromTask : null;
        }
    }

    public static bool IsRunning => Process.GetProcessesByName(ProcessName).Length > 0;

    public static AgentReport? ReadReport()
    {
        try
        {
            using var stream = new FileStream(StatusPath, FileMode.Open, FileAccess.Read,
                                              FileShare.ReadWrite | FileShare.Delete);
            return JsonSerializer.Deserialize<AgentReport>(stream);
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>
    /// Последние строки журнала. Читаем с конца и не больше хвоста: журнал
    /// бывает в полмегабайта, а показать нужно два десятка строк.
    /// </summary>
    public static List<string> Tail(int lines)
    {
        try
        {
            using var stream = new FileStream(LogPath, FileMode.Open, FileAccess.Read,
                                              FileShare.ReadWrite | FileShare.Delete);
            const int window = 16 * 1024;
            if (stream.Length > window) stream.Seek(-window, SeekOrigin.End);
            using var reader = new StreamReader(stream, Encoding.UTF8);
            var text = reader.ReadToEnd();
            var all = text.Split('\n', StringSplitOptions.RemoveEmptyEntries)
                          .Select(line => line.TrimEnd('\r'))
                          .ToList();
            // Первая строка окна почти наверняка обрезана посередине.
            if (stream.Length > window && all.Count > 0) all.RemoveAt(0);
            return all.TakeLast(lines).ToList();
        }
        catch (Exception)
        {
            return new List<string>();
        }
    }

    /// <summary>Есть ли задача планировщика, которая поднимает агента.</summary>
    public static bool HasTask => Schtasks("/Query", "/TN", Settings.AgentTask) == 0;

    /// <summary>
    /// Запустить. Сначала задачей — она даёт права администратора без запроса.
    /// Не вышло — напрямую, без прав.
    /// </summary>
    public static async Task<string?> StartAsync()
    {
        if (IsRunning) return null;

        if (HasTask)
        {
            if (Schtasks("/Run", "/TN", Settings.AgentTask) == 0
                && await WaitAsync(running: true)) return null;
        }

        var exe = ExePath;
        if (exe is null)
        {
            return Lang.T("agent_missing");
        }
        try
        {
            Process.Start(new ProcessStartInfo(exe)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetDirectoryName(exe)!,
            });
        }
        catch (Exception error)
        {
            return Lang.T("agent_failed", error.Message);
        }
        return await WaitAsync(running: true) ? null : Lang.T("agent_exited");
    }

    /// <summary>
    /// Запустить с правами администратора — с запросом UAC. Нужно тем, у
    /// кого задачи нет: без прав агент работает, но температур не читает.
    /// </summary>
    public static async Task<string?> StartElevatedAsync()
    {
        var exe = ExePath;
        if (exe is null) return Lang.T("agent_missing");

        await StopAsync();
        try
        {
            Process.Start(new ProcessStartInfo(exe)
            {
                UseShellExecute = true,
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden,
                WorkingDirectory = Path.GetDirectoryName(exe)!,
            });
        }
        catch (System.ComponentModel.Win32Exception)
        {
            // Человек нажал «Нет» в окне UAC — это не ошибка, а ответ.
            return Lang.T("agent_admin_cancelled");
        }
        catch (Exception error)
        {
            return Lang.T("agent_failed", error.Message);
        }
        return await WaitAsync(running: true) ? null : Lang.T("agent_exited");
    }

    public static async Task<string?> StopAsync()
    {
        if (!IsRunning) return null;

        // Задача знает свой процесс и гасит его при любых правах, которые
        // у неё есть. Приложению без прав убить повышенный процесс Windows
        // не даст, поэтому это основной путь.
        if (HasTask) Schtasks("/End", "/TN", Settings.AgentTask);
        if (await WaitAsync(running: false)) return null;

        // Агент запущен не задачей — например, руками. Гасим сами.
        foreach (var process in Process.GetProcessesByName(ProcessName))
        {
            try { process.Kill(); }
            catch (Exception) { /* повышенный процесс не даст — скажем ниже */ }
        }
        return await WaitAsync(running: false)
            ? null
            : Lang.T("agent_not_stopped");
    }

    public static async Task<string?> RestartAsync()
    {
        var stopped = await StopAsync();
        return stopped ?? await StartAsync();
    }

    /// <summary>
    /// Разведка: запустить агента с ключом и вернуть то, что он напечатал.
    /// Разведка к брокеру не ходит и хуков не ставит, поэтому работающему
    /// агенту не мешает.
    /// </summary>
    public static async Task<string> DiagnoseAsync(string key)
    {
        var exe = ExePath;
        if (exe is null) return Lang.T("agent_missing");

        var info = new ProcessStartInfo(exe, key)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        try
        {
            using var process = Process.Start(info);
            if (process is null) return Lang.T("agent_failed", "");
            var output = process.StandardOutput.ReadToEndAsync();
            var errors = process.StandardError.ReadToEndAsync();
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            await process.WaitForExitAsync(timeout.Token);
            return (await output + await errors).Trim();
        }
        catch (OperationCanceledException)
        {
            return Lang.T("agent_probe_timeout");
        }
        catch (Exception error)
        {
            return Lang.T("agent_failed", error.Message);
        }
    }

    // ------------------------------------------------------------ мелочи

    private static async Task<bool> WaitAsync(bool running)
    {
        for (var i = 0; i < 20; i++)
        {
            if (IsRunning == running) return true;
            await Task.Delay(150);
        }
        return IsRunning == running;
    }

    /// <summary>
    /// Путь к агенту из задачи планировщика — через его программный
    /// интерфейс, а не разбором вывода schtasks. Тот печатает в кодировке
    /// консоли, и путь с кириллицей («Программа») приходил бы искажённым.
    /// </summary>
    private static string? TaskExecutable()
    {
        try
        {
            var type = Type.GetTypeFromProgID("Schedule.Service");
            if (type is null) return null;
            dynamic service = Activator.CreateInstance(type)!;
            service.Connect();
            dynamic task = service.GetFolder("\\").GetTask(Settings.AgentTask);
            dynamic action = task.Definition.Actions.Item(1);
            return ((string)action.Path).Trim('"');
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static int Schtasks(params string[] args)
    {
        try
        {
            var info = new ProcessStartInfo("schtasks")
            {
                CreateNoWindow = true,
                UseShellExecute = false,
                // Вывод не нужен — хватает кода возврата. А непрочитанный
                // перенаправленный поток однажды заполнится и повесит
                // schtasks, поэтому не перенаправляем вовсе.
            };
            foreach (var arg in args) info.ArgumentList.Add(arg);
            using var process = Process.Start(info);
            if (process is null) return -1;
            return process.WaitForExit(5000) ? process.ExitCode : -1;
        }
        catch (Exception)
        {
            return -1;
        }
    }
}
