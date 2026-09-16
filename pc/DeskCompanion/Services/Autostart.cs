using System.Diagnostics;
using System.Security;
using System.Security.Principal;
using DeskCompanion.Collection;
using Microsoft.Win32;

namespace DeskCompanion.Services;

/// <summary>
/// Автозапуск — задачей планировщика с наивысшими правами.
/// </summary>
/// <remarks>
/// Задачей, а не веткой Run реестра, потому что приложению нужны права
/// администратора: без них сбор данных не читает температуры. Через Run это
/// означало бы запрос UAC при каждом входе в систему, а на такое человек
/// соглашается ровно два раза. Задача повышает права сама и молча.
///
/// Завести и снять задачу — тоже дело администратора, поэтому приложение
/// без прав делает это через само себя с ключом --autostart: один запрос
/// UAC, когда человек сам поставил галочку.
///
/// Раньше автозапуск был двойным: приложение — веткой Run, агент — своей
/// задачей. Агента больше нет, и при заведении новой задачи обе старые
/// записи убираются, иначе приложение поднималось бы при входе дважды.
/// Ветка Run прошлой версии до тех пор считается включённым автозапуском:
/// человек его включал, и галочка не должна делать вид, что нет.
/// </remarks>
public static class Autostart
{
    public const string TaskName = "Desk Companion";

    private const string OldAgentTask = "DeskCompanion Agent";
    private const string OldAgentProcess = "DeskAgent";
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string RunValue = "DeskCompanion";

    // Константы планировщика.
    private const int CreateOrUpdate = 6;
    private const int InteractiveToken = 3;
    private const int SecurityDacl = 4;

