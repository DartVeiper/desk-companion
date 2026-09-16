using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;

namespace DeskCompanion.Services;

/// <summary>
/// Один экземпляр приложения на пользователя.
/// </summary>
/// <remarks>
/// Второй запуск не открывает второе окно, а просит первое показаться и
/// выходит. Иначе было бы два сбора данных, два значка в трее и два потока
/// одинаковых сообщений на блок.
///
/// Тонкость в правах. Первый экземпляр обычно поднят задачей планировщика с
/// правами администратора, а второй — двойным щелчком, без них. Объекты,
/// созданные процессом с правами, процесс без прав по умолчанию открыть не
/// может, поэтому мьютекс и канал создаются с явным разрешением для самого
/// пользователя.
/// </remarks>
public sealed class SingleInstance : IDisposable
{
    private static readonly string Name = $"DeskCompanion.{Elevation.UserSid}";

    private readonly Mutex _mutex;
    private readonly CancellationTokenSource _stop = new();

    private SingleInstance(Mutex mutex) => _mutex = mutex;

    [DllImport("user32.dll")]
    private static extern bool AllowSetForegroundWindow(int processId);

    private const int AnyProcess = -1;

    /// <summary>Занять место. null — место уже занято другим экземпляром.</summary>
    public static SingleInstance? TryClaim()
    {
        try
        {
            var security = new MutexSecurity();
            security.AddAccessRule(new MutexAccessRule(
                new SecurityIdentifier(Elevation.UserSid),
                MutexRights.FullControl, AccessControlType.Allow));
            var mutex = MutexAcl.Create(true, $@"Local\{Name}", out var created, security);
            if (created) return new SingleInstance(mutex);
            mutex.Dispose();
            return null;
        }
        catch (UnauthorizedAccessException)
        {
            // Мьютекс есть, но создан с правами, которых у нас нет, — значит,
            // экземпляр уже работает.
            return null;
        }
    }

    /// <summary>Работает ли уже экземпляр — не занимая место.</summary>
    public static bool IsRunning()
    {
        try
        {
            using var existing = Mutex.OpenExisting($@"Local\{Name}");
            return true;
        }
        catch (WaitHandleCannotBeOpenedException)
        {
            return false;
        }
        catch (UnauthorizedAccessException)
        {
            return true;
        }
    }

    /// <summary>Попросить работающий экземпляр показать окно.</summary>
    /// <param name="page">Какую страницу открыть; -1 — какая была.</param>
    public static bool SignalShow(TimeSpan wait, int page = -1)
    {
        try
        {
            // Вывести окно поверх других Windows разрешает только тому, кто
            // сейчас впереди, — то есть нам, а не экземпляру, которого просим.
            // Делимся разрешением, иначе окно лишь мигнуло бы на панели задач.
            AllowSetForegroundWindow(AnyProcess);
            using var client = new NamedPipeClientStream(".", Name, PipeDirection.Out);
            client.Connect((int)wait.TotalMilliseconds);
            client.WriteByte((byte)Math.Clamp(page + 1, 0, 255));
            client.Flush();
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>
    /// Слушать просьбы показаться. onShow получает номер страницы (-1 — любая)
    /// и зовётся не из потока окна.
    /// </summary>
    public void Listen(Action<int> onShow)
    {
        var security = new PipeSecurity();
        security.AddAccessRule(new PipeAccessRule(
            new SecurityIdentifier(Elevation.UserSid),
            PipeAccessRights.ReadWrite | PipeAccessRights.CreateNewInstance,
            AccessControlType.Allow));

        _ = Task.Run(async () =>
        {
            while (!_stop.IsCancellationRequested)
            {
                try
                {
                    await using var server = NamedPipeServerStreamAcl.Create(
                        Name, PipeDirection.In, 1, PipeTransmissionMode.Byte,
                        PipeOptions.Asynchronous, 0, 0, security);
                    await server.WaitForConnectionAsync(_stop.Token);
                    var buffer = new byte[1];
                    if (await server.ReadAsync(buffer, _stop.Token) > 0) onShow(buffer[0] - 1);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (IOException)
                {
                    // Клиент ушёл посреди разговора или канал ещё держит
                    // прежний экземпляр, только что уступивший место. Ждём и
                    // пробуем снова — без паузы цикл крутился бы вхолостую.
                    try { await Task.Delay(200, _stop.Token); }
                    catch (OperationCanceledException) { break; }
                }
            }
        });
    }

    public void Dispose()
    {
        _stop.Cancel();
        try { _mutex.ReleaseMutex(); } catch (Exception) { }
        _mutex.Dispose();
    }
}
