# ВНИМАНИЕ для правящих этот файл: он сохранён в UTF-8 С МЕТКОЙ (BOM).
# Windows PowerShell 5.1 читает скрипты без метки как ANSI, кириллица в них
# рассыпается, и скрипт перестаёт разбираться — с сообщением про пропущенную
# скобку, которое никак не намекает на кодировку.
#
# Сборка приложения для ПК.
#
#   Правый клик по файлу -> «Выполнить с помощью PowerShell»
#   либо:  powershell -ExecutionPolicy Bypass -File pc\build.ps1
#
# Прав администратора не нужно. Результат — один файл в папке «Программа»
# в корне проекта:
#
#   DeskCompanion.exe   окно и сбор данных с компьютера для блока
#
# Раньше сбор был отдельной программой, DeskAgent.exe. Теперь он внутри
# приложения, и старый файл из папки убирается, чтобы его не запустили по
# привычке.
#
# Зачем отдельная папка. Своё место сборки dotnet кладёт по адресу вида
# pc\DeskCompanion\bin\Release\net8.0-windows\win-x64\publish\ — этот путь
# невозможно ни запомнить, ни найти, и именно так программа и терялась.
# Здесь же он один и не меняется: на него ссылаются и ярлык, и автозапуск.
#
# Exe самодостаточен — рантайм .NET внутри. Ставить на компьютер ничего не
# надо, и это не роскошь: без этого скачавший релиз получает вместо окна
# системное сообщение про отсутствующий Desktop Runtime.

param(
    # Не создавать ярлык на рабочем столе.
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "Программа"
$exeName = "DeskCompanion.exe"
$taskName = "Desk Companion"

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "  Не найден dotnet. Нужен .NET 8 SDK:"
    Write-Host "    https://dotnet.microsoft.com/download/dotnet/8.0"
    Write-Host ""
    return
}

# Запущенную программу перезаписать нельзя — Windows держит файл. Гасим
# сами, иначе сборка падает на копировании с невнятным «отказано в
# доступе», и непонятно, что виновата собственная работающая копия.
# DeskAgent — сборщик прошлых версий: его файл удаляется ниже.
$wasRunning = $false
foreach ($name in @("DeskCompanion", "DeskAgent")) {
    foreach ($process in @(Get-Process -Name $name -ErrorAction SilentlyContinue)) {
        try {
            $process | Stop-Process -Force
            if ($name -eq "DeskCompanion") { $wasRunning = $true }
        } catch {
            # Приложение поднято автозапуском с правами, а сборка идёт без
            # них. Остановить его может только сам человек.
            Write-Host ""
            Write-Host "  $name работает с правами администратора, и остановить его"
            Write-Host "  отсюда нельзя. Закрой приложение из трея (правый клик по"
            Write-Host "  значку -> Выход) и запусти сборку снова."
            Write-Host ""
            return
        }
    }
}
if ($wasRunning) {
    Start-Sleep -Milliseconds 700
    Write-Host "  приложение остановлено на время сборки"
}

New-Item -ItemType Directory -Path $out -Force | Out-Null

Write-Host "  собираю..." -NoNewline
$staging = Join-Path $env:TEMP "desk-build-DeskCompanion"
Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue

# Вывод прячем: dotnet печатает десяток строк про восстановление пакетов,
# и на их фоне единственная важная строка — ошибка — теряется.
$log = & dotnet publish (Join-Path $root "pc\DeskCompanion\DeskCompanion.csproj") `
    -c Release -o $staging --nologo -v quiet 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host " ошибка"
    Write-Host ""
    $log | ForEach-Object { Write-Host "    $_" }
    Write-Host ""
    return
}

$built = Join-Path $staging $exeName
if (-not (Test-Path $built)) {
    Write-Host " ошибка: собралось, но $exeName нет"
    return
}
Copy-Item $built (Join-Path $out $exeName) -Force
$mb = [math]::Round((Get-Item $built).Length / 1MB, 1)
Write-Host " готово, $mb МБ"
Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue

$oldAgent = Join-Path $out "DeskAgent.exe"
if (Test-Path $oldAgent) {
    Remove-Item $oldAgent -Force
    Write-Host "  убран DeskAgent.exe — сбор данных теперь внутри приложения"
}
# Драйвер датчиков, который библиотека распаковала рядом со старым агентом.
# Пока он загружен, файл занят — тогда он уйдёт при следующей сборке.
Remove-Item (Join-Path $out "DeskAgent.sys") -Force -ErrorAction SilentlyContinue

if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut(
        (Join-Path $desktop "Desk Companion.lnk"))
    $link.TargetPath = Join-Path $out $exeName
    $link.WorkingDirectory = $out
    $link.Description = "Desk Companion — окно настольного блока"
    $link.Save()
    Write-Host "  ярлык на рабочем столе: Desk Companion"
}

# Что погасили ради сборки — поднимаем обратно. Иначе сборка выглядит
# безобидной, а на деле после неё блок перестаёт получать данные с
# компьютера, и связать одно с другим потом трудно.
if ($wasRunning) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        # Через задачу, а не напрямую: так приложение получает те же права,
        # что и при входе в систему, и проверка после сборки честная.
        Start-ScheduledTask -TaskName $taskName
    } else {
        Start-Process (Join-Path $out $exeName) -ArgumentList "--tray"
    }
    Write-Host "  приложение запущено обратно"
}

Write-Host ""
Write-Host "  Программа здесь:"
Write-Host "    $out"
Write-Host ""
Write-Host "  Автозапуск включается в самом приложении: Настройки -> Запуск."
Write-Host ""
