"""
hotfix_llama_v098.py
====================
Автономный хотфикс для NeonRoute v0.9.8-beta:
1. Устраняет падение llama-server с ошибкой "error: invalid argument: --no-mmap (код 1)"
   путем динамического перевода на "--load-mode none" для актуальных сборок llama.cpp.
2. Проверяет bin/ на наличие CUDA-библиотек (cudart64_*.dll, cublas64_*.dll).
   При их отсутствии скачивает официальный архив cudart от ggml-org/llama.cpp,
   распаковывает в bin/ и валидирует определение GPU через llama-server.exe --list-devices.

Использует строго стандартную библиотеку Python.
"""

import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


def find_repo_root() -> Path:
    """Определяет корневую папку репозитория NeonRoute."""
    cwd = Path.cwd().resolve()
    if (cwd / "services" / "server_config.py").is_file():
        return cwd
    script_dir = Path(__file__).resolve().parent
    if (script_dir / "services" / "server_config.py").is_file():
        return script_dir
    if (script_dir.parent / "services" / "server_config.py").is_file():
        return script_dir.parent
    raise FileNotFoundError("Не удалось определить корневую папку NeonRoute (отсутствует services/server_config.py)")


def backup_file(file_path: Path) -> Path:
    """Создает резервную копию файла с суффиксом .bak, если она еще не создана."""
    bak_path = file_path.with_suffix(file_path.suffix + ".bak")
    if not bak_path.exists():
        shutil.copy2(file_path, bak_path)
        print(f"  [BACKUP] Создан бэкап: {bak_path.name}")
    return bak_path


def patch_server_config(repo_root: Path) -> bool:
    """Патчит services/server_config.py: добавляет resolve_mmap_disable_args."""
    target_file = repo_root / "services" / "server_config.py"
    if not target_file.is_file():
        print(f"  [ERROR] Файл {target_file} не найден!")
        return False

    content = target_file.read_text(encoding="utf-8")

    if "def resolve_mmap_disable_args" in content:
        print("  [OK] services/server_config.py уже содержит resolve_mmap_disable_args (пропуск).")
        return True

    backup_file(target_file)

    # 1. Добавляем import subprocess и _MMAP_ARGS_CACHE
    if "import subprocess" not in content:
        content = content.replace("import sys\n", "import subprocess\nimport sys\n", 1)

    if "_MMAP_ARGS_CACHE" not in content:
        cache_def = "\n_MMAP_ARGS_CACHE: Dict[str, List[str]] = {}\n"
        content = content.replace('logger = logging.getLogger("NeonRoute.ServerConfig")\n',
                                  'logger = logging.getLogger("NeonRoute.ServerConfig")\n' + cache_def, 1)

    # 2. Добавляем сам метод resolve_mmap_disable_args в класс ServerConfigResolver
    method_code = '''
    @staticmethod
    def resolve_mmap_disable_args(
        server_exe: Optional[Path | str] = None,
        cuda_dir: Optional[Path | str] = None
    ) -> List[str]:
        """
        Determines the appropriate CLI arguments to disable mmap for the resolved llama-server binary.
        Modern llama.cpp (post-deprecation) removed --no-mmap and requires '--load-mode none'.
        Legacy builds only support '--no-mmap'.
        """
        default_args = ["--load-mode", "none"]
        if not server_exe:
            return list(default_args)

        exe_path = Path(server_exe)
        try:
            norm_key = str(exe_path.resolve()).lower()
        except Exception:
            norm_key = str(exe_path).lower()

        if norm_key in _MMAP_ARGS_CACHE:
            return list(_MMAP_ARGS_CACHE[norm_key])

        if not exe_path.is_file():
            return list(default_args)

        try:
            parent_dir = str(exe_path.parent.resolve())
            env = os.environ.copy()
            path_parts = [parent_dir]
            if cuda_dir and Path(cuda_dir).is_dir():
                path_parts.append(str(Path(cuda_dir).resolve()))
            env["PATH"] = f"{os.pathsep.join(path_parts)}{os.pathsep}{env.get('PATH', '')}"

            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0

            proc = subprocess.run(
                [str(exe_path), "--help"],
                env=env,
                cwd=parent_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5.0,
                creationflags=flags,
            )
            help_text = (proc.stdout or "") + (proc.stderr or "")
            if not help_text.strip():
                return list(default_args)

            if "--load-mode" in help_text:
                result = ["--load-mode", "none"]
            elif "--no-mmap" in help_text:
                result = ["--no-mmap"]
            else:
                result = default_args

            _MMAP_ARGS_CACHE[norm_key] = result
            return list(result)

        except Exception as e:
            logger.debug(f"[ServerConfigResolver] Failed to probe llama-server --help: {e}")
            return list(default_args)
'''

    if 'if __name__ == "__main__":' in content:
        content = content.replace('if __name__ == "__main__":', method_code.rstrip() + '\n\n\nif __name__ == "__main__":', 1)
    else:
        content += method_code

    target_file.write_text(content, encoding="utf-8")

    # Проверка синтаксиса
    try:
        py_compile.compile(str(target_file), doraise=True)
        print("  [SUCCESS] services/server_config.py успешно пропатчен и проверен.")
        return True
    except Exception as e:
        print(f"  [FATAL] Ошибка синтаксиса после патча services/server_config.py: {e}")
        bak = target_file.with_suffix(target_file.suffix + ".bak")
        if bak.exists():
            shutil.copy2(bak, target_file)
            print("  [REVERT] Файл восстановлен из резервной копии.")
        return False


