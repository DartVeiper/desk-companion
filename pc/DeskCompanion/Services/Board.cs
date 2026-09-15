using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace DeskCompanion.Services;

/// <summary>Живое состояние блока. Поля повторяют /api/live.</summary>
public sealed class Live
{
    public int? Co2;
    public double? Temperature;
    public double? Humidity;
    public bool Presence;
    public double? WeatherTemp;
    public string? WeatherCond;
    public bool PcOnline;
    public string? ActiveApp;
    public int Keystrokes;
    public int Clicks;
    public double? GpuLoad;
    public double? GpuTemp;
    public double? CpuLoad;
    public double? CpuTemp;
    public bool WifiOk;
    public string? Ip;
    public string? Version;
    public int? WifiDbm;
    public int? RamUsedMb;
    public int? RamTotalMb;
    public double? BoardTemp;
    public long UptimeSeconds;
    public bool Scd41Ok;
    public bool Ld2410Ok;
    public List<(string Label, string Detail, bool Critical)> Problems = new();
    public int? DistanceCm;
}

/// <summary>Сводка за день. Поля повторяют /api/today.</summary>
public sealed class Today
{
    public int AtDeskMinutes;
    public int LongestSitting;
    public int SittingLimit = 120;
    public int Keystrokes;
    public int Clicks;
    public int Streak;
    public int[] Hours = new int[24];
    public Dictionary<string, int> ByCategory = new();
}

public sealed record ScreenEntry(string Key, string Label, bool On);

