using System.IO;
using System.Text.Json;
using Microsoft.Win32;

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
    public bool Autostart { get; set; }

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

    // ----------------------------------------------------------- автозапуск

    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "DeskCompanion";

    /// <summary>
    /// Автозапуск через ветку Run текущего пользователя.
    ///
    /// Не через планировщик и не через службу: и то и другое требует прав
    /// администратора, а приложение — обычное пользовательское. Ветка Run
    /// правится без повышения прав и видна человеку в диспетчере задач,
    /// на вкладке автозагрузки, — то есть он может её отключить и без нас.
    /// </summary>
    public static bool IsAutostartOn()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKey);
            return key?.GetValue(ValueName) is not null;
        }
        catch (Exception) { return false; }
    }

    public static bool SetAutostart(bool on)
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKey, writable: true);
            if (key is null) return false;
            if (on)
            {
                var exe = Environment.ProcessPath;
                if (exe is null) return false;
                key.SetValue(ValueName, $"\"{exe}\" --tray");
            }
            else
            {
                key.DeleteValue(ValueName, throwOnMissingValue: false);
            }
            return true;
        }
        catch (Exception) { return false; }
    }
}
