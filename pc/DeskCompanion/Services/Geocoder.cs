using System.Net.Http;
using System.Text.Json;

namespace DeskCompanion.Services;

/// <summary>Найденное место: то, что человек выбирает в списке.</summary>
/// <param name="Name">Город: «Белгород».</param>
/// <param name="Where">Чем он отличается от тёзок: «Россия, Белгородская область».</param>
public sealed record Place(string Name, string Where, double Latitude, double Longitude)
{
    public string Full => string.IsNullOrEmpty(Where) ? Name : $"{Name} — {Where}";
}

/// <summary>
/// Поиск города по названию.
///
/// Почему не зашитый список. Просился «огромный список всех больших городов
/// планеты», и соблазн велик — но любой такой список это выбор за человека,
/// какие города считать большими. Деревня, в которой он живёт, в него не
/// попадёт, а весит он мегабайты и устаревает.
///
/// Open-Meteo отдаёт свою базу поиском: те же серверы, что дают погоду, без
/// ключа и регистрации, с названиями на русском. Это буквально все населённые
/// пункты, а не выборка из них.
///
/// Цена — нужна сеть в момент выбора. Для программы, которая иначе показывает
/// погоду из интернета, это не цена.
/// </summary>
public sealed class Geocoder
{
    private const string Api = "https://geocoding-api.open-meteo.com/v1/search";

    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(10) };

    public string? LastError { get; private set; }

    public async Task<List<Place>> SearchAsync(string query, CancellationToken token = default)
    {
        LastError = null;
        query = query.Trim();
        // Одна буква даёт сотни совпадений и ничего не сообщает.
        if (query.Length < 2) return new List<Place>();

        var url = $"{Api}?name={Uri.EscapeDataString(query)}&count=25&language=ru&format=json";
        try
        {
            using var stream = await _http.GetStreamAsync(url, token);
            using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: token);
            if (!doc.RootElement.TryGetProperty("results", out var results))
                return new List<Place>();   // не ошибка: просто не нашлось

            var found = new List<Place>();
            foreach (var item in results.EnumerateArray())
            {
                var name = Text(item, "name");
                if (name is null) continue;
                if (!item.TryGetProperty("latitude", out var lat)) continue;
                if (!item.TryGetProperty("longitude", out var lon)) continue;

                // Тёзок много: Белгород есть и в России, и на Украине. Без
                // страны и области выбор был бы гаданием.
                var parts = new[] { Text(item, "country"), Text(item, "admin1") }
                    .Where(p => !string.IsNullOrEmpty(p));
                found.Add(new Place(name, string.Join(", ", parts),
                                    lat.GetDouble(), lon.GetDouble()));
            }
            return found;
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error)
        {
            LastError = error is HttpRequestException
                ? "нет связи с поиском городов"
                : error.Message;
            return new List<Place>();
        }
    }

    private static string? Text(JsonElement element, string name)
        => element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() : null;
}
