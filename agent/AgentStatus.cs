using System.Text.Json;

namespace DeskAgent;

/// <summary>
/// Состояние агента для приложения — файл рядом с журналом.
/// </summary>
/// <remarks>
/// Приложению нужно знать то, чего с блока не видно: подключился ли агент к
/// брокеру, к какому адресу, читаются ли температуры и почему нет. Раньше
/// всё это печаталось в консоль, а консоли больше нет.
///
/// Файл, а не сокет или именованный канал, и тому есть причина. Агент
/// работает с правами администратора, приложение — без них, и объекты ядра,
/// созданные повышенным процессом, обычный процесс открыть часто не может.
/// Файл в папке пользователя читается при любых правах.
///
/// Пишется целиком через временный файл: иначе приложение однажды прочитает
/// его наполовину записанным.
/// </remarks>
internal sealed class AgentStatus
{
    public static string FilePath => Path.Combine(Log.Folder, "agent-status.json");

    public int Pid { get; set; } = Environment.ProcessId;
    public DateTime Started { get; set; } = DateTime.Now;
    public DateTime Updated { get; set; }
    public string Host { get; set; } = "";
    public int Port { get; set; }
    public bool Connected { get; set; }
    public bool Elevated { get; set; }
    public bool Sensors { get; set; }
    public bool InputHooks { get; set; }
    public string? LastError { get; set; }

    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    public void Save()
    {
        Updated = DateTime.Now;
        try
        {
            Directory.CreateDirectory(Log.Folder);
            var temp = FilePath + ".tmp";
            File.WriteAllText(temp, JsonSerializer.Serialize(this, Options));
            File.Move(temp, FilePath, overwrite: true);
        }
        catch
        {
            // Приложение увидит устаревшую метку времени и скажет об этом
            // само — молчать о сбое тут не страшно.
        }
    }
}
