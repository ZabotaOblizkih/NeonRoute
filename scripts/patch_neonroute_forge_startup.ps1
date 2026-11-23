<#
.SYNOPSIS
    Патчер запуска SD WebUI Forge для сборки NeonRoute на ПК коллеги.

.DESCRIPTION
    Применяет архитектурно выверенные исправления в кодовую базу NeonRoute:
    1. core/forge_locator.py:
       - Очищает переменные окружения прокси (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, FTP_PROXY, AG_LS_PROXY и вариант в нижнем регистре).
       - Задает NO_PROXY="localhost,127.0.0.1,0.0.0.0" и PYTHONUNBUFFERED="1", устраняя зависание внутреннего Gradio handshake.
    2. services/process_manager.py:
       - В ветке таймаута запуска Forge (150 с) гарантированно завершает дерево процессов (_run_forge_stop),
         закрывает дескриптор forge.log и сбрасывает ссылку на процесс, предотвращая утечку зомби-процессов.
    
    Скрипт полностью идемпотентен (безопасен при повторном запуске) и создает резервные копии с меткой времени.

.PARAMETER TargetDir
    Путь к корневой директории NeonRoute.
    По умолчанию: "C:\neyro\NeonRoute_v0.9.8.4-beta"

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\patch_neonroute_forge_startup.ps1
    powershell -ExecutionPolicy Bypass -File .\patch_neonroute_forge_startup.ps1 -TargetDir "C:\neyro\NeonRoute_v0.9.8.4-beta"
#>

