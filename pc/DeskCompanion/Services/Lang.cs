using System.ComponentModel;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Markup;

namespace DeskCompanion.Services;

/// <summary>
/// Язык интерфейса.
/// </summary>
/// <remarks>
/// Устроен так, чтобы язык добавлялся одним файлом. Все строки лежат в
/// словарях <see cref="Strings"/>, по одному на язык; ключ — короткое имя,
/// значение — текст. Новый язык — это новый файл в Strings и одна строчка в
/// <see cref="Available"/>, больше ничего.
///
/// Переключается на лету, без перезапуска. Разметка берёт текст через
/// <c>{l:T ключ}</c> — это привязка к индексатору, и когда язык меняется,
/// WPF сам перечитывает все такие привязки. Текст, который собирает код,
/// перерисовывает окно по событию <see cref="Changed"/>.
///
/// Недостающая строка берётся из русского словаря, а не показывается
/// ключом: неполный перевод должен выглядеть недопереведённым, а не
/// сломанным. Найти такие места помогает проверка в тестах.
/// </remarks>
public sealed class Lang : INotifyPropertyChanged
{
    public static Lang Instance { get; } = new();

    public sealed record Choice(string Code, string Name)
    {
        public override string ToString() => Name;
    }

    /// <summary>
    /// Языки для выбора. Каждый назван на самом себе: человек, не читающий
    /// по-русски, должен найти свой язык в списке, не зная, как он
    /// называется по-русски.
    /// </summary>
    public static IReadOnlyList<Choice> Available { get; } = new[]
    {
        new Choice("ru", "Русский"),
        new Choice("en", "English"),
    };

    /// <summary>
    /// Какие языки знает экран блока. Приложение может знать больше: тогда
    /// блок получает английский — его понимает больше людей, чем русский.
    /// </summary>
    public static IReadOnlySet<string> DeviceLanguages { get; } = new HashSet<string> { "ru", "en" };

    private static readonly Dictionary<string, IReadOnlyDictionary<string, string>> Tables = new()
    {
        ["ru"] = Strings.Ru,
        ["en"] = Strings.En,
    };

    private static readonly Dictionary<string, CultureInfo> Cultures = new()
    {
        ["ru"] = CultureInfo.GetCultureInfo("ru-RU"),
        ["en"] = CultureInfo.GetCultureInfo("en-US"),
    };

    private IReadOnlyDictionary<string, string> _table = Strings.Ru;

    public static string Code { get; private set; } = "ru";

    /// <summary>
    /// Культура для чисел и дат. По языку интерфейса, а не по системе:
    /// английское окно с «1 234,5» вместо «1,234.5» выглядело бы ошибкой.
    /// </summary>
    public static CultureInfo Culture => Cultures.TryGetValue(Code, out var found)
        ? found
        : CultureInfo.InvariantCulture;

    /// <summary>Язык, в котором звучит блок, при выбранном языке окна.</summary>
    public static string DeviceCode => DeviceLanguages.Contains(Code) ? Code : "en";

    public string this[string key] => Lookup(key);

    public static string T(string key) => Instance.Lookup(key);

    public static string T(string key, params object?[] args)
        => string.Format(Culture, Instance.Lookup(key), args);

    /// <summary>Язык по умолчанию — язык системы, если мы его знаем.</summary>
    public static string Default()
    {
        var system = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName;
        return Tables.ContainsKey(system) ? system : "en";
    }

    public static bool Knows(string? code) => code is not null && Tables.ContainsKey(code);

    public static event Action? Changed;

    public event PropertyChangedEventHandler? PropertyChanged;

    public static void Use(string? code)
    {
        if (!Knows(code)) code = Default();
        Code = code!;
        Instance._table = Tables[Code];
        // «Item[]» — так WPF узнаёт, что сменились разом все значения
        // индексатора, и перечитывает каждую привязку вида {l:T ключ}.
        Instance.PropertyChanged?.Invoke(Instance, new PropertyChangedEventArgs("Item[]"));
        Changed?.Invoke();
    }

    /// <summary>Ключи, которых нет в словаре языка. Для проверки полноты.</summary>
    public static IEnumerable<string> Missing(string code)
        => Tables.TryGetValue(code, out var table)
            ? Strings.Ru.Keys.Where(key => !table.ContainsKey(key))
            : Strings.Ru.Keys;

    private string Lookup(string key)
    {
        if (_table.TryGetValue(key, out var found)) return found;
        if (Strings.Ru.TryGetValue(key, out var fallback)) return fallback;
        return key;
    }
}

/// <summary>
/// <c>{l:T ключ}</c> в разметке — текст на текущем языке, меняющийся вместе
/// с ним.
/// </summary>
[MarkupExtensionReturnType(typeof(object))]
public sealed class TExtension : MarkupExtension
{
    public TExtension()
    {
    }

    public TExtension(string key) => Key = key;

    public string Key { get; set; } = "";

    public override object ProvideValue(IServiceProvider services)
    {
        var binding = new Binding($"[{Key}]")
        {
            Source = Lang.Instance,
            Mode = BindingMode.OneWay,
        };
        return binding.ProvideValue(services);
    }
}
