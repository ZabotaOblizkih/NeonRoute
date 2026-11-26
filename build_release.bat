@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul
title NeonRoute Release Builder Pipeline
cd /d "%~dp0"

echo =======================================================
echo          NeonRoute - Release Builder Pipeline
echo =======================================================
echo.

set "PY_EXE="
if exist "venv\Scripts\python.exe" (
    set "PY_EXE=venv\Scripts\python.exe"
    goto :run_builder
)

for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3.10" "py -3" "python") do (
    %%~C -c "import sys; sys.exit(0 if sys.version_info[0] >= 3 and sys.version_info[1] >= 10 else 1)" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "PY_EXE=%%~C"
        goto :run_builder
    )
)

:run_builder
if not defined PY_EXE (
    echo [ERROR] Python 3.10+ interpreter not found.
    pause
    exit /b 1
)

echo [INFO] Using Python: %PY_EXE%
echo [INFO] Generating and validating model presets catalog...
%PY_EXE% scripts\build_model_catalog.py
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Model catalog generation failed!
    pause
    exit /b %ERRORLEVEL%
)

set "GIT_TERMINAL_PROMPT=0"
where git >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    git rev-parse --is-inside-work-tree >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        git diff --quiet -- data/model_presets_catalog.json
        if !ERRORLEVEL! EQU 1 (
            echo [INFO] Model catalog changed, committing data/model_presets_catalog.json...
            git add -- data/model_presets_catalog.json
            git commit --only data/model_presets_catalog.json -m "chore(catalog): update model_presets_catalog.json"
            if !ERRORLEVEL! EQU 0 (
                for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "CUR_BRANCH=%%B"
                if defined CUR_BRANCH if not "!CUR_BRANCH!"=="HEAD" (
                    git rev-parse --abbrev-ref --symbolic-full-name @{u} >nul 2>&1
                    if !ERRORLEVEL! EQU 0 (
                        echo [INFO] Pushing catalog update to origin HEAD...
                        git push origin HEAD
                        if !ERRORLEVEL! NEQ 0 (
                            echo [WARN] git push failed or is disabled on remote, continuing release build...
                        )
                    ) else (
                        echo [INFO] No upstream configured for branch !CUR_BRANCH!, skipping git push.
                    )
                )
            )
        ) else (
            echo [INFO] data/model_presets_catalog.json is up to date.
        )
    )
)

%PY_EXE% scripts\build_release.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo =======================================================
    echo [ERROR] Release build failed!
    echo =======================================================
    pause
    exit /b %ERRORLEVEL%
)

echo.
set "PUBLISH_ANS="
set /p "PUBLISH_ANS=Опубликовать релиз на GitHub после успешной сборки и тестов? [Y/N]: "
if /i "%PUBLISH_ANS%"=="Y" (
    echo.
    echo [INFO] Publishing release assets to GitHub Releases...
    %PY_EXE% scripts\build_release.py --publish-only --non-interactive
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] GitHub Release publication failed!
        pause
        exit /b !ERRORLEVEL!
    )
)

echo.
pause
exit /b 0
