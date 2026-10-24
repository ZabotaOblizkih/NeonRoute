@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul
title NeonRoute Local LLM Server (Universal Portable & Config Driven)

:: =====================================================================
:: 0. ИНИЦИАЛИЗАЦИЯ И ЧТЕНИЕ SERVER_CONFIG.INI (ЗАЩИТА ОТ ПРОБЕЛОВ И КАВЫЧЕК)
:: =====================================================================
set "SCRIPT_DIR=%~dp0"
set "CONFIG_FILE=%SCRIPT_DIR%server_config.ini"

set "HOST=127.0.0.1"
set "PORT=1234"
set "CONTEXT=14336"
set "GPU_LAYERS=99"
set "FLASH_ATTN=on"
set "CACHE_K=q8_0"
set "CACHE_V=q8_0"
set "SLOTS=1"

set "CFG_SERVER_BIN="
set "CFG_CUDA_DIR="
set "CFG_MODELS_DIR="
set "CFG_DEFAULT_MODEL="

if exist "%CONFIG_FILE%" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%CONFIG_FILE%") do (
        set "RAW_KEY=%%A"
        set "RAW_VAL=%%B"
        if not "!RAW_KEY!"=="" (
            set "FIRST_CHAR=!RAW_KEY:~0,1!"
            if not "!FIRST_CHAR!"==";" if not "!FIRST_CHAR!"=="#" if not "!FIRST_CHAR!"=="[" (
                :: Очистка ключа от пробелов и табуляций
                for /f "tokens=* delims= 	" %%K in ("!RAW_KEY!") do set "CLEAN_KEY=%%K"
                for /l %%i in (1,1,32) do if "!CLEAN_KEY:~-1!"==" " set "CLEAN_KEY=!CLEAN_KEY:~0,-1!"

                :: Очистка значения от ведущих/концевых пробелов и кавычек
                set "CLEAN_VAL=!RAW_VAL!"
                if defined CLEAN_VAL (
                    for /f "tokens=* delims= 	" %%V in ("!CLEAN_VAL!") do set "CLEAN_VAL=%%V"
                    for /l %%i in (1,1,32) do if "!CLEAN_VAL:~-1!"==" " set "CLEAN_VAL=!CLEAN_VAL:~0,-1!"
                    set "CLEAN_VAL=!CLEAN_VAL:"=!"
                )

                if /i "!CLEAN_KEY!"=="host" if defined CLEAN_VAL set "HOST=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="port" if defined CLEAN_VAL set "PORT=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="default_context" if defined CLEAN_VAL set "CONTEXT=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="gpu_layers" if defined CLEAN_VAL set "GPU_LAYERS=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="flash_attn" if defined CLEAN_VAL set "FLASH_ATTN=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="cache_type_k" if defined CLEAN_VAL set "CACHE_K=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="cache_type_v" if defined CLEAN_VAL set "CACHE_V=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="parallel_slots" if defined CLEAN_VAL set "SLOTS=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="server_bin_path" set "CFG_SERVER_BIN=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="cuda_dll_dir" set "CFG_CUDA_DIR=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="models_dir" set "CFG_MODELS_DIR=!CLEAN_VAL!"
                if /i "!CLEAN_KEY!"=="default_model" set "CFG_DEFAULT_MODEL=!CLEAN_VAL!"
            )
        )
    )
)

:: Позиционные переопределения из ProcessManager (%~2 = CONTEXT, %~3 = GPU_LAYERS)
if not "%~2"=="" set "CONTEXT=%~2"
if not "%~3"=="" set "GPU_LAYERS=%~3"

:: =====================================================================
:: 1. 3-УРОВНЕВЫЙ КАСКАД ПОИСКА LLAMA-SERVER.EXE
:: =====================================================================
set "SERVER_EXE="

:: Уровень 1: Локальная папка проекта (./bin или корень)
if exist "%SCRIPT_DIR%bin\llama-server.exe" set "SERVER_EXE=%SCRIPT_DIR%bin\llama-server.exe"
if not defined SERVER_EXE if exist "%SCRIPT_DIR%llama-server.exe" set "SERVER_EXE=%SCRIPT_DIR%llama-server.exe"

