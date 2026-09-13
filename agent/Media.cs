using Windows.Media.Control;

namespace DeskAgent;

/// <summary>Что играет прямо сейчас.</summary>
public readonly record struct Track(string Artist, string Title, bool Playing)
{
    public bool Empty => Title.Length == 0;

    /// <summary>Ключ для сравнения: по нему решаем, публиковать ли заново.</summary>
    public string Key => $"{Artist}|{Title}|{Playing}";
}

/// <summary>
/// Текущий трек из Windows, для любого плеера сразу.
///
/// Система сама собирает то, что играет, в общий список сеансов — туда
/// попадают и Яндекс Музыка, и Spotify, и видео в браузере, и голосовое
/// сообщение в телеграме. Поэтому не нужно ни лезть в конкретный плеер, ни
/// знать, какой из них установлен: спрашиваем систему.
///
/// Ничего не устанавливаем и ни за чем не следим — только читаем то, что
/// плеер сам о себе объявил, чтобы это показала панель громкости.
/// </summary>
public sealed class NowPlaying
{
    private GlobalSystemMediaTransportControlsSessionManager? _manager;

    /// <summary>
    /// Что звучит. Пустой трек — не играет ничего, что стоит показывать.
    ///
    /// Из нескольких сеансов берём играющий: на паузе плееров может висеть
    /// сколько угодно, и показывать давно остановленный трек — врать.
    /// </summary>
    public async Task<Track> CurrentAsync()
    {
        try
        {
            _manager ??= await GlobalSystemMediaTransportControlsSessionManager.RequestAsync();
            foreach (var session in _manager.GetSessions())
            {
                if (session.GetPlaybackInfo().PlaybackStatus
                    != GlobalSystemMediaTransportControlsSessionPlaybackStatus.Playing)
                    continue;

                var properties = await session.TryGetMediaPropertiesAsync();
                var title = (properties.Title ?? "").Trim();
                if (title.Length == 0) continue;
                return new Track((properties.Artist ?? "").Trim(), title, true);
            }
        }
        catch (Exception)
        {
            // Список сеансов живёт в системной службе, и она умеет
            // перезапускаться. Молчание здесь правильнее исключения: без
            // музыки часы работают, без часов музыка тоже.
            _manager = null;
        }
        return new Track("", "", false);
    }
}
