$Source = "C:\NeonRoute"
$Destination = "E:\Мой диск\NeonRoute"

$ExcludeDirs = @(
    ".venv", "venv", ".git", "__pycache__", ".pytest_cache",
    ".ruff_cache", "outputs", "models", "lancedb_data", "data"
)

$ExcludeFiles = @(
    "*.safetensors", "*.gguf", "*.bin", "*.pt", "*.pth",
    "*.onnx", "*.db", "*.sqlite", "*.log"
)

Write-Host "Синхронизация NeonRoute -> Google Диск (E:)..." -ForegroundColor Cyan

robocopy $Source $Destination /MIR /FFT /Z /XA:SH `
    /XD $ExcludeDirs `
    /XF $ExcludeFiles `
    /R:1 /W:1 /NP /NDL

Write-Host "Готово! Изменения отправлены." -ForegroundColor Green