:: Уровень 2: Путь из server_config.ini
if not defined SERVER_EXE if defined CFG_SERVER_BIN (
    if exist "!CFG_SERVER_BIN!" set "SERVER_EXE=!CFG_SERVER_BIN!"
    if not defined SERVER_EXE if exist "%SCRIPT_DIR%!CFG_SERVER_BIN!" set "SERVER_EXE=%SCRIPT_DIR%!CFG_SERVER_BIN!"
)

:: Уровень 3: Системный поиск в LM Studio backends
if not defined SERVER_EXE (
    for /f "delims=" %%I in ('dir /b /s "%USERPROFILE%\.lmstudio\extensions\backends\*cuda*llama-server.exe" 2^>nul') do (
        set "SERVER_EXE=%%I"
        goto :found_server
    )
)
if not defined SERVER_EXE (
    for /f "delims=" %%I in ('dir /b /s "%USERPROFILE%\.lmstudio\extensions\backends\llama-server.exe" 2^>nul') do (
        set "SERVER_EXE=%%I"
        goto :found_server
    )
)

:found_server
if not defined SERVER_EXE (
    echo [ОШИБКА] Исполняемый файл llama-server.exe не обнаружен!
    echo Положите llama-server.exe в папку bin\ или укажите server_bin_path в server_config.ini.
    exit /b 1
)

:: =====================================================================
:: 2. 3-УРОВНЕВЫЙ КАСКАД ПОИСКА CUDA DLL
:: =====================================================================
set "CUDA_DIR="
for %%I in ("!SERVER_EXE!") do set "SERVER_DIR=%%~dpI"

:: Уровень 1: Рядом с бинарником или в ./bin
if exist "!SERVER_DIR!cudart64*.dll" set "CUDA_DIR=!SERVER_DIR!"
if not defined CUDA_DIR if exist "%SCRIPT_DIR%bin\cudart64*.dll" set "CUDA_DIR=%SCRIPT_DIR%bin"

:: Уровень 2: Из server_config.ini
if not defined CUDA_DIR if defined CFG_CUDA_DIR (
    if exist "!CFG_CUDA_DIR!\cudart64*.dll" set "CUDA_DIR=!CFG_CUDA_DIR!"
)

:: Уровень 3: Вендоры LM Studio
if not defined CUDA_DIR (
    for /f "delims=" %%D in ('dir /b /s /ad "%USERPROFILE%\.lmstudio\extensions\backends\vendor\*cuda12*" 2^>nul') do (
        if exist "%%D\cudart64*.dll" (
            set "CUDA_DIR=%%D"
            goto :found_cuda
        )
    )
)
if not defined CUDA_DIR (
    for /f "delims=" %%D in ('dir /b /s /ad "%USERPROFILE%\.lmstudio\extensions\backends\vendor\*cuda*" 2^>nul') do (
        if exist "%%D\cudart64*.dll" (
            set "CUDA_DIR=%%D"
            goto :found_cuda
        )
    )
)

:found_cuda
if defined CUDA_DIR set "PATH=!CUDA_DIR!;%PATH%"

:: =====================================================================
:: 3. 3-УРОВНЕВЫЙ КАСКАД ОПРЕДЕЛЕНИЯ МОДЕЛИ (.GGUF)
:: =====================================================================
set "MODEL_PATH="

:: 3.0. Явный аргумент командной строки или Drag-and-Drop (%~1)
if not "%~1"=="" (
    if exist "%~1" set "MODEL_PATH=%~1"
    if not defined MODEL_PATH if exist "%SCRIPT_DIR%models\%~1" set "MODEL_PATH=%SCRIPT_DIR%models\%~1"
    if not defined MODEL_PATH if defined CFG_MODELS_DIR if exist "!CFG_MODELS_DIR!\%~1" set "MODEL_PATH=!CFG_MODELS_DIR!\%~1"
)

