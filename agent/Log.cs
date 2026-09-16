using System.Text;

namespace DeskAgent;

/// <summary>
/// Журнал агента — в файл, а не в консоль.
/// </summary>
/// <remarks>
/// Консольного окна у агента больше нет: оно висело на экране с момента
/// входа в систему и мозолило глаз. Смотреть, что агент делает, теперь
/// нужно в приложении — оно читает этот файл. Заодно журнал переживает
/// закрытие агента: раньше последние строки пропадали вместе с окном, то
/// есть ровно тогда, когда их и надо было прочитать.
///
/// Если агента запустили из терминала с ключом разведки, строки дублируются
/// и в консоль — см. <see cref="Program"/>.
/// </remarks>
internal static class Log
{
    public static string Folder => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "DeskCompanion");

    public static string FilePath => Path.Combine(Folder, "agent.log");

    /// <summary>
    /// Предел размера. Агент живёт неделями, и без предела журнал рос бы
    /// бесконечно. Полмегабайта — это несколько тысяч строк: хватает, чтобы
    /// разобраться в сбое, и не жалко места.
    /// </summary>
    private const long MaxBytes = 512 * 1024;

    private static readonly Encoding Utf8 = new UTF8Encoding(false);
    private static readonly object Gate = new();

    public static void Info(string line) => Write(line, error: false);

    public static void Error(string line) => Write(line, error: true);

    private static void Write(string line, bool error)
    {
        var stamped = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss}  {(error ? "! " : "")}{line}";
        lock (Gate)
        {
            try
            {
                Directory.CreateDirectory(Folder);
                var info = new FileInfo(FilePath);
                if (info.Exists && info.Length > MaxBytes)
                {
                    // Одну прежнюю часть храним: сбой часто начинается раньше,
                    // чем его заметили, и срезать журнал ровно по нему обидно.
                    File.Move(FilePath, FilePath + ".1", overwrite: true);
                }
                File.AppendAllText(FilePath, stamped + Environment.NewLine, Utf8);
            }
            catch
            {
                // Журнал не повод ронять агента: диск занят, файл открыт
                // антивирусом — данные на блок важнее строки в файле.
            }
        }

        try
        {
            (error ? Console.Error : Console.Out).WriteLine(line);
        }
        catch
        {
            // Консоли может не быть вовсе — у фонового агента её и нет.
        }
    }
}