    /// <summary>Заведена ли задача и на какой exe она смотрит.</summary>
    public static string? Target()
    {
        try
        {
            dynamic task = Folder().GetTask(TaskName);
            dynamic action = task.Definition.Actions.Item(1);
            return ((string)action.Path).Trim('"');
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>Автозапуск включён — задачей или, по-старому, веткой Run.</summary>
    public static bool IsOn => Target() is not null || HasRunValue();

    /// <summary>Автозапуск заведён задачей, то есть с правами.</summary>
    public static bool HasTask => Target() is not null;

    /// <summary>
    /// Задача есть, но смотрит не на этот exe — приложение перенесли в другую
    /// папку. Такой автозапуск молча не работает, и чинить его надо заново.
    /// </summary>
    public static bool IsStale => Target() is { } path
        && !string.Equals(path, Environment.ProcessPath, StringComparison.OrdinalIgnoreCase);

    /// <summary>Запустить задачу сейчас — так приложение получает права без запроса.</summary>
    public static bool RunNow()
    {
        try
        {
            dynamic task = Folder().GetTask(TaskName);
            task.Run(null);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>
    /// Включить или выключить. Без прав — через само приложение с запросом UAC.
    /// </summary>
    /// <returns>null — получилось; иначе причина.</returns>
    public static async Task<string?> SetAsync(bool on)
    {
        if (!on && !HasTask)
        {
            // Выключить автозапуск прошлой версии можно и без прав: ветка Run
            // своя у каждого пользователя.
            RemoveRunValue();
            return null;
        }
        if (Elevation.IsElevated)
        {
            return Apply(on);
        }
        var code = await Elevation.RunSelfAsync(on ? "--autostart on" : "--autostart off");
        return code switch
        {
            null => Lang.T("autostart_cancelled"),
            0 => null,
            _ => Lang.T("autostart_failed"),
        };
    }

    /// <summary>
    /// Режим помощника: приложение, запущенное с --autostart и правами,
    /// делает дело и выходит, не открывая окна.
    /// </summary>
    public static int HandleHelper(string mode)
    {
        if (!Elevation.IsElevated) return 2;
        return Apply(mode == "on") is null ? 0 : 1;
    }

    private static string? Apply(bool on)
    {
        try
        {
            if (on) Register();
            else Folder().DeleteTask(TaskName, 0);
            // Ветку Run убираем в обе стороны: при включении её заменяет
            // задача, при выключении автозапуска не должно остаться никакого.
            CleanUpOldSetup(removeRunKey: true);
            return null;
        }
        catch (Exception error)
        {
            if (!on && error.HResult == unchecked((int)0x80070002))
            {
                CleanUpOldSetup(removeRunKey: true);
                return null;  // задачи и так нет
            }
            return error.Message;
        }
    }

    private static void Register()
    {
        var exe = Environment.ProcessPath ?? throw new InvalidOperationException("no process path");
        var folder = Folder();
        using var identity = WindowsIdentity.GetCurrent();

        folder.RegisterTask(TaskName, TaskXml(exe, identity.Name, Elevation.UserSid),
                            CreateOrUpdate, null, null, InteractiveToken, null);

        // Право запускать задачу — самому пользователю. По умолчанию у него
        // только право на неё смотреть, а приложение без прав поднимает себя
        // именно этой задачей: без этой строчки передача прав не работала бы
        // ни у кого, у кого включён контроль учётных записей.
        dynamic task = folder.GetTask(TaskName);
        string sddl = task.GetSecurityDescriptor(SecurityDacl);
        var ace = $"(A;;FRFX;;;{Elevation.UserSid})";
        if (!sddl.Contains(ace)) task.SetSecurityDescriptor(sddl + ace, 0);
    }

    private static string TaskXml(string exe, string user, string sid)
    {
        var directory = System.IO.Path.GetDirectoryName(exe) ?? "";
        // Приоритет 5 — обычный. Планировщик по умолчанию ставит 7, ниже
        // обычного, и прежний агент так и работал. Для приложения с окном и
        // с перехватом ввода это плохо: под нагрузкой перехват запаздывает, а
        // вместе с ним — мышь и клавиатура во всей системе.
        return $"""
            <?xml version="1.0" encoding="UTF-16"?>
            <Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
              <RegistrationInfo>
                <Description>Desk Companion</Description>
              </RegistrationInfo>
              <Triggers>
                <LogonTrigger>
                  <Enabled>true</Enabled>
                  <UserId>{SecurityElement.Escape(user)}</UserId>
                </LogonTrigger>
              </Triggers>
              <Principals>
                <Principal id="Author">
                  <UserId>{SecurityElement.Escape(sid)}</UserId>
                  <LogonType>InteractiveToken</LogonType>
                  <RunLevel>HighestAvailable</RunLevel>
                </Principal>
              </Principals>
              <Settings>
                <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
                <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
                <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
                <AllowHardTerminate>true</AllowHardTerminate>
                <StartWhenAvailable>false</StartWhenAvailable>
                <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
                <IdleSettings>
                  <StopOnIdleEnd>false</StopOnIdleEnd>
                  <RestartOnIdle>false</RestartOnIdle>
                </IdleSettings>
                <AllowStartOnDemand>true</AllowStartOnDemand>
                <Enabled>true</Enabled>
                <Hidden>false</Hidden>
                <RunOnlyIfIdle>false</RunOnlyIfIdle>
                <WakeToRun>false</WakeToRun>
                <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
                <Priority>5</Priority>
                <RestartOnFailure>
                  <Interval>PT1M</Interval>
                  <Count>3</Count>
                </RestartOnFailure>
              </Settings>
              <Actions Context="Author">
                <Exec>
                  <Command>{SecurityElement.Escape(exe)}</Command>
                  <Arguments>--tray</Arguments>
                  <WorkingDirectory>{SecurityElement.Escape(directory)}</WorkingDirectory>
                </Exec>
              </Actions>
            </Task>
            """;
    }

    /// <summary>
    /// Привести автозапуск к нынешнему устройству при запуске приложения.
    /// </summary>
    /// <remarks>
    /// Автозапуск прошлой версии, веткой Run, при наличии прав молча
    /// переводится на задачу: человек его уже включал, а задача даёт то, чего
    /// Run не умеет, — температуры. Без прав остаётся как был: работает, а
    /// страница «Компьютер» предложит завести задачу.
    /// </remarks>
    /// <returns>Прежний агент ещё работает и остановить его не вышло.</returns>
    public static bool Migrate()
    {
        if (Elevation.IsElevated && !HasTask && HasRunValue())
        {
            try
            {
                Register();
                ComputerLog.Info(Lang.T("log_autostart_moved"));
            }
            catch (Exception)
            {
                // Останется ветка Run: приложение поднимется, просто без прав.
            }
        }
        return CleanUpOldSetup(removeRunKey: HasTask);
    }

    /// <summary>
    /// Убрать следы прежнего устройства: отдельного агента и его задачу, а
    /// если новая задача заведена — и запуск приложения веткой Run.
    /// </summary>
    /// <returns>Прежний агент ещё работает и остановить его не вышло.</returns>
    public static bool CleanUpOldSetup(bool removeRunKey)
    {
        if (Elevation.IsElevated)
        {
            try
            {
                Folder().DeleteTask(OldAgentTask, 0);
                ComputerLog.Info(Lang.T("log_old_task_removed"));
            }
            catch (Exception)
            {
                // Задачи нет — и хорошо.
            }
        }

        if (removeRunKey) RemoveRunValue();

        // Прежний агент и новый сбор слали бы одни и те же данные вперемешку,
        // а брокер выбивал бы их друг у друга, раз клиент у них один. Поэтому
        // старый агент должен уйти до того, как начнётся сбор.
        var alive = false;
        foreach (var process in Process.GetProcessesByName(OldAgentProcess))
        {
            try
            {
                process.Kill();
                process.WaitForExit(3000);
                ComputerLog.Info(Lang.T("log_old_agent_stopped"));
            }
            catch (Exception)
            {
                // Агент запущен с правами, которых у приложения нет.
                alive = true;
            }
            finally
            {
                process.Dispose();
            }
        }
        if (alive) ComputerLog.Error(Lang.T("log_old_agent_alive"));
        return alive;
    }

    private static bool HasRunValue()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKey);
            return key?.GetValue(RunValue) is not null;
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static void RemoveRunValue()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKey, writable: true);
            key?.DeleteValue(RunValue, throwOnMissingValue: false);
        }
        catch (Exception)
        {
            // Не вышло — приложение просто поднимется дважды, и второй
            // экземпляр тут же уступит первому.
        }
    }

    private static dynamic Folder()
    {
        var type = Type.GetTypeFromProgID("Schedule.Service")
                   ?? throw new InvalidOperationException("Task Scheduler is unavailable");
        dynamic service = Activator.CreateInstance(type)!;
        service.Connect();
        return service.GetFolder("\\");
    }
}