:: 3.1. Модель по умолчанию из server_config.ini
if not defined MODEL_PATH if defined CFG_DEFAULT_MODEL (
    if exist "!CFG_DEFAULT_MODEL!" set "MODEL_PATH=!CFG_DEFAULT_MODEL!"
    if not defined MODEL_PATH if exist "%SCRIPT_DIR%models\!CFG_DEFAULT_MODEL!" set "MODEL_PATH=%SCRIPT_DIR%models\!CFG_DEFAULT_MODEL!"
    if not defined MODEL_PATH if defined CFG_MODELS_DIR if exist "!CFG_MODELS_DIR!\!CFG_DEFAULT_MODEL!" set "MODEL_PATH=!CFG_MODELS_DIR!\!CFG_DEFAULT_MODEL!"
)

:: 3.2. Уровень 1: Локальная папка проекта ./models
if not defined MODEL_PATH if exist "%SCRIPT_DIR%models\" (
    for /f "delims=" %%M in ('dir /b /s "%SCRIPT_DIR%models\*.gguf" 2^>nul') do (
        echo %%~nxM | findstr /i /v "mmproj" >nul
        if not errorlevel 1 (
            set "MODEL_PATH=%%M"
            goto :check_model
        )
    )
)

:: 3.3. Уровень 2: Папка models_dir из server_config.ini
if not defined MODEL_PATH if defined CFG_MODELS_DIR if exist "!CFG_MODELS_DIR!" (
    for /f "delims=" %%M in ('dir /b /s "!CFG_MODELS_DIR!\*.gguf" 2^>nul') do (
        echo %%~nxM | findstr /i /v "mmproj" >nul
        if not errorlevel 1 (
            set "MODEL_PATH=%%M"
            goto :check_model
        )
    )
)

:: 3.4. Уровень 3: Системный каталог LM Studio
if not defined MODEL_PATH (
    for /f "delims=" %%M in ('dir /b /s "%USERPROFILE%\.lmstudio\models\*.gguf" 2^>nul') do (
        echo %%~nxM | findstr /i /v "mmproj" >nul
        if not errorlevel 1 (
            set "MODEL_PATH=%%M"
            goto :check_model
        )
    )
)

:check_model
if not defined MODEL_PATH (
    echo [ОШИБКА] Файлы моделей (.gguf) не найдены!
    exit /b 1
)

:: Автопоиск мультимодального проектора (mmproj) рядом с моделью
for %%I in ("!MODEL_PATH!") do set "MODEL_DIR=%%~dpI"
set "MMPROJ_ARG="
for /f "delims=" %%F in ('dir /b "!MODEL_DIR!*mmproj*.gguf" 2^>nul') do (
    set "MMPROJ_ARG=--mmproj "!MODEL_DIR!%%F""
    goto :mmproj_done
)
:mmproj_done

:: =====================================================================
:: 4. ОСВОБОЖДЕНИЕ ПОРТА 1234 ПЕРЕД СТАРТОМ
:: =====================================================================
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :%PORT% ^| findstr LISTENING') do (
    taskkill /F /T /PID %%a >nul 2>&1
)

:: =====================================================================
:: 5. ЗАПУСК АВТОНОМНОГО СЕРВЕРА
:: =====================================================================
echo =====================================================================
echo  NeonRoute Autonomous Headless LLM Server
echo =====================================================================
echo  Сервер:    "!SERVER_EXE!"
echo  Модель:    "!MODEL_PATH!"
echo  Контекст:  !CONTEXT! токенов ^| GPU Layers: !GPU_LAYERS! ^| Слоты: !SLOTS!
echo  Адрес:     http://!HOST!:!PORT!/v1
echo =====================================================================

"!SERVER_EXE!" ^
  -m "!MODEL_PATH!" ^
  -c !CONTEXT! ^
  -ngl !GPU_LAYERS! ^
  -fa !FLASH_ATTN! ^
  -ctk !CACHE_K! ^
  -ctv !CACHE_V! ^
  -np !SLOTS! ^
  !MMPROJ_ARG! ^
  --port !PORT! ^
  --host !HOST!

exit /b %ERRORLEVEL%
