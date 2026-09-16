# ВНИМАНИЕ для правящих этот файл: он сохранён в UTF-8 С МЕТКОЙ (BOM).
# Windows PowerShell 5.1 читает скрипты без метки как ANSI, кириллица в них
# рассыпается, и скрипт перестаёт разбираться — с сообщением про пропущенную
# скобку, которое никак не намекает на кодировку.
#
# Автозапуск обеих программ на ПК.
#
#   Правый клик по файлу -> «Выполнить с помощью PowerShell»
#   либо:  powershell -ExecutionPolicy Bypass -File pc\autostart.ps1
#
# Запускать от администратора: задача планировщика с высокими правами
# иначе не создастся.
#
# Почему две разные механики.
#
# Приложение — обычная пользовательская программа, ему хватает ветки Run:
# она правится без повышения прав и видна человеку в диспетчере задач, на
# вкладке автозагрузки, то есть отключить её можно и без нас.
#
# Агенту нужны права администратора: без них LibreHardwareMonitor не читает
# температуры. Через ту же ветку Run это означало бы запрос UAC при каждом
# входе в систему — а на такое человек соглашается ровно два раза, после
# чего выключает автозапуск. Поэтому агент идёт задачей планировщика с
# галкой «с наивысшими правами»: она стартует без запроса.
#
# Снять всё обратно:  .\autostart.ps1 -Remove

param([switch]$Remove)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
# Обе программы берём из папки, которую делает build.ps1. Раньше здесь были
# пути внутрь bin\Release\...\publish — они рабочие, но привязаны к тому,
# как dotnet раскладывает сборку: меняется версия цели, и автозапуск молча
# начинает указывать в пустоту. Одна папка на виду этим не страдает.
$out = Join-Path $root "Программа"
$agent = Join-Path $out "DeskAgent.exe"
$app = Join-Path $out "DeskCompanion.exe"

$taskName = "DeskCompanion Agent"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$runName = "DeskCompanion"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if ($Remove) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "  задача агента снята"
    }
    Remove-ItemProperty -Path $runKey -Name $runName -ErrorAction SilentlyContinue
    Write-Host "  приложение убрано из автозапуска"
    return
}

if (-not (Test-Admin)) {
    Write-Host "  Нужны права администратора: задача планировщика иначе не создастся."
    Write-Host "  Правый клик по файлу -> «Выполнить с помощью PowerShell» от имени администратора."
    return
}

foreach ($path in @($agent, $app)) {
    if (-not (Test-Path $path)) {
        Write-Host "  Не найден: $path"
        Write-Host "  Сначала собери:  powershell -ExecutionPolicy Bypass -File pc\build.ps1"
        return
    }
}

# Агент — задачей с высокими правами.
$action = New-ScheduledTaskAction -Execute $agent
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Задача не должна умирать по таймауту и должна подниматься на батарее:
# по умолчанию планировщик глушит задачи через три дня и не стартует их
# на питании от батареи, а блок должен работать всегда.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Highest -Force | Out-Null

# Право запускать задачу — самому пользователю. По умолчанию у него есть
# только право на неё смотреть, а запускать может лишь администратор. Но
# приложение, работающее без прав, поднимает агента именно этой задачей:
# без этой строчки кнопка «Запустить» и слежение за агентом не работали бы
# ни у кого, у кого включён контроль учётных записей, — то есть почти у всех.
$service = New-Object -ComObject Schedule.Service
$service.Connect()
$registered = $service.GetFolder("\").GetTask($taskName)
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$sddl = $registered.GetSecurityDescriptor(0xF)
$ace = "(A;;FRFX;;;$sid)"
if (-not $sddl.Contains($ace)) {
    $registered.SetSecurityDescriptor($sddl + $ace, 0)
}
Write-Host "  агент: задача «$taskName» создана, стартует при входе"

# Приложение — обычной веткой Run, свёрнутым в трей.
Set-ItemProperty -Path $runKey -Name $runName -Value "`"$app`" --tray"
Write-Host "  приложение: добавлено в автозапуск, стартует свёрнутым"

Write-Host ""
Write-Host "  Проверить: выйти и войти в систему, либо запустить сейчас:"
Write-Host "    Start-ScheduledTask -TaskName '$taskName'"