param (
    [string]$TargetDir = "C:\neyro\NeonRoute_v0.9.8.4-beta"
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   NeonRoute: Патчер устранения зависания запуска Forge     " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Проверка существования целевой директории
$ResolvedTarget = $TargetDir
if (-not (Test-Path $ResolvedTarget)) {
    # Попробуем проверить текущую директорию скрипта
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ParentDir = Split-Path -Parent $ScriptDir
    if (Test-Path (Join-Path $ParentDir "core\forge_locator.py")) {
        $ResolvedTarget = $ParentDir
        Write-Host "[*] Директория по умолчанию '$TargetDir' не найдена. Используется: '$ResolvedTarget'" -ForegroundColor Yellow
    } else {
        Write-Host "[-] ОШИБКА: Целевая папка не найдена: $TargetDir" -ForegroundColor Red
        Write-Host "    Укажите путь через параметр: -TargetDir `"C:\путь\к\NeonRoute`"" -ForegroundColor Red
        exit 1
    }
}

$ForgeLocPath = Join-Path $ResolvedTarget "core\forge_locator.py"
$ProcMgrPath  = Join-Path $ResolvedTarget "services\process_manager.py"

if (-not (Test-Path $ForgeLocPath)) {
    Write-Host "[-] ОШИБКА: Не найден файл $ForgeLocPath" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $ProcMgrPath)) {
    Write-Host "[-] ОШИБКА: Не найден файл $ProcMgrPath" -ForegroundColor Red
    exit 1
}

Write-Host "[+] Целевой каталог: $ResolvedTarget" -ForegroundColor Green
Write-Host "[+] Найден core\forge_locator.py" -ForegroundColor Green
Write-Host "[+] Найден services\process_manager.py" -ForegroundColor Green
Write-Host ""

# 2. Поиск интерпретатора Python
$PythonCandidates = @(
    (Join-Path $ResolvedTarget "runtime\python\python.exe"),
    "C:\neyro\neyroGPD\webui_forge_cu121_torch231\system\python\python.exe",
    "C:\neyroGPD\webui_forge_cu121_torch231\system\python\python.exe"
)

$PythonExe = $null
foreach ($cand in $PythonCandidates) {
    if (Test-Path $cand) {
        $PythonExe = $cand
        break
    }
}

if (-not $PythonExe) {
    $SysPy = Get-Command python -ErrorAction SilentlyContinue
    if ($SysPy) {
        $PythonExe = $SysPy.Source
    }
}

if (-not $PythonExe) {
    $SysPyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($SysPyLauncher) {
        $PythonExe = $SysPyLauncher.Source
    }
}

if (-not $PythonExe) {
    Write-Host "[-] ОШИБКА: Не удалось найти интерпретатор Python для безопасного применения правок." -ForegroundColor Red
    exit 1
}

Write-Host "[+] Интерпретатор Python: $PythonExe" -ForegroundColor Cyan
Write-Host ""

# 3. Встроенный Python-скрипт безопасного идемпотентного патчинга
$PyScript = @"
import os
import sys
import shutil
import re
from datetime import datetime
from pathlib import Path

target_dir = Path(sys.argv[1]).resolve()
forge_loc_path = target_dir / "core" / "forge_locator.py"
proc_mgr_path = target_dir / "services" / "process_manager.py"

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# --- 1. Патчим core/forge_locator.py ---
loc_content = forge_loc_path.read_text(encoding="utf-8")
loc_modified = False

if 'env["NO_PROXY"] = "localhost,127.0.0.1,0.0.0.0"' in loc_content and 'AG_LS_PROXY' in loc_content:
    print("[=] core/forge_locator.py: уже содержит очистку прокси-переменных и NO_PROXY (пропуск).")
else:
    bak_loc = forge_loc_path.with_name(f"{forge_loc_path.name}.bak_{timestamp}")
    shutil.copy2(forge_loc_path, bak_loc)
    print(f"[+] Создана резервная копия: {bak_loc.name}")

    old_clean_snippet = '''    # 1. Удаляем переменные изоляции Python
    venv_dir = env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)

    # 2. Очищаем PATH от записей активного venv и собственного Python NeonRoute'''

    new_clean_snippet = '''    # 1. Удаляем переменные изоляции Python
    venv_dir = env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)

    # 2. Очищаем прокси-переменные, мешающие внутренним loopback-запросам Gradio/Uvicorn
    proxy_vars = [
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "AG_LS_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "ag_ls_proxy",
    ]
    for p_var in proxy_vars:
        env.pop(p_var, None)

    # Гарантируем прямой доступ к localhost и небуферизованный вывод логов
    env["NO_PROXY"] = "localhost,127.0.0.1,0.0.0.0"
    env["no_proxy"] = "localhost,127.0.0.1,0.0.0.0"
    env["PYTHONUNBUFFERED"] = "1"

    # 3. Очищаем PATH от записей активного venv и собственного Python NeonRoute'''

    if old_clean_snippet in loc_content:
        loc_content = loc_content.replace(old_clean_snippet, new_clean_snippet, 1)
        forge_loc_path.write_text(loc_content, encoding="utf-8")
        loc_modified = True
        print("[✓] core/forge_locator.py успешно обновлен.")
    else:
        pattern = r'(venv_dir = env\.pop\("VIRTUAL_ENV", None\)\s+env\.pop\("PYTHONPATH", None\)\s+env\.pop\("PYTHONHOME", None\))'
        replacement = r'''\1

    # 2. Очищаем прокси-переменные, мешающие внутренним loopback-запросам Gradio/Uvicorn
    proxy_vars = [
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "AG_LS_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "ag_ls_proxy",
    ]
    for p_var in proxy_vars:
        env.pop(p_var, None)

    # Гарантируем прямой доступ к localhost и небуферизованный вывод логов
    env["NO_PROXY"] = "localhost,127.0.0.1,0.0.0.0"
    env["no_proxy"] = "localhost,127.0.0.1,0.0.0.0"
    env["PYTHONUNBUFFERED"] = "1"'''
        if re.search(pattern, loc_content):
            loc_content = re.sub(pattern, replacement, loc_content, count=1)
            forge_loc_path.write_text(loc_content, encoding="utf-8")
            loc_modified = True
            print("[✓] core/forge_locator.py успешно обновлен (через regex).")
        else:
            print("[!] ОШИБКА: Не удалось найти фрагмент для замены в core/forge_locator.py.")
            sys.exit(1)

# --- 2. Патчим services/process_manager.py ---
pm_content = proc_mgr_path.read_text(encoding="utf-8")
pm_modified = False

timeout_idx = pm_content.find('Превышено время ожидания запуска Forge')
stop_idx = pm_content.find('self._run_forge_stop()', timeout_idx) if timeout_idx != -1 else -1

if timeout_idx != -1 and stop_idx != -1 and (stop_idx - timeout_idx < 300):
    print("[=] services/process_manager.py: уже содержит завершение процесса при таймауте (пропуск).")
else:
    bak_pm = proc_mgr_path.with_name(f"{proc_mgr_path.name}.bak_{timestamp}")
    shutil.copy2(proc_mgr_path, bak_pm)
    print(f"[+] Создана резервная копия: {bak_pm.name}")

    old_pm_snippet = '''            else:
                err_msg = f"Превышено время ожидания запуска Forge ({startup_timeout} с). Порт 7860 не отвечает."
                logger.warning(err_msg)
                self.forge_status_changed.emit(False, err_msg)
                self.action_completed.emit("forge_start", False, err_msg)'''

    new_pm_snippet = '''            else:
                err_msg = f"Превышено время ожидания запуска Forge ({startup_timeout} с). Порт 7860 не отвечает."
                logger.warning(err_msg)
                try:
                    self._run_forge_stop()
                except Exception as stop_err:
                    logger.warning(f"Ошибка при очистке процесса Forge после таймаута: {stop_err}")
                self._forge_process = None
                if self._forge_log_file and not self._forge_log_file.closed:
                    try:
                        self._forge_log_file.close()
                    except Exception:
                        pass
                    self._forge_log_file = None
                self.forge_status_changed.emit(False, err_msg)
                self.action_completed.emit("forge_start", False, err_msg)'''

    if old_pm_snippet in pm_content:
        pm_content = pm_content.replace(old_pm_snippet, new_pm_snippet, 1)
        proc_mgr_path.write_text(pm_content, encoding="utf-8")
        pm_modified = True
        print("[✓] services/process_manager.py успешно обновлен.")
    else:
        pattern = r'(else:\s+err_msg = f"Превышено время ожидания запуска Forge \(\{startup_timeout\} с\)\. Порт 7860 не отвечает\."\s+logger\.warning\(err_msg\))(\s+self\.forge_status_changed\.emit\(False, err_msg\))'
        replacement = r'''\1
                try:
                    self._run_forge_stop()
                except Exception as stop_err:
                    logger.warning(f"Ошибка при очистке процесса Forge после таймаута: {stop_err}")
                self._forge_process = None
                if self._forge_log_file and not self._forge_log_file.closed:
                    try:
                        self._forge_log_file.close()
                    except Exception:
                        pass
                    self._forge_log_file = None\2'''
        if re.search(pattern, pm_content):
            pm_content = re.sub(pattern, replacement, pm_content, count=1)
            proc_mgr_path.write_text(pm_content, encoding="utf-8")
            pm_modified = True
            print("[✓] services/process_manager.py успешно обновлен (через regex).")
        else:
            print("[!] ОШИБКА: Не удалось найти ветку таймаута в services/process_manager.py.")
            sys.exit(1)

print("\n[SUCCESS] Все операции успешно выполнены.")
"@

$TempPyFile = [System.IO.Path]::GetTempFileName() + ".py"
try {
    [System.IO.File]::WriteAllText($TempPyFile, $PyScript, [System.Text.Encoding]::UTF8)
    & $PythonExe $TempPyFile $ResolvedTarget
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[-] Ошибка выполнения патчера (код $LASTEXITCODE)." -ForegroundColor Red
        exit $LASTEXITCODE
    }
}
finally {
    if (Test-Path $TempPyFile) {
        Remove-Item -Force $TempPyFile -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   ИНСТРУКЦИЯ ПО ПРОВЕРКЕ ЗАПУСКА FORGE НА ПК КОЛЛЕГИ       " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "1. Закройте висящие процессы Forge (если остались от старых запусков):" -ForegroundColor White
Write-Host "   taskkill /F /IM python.exe /T" -ForegroundColor Gray
Write-Host "   taskkill /F /IM cmd.exe /T" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Запустите NeonRoute (run.bat или python main.py)." -ForegroundColor White
Write-Host "3. Нажмите кнопку запуска Forge в интерфейсе NeonRoute." -ForegroundColor White
Write-Host "4. Проверьте логи:" -ForegroundColor White
Write-Host "   - В logs/forge.log строка 'Model selected:' больше НЕ зависает," -ForegroundColor Green
Write-Host "     появляется строка 'Model loaded in ...s' и Gradio URL." -ForegroundColor Green
Write-Host "   - В logs/neonroute.log появляется статус:" -ForegroundColor Green
Write-Host "     'Forge server is online and responding on port 7860'." -ForegroundColor Green
Write-Host "   - Handshake проходит штатно, генерация изображений активна." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
