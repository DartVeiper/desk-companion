# ВНИМАНИЕ для правящих этот файл: он сохранён в UTF-8 С МЕТКОЙ (BOM).
# Windows PowerShell 5.1 читает скрипты без метки как ANSI, кириллица в них
# рассыпается, и скрипт перестаёт разбираться — с сообщением про пропущенную
# скобку, которое никак не намекает на кодировку.
#
# Сборка обеих программ для ПК в одну папку.
#
#   Правый клик по файлу -> «Выполнить с помощью PowerShell»
#   либо:  powershell -ExecutionPolicy Bypass -File pc\build.ps1
#
# Прав администратора не нужно. Результат — два файла в папке «Программа»
# в корне проекта:
#
#   DeskCompanion.exe   окно: обзор, экраны, статистика, настройки
#   DeskAgent.exe       фоновый сборщик данных с компьютера
#
# Зачем отдельная папка. Своё место сборки dotnet кладёт по адресу вида
# pc\DeskCompanion\bin\Release\net8.0-windows\win-x64\publish\ — этот путь
# невозможно ни запомнить, ни найти, и именно так программа и терялась.
# Здесь же он один и не меняется: на него ссылаются и ярлык, и автозапуск.
#
# Оба exe самодостаточны — рантайм .NET внутри. Ставить на компьютер
# ничего не надо, и это не роскошь: без этого скачавший релиз получает
# вместо окна системное сообщение про отсутствующий Desktop Runtime.

param(
    # Не создавать ярлык на рабочем столе.
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "Программа"

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
$stopped = @()
foreach ($name in @("DeskCompanion", "DeskAgent")) {
    $running = Get-Process -Name $name -ErrorAction SilentlyContinue
    if ($running) {
        $running | Stop-Process -Force
        $stopped += $name
    }
}
if ($stopped.Count) {
    Start-Sleep -Milliseconds 700
    Write-Host "  остановлено на время сборки: $($stopped -join ', ')"
}

New-Item -ItemType Directory -Path $out -Force | Out-Null

$projects = @(
    @{ Name = "DeskCompanion"; Path = "pc\DeskCompanion\DeskCompanion.csproj"; What = "окно" },
    @{ Name = "DeskAgent";     Path = "agent\DeskAgent.csproj";                What = "агент" }
)

foreach ($project in $projects) {
    Write-Host "  собираю $($project.What)..." -NoNewline
    $staging = Join-Path $env:TEMP "desk-build-$($project.Name)"
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue

    # Вывод прячем: dotnet печатает десяток строк про восстановление
    # пакетов, и на их фоне единственная важная строка — ошибка — теряется.
    $log = & dotnet publish (Join-Path $root $project.Path) `
        -c Release -o $staging --nologo -v quiet 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host " ошибка"
        Write-Host ""
        $log | ForEach-Object { Write-Host "    $_" }
        Write-Host ""
        return
    }

    $exe = Join-Path $staging "$($project.Name).exe"
    if (-not (Test-Path $exe)) {
        Write-Host " ошибка: собралось, но $($project.Name).exe нет"
        return
    }
    Copy-Item $exe (Join-Path $out "$($project.Name).exe") -Force

    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host " готово, $mb МБ"
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut(
        (Join-Path $desktop "Desk Companion.lnk"))
    $link.TargetPath = Join-Path $out "DeskCompanion.exe"
    $link.WorkingDirectory = $out
    $link.Description = "Desk Companion — окно настольного блока"
    $link.Save()
    Write-Host "  ярлык на рабочем столе: Desk Companion"
}

# Что погасили ради сборки — поднимаем обратно. Иначе сборка выглядит
# безобидной, а на деле после неё блок перестаёт получать данные с
# компьютера, и связать одно с другим потом трудно.
foreach ($name in $stopped) {
    $exe = Join-Path $out "$name.exe"
    if (-not (Test-Path $exe)) { continue }
    if ($name -eq "DeskCompanion") {
        Start-Process $exe -ArgumentList "--tray"
    } elseif (Get-ScheduledTask -TaskName "DeskCompanion Agent" -ErrorAction SilentlyContinue) {
        # Через задачу, а не напрямую: так агент получает те же права, что
        # и при входе в систему, и проверка после сборки честная.
        Start-ScheduledTask -TaskName "DeskCompanion Agent"
    } else {
        Start-Process $exe
    }
    Write-Host "  запущено обратно: $name"
}

Write-Host ""
Write-Host "  Программы здесь:"
Write-Host "    $out"
Write-Host ""
Write-Host "  Автозапуск обеих (от администратора):"
Write-Host "    powershell -ExecutionPolicy Bypass -File pc\autostart.ps1"
Write-Host ""
