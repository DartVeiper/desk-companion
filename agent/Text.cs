using System.Globalization;

namespace DeskAgent;

/// <summary>
/// Язык журнала и разведки.
/// </summary>
/// <remarks>
/// Агент своего окна не имеет — его текст показывает приложение, на
/// странице агента. И если приложение говорит по-английски, а журнал в нём
/// русский, страница выглядит сломанной. Поэтому агент пишет на том языке,
/// который выбран в приложении; не выбран — на языке системы.
///
/// Словарь свой, а не общий с приложением: у агента двадцать строк, и
/// тащить ради них сборку приложения было бы несоразмерно.
/// </remarks>
internal static class Text
{
    public static string Code { get; private set; } = Default();

    public static void Use(string? code) => Code = code is "ru" or "en" ? code : Default();

    private static string Default()
        => CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "ru" ? "ru" : "en";

    public static string T(string key)
    {
        var table = Code == "ru" ? Ru : En;
        if (table.TryGetValue(key, out var found)) return found;
        return Ru.TryGetValue(key, out var fallback) ? fallback : key;
    }

    public static string T(string key, params object?[] args)
        => string.Format(CultureInfo.InvariantCulture, T(key), args);

    private static readonly Dictionary<string, string> Ru = new()
    {
        ["front_now"] = "  впереди сейчас: {0} -> {1} (видеокарта {2}%)",
        ["overrides_from"] = "  исключения читаются из {0}",
        ["temps_ok"] = "\nтемпературы доступны",
        ["temps_none"] = "\nтемператур нет — нужен запуск с правами администратора",
        ["already_running"] = "агент уже запущен — второй экземпляр выходит",
        ["already_running_rights"] = "агент уже запущен с другими правами — второй экземпляр выходит",
        ["start_admin"] = "запуск: брокер {0}:{1}, с правами администратора",
        ["start_user"] = "запуск: брокер {0}:{1}, без прав администратора",
        ["no_hooks"] = "не удалось поставить хуки ввода — нажатия считаться не будут",
        ["connected"] = "подключено к {0}:{1}",
        ["connect_failed"] = "не подключиться к {0}:{1} — {2}",
        ["local_names"] = "имена .local из Windows находятся через раз — впиши адрес блока цифрами в настройках приложения",
        ["no_temps"] = "датчики температуры недоступны — нужен запуск с правами администратора, иначе экран GPU / CPU на блоке будет пустым",
        ["stopped"] = "остановлен",
        ["no_host"] = "адрес блока в приложении не задан — ищу по имени deskpi.local; адрес цифрами задаётся в приложении, в настройках",
        ["settings_unreadable"] = "настройки приложения не прочитались ({0}): {1}",
        ["col_process"] = "процесс",
        ["col_category"] = "занятие",
        ["col_path"] = "путь",
        ["by_list"] = " (по списку)",
        ["no_path"] = "путь не виден",
    };

    private static readonly Dictionary<string, string> En = new()
    {
        ["front_now"] = "  in front now: {0} -> {1} (graphics card {2}%)",
        ["overrides_from"] = "  overrides are read from {0}",
        ["temps_ok"] = "\ntemperatures are available",
        ["temps_none"] = "\nno temperatures — the agent needs administrator rights",
        ["already_running"] = "the agent is already running — this second copy exits",
        ["already_running_rights"] = "the agent is already running with other rights — this second copy exits",
        ["start_admin"] = "start: broker {0}:{1}, with administrator rights",
        ["start_user"] = "start: broker {0}:{1}, without administrator rights",
        ["no_hooks"] = "could not install input hooks — keystrokes will not be counted",
        ["connected"] = "connected to {0}:{1}",
        ["connect_failed"] = "cannot connect to {0}:{1} — {2}",
        ["local_names"] = ".local names resolve only about half the time from Windows — enter the device address as numbers in the app's settings",
        ["no_temps"] = "temperature sensors are unavailable — the agent needs administrator rights, otherwise the GPU / CPU screen on the device stays empty",
        ["stopped"] = "stopped",
        ["no_host"] = "no device address set in the app — looking for deskpi.local by name; set the address as numbers in the app's settings",
        ["settings_unreadable"] = "could not read the app settings ({0}): {1}",
        ["col_process"] = "process",
        ["col_category"] = "category",
        ["col_path"] = "path",
        ["by_list"] = " (listed)",
        ["no_path"] = "path not visible",
    };
}
