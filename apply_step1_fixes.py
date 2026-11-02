import re
from pathlib import Path

# 1. Исправление ui/controllers/hardware_engine.py (оборачивание генерации в finally)
hw_file = Path("ui/controllers/hardware_engine.py")
if hw_file.exists():
    text = hw_file.read_text(encoding="utf-8").replace("\r\n", "\n")
    pattern_hw = re.compile(
        r'([ \t]*kwargs = plan\.to_forge_kwargs\(output_dir=sess_images_dir, session_id=sess_id\)\n)'
        r'[\s\S]*?'
        r'([ \t]*return \{"image_path": img_path, "plan": plan, "checkpoint": plan\.target_checkpoint\})'
    )
    replacement_hw = (
        r'\1'
        '            img_path = None\n'
        '            try:\n'
        '                img_path = self.forge_client.text_to_image(**kwargs)\n'
        '            finally:\n'
        '                try:\n'
        '                    self.forge_client.unload_checkpoint()\n'
        '                    self.forge_client.free_vram()\n'
        '                except Exception as _e_unl:\n'
        '                    logger.debug(f"[ForgeClient] Ошибка выгрузки чекпоинта: {_e_unl}")\n'
        '                self._reload_active_lms_model_async(delay=1.2)\n'
        r'\2'
    )
    new_text, count = pattern_hw.subn(replacement_hw, text, count=1)
    if count > 0:
        hw_file.write_text(new_text, encoding="utf-8")
        print("OK: ui/controllers/hardware_engine.py успешно исправлен (добавлен блок finally)")
    else:
        print("WARN: Шаблон не найден в ui/controllers/hardware_engine.py")
else:
    print("ERROR: ui/controllers/hardware_engine.py не найден")

# 2. Исправление services/art_director/director.py (приоритет аппаратного лимита над JSON-профилем)
dir_file = Path("services/art_director/director.py")
if dir_file.exists():
    text = dir_file.read_text(encoding="utf-8").replace("\r\n", "\n")
    pattern_dir = re.compile(
        r'([ \t]*if "enable_hr" in model_pipeline and model_pipeline\["enable_hr"\] is not None:\n'
        r'[ \t]*enable_hr = bool\(model_pipeline\["enable_hr"\]\) and not is_bg\n'
        r'[ \t]*hr_scale = float\(model_pipeline\.get\("hr_scale", 1\.5\)\) if enable_hr else 1\.0\n'
        r'[ \t]*else:\n'
        r'[ \t]*if arch_cfg\.get\("is_xl"\) or is_large_canvas:\n'
        r'[ \t]*hr_scale = 1\.0\n'
        r'[ \t]*enable_hr = False\n'
        r'[ \t]*else:\n'
        r'[ \t]*hr_scale = float\(model_pipeline\.get\("hr_scale", 1\.5\)\)\n'
        r'[ \t]*enable_hr = True if not is_bg else False)'
    )
    replacement_dir = (
        '        if "enable_hr" in model_pipeline and model_pipeline["enable_hr"] is not None:\n'
        '            enable_hr = bool(model_pipeline["enable_hr"]) and not is_bg\n'
        '            hr_scale = float(model_pipeline.get("hr_scale", 1.5)) if enable_hr else 1.0\n'
        '        else:\n'
        '            hr_scale = float(model_pipeline.get("hr_scale", 1.5))\n'
        '            enable_hr = True if not is_bg else False\n'
        '\n'
        '        if arch_cfg.get("is_xl") or is_large_canvas:\n'
        '            enable_hr = False\n'
        '            hr_scale = 1.0'
    )
    new_text, count = pattern_dir.subn(replacement_dir, text)
    if count > 0:
        dir_file.write_text(new_text, encoding="utf-8")
        print(f"OK: services/art_director/director.py успешно исправлен ({count} вхождения)")
    else:
        print("WARN: Шаблон не найден в services/art_director/director.py")
else:
    print("ERROR: services/art_director/director.py не найден")
