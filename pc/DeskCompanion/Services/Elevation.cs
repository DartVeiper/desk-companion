using System.ComponentModel;
using System.Diagnostics;
using System.Security.Principal;

namespace DeskCompanion.Services;

/// <summary>Права администратора: есть ли они и как их попросить.</summary>
public static class Elevation
{
    public static bool IsElevated { get; } = Check();

    public static string UserSid { get; } = WindowsIdentity.GetCurrent().User?.Value ?? "";

    private static bool Check()
    {
        try
        {
            using var identity = WindowsIdentity.GetCurrent();
            return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>
    /// Запустить себя же с правами администратора и дождаться. Человек увидит
    /// окно UAC; отказ — это ответ, а не ошибка.
    /// </summary>
    /// <returns>Код выхода; null — человек отказал или запуск не удался.</returns>
    public static async Task<int?> RunSelfAsync(string arguments)
    {
        var exe = Environment.ProcessPath;
        if (exe is null) return null;
        try
        {
            using var process = Process.Start(new ProcessStartInfo(exe, arguments)
            {
                UseShellExecute = true,
                Verb = "runas",
            });
            if (process is null) return null;
            await process.WaitForExitAsync();
            return process.ExitCode;
        }
        catch (Win32Exception)
        {
            return null;  // «Нет» в окне UAC
        }
    }
}
