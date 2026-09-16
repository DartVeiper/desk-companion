using System.IO;

namespace DeskCompanion.Services;

/// <summary>
/// Копии базы измерений — с платы на компьютер.
///
/// Зачем. База живёт на карте памяти блока в единственном экземпляре, и
/// карта — расходник: они умирают без предупреждения, особенно недорогие.
/// Код при этом лежит в репозитории на двух машинах, а вот история
/// измерений нигде больше не повторяется. Жалко именно её: на ней потом
/// будет учиться поиск аномалий, и восстановить её неоткуда — это
/// запись прожитых дней, а не файл, который можно пересобрать.
///
/// Почему копию делает приложение, а не сам блок. Блок может копировать
/// базу только к себе же, то есть на ту самую карту, — от её смерти это
/// не спасает никак. Компьютер же стоит рядом, включён тогда же, когда и
/// человек за столом, и умеет забрать базу по сети сам.
/// </summary>
public sealed class Backup
{
    //: Сколько копий держим. Неделя — достаточно, чтобы заметить пропажу
    //: и откатиться, и достаточно мало, чтобы не копить гигабайты.
    private const int Keep = 7;

    private readonly Board _board;

    public Backup(Board board) => _board = board;

    public static string Folder => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "DeskCompanion", "backups");

    /// <summary>Когда снята последняя копия. null — копий нет.</summary>
    public DateTime? LastAt()
    {
        var newest = Files().FirstOrDefault();
        return newest is null ? null : File.GetLastWriteTime(newest);
    }

    public string? LastError { get; private set; }

    private static List<string> Files()
    {
        if (!Directory.Exists(Folder)) return new List<string>();
        return Directory.GetFiles(Folder, "desk-*.db")
            .OrderByDescending(File.GetLastWriteTime)
            .ToList();
    }

    /// <summary>
    /// Снять копию, если сегодняшней ещё нет.
    ///
    /// Раз в сутки, а не при каждом запуске: база растёт на сотню
    /// килобайт в день, и десять копий одного и того же дня не добавляют
    /// сохранности, зато добавляют записи на диск.
    /// </summary>
    public async Task<bool> EnsureTodayAsync(CancellationToken token = default)
    {
        var last = LastAt();
        if (last is not null && last.Value.Date == DateTime.Today) return false;
        return await RunAsync(token);
    }

    public async Task<bool> RunAsync(CancellationToken token = default)
    {
        Directory.CreateDirectory(Folder);
        var target = Path.Combine(Folder, $"desk-{DateTime.Now:yyyy-MM-dd}.db");

        // Сначала во временный файл: оборванная закачка не должна
        // оставлять полуфайл, который потом примут за целую копию.
        var partial = target + ".part";
        try
        {
            var bytes = await _board.DownloadDatabaseAsync(token);
            if (bytes is null || bytes.Length == 0)
            {
                LastError = _board.LastError ?? Lang.T("board_empty_file");
                return false;
            }
            await File.WriteAllBytesAsync(partial, bytes, token);
            File.Move(partial, target, overwrite: true);
            LastError = null;
            Prune();
            return true;
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error)
        {
            LastError = error.Message;
            try { File.Delete(partial); } catch (Exception) { }
            return false;
        }
    }

    private static void Prune()
    {
        foreach (var old in Files().Skip(Keep))
        {
            try { File.Delete(old); } catch (Exception) { }
        }
    }
}