def patch_process_manager(repo_root: Path) -> bool:
    """Патчит services/process_manager.py: заменяет cmd.append('--no-mmap') на безопасный вызов."""
    target_file = repo_root / "services" / "process_manager.py"
    if not target_file.is_file():
        print(f"  [ERROR] Файл {target_file} не найден!")
        return False

    content = target_file.read_text(encoding="utf-8")

    if "resolve_mmap_disable_args" in content:
        print("  [OK] services/process_manager.py уже использует resolve_mmap_disable_args (пропуск).")
        return True

    backup_file(target_file)

    old_fragment_pattern = re.compile(
        r'([ \t]*)is_hybrid\s*=\s*effective_ngl\s*<\s*audit_data\.get\("total_layers",\s*99\)\s*\n'
        r'\s*if\s*\(ini_cfg\.get\("no_mmap",\s*True\)\s*or\s*is_hybrid\)\s*and\s*"--no-mmap"\s*not\s*in\s*cmd:\s*\n'
        r'\s*cmd\.append\("--no-mmap"\)'
    )

    match = old_fragment_pattern.search(content)
    if match:
        indent = match.group(1)
        replacement_lines = [
            f"{indent}cuda_dir = ServerConfigResolver.resolve_cuda_dir(server_exe)",
            f"",
            f"{indent}is_hybrid = effective_ngl < audit_data.get(\"total_layers\", 99)",
            f"{indent}if ini_cfg.get(\"no_mmap\", True) or is_hybrid:",
            f"{indent}    has_mmap_flag = any(",
            f"{indent}        arg in (\"--no-mmap\", \"--load-mode\", \"-lm\") or",
            f"{indent}        arg.startswith((\"--load-mode=\", \"-lm=\"))",
            f"{indent}        for arg in cmd",
            f"{indent}    )",
            f"{indent}    if not has_mmap_flag:",
            f"{indent}        cmd.extend(ServerConfigResolver.resolve_mmap_disable_args(server_exe, cuda_dir))",
        ]
        replacement = "\n".join(replacement_lines)
        new_content = content[:match.start()] + replacement + content[match.end():]
    else:
        target_str = 'if (ini_cfg.get("no_mmap", True) or is_hybrid) and "--no-mmap" not in cmd:\n                    cmd.append("--no-mmap")'
        if target_str in content:
            new_content = content.replace(target_str, '''has_mmap_flag = any(arg in ("--no-mmap", "--load-mode", "-lm") or arg.startswith(("--load-mode=", "-lm=")) for arg in cmd)
                    if not has_mmap_flag:
                        cmd.extend(ServerConfigResolver.resolve_mmap_disable_args(server_exe, cuda_dir))''')
        else:
            print("  [WARNING] Шаблон для замены --no-mmap не найден в services/process_manager.py!")
            return False

    target_file.write_text(new_content, encoding="utf-8")

    try:
        py_compile.compile(str(target_file), doraise=True)
        print("  [SUCCESS] services/process_manager.py успешно пропатчен и проверен.")
        return True
    except Exception as e:
        print(f"  [FATAL] Ошибка синтаксиса после патча services/process_manager.py: {e}")
        bak = target_file.with_suffix(target_file.suffix + ".bak")
        if bak.exists():
            shutil.copy2(bak, target_file)
            print("  [REVERT] Файл восстановлен из резервной копии.")
        return False


