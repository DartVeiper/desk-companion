using System.IO;
using System.Text;

namespace DeskCompanion.Collection;

/// <summary>
/// Журнал сбора данных с компьютера: файл и последние строки в памяти.
/// </summary>
/// <remarks>
/// Файл — чтобы разобраться в сбое, случившемся без открытого окна: сбор
/// идёт и тогда, когда приложение свёрнуто в трей. Строки в памяти — чтобы
/// страница «Компьютер» не перечитывала файл каждую секунду.
///
/// При запуске хвост файла подтягивается в память: иначе после перезапуска
/// страница показывала бы пустой журнал ровно тогда, когда интересно, что
/// было перед ним.
/// </remarks>
public static class ComputerLog
{
    public static string FilePath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "DeskCompanion", "computer.log");

    /// <summary>
    /// Предел размера файла. Сбор живёт неделями; полмегабайта — несколько
    /// тысяч строк, хватает разобраться в сбое и не жалко места.
    /// </summary>
    private const long MaxBytes = 512 * 1024;

    /// <summary>Сколько последних строк держать в памяти.</summary>
    private const int Keep = 200;

    private static readonly Encoding Utf8 = new UTF8Encoding(false);
    private static readonly object Gate = new();
    private static readonly Queue<string> Recent = new();
    private static bool _loaded;

    /// <summary>Записана новая строка. Зовётся не из потока окна.</summary>
    public static event Action? Written;

    public static void Info(string line) => Write(line, error: false);

    public static void Error(string line) => Write(line, error: true);

    /// <summary>
    /// Перечитать хвост файла. Нужно после помощника с правами: он пишет в
    /// тот же файл из другого процесса, и в памяти этих строк нет.
    /// </summary>
    public static void Reload()
    {
        lock (Gate)
        {
            Recent.Clear();
            _loaded = false;
            LoadTail();
        }
        Written?.Invoke();
    }

    public static IReadOnlyList<string> Lines()
    {
        lock (Gate)
        {
            LoadTail();
            return Recent.ToList();
        }
    }

    private static void Write(string line, bool error)
    {
        var stamped = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss}  {(error ? "! " : "")}{line}";
        lock (Gate)
        {
            LoadTail();
            Recent.Enqueue(stamped);
            while (Recent.Count > Keep) Recent.Dequeue();
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
                var info = new FileInfo(FilePath);
                if (info.Exists && info.Length > MaxBytes)
                {
                    // Одну прежнюю часть храним: сбой часто начинается раньше,
                    // чем его заметили.
                    File.Move(FilePath, FilePath + ".1", overwrite: true);
                }
                File.AppendAllText(FilePath, stamped + Environment.NewLine, Utf8);
            }
            catch (Exception)
            {
                // Журнал не повод прерывать сбор: данные на блок важнее
                // строки в файле.
            }
        }
        Written?.Invoke();
    }

    private static void LoadTail()
    {
        if (_loaded) return;
        _loaded = true;
        try
        {
            if (!File.Exists(FilePath)) return;
            using var stream = new FileStream(FilePath, FileMode.Open, FileAccess.Read,
                                              FileShare.ReadWrite | FileShare.Delete);
            const int window = 32 * 1024;
            var cut = stream.Length > window;
            if (cut) stream.Seek(-window, SeekOrigin.End);
            using var reader = new StreamReader(stream, Encoding.UTF8);
            var lines = reader.ReadToEnd()
                .Split('\n', StringSplitOptions.RemoveEmptyEntries)
                .Select(line => line.TrimEnd('\r'))
                .ToList();
            // Первая строка окна почти наверняка обрезана посередине.
            if (cut && lines.Count > 0) lines.RemoveAt(0);
            foreach (var line in lines.TakeLast(Keep)) Recent.Enqueue(line);
        }
        catch (Exception)
        {
            // Нечитаемый журнал — начнём с чистого листа.
        }
    }
}
