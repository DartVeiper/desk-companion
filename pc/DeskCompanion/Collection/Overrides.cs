using System.IO;
using System.Text.Json;

namespace DeskCompanion.Collection;

/// <summary>
/// Ручные исключения: какую программу к чему относить.
///
/// Зачем нужны, если есть признаки. Признаки покрывают классы — игры из
/// библиотеки Steam, всё на Unreal, всё на Unity, полноэкранное с
/// загруженной видеокартой. Но всегда найдётся то, что мимо: сборка модов
/// в своей папке на другом диске, старая игра, не грузящая видеокарту,
/// программа, которую хочется считать работой, а не «прочим».
///
/// Спорить с такими случаями бессмысленно — нужен способ сказать прямо.
/// Файл лежит рядом с настройками приложения, чтобы приложение могло его
/// однажды редактировать само.
///
/// Формат простой:
///
///     { "SkyrimSE": "game", "obs64": "other", "Blender": "code" }
///
/// Ключ — имя процесса без .exe, как его показывает разведка на странице
/// «Компьютер».
/// Значение — code, browser, game или other.
/// </summary>
public static class Overrides
{
    private static Dictionary<string, string> _map = new(StringComparer.OrdinalIgnoreCase);
    private static DateTime _readAt;
    private static DateTime _fileAt;

    public static string Path => System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "DeskCompanion", "apps.json");

    /// <summary>Что человек назначил этой программе. null — ничего.</summary>
    public static string? For(string process)
    {
        Reload();
        return _map.TryGetValue(process, out var category) ? category : null;
    }

    /// <summary>
    /// Перечитать файл, если он изменился.
    ///
    /// Проверяем не чаще раза в пять секунд: запрос идёт на каждом круге
    /// опроса, а трогать диск ради этого каждую секунду незачем. Зато
    /// правка файла подхватывается сама — перезапускать ничего не нужно.
    /// </summary>
    private static void Reload()
    {
        var now = DateTime.UtcNow;
        if ((now - _readAt).TotalSeconds < 5) return;
        _readAt = now;

        try
        {
            if (!File.Exists(Path))
            {
                if (_map.Count > 0) _map = new(StringComparer.OrdinalIgnoreCase);
                return;
            }
            var stamp = File.GetLastWriteTimeUtc(Path);
            if (stamp == _fileAt) return;
            _fileAt = stamp;

            var parsed = JsonSerializer.Deserialize<Dictionary<string, string>>(
                File.ReadAllText(Path));
            _map = parsed is null
                ? new(StringComparer.OrdinalIgnoreCase)
                : new(parsed, StringComparer.OrdinalIgnoreCase);
        }
        catch (Exception)
        {
            // Битый файл не повод останавливать сбор: едем на признаках,
            // как будто исключений нет вовсе.
        }
    }
}