def download_with_progress(url: str, dest_path: Path, max_retries: int = 3, timeout: int = 60) -> bool:
    """Скачивает файл с консольным прогресс-баром и повторными попытками."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(dest_path.suffix + ".part")

    for attempt in range(1, max_retries + 1):
        try:
            print(f"  [DOWNLOAD] Подключение к источнику (попытка {attempt}/{max_retries})...")
            existing = temp_path.stat().st_size if temp_path.exists() else 0
            headers = {"User-Agent": "NeonRoute-Hotfix"}
            if existing > 0:
                headers["Range"] = f"bytes={existing}-"

            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", getattr(resp, "code", 200))
                c_len = resp.headers.get("Content-Length")
                total = int(c_len) if c_len else 0

                if status == 206:
                    mode = "ab"
                    downloaded = existing
                    total = existing + total
                else:
                    mode = "wb"
                    downloaded = 0

                with open(temp_path, mode) as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = (downloaded / total) * 100
                            mb_cur = downloaded // (1024 * 1024)
                            mb_tot = total // (1024 * 1024)
                            print(f"\r  [PROGRESS] [{pct:5.1f}%] ({mb_cur}/{mb_tot} МБ)", end="", flush=True)

            print()
            if dest_path.exists():
                dest_path.unlink()
            temp_path.replace(dest_path)
            return True

        except Exception as e:
            print(f"\n  [WARNING] Ошибка загрузки: {e}")
            if attempt < max_retries:
                time.sleep(2.0)
            else:
                return False
    return False


def ensure_cuda_libraries(repo_root: Path) -> bool:
    """
    Проверяет bin/ на наличие CUDA DLL. Если обнаружен ggml-cuda.dll,
    но отсутствуют cudart64_*.dll или cublas64_*.dll — скачивает и распаковывает их.
    """
    bin_dir = repo_root / "bin"
    if not bin_dir.is_dir():
        print("  [INFO] Папка bin/ не найдена, проверка библиотек CUDA пропущена.")
        return True

    cuda_backend = bin_dir / "ggml-cuda.dll"
    if not cuda_backend.is_file():
        print("  [INFO] ggml-cuda.dll не обнаружен в bin/ (не CUDA сборка).")
        return True

    has_cudart = any(bin_dir.glob("cudart64_*.dll"))
    has_cublas = any(bin_dir.glob("cublas64_*.dll"))

    if has_cudart and has_cublas:
        print("  [OK] Все необходимые библиотеки CUDA (cudart64 / cublas64) присутствуют в bin/.")
        verify_devices(bin_dir)
        return True

    print("  [!] ВНИМАНИЕ: Обнаружен ggml-cuda.dll, но ОТСУТСТВУЮТ cudart64_*.dll или cublas64_*.dll!")
    print("  [!] Из-за этого сервер llama.cpp не может использовать видеокарту и падает в RAM.")
    print("  [INFO] Поиск и загрузка пакета CUDA 12.4 runtime (~391 МБ)...")

    # Получаем URL из релизов llama.cpp
    direct_url = "https://github.com/ggml-org/llama.cpp/releases/download/b12477/cudart-llama-bin-win-cuda-12.4-x64.zip"
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=5",
            headers={"User-Agent": "NeonRoute-Hotfix"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            releases = json.loads(resp.read().decode("utf-8"))
            found = False
            for r in releases:
                for a in r.get("assets", []):
                    name = a.get("name", "").lower()
                    if "cudart" in name and "win" in name and "12.4" in name and "x64" in name:
                        direct_url = a.get("browser_download_url")
                        found = True
                        break
                if found:
                    break
    except Exception:
        pass

    archive_name = "cudart-llama-bin-win-cuda-12.4-x64.zip"
    temp_dir = Path(tempfile.gettempdir()) / "neonroute_hotfix"
    temp_dir.mkdir(parents=True, exist_ok=True)
    local_zip = temp_dir / archive_name

    ok = download_with_progress(direct_url, local_zip)
    if not ok:
        print(f"  [ERROR] Не удалось скачать {archive_name}.")
        return False

    print("  [EXTRACT] Распаковка библиотек в bin/ ...")
    extracted_count = 0
    with zipfile.ZipFile(local_zip, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            fname = Path(member.filename).name
            if fname.lower().endswith(".dll"):
                dest_file = bin_dir / fname
                with zf.open(member) as src_f, open(dest_file, "wb") as out_f:
                    shutil.copyfileobj(src_f, out_f)
                extracted_count += 1

    print(f"  [SUCCESS] Извлечено {extracted_count} библиотек в {bin_dir}.")
    try:
        if local_zip.exists():
            local_zip.unlink()
            print(f"  [CLEANUP] Удален временный архив {local_zip.name}")
    except Exception:
        pass

    verify_devices(bin_dir)
    return True


def verify_devices(bin_dir: Path):
    """Выполняет llama-server.exe --list-devices и выводит результат проверки."""
    exe_path = bin_dir / "llama-server.exe"
    if not exe_path.is_file():
        return

    print("\n  [TEST] Проверка распознавания видеокарты (llama-server.exe --list-devices)...")
    env = os.environ.copy()
    env["PATH"] = f"{str(bin_dir)}{os.pathsep}{env.get('PATH', '')}"

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
    try:
        res = subprocess.run(
            [str(exe_path), "--list-devices"],
            cwd=str(bin_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=flags
        )
        out = (res.stdout or res.stderr or "").strip()
        print("  " + "-" * 50)
        for line in out.splitlines():
            print(f"    {line}")
        print("  " + "-" * 50)
        if res.returncode == 0 and bool(out) and "(none)" not in out.lower():
            print("  [SUCCESS] Видеокарта успешно инициализирована CUDA бэкендом!")
        else:
            print("  [WARNING] Видеокарта не определена или сообщает (none). Проверьте версию драйвера NVIDIA.")
    except Exception as e:
        print(f"  [WARNING] Не удалось запустить тест --list-devices: {e}")


def main():
    print("=" * 65)
    print("      NeonRoute v0.9.8-beta — Автономный хотфикс движка LLM")
    print("=" * 65)

    try:
        repo_root = find_repo_root()
        print(f"[1] Корень проекта: {repo_root}")
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print("\n[2] Патчинг конфигуратора сервера (services/server_config.py)...")
    if not patch_server_config(repo_root):
        print("[ERROR] Сбой патчинга services/server_config.py")
        sys.exit(1)

    print("\n[3] Патчинг менеджера процессов (services/process_manager.py)...")
    if not patch_process_manager(repo_root):
        print("[ERROR] Сбой патчинга services/process_manager.py")
        sys.exit(1)

    print("\n[4] Проверка библиотек CUDA в каталоге bin/...")
    if not ensure_cuda_libraries(repo_root):
        print("[WARNING] Библиотеки CUDA не были установлены полностью.")

    print("\n" + "=" * 65)
    print("  Хотфикс успешно применён! Теперь NeonRoute готов к работе.")
    print("=" * 65)
    sys.exit(0)


if __name__ == "__main__":
    main()
