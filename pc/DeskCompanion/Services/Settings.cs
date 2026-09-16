using System.IO;
using System.Text.Json;

namespace DeskCompanion.Services;

/// <summary>
/// Настройки самого приложения: где искать плату и как себя вести.
///
/// Рядом с исполняемым файлом класть нельзя — Program Files доступен
/// только на чтение. Поэтому AppData, как у всех.
/// </summary>
public sealed class Settings
{
    public string Host { get; set; } = "deskpi.local";
    public int Port { get; set; } = 843;
    public bool StartMinimized { get; set; } = true;

    /// <summary>Язык окна. null — ещё не выбирали: берётся язык системы.</summary>
    public string? Language { get; set; }

    /// <summary>
    /// Отправлять ли блоку данные с этого компьютера. Выключается одной
    /// галочкой: нажатия и активное окно — это данные о человеке, и
    /// возможность их не отдавать должна быть на виду, а не в коде.
    /// </summary>
    public bool CollectorEnabled { get; set; } = true;

    private static string Folder => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "DeskCompanion");

    private static string File => Path.Combine(Folder, "settings.json");

    public static Settings Load()
    {
        try
        {
            if (System.IO.File.Exists(File))
                return JsonSerializer.Deserialize<Settings>(
                    System.IO.File.ReadAllText(File)) ?? new Settings();
        }
        catch (Exception)
        {
            // Битый файл настроек не повод не запуститься: поедем на
            // умолчаниях и перезапишем его при первом сохранении.
        }
        return new Settings();
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(Folder);
            System.IO.File.WriteAllText(File,
                JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (Exception)
        {
            // Не сохранилось — переживём. Ронять приложение из-за настроек
            // хуже, чем забыть адрес до следующего запуска.
        }
    }
}
