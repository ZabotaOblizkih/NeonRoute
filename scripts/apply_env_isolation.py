from pathlib import Path
import re

w_path = Path("ui/models_manager/workers.py")
text = w_path.read_text(encoding="utf-8")

old_env_chunk = """        import os as _os
        import collections as _collections
        env = dict(_os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        stdout_tail = _collections.deque(maxlen=30)"""

new_env_chunk = """        import os as _os
        import collections as _collections

        py_dir = Path(self.python_exe).parent
        torch_lib = py_dir / "lib" / "site-packages" / "torch" / "lib"
        scripts_dir = py_dir / "Scripts"

        env = dict(_os.environ)
        env["PYTHONIOENCODING"] = "utf-8"

        # Изоляция PATH: ставим родные DLL Forge на первое место и исключаем библиотеки llama-server
        clean_path_parts = [str(torch_lib), str(py_dir), str(scripts_dir)]
        for p in _os.environ.get("PATH", "").split(_os.pathsep):
            p_low = p.lower()
            if "neonroute" not in p_low and ".lmstudio" not in p_low:
                clean_path_parts.append(p)
        env["PATH"] = _os.pathsep.join(clean_path_parts)

        stdout_tail = _collections.deque(maxlen=30)"""

if old_env_chunk in text:
    text = text.replace(old_env_chunk, new_env_chunk, 1)
    w_path.write_text(text, encoding="utf-8")
    print("✓ ui/models_manager/workers.py: изоляция PATH успешно внедрена")
else:
    # Запасной вариант с регуляркой
    pattern = re.compile(
        r'env = dict\(_os\.environ\)\s+env\["PYTHONIOENCODING"\] = "utf-8"\s+stdout_tail = _collections\.deque\(maxlen=30\)',
        re.DOTALL
    )
    if pattern.search(text):
        text = pattern.sub(
            """py_dir = Path(self.python_exe).parent\n        torch_lib = py_dir / "lib" / "site-packages" / "torch" / "lib"\n        scripts_dir = py_dir / "Scripts"\n\n        env = dict(_os.environ)\n        env["PYTHONIOENCODING"] = "utf-8"\n\n        clean_path_parts = [str(torch_lib), str(py_dir), str(scripts_dir)]\n        for p in _os.environ.get("PATH", "").split(_os.pathsep):\n            p_low = p.lower()\n            if "neonroute" not in p_low and ".lmstudio" not in p_low:\n                clean_path_parts.append(p)\n        env["PATH"] = _os.pathsep.join(clean_path_parts)\n\n        stdout_tail = _collections.deque(maxlen=30)""",
            text,
            count=1
        )
        w_path.write_text(text, encoding="utf-8")
        print("✓ ui/models_manager/workers.py: изоляция PATH внедрена через regex")
    else:
        print("! Точка внедрения не найдена")
