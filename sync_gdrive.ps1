$Source = "C:\NeonRoute"
$Destination = "E:\Мой диск\NeonRoute"

# Папки для исключения (добавил правильное имя __pycache__)
$ExcludeDirs = @(
    ".venv", "venv", ".git", "__pycache__", "pycache", ".pytest_cache",
    ".ruff_cache", "outputs", "models", "lancedb_data", "data",
    "scripts", "PyQt-Fluent-Widgets"
)

# Файлы для исключения (исправлены маски: добавлены звездочки *)
$ExcludeFiles = @(
    "*.safetensors", "*.gguf", "*.bin", "*.pt", "*.pth",
    "*.onnx", "*.db", "*.sqlite", "*.log"
)

Write-Host "`n=== Синхронизация NeonRoute -> Google Диск ===" -ForegroundColor Cyan

# 1. Сканирование без исключенных папок
Write-Host "[1/2] Сканирование структуры (папки scripts и PyQt-Fluent-Widgets исключены)..." -ForegroundColor Yellow

function Get-FilesFast ($dir) {
    $items = Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue
    foreach ($item in $items) {
        if ($item.PSIsContainer) {
            if ($ExcludeDirs -notcontains $item.Name) {
                Get-FilesFast $item.FullName
            }
        } else {
            $skip = $false
            foreach ($pattern in $ExcludeFiles) {
                if ($item.Name -like $pattern) {
                    $skip = $true
                    break
                }
            }
            if (-not $skip) {
                $item
            }
        }
    }
}

$AllSourceFiles = @(Get-FilesFast $Source)
$total = $AllSourceFiles.Count
Write-Host "Найдено файлов для проверки: $total" -ForegroundColor Green

if (-not (Test-Path -LiteralPath $Destination)) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
}

# 2. Анализ с динамическим показом каждого файла
Write-Host "[2/2] Анализ и синхронизация изменений..." -ForegroundColor Yellow

$copiedCount = 0
$index = 0

foreach ($file in $AllSourceFiles) {
    $index++
    $relPath = $file.FullName.Substring($Source.Length).TrimStart('\', '/')
    $destFile = Join-Path $Destination $relPath
    $destDir = Split-Path $destFile -Parent

    # Динамическая строка: показывает текущий файл прямо в процессе проверки
    $statusText = "[$index/$total] Анализ: $relPath"
    if ($statusText.Length -gt 100) { 
        $statusText = $statusText.Substring(0, 97) + "..." 
    }
    Write-Host ("`r" + $statusText.PadRight(100)) -NoNewline

    $needCopy = $false
    $reason = ""

    if (-not (Test-Path -LiteralPath $destFile)) {
        $needCopy = $true
        $reason = "НОВЫЙ ФАЙЛ"
    } else {
        $destItem = Get-Item -LiteralPath $destFile
        
        # 1. Сравниваем размер
        if ($file.Length -ne $destItem.Length) {
            $needCopy = $true
            $reason = "РАЗМЕР ИЗМЕНИЛСЯ"
        } else {
            # 2. Размеры равны: смотрим дату
            $timeDiff = [math]::Abs(($file.LastWriteTimeUtc - $destItem.LastWriteTimeUtc).TotalSeconds)
            if ($timeDiff -gt 2) {
                # Дата отличается: проверяем реальный код хэшем
                $srcHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
                $dstHash = (Get-FileHash -LiteralPath $destFile -Algorithm SHA256).Hash
                if ($srcHash -ne $dstHash) {
                    $needCopy = $true
                    $reason = "КОД ИЗМЕНЕН"
                }
            }
        }
    }

    if ($needCopy) {
        if (-not (Test-Path -LiteralPath $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        Copy-Item -LiteralPath $file.FullName -Destination $destFile -Force
        (Get-Item -LiteralPath $destFile).LastWriteTimeUtc = $file.LastWriteTimeUtc
        
        Write-Host "`n[$index/$total] -> [$reason] $relPath" -ForegroundColor Green
        $copiedCount++
    }
}

Write-Host ("`r" + (" " * 100) + "`r") -NoNewline
Write-Host "Готово! Обновлено файлов: $copiedCount из $total проверенных.`n" -ForegroundColor Cyan