using System.Net;
using System.Net.Http;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text.Json;

namespace DeskCompanion.Services;

/// <summary>
/// Поиск блока в домашней сети.
/// </summary>
/// <remarks>
/// Имя deskpi.local из Windows находится через раз, а адрес цифрами
/// человек не знает, пока не залезет в роутер. 16.09 выяснилось, что
/// приложение так и жило на имени: адрес цифрами лежал только в копии
/// настроек, которую сама система не видела.
///
/// Поэтому приложение ищет блок само: сначала там, где он был, потом по
/// имени, потом обходит свою подсеть и спрашивает каждый адрес, не Desk
/// Companion ли он. Найденное запоминается цифрами — ими и пользуется и окно,
/// и сбор данных.
///
/// Обход подсети — это двести пятьдесят коротких запросов на один порт. Он
/// идёт только когда блок не отвечает, не чаще раза в десять минут, и
/// занимает пару секунд.
/// </remarks>
public static class Discovery
{
    public const string AppName = "desk-companion";

    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromMilliseconds(900) };

    /// <summary>
    /// Найти блок. Возвращает адрес цифрами или null.
    /// </summary>
    /// <param name="known">Где искать в первую очередь: сохранённый адрес.</param>
    /// <param name="progress">Сколько адресов подсети уже опрошено из скольких.</param>
    public static async Task<string?> FindAsync(string? known, int port,
                                                IProgress<(int Done, int Total)>? progress = null,
                                                CancellationToken token = default)
    {
        foreach (var name in new[] { known, "deskpi.local", "deskpi" })
        {
            if (string.IsNullOrWhiteSpace(name)) continue;
            foreach (var address in await ResolveAsync(name!, token))
            {
                if (await IsBoardAsync(address, port, token)) return address.ToString();
            }
        }

        var candidates = Neighbours().ToList();
        var done = 0;
        using var found = CancellationTokenSource.CreateLinkedTokenSource(token);
        string? result = null;
        // Не больше полусотни запросов разом: иначе на слабом роутере
        // обход сам по себе похож на сбой сети.
        using var gate = new SemaphoreSlim(48);

        var probes = candidates.Select(async address =>
        {
            await gate.WaitAsync(found.Token).ConfigureAwait(false);
            try
            {
                if (await IsBoardAsync(address, port, found.Token).ConfigureAwait(false))
                {
                    Interlocked.CompareExchange(ref result, address.ToString(), null);
                    found.Cancel();
                }
            }
            catch (OperationCanceledException)
            {
                // Нашли в другом месте или отменили — этот больше не нужен.
            }
            finally
            {
                gate.Release();
                progress?.Report((Interlocked.Increment(ref done), candidates.Count));
            }
        });

        try
        {
            await Task.WhenAll(probes).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // Отменили снаружи — вернём то, что успели найти.
        }
        return result;
    }

    private static async Task<IPAddress[]> ResolveAsync(string name, CancellationToken token)
    {
        if (IPAddress.TryParse(name, out var literal)) return new[] { literal };
        try
        {
            using var limit = CancellationTokenSource.CreateLinkedTokenSource(token);
            limit.CancelAfter(TimeSpan.FromSeconds(2));
            var addresses = await Dns.GetHostAddressesAsync(name, limit.Token);
            return addresses.Where(a => a.AddressFamily == AddressFamily.InterNetwork).ToArray();
        }
        catch (Exception)
        {
            return Array.Empty<IPAddress>();
        }
    }

    /// <summary>Отвечает ли на этом адресе именно наш блок.</summary>
    public static async Task<bool> IsBoardAsync(IPAddress address, int port, CancellationToken token)
    {
        try
        {
            var body = await Http.GetStringAsync($"http://{address}:{port}/api/hello", token);
            using var doc = JsonDocument.Parse(body);
            return doc.RootElement.TryGetProperty("app", out var app)
                   && app.ValueKind == JsonValueKind.String
                   && app.GetString() == AppName;
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>
    /// Адреса своей подсети. Только интерфейсы, которые работают и ведут к
    /// шлюзу, — у виртуальных адаптеров шлюза обычно нет, и обходить их сети
    /// незачем. Подсеть шире /24 обходим только ближние двести пятьдесят
    /// адресов: домашний роутер больше и не раздаёт.
    /// </summary>
    private static IEnumerable<IPAddress> Neighbours()
    {
        var seen = new HashSet<string>();
        foreach (var network in NetworkInterface.GetAllNetworkInterfaces())
        {
            if (network.OperationalStatus != OperationalStatus.Up) continue;
            if (network.NetworkInterfaceType is NetworkInterfaceType.Loopback
                or NetworkInterfaceType.Tunnel) continue;
            var properties = network.GetIPProperties();
            if (!properties.GatewayAddresses.Any(g => g.Address.AddressFamily == AddressFamily.InterNetwork
                                                      && !g.Address.Equals(IPAddress.Any)))
                continue;

            foreach (var unicast in properties.UnicastAddresses)
            {
                if (unicast.Address.AddressFamily != AddressFamily.InterNetwork) continue;
                var bytes = unicast.Address.GetAddressBytes();
                if (bytes[0] == 169 && bytes[1] == 254) continue;  // без DHCP — не сеть
                var prefix = $"{bytes[0]}.{bytes[1]}.{bytes[2]}.";
                if (!seen.Add(prefix)) continue;
                for (var last = 1; last < 255; last++)
                {
                    if (last == bytes[3]) continue;  // сам компьютер
                    yield return IPAddress.Parse(prefix + last);
                }
            }
        }
    }
}
