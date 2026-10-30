from pathlib import Path

dlg_path = Path("ui/dialogs/model_sampling_dialog.py")
if dlg_path.is_file():
    lines = dlg_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    print(f"=== АНАЛИЗ {dlg_path.as_posix()} ===")
    for i, l in enumerate(lines):
        if any(k in l for k in ("is_engineer", "temperature", "temp_slider", "setEnabled", "setRange", "setMaximum")):
            print(f"{i+1:4d} | {l}")
else:
    print("Файл model_sampling_dialog.py не найден!")