/// <summary>
/// Единственное место, которое ходит на плату по сети.
///
/// Приложение сознательно не считает ничего само: и метрики, и список
/// экранов живут на блоке, он же пишет базу. Вторая копия логики на
/// стороне ПК означала бы два источника правды, расходящихся ровно тогда,
/// когда на них смотрят.
/// </summary>
public sealed class Board
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(4) };

    public string Host { get; set; } = "deskpi.local";
    public int Port { get; set; } = 843;

    /// <summary>Пусто, пока всё хорошо; иначе — почему не отвечает.</summary>
    public string? LastError { get; private set; }

    private string Url(string path) => $"http://{Host}:{Port}{path}";

    private async Task<JsonDocument?> GetAsync(string path, CancellationToken token)
    {
        try
        {
            var body = await _http.GetStringAsync(Url(path), token);
            LastError = null;
            return JsonDocument.Parse(body);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception error)
        {
            // Блок выключен или Wi-Fi моргнул — это нормальная жизнь, а не
            // повод падать. Сообщение сохраняем: именно его увидит человек
            // вместо пустого окна.
            LastError = Explain(error);
            return null;
        }
    }

    private static string Explain(Exception error) => error switch
    {
        TaskCanceledException => "плата не ответила вовремя",
        HttpRequestException http when http.Message.Contains("No such host")
            => "адрес не находится в сети",
        HttpRequestException => "нет связи с платой",
        _ => error.Message,
    };

    public async Task<Live?> LiveAsync(CancellationToken token = default)
    {
        using var doc = await GetAsync("/api/live", token);
        if (doc is null) return null;

        var root = doc.RootElement;
        var env = root.GetProperty("env");
        var weather = root.GetProperty("weather");
        var pc = root.GetProperty("pc");
        var health = root.GetProperty("health");

        var live = new Live
        {
            Co2 = Int(env, "co2"),
            Temperature = Double(env, "temperature"),
            Humidity = Double(env, "humidity"),
            Presence = Bool(root, "presence"),
            WeatherTemp = Double(weather, "temp"),
            WeatherCond = Str(weather, "cond"),
            PcOnline = Bool(root, "pc_online"),
            ActiveApp = Str(pc, "active_app"),
            Keystrokes = Int(pc, "keystrokes") ?? 0,
            Clicks = Int(pc, "mouse_clicks") ?? 0,
            GpuLoad = Double(pc, "gpu_load"),
            GpuTemp = Double(pc, "gpu_temp"),
            CpuLoad = Double(pc, "cpu_load"),
            CpuTemp = Double(pc, "cpu_temp"),
            WifiOk = Bool(health, "wifi_ok"),
            Ip = Str(health, "ip"),
            Version = Str(health, "version"),
            WifiDbm = Int(health, "wifi_signal_dbm"),
            RamUsedMb = Int(health, "ram_used_mb"),
            RamTotalMb = Int(health, "ram_total_mb"),
            BoardTemp = Double(health, "cpu_temp"),
            UptimeSeconds = Int(health, "uptime_seconds") ?? 0,
            Scd41Ok = Bool(health, "scd41_ok"),
            Ld2410Ok = Bool(health, "ld2410_ok"),
        };

        if (root.TryGetProperty("radar", out var radar) && radar.ValueKind == JsonValueKind.Object)
            live.DistanceCm = Int(radar, "distance_cm");

        if (root.TryGetProperty("problems", out var problems))
            foreach (var item in problems.EnumerateArray())
                live.Problems.Add((Str(item, "label") ?? "", Str(item, "detail") ?? "",
                                   Bool(item, "critical")));

        return live;
    }

    public async Task<Today?> TodayAsync(CancellationToken token = default)
    {
        using var doc = await GetAsync("/api/today", token);
        if (doc is null) return null;

        var root = doc.RootElement;
        var today = new Today
        {
            AtDeskMinutes = Int(root, "at_desk_minutes") ?? 0,
            LongestSitting = Int(root, "longest_sitting") ?? 0,
            SittingLimit = Int(root, "sitting_limit") ?? 120,
            Keystrokes = Int(root, "keystrokes") ?? 0,
            Clicks = Int(root, "clicks") ?? 0,
            Streak = Int(root, "streak") ?? 0,
        };

        if (root.TryGetProperty("hours", out var hours))
        {
            var list = hours.EnumerateArray().Select(h => h.GetInt32()).ToArray();
            for (var i = 0; i < Math.Min(24, list.Length); i++) today.Hours[i] = list[i];
        }
        if (root.TryGetProperty("by_category", out var cats))
            foreach (var pair in cats.EnumerateObject())
                today.ByCategory[pair.Name] = pair.Value.GetInt32();

        return today;
    }

    public async Task<List<ScreenEntry>?> ScreensAsync(CancellationToken token = default)
    {
        using var doc = await GetAsync("/api/settings", token);
        if (doc is null) return null;
        if (!doc.RootElement.TryGetProperty("screens", out var screens)) return null;

        return screens.EnumerateArray()
            .Select(s => new ScreenEntry(Str(s, "key") ?? "", Str(s, "label") ?? "",
                                         Bool(s, "on")))
            .ToList();
    }

    /// <summary>
    /// Насколько легко нажимается экран. null — плата не ответила.
    ///
    /// Число — порог сопротивления между слоями резистивной панели. Больше
    /// значит легче: сопротивление обратно силе нажатия.
    /// </summary>
    public async Task<int?> TouchSensitivityAsync(CancellationToken token = default)
    {
        using var doc = await GetAsync("/api/settings", token);
        if (doc is null) return null;
        if (!doc.RootElement.TryGetProperty("values", out var values)) return null;
        if (!values.TryGetProperty("touch_sensitivity", out var found)) return null;
        return found.TryGetInt32(out var number) ? number : null;
    }

    public Task<bool> SaveTouchSensitivityAsync(int value,
                                                CancellationToken token = default)
        => PostSettingsAsync(new { touch_sensitivity = value }, token);

    /// <summary>Язык надписей на экране блока: "ru" или "en".</summary>
    public async Task<string?> LanguageAsync(CancellationToken token = default)
    {
        using var doc = await GetAsync("/api/settings", token);
        if (doc is null) return null;
        if (!doc.RootElement.TryGetProperty("values", out var values)) return null;
        return Str(values, "language");
    }

    public Task<bool> SaveLanguageAsync(string code, CancellationToken token = default)
        => PostSettingsAsync(new { language = code }, token);

    /// <summary>Город, для которого блок показывает погоду.</summary>
    public async Task<Place?> CityAsync(CancellationToken token = default)
    {
        using var doc = await GetAsync("/api/settings", token);
        if (doc is null) return null;
        if (!doc.RootElement.TryGetProperty("values", out var values)) return null;
        if (!values.TryGetProperty("city_lat", out var lat) ||
            !values.TryGetProperty("city_lon", out var lon)) return null;
        if (lat.ValueKind != JsonValueKind.Number || lon.ValueKind != JsonValueKind.Number)
            return null;
        return new Place(Str(values, "city_name") ?? "", "", lat.GetDouble(), lon.GetDouble());
    }

    public Task<bool> SaveCityAsync(Place place, CancellationToken token = default)
        => PostSettingsAsync(new
        {
            // Тройкой, а не тремя отдельными полями: название без координат
            // или наоборот — это подписанный на экране город, для которого
            // показана чужая погода.
            city = new { name = place.Name, lat = place.Latitude, lon = place.Longitude },
        }, token);

    /// <summary>
    /// Сохранить состав и порядок экранов.
    ///
    /// Порядок в списке и есть порядок в карусели — блок принимает его как
    /// есть, поэтому перетаскивание строк мышью ничего дополнительно не
    /// кодирует.
    ///
    /// Шлём список включённых ключей, а не пары «ключ — включён». Раньше
    /// здесь были пары, и плата на них падала: она разбирает список как
    /// набор строк, а строку из словаря не составить. Кнопка «Применить»
    /// при этом выглядела рабочей — приложение не показывало ничего, кроме
    /// оборванного соединения, а настройки просто не менялись.
    /// </summary>
    public Task<bool> SaveScreensAsync(IEnumerable<ScreenEntry> screens,
                                       CancellationToken token = default)
        => PostSettingsAsync(new
        {
            screens = screens.Where(s => s.On).Select(s => s.Key).ToArray(),
        }, token);

    private async Task<bool> PostSettingsAsync(object payload, CancellationToken token)
    {
        var body = new StringContent(JsonSerializer.Serialize(payload),
                                     Encoding.UTF8, "application/json");
        try
        {
            var response = await _http.PostAsync(Url("/api/settings"), body, token);
            LastError = response.IsSuccessStatusCode ? null : $"плата ответила {(int)response.StatusCode}";
            return response.IsSuccessStatusCode;
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error)
        {
            LastError = Explain(error);
            return false;
        }
    }

    /// <summary>
    /// Скачать базу целиком. null — не отдалась.
    ///
    /// Плата собирает копию через механизм самой SQLite, а не копированием
    /// файла: при включённом журнале WAL простое копирование ловит базу в
    /// середине записи и даёт битый файл.
    /// </summary>
    public async Task<byte[]?> DownloadDatabaseAsync(CancellationToken token = default)
    {
        try
        {
            // Таймаут больше обычного: база собирается на слабом
            // процессоре и уезжает по Wi-Fi, четырёх секунд ей мало.
            using var slow = new HttpClient { Timeout = TimeSpan.FromMinutes(2) };
            var bytes = await slow.GetByteArrayAsync(Url("/db"), token);
            LastError = null;
            return bytes;
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error)
        {
            LastError = Explain(error);
            return null;
        }
    }

    // ------------------------------------------------------------ разбор
    // Плата имеет право прислать null в любом поле: датчик мог не успеть
    // прогреться, агент — не запуститься. Поэтому всё читается мягко и
    // возвращает nullable, а не бросает на первом прочерке.

    private static string? Str(JsonElement parent, string name) =>
        parent.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString() : null;

    private static int? Int(JsonElement parent, string name) =>
        parent.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number
            ? (int)v.GetDouble() : null;

    private static double? Double(JsonElement parent, string name) =>
        parent.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number
            ? v.GetDouble() : null;

    private static bool Bool(JsonElement parent, string name) =>
        parent.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.True;
}
