"""
NeonRoute - Autonomous Server Configuration Resolver
Implements 3-level cascade resolution for headless llama-server, CUDA libraries, and GGUF models.
"""

import configparser
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

import config
from config import DEFAULT_LLAMA_UBATCH_SIZE, DEFAULT_LLAMA_BATCH_SIZE

logger = logging.getLogger("NeonRoute.ServerConfig")

_MMAP_ARGS_CACHE: Dict[str, List[str]] = {}


def _normalize_kv_type(val: Optional[str], default: str = "q8_0") -> str:
    """Normalizes raw KV cache type strings to valid llama.cpp flags."""
    if not val:
        return default
    v = str(val).strip().lower()
    if v in ("fp16", "f16"):
        return "f16"
    if v in ("q4", "q4_0"):
        return "q4_0"
    if v in ("q8", "q8_0"):
        return "q8_0"
    return v


def resolve_kv_cache_types(
    raw_type: Optional[str],
    cache_type_k: Optional[str] = None,
    cache_type_v: Optional[str] = None
) -> Tuple[str, str]:
    """
    SSOT resolver for llama.cpp KV cache quantization types (-ctk and -ctv).
    Maps high-level presets ('q8_0_q4_0', 'q4_0', 'f16', 'q8_0') or explicit ctk/ctv
    values to a valid (cache_type_k, cache_type_v) tuple.
    """
    raw_clean = str(raw_type).strip().lower() if raw_type else ""

    if raw_clean in ("q8_0_q4_0", "q8_0/q4_0", "q8_0-q4_0"):
        return ("q8_0", "q4_0")
    if raw_clean in ("q4_0", "q4"):
        return ("q4_0", "q4_0")
    if raw_clean in ("f16", "fp16"):
        return ("f16", "f16")

    # If explicit cache_type_k and/or cache_type_v are provided (e.g. from server_config.ini)
    if cache_type_k is not None or cache_type_v is not None:
        norm_k = _normalize_kv_type(cache_type_k, "q8_0")
        norm_v = _normalize_kv_type(cache_type_v, norm_k)
        if raw_clean not in ("q8_0", "q8", ""):
            return (_normalize_kv_type(raw_clean), _normalize_kv_type(raw_clean))
        return (norm_k, norm_v)

    if raw_clean in ("q8_0", "q8", ""):
        return ("q8_0", "q8_0")

    norm_val = _normalize_kv_type(raw_clean)
    return (norm_val, norm_val)


def _sanitize_ini_value(raw: Optional[str]) -> str:
    """
    Sanitizes raw string values read from INI files:
    - Strips whitespace
    - Strips enclosing quotes (single and double)
    - Strips inline comments (; or #) unless quoted
    """
    if not raw:
        return ""
    val = raw.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    else:
        # Strip inline comments if not quoted
        for sep in (";", "#"):
            if sep in val:
                val = val.split(sep, 1)[0].strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1].strip()
    return val


class ServerConfigResolver:
    """
    Resolves configuration, binaries, CUDA libraries, and models for headless llama-server.
    """

    DEFAULT_CONFIG: Dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 1234,
        "default_context": 14336,
        "gpu_layers": 99,
        "flash_attn": "on",
        "cache_type_k": "q8_0",
        "cache_type_v": "q8_0",
        "parallel_slots": 1,
        "ubatch_size": DEFAULT_LLAMA_UBATCH_SIZE,
        "batch_size": DEFAULT_LLAMA_BATCH_SIZE,
        "no_mmap": True,
        "server_bin_path": "",
        "cuda_dll_dir": "",
        "models_dir": "",
        "default_model": "",
        "verbose": False,
        "log_file": "logs/llama_server.log",
    }

    @staticmethod
    def load_ini() -> Dict[str, Any]:
        """
        Reads SERVER_CONFIG_INI_PATH using configparser.ConfigParser(interpolation=None) in utf-8-sig.
        Returns a dictionary with sanitized values and safe defaults.
        """
        cfg = ServerConfigResolver.DEFAULT_CONFIG.copy()
        ini_path = config.SERVER_CONFIG_INI_PATH

        if not ini_path.exists():
            logger.warning(f"Server config file not found at {ini_path}, using defaults.")
            return cfg

        parser = configparser.ConfigParser(interpolation=None)
        try:
            with open(ini_path, "r", encoding="utf-8-sig") as f:
                parser.read_file(f)
        except Exception as e:
            logger.error(f"Failed to read {ini_path}: {e}. Falling back to defaults.")
            return cfg

        # Section [SERVER]
        if parser.has_section("SERVER"):
            s = parser["SERVER"]
            if "host" in s:
                cfg["host"] = _sanitize_ini_value(s.get("host")) or cfg["host"]
            if "port" in s:
                try:
                    cfg["port"] = int(_sanitize_ini_value(s.get("port")))
                except (ValueError, TypeError):
                    pass
            if "default_context" in s:
                try:
                    cfg["default_context"] = int(_sanitize_ini_value(s.get("default_context")))
                except (ValueError, TypeError):
                    pass
            if "gpu_layers" in s:
                try:
                    cfg["gpu_layers"] = int(_sanitize_ini_value(s.get("gpu_layers")))
                except (ValueError, TypeError):
                    pass
            if "flash_attn" in s:
                cfg["flash_attn"] = _sanitize_ini_value(s.get("flash_attn")) or cfg["flash_attn"]
            if "cache_type_k" in s:
                cfg["cache_type_k"] = _sanitize_ini_value(s.get("cache_type_k")) or cfg["cache_type_k"]
            if "cache_type_v" in s:
                cfg["cache_type_v"] = _sanitize_ini_value(s.get("cache_type_v")) or cfg["cache_type_v"]
            if "no_mmap" in s:

                raw_nm = _sanitize_ini_value(s.get("no_mmap")).lower()

                cfg["no_mmap"] = raw_nm in ("true", "1", "yes", "on")

            if "parallel_slots" in s:
                try:
                    cfg["parallel_slots"] = int(_sanitize_ini_value(s.get("parallel_slots")))
                except (ValueError, TypeError):
                    pass
            if "ubatch_size" in s:
                try:
                    cfg["ubatch_size"] = int(_sanitize_ini_value(s.get("ubatch_size")))
                except (ValueError, TypeError):
                    pass
            if "batch_size" in s:
                try:
                    cfg["batch_size"] = int(_sanitize_ini_value(s.get("batch_size")))
                except (ValueError, TypeError):
                    pass

            # Гарантируем batch_size >= ubatch_size
            ub_val = cfg.get("ubatch_size", DEFAULT_LLAMA_UBATCH_SIZE)
            b_val = cfg.get("batch_size", DEFAULT_LLAMA_BATCH_SIZE)
            if b_val < ub_val:
                cfg["batch_size"] = ub_val

        # Section [PATHS]
        if parser.has_section("PATHS"):
            p = parser["PATHS"]
            if "server_bin_path" in p:
                cfg["server_bin_path"] = _sanitize_ini_value(p.get("server_bin_path"))
            if "cuda_dll_dir" in p:
                cfg["cuda_dll_dir"] = _sanitize_ini_value(p.get("cuda_dll_dir"))
            if "models_dir" in p:
                cfg["models_dir"] = _sanitize_ini_value(p.get("models_dir"))
            if "default_model" in p:
                cfg["default_model"] = _sanitize_ini_value(p.get("default_model"))

        # Section [LOGGING]
        if parser.has_section("LOGGING"):
            lg = parser["LOGGING"]
            if "verbose" in lg:
                raw_v = _sanitize_ini_value(lg.get("verbose")).lower()
                cfg["verbose"] = raw_v in ("true", "1", "yes", "on")
            if "log_file" in lg:
                cfg["log_file"] = _sanitize_ini_value(lg.get("log_file")) or cfg["log_file"]

        return cfg

    @staticmethod
    def resolve_server_profile_for_model(model_path: Optional[Path | str] = None) -> str:
        """
        Determines which llama-server profile is required for the given model:
        - 'prismml': PTQ1_0 / PQ2_0 / Ternary-Bonsai models requiring PrismML's fork
        - 'standard': standard ggml-org/llama.cpp
        """
        if not model_path:
            return "standard"
        try:
            from services.layer_budgeter import LayerBudgeter
            return LayerBudgeter.detect_required_server_profile(model_path)
        except Exception:
            name_lower = Path(model_path).name.lower()
            if any(m in name_lower for m in ("ptq1_0", "pq2_0", "ternary-bonsai", "ternary_bonsai")):
                return "prismml"
            return "standard"

    @staticmethod
    def resolve_server_exe(
        model_path: Optional[Path | str] = None,
        server_profile: Optional[str] = None,
    ) -> Optional[Path]:
        """
        3-level cascade resolution for llama-server.exe:
        - If server_profile == 'prismml' (or detected from model_path):
          resolves exclusively from CUSTOM_BIN_PRISM_DIR ('bin/prismml/llama-server.exe')
          or prism_server_bin_path in server_config.ini.
        - Otherwise ('standard'):
          Level 1: Local project folders (LOCAL_BIN_DIR / 'llama-server.exe' or BASE_DIR / 'llama-server.exe')
          Level 2: server_bin_path from server_config.ini
          Level 3: Recursive search in Path.home() / '.lmstudio' / 'extensions' / 'backends'
                   (prioritizing *cuda*llama-server.exe, sorted by st_mtime descending)
        """
        profile = server_profile or (
            ServerConfigResolver.resolve_server_profile_for_model(model_path) if model_path else "standard"
        )

        if profile == "prismml":
            prism_exe = config.CUSTOM_BIN_PRISM_DIR / "llama-server.exe"
            if prism_exe.is_file():
                return prism_exe.resolve()

            ini_path = config.SERVER_CONFIG_INI_PATH
            if ini_path.exists():
                try:
                    parser = configparser.ConfigParser(interpolation=None)
                    with open(ini_path, "r", encoding="utf-8-sig") as f:
                        parser.read_file(f)
                    if parser.has_section("PATHS") and "prism_server_bin_path" in parser["PATHS"]:
                        custom_prism = _sanitize_ini_value(parser["PATHS"].get("prism_server_bin_path"))
                        if custom_prism:
                            p = Path(custom_prism)
                            if not p.is_absolute():
                                p = config.BASE_DIR / p
                            if p.is_file():
                                return p.resolve()
                except Exception:
                    pass
            return None

        # Level 1: Local project directory
        local_bin_exe = config.LOCAL_BIN_DIR / "llama-server.exe"
        if local_bin_exe.is_file():
            return local_bin_exe.resolve()

        base_bin_exe = config.BASE_DIR / "llama-server.exe"
        if base_bin_exe.is_file():
            return base_bin_exe.resolve()

        # Level 2: server_config.ini
        ini_cfg = ServerConfigResolver.load_ini()
        custom_bin = ini_cfg.get("server_bin_path", "").strip()
        if custom_bin:
            p = Path(custom_bin)
            if not p.is_absolute():
                p = config.BASE_DIR / p
            if p.is_file():
                return p.resolve()

        # Level 3: LM Studio backends search
        lm_backends_dir = Path.home() / ".lmstudio" / "extensions" / "backends"
        if lm_backends_dir.exists() and lm_backends_dir.is_dir():
            try:
                candidates: List[Path] = [
                    f for f in lm_backends_dir.rglob("llama-server.exe") if f.is_file()
                ]
                if candidates:
                    def _server_rank(c: Path) -> tuple:
                        c_str = str(c).lower()
                        is_cuda12 = 1 if "cuda12" in c_str else 0
                        is_cuda = 1 if "cuda" in c_str else 0
                        # Directory or file modification time (unzipped binaries may retain 2000-01-01 timestamps)
                        try:
                            mtime = max(c.stat().st_mtime, c.parent.stat().st_mtime)
                        except Exception:
                            mtime = 0
                        # Version tuple (e.g. 2.64.0 -> (2, 64, 0))
                        v_match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", c_str)
                        v_tuple = tuple(int(x) for x in v_match.groups() if x is not None) if v_match else (0,)
                        return (is_cuda12, is_cuda, mtime, v_tuple)

                    cuda_candidates = [
                        c for c in candidates
                        if "cuda" in c.name.lower() or "cuda" in str(c.parent).lower()
                    ]
                    if cuda_candidates:
                        cuda_candidates.sort(key=_server_rank, reverse=True)
                        return cuda_candidates[0].resolve()

                    candidates.sort(key=_server_rank, reverse=True)
                    return candidates[0].resolve()
            except Exception as e:
                logger.debug(f"Error scanning LM Studio backends for llama-server.exe: {e}")

        return None

    @staticmethod
    def resolve_cuda_dir(server_exe: Optional[Path] = None) -> Optional[Path]:
        """
        Resolves directory containing cudart64*.dll libraries:
        Level 1: Beside server_exe or in LOCAL_BIN_DIR
        Level 2: cuda_dll_dir from server_config.ini
        Level 3: In Path.home() / '.lmstudio' / 'extensions' / 'backends' vendor folders (*cuda12*, *cuda*)
        """
        if server_exe is None:
            server_exe = ServerConfigResolver.resolve_server_exe()

        # Level 1: Beside server executable or in LOCAL_BIN_DIR
        if server_exe and server_exe.is_file():
            exe_parent = server_exe.parent
            if any(exe_parent.glob("cudart64*.dll")):
                return exe_parent.resolve()

        if config.LOCAL_BIN_DIR.exists() and any(config.LOCAL_BIN_DIR.glob("cudart64*.dll")):
            return config.LOCAL_BIN_DIR.resolve()

        # Level 2: From server_config.ini
        ini_cfg = ServerConfigResolver.load_ini()
        custom_cuda = ini_cfg.get("cuda_dll_dir", "").strip()
        if custom_cuda:
            p = Path(custom_cuda)
            if not p.is_absolute():
                p = config.BASE_DIR / p
            if p.is_dir():
                return p.resolve()

        # Level 3: LM Studio vendor folders
        lm_backends_dir = Path.home() / ".lmstudio" / "extensions" / "backends"
        if lm_backends_dir.exists() and lm_backends_dir.is_dir():
            try:
                candidate_dirs: List[Path] = []
                for dll in lm_backends_dir.rglob("cudart64*.dll"):
                    if dll.is_file() and dll.parent not in candidate_dirs:
                        candidate_dirs.append(dll.parent)

                if candidate_dirs:
                    server_str = str(server_exe).lower() if server_exe else ""
                    exe_has_cuda12 = "cuda12" in server_str

                    def _cuda_rank(d: Path) -> tuple:
                        d_str = str(d).lower()
                        cuda12_dir = "cuda12" in d_str
                        # Match cuda12 directory if binary is cuda12; otherwise prefer non-cuda12
                        ver_match = 1 if (exe_has_cuda12 and cuda12_dir) or (not exe_has_cuda12 and not cuda12_dir) else 0
                        is_cuda12 = 1 if cuda12_dir else 0
                        is_cuda = 1 if "cuda" in d_str else 0
                        try:
                            mtime = max(d.stat().st_mtime, *(f.stat().st_mtime for f in d.glob("cudart64*.dll")))
                        except Exception:
                            mtime = 0
                        return (ver_match, is_cuda12, is_cuda, mtime)

                    candidate_dirs.sort(key=_cuda_rank, reverse=True)
                    return candidate_dirs[0].resolve()
            except Exception as e:
                logger.debug(f"Error scanning LM Studio vendor dirs for CUDA DLLs: {e}")

        return None

    @staticmethod
    def discover_gguf_models(include_lmstudio: bool = True, include_lm_studio: bool = True) -> Dict[str, Path]:
        """
        Discovers all available .gguf models (excluding mmproj files) in:
        1. LOCAL_MODELS_DIR (BASE_DIR / 'models')
        2. models_dir from server_config.ini
        3. ~/.lmstudio/models and ~/.cache/lm-studio/models (if include_lmstudio is True)
        Returns an ordered dictionary mapping {model_key: full_path}.
        """
        include_lms = include_lmstudio and include_lm_studio
        discovered: Dict[str, Path] = {}

        scan_roots: List[Path] = [config.LOCAL_MODELS_DIR]

        # INI models_dir
        ini_cfg = ServerConfigResolver.load_ini()
        cfg_models = ini_cfg.get("models_dir", "").strip()
        if cfg_models:
            p = Path(cfg_models)
            if not p.is_absolute():
                p = config.BASE_DIR / p
            if p.is_dir() and p not in scan_roots:
                scan_roots.append(p)

        # LM Studio roots
        if include_lms:
            lm_roots = [
                Path.home() / ".lmstudio" / "models",
                Path.home() / ".cache" / "lm-studio" / "models"
            ]
            for r in lm_roots:
                if r.is_dir() and r not in scan_roots:
                    scan_roots.append(r)

        for root in scan_roots:
            if not root.exists() or not root.is_dir():
                continue
            try:
                for gguf in root.rglob("*.gguf"):
                    if not gguf.is_file():
                        continue
                    # Skip multimodal projection adapters
                    if "mmproj" in gguf.name.lower():
                        continue

                    # Construct model key compatible with existing format
                    if gguf.parent == root:
                        model_key = gguf.stem
                    else:
                        model_key = f"{gguf.parent.name}/{gguf.name}"

                    if model_key not in discovered:
                        discovered[model_key] = gguf.resolve()
            except Exception as e:
                logger.debug(f"Error scanning models directory {root}: {e}")

        return discovered

    @staticmethod
    def resolve_model_path(model_identifier: Optional[str] = None) -> Optional[Path]:
        """
        Resolves model identifier to an absolute Path:
        - If direct path exists on disk, returns it
        - Searches exact match by key, filename, stem, or parent folder
        - Fuzzy/slug match for compatible naming
        - Fallback: default_model from INI -> first model in discover_gguf_models()
        """
        discovered = ServerConfigResolver.discover_gguf_models()

        if model_identifier:
            ident_str = str(model_identifier).strip()
            # 1. Direct path check
            direct_p = Path(ident_str)
            if direct_p.is_file():
                return direct_p.resolve()
            if (config.BASE_DIR / direct_p).is_file():
                return (config.BASE_DIR / direct_p).resolve()
            if (config.LOCAL_MODELS_DIR / direct_p).is_file():
                return (config.LOCAL_MODELS_DIR / direct_p).resolve()

            ini_cfg = ServerConfigResolver.load_ini()
            ini_models_dir = ini_cfg.get("models_dir", "").strip()
            if ini_models_dir:
                custom_root = Path(ini_models_dir)
                if not custom_root.is_absolute():
                    custom_root = config.BASE_DIR / custom_root
                if (custom_root / direct_p).is_file():
                    return (custom_root / direct_p).resolve()

            # 2. Exact key match
            if ident_str in discovered:
                return discovered[ident_str]

            # 3. Case-insensitive key match
            ident_lower = ident_str.lower()
            for k, p in discovered.items():
                if k.lower() == ident_lower:
                    return p

            # 4. Exact filename or stem match
            for k, p in discovered.items():
                if p.name.lower() == ident_lower or p.name.lower() == f"{ident_lower}.gguf":
                    return p
                if p.stem.lower() == ident_lower:
                    return p

            # 5. Parent folder match
            for k, p in discovered.items():
                if p.parent.name.lower() == ident_lower:
                    return p

            # 6. Slug / normalized substring match
            norm_ident = re.sub(r"[^a-z0-9]", "", ident_lower)
            if norm_ident:
                # 6a. Exact normalized match with stem, parent or key
                for k, p in discovered.items():
                    norm_stem = re.sub(r"[^a-z0-9]", "", p.stem.lower())
                    norm_parent = re.sub(r"[^a-z0-9]", "", p.parent.name.lower())
                    norm_key = re.sub(r"[^a-z0-9]", "", k.lower())
                    if norm_ident in (norm_stem, norm_parent, norm_key):
                        return p

                # 6b. Substring match
                for k, p in discovered.items():
                    norm_stem = re.sub(r"[^a-z0-9]", "", p.stem.lower())
                    norm_parent = re.sub(r"[^a-z0-9]", "", p.parent.name.lower())
                    norm_key = re.sub(r"[^a-z0-9]", "", k.lower())
                    if norm_ident in norm_stem or norm_ident in norm_parent or norm_ident in norm_key:
                        return p

        # Fallback 1: default_model from server_config.ini
        ini_cfg = ServerConfigResolver.load_ini()
        default_model = ini_cfg.get("default_model", "").strip()
        if default_model and default_model != model_identifier:
            resolved_default = ServerConfigResolver.resolve_model_path(default_model)
            if resolved_default is not None:
                return resolved_default

        # Fallback 2: first discovered model
        if discovered:
            return next(iter(discovered.values()))

        return None

    @staticmethod
    def update_server_config_models_dir(new_path: Path | str, config_file: Optional[Path | str] = None) -> bool:
        """
        Записывает models_dir в секцию [llama_server] файла server_config.ini.
        """
        try:
            import configparser
            ini_path = Path(config_file) if config_file else config.SERVER_CONFIG_INI_PATH
            ini_path.parent.mkdir(parents=True, exist_ok=True)
            parser = configparser.ConfigParser()
            if ini_path.is_file():
                parser.read(str(ini_path), encoding="utf-8")
            if not parser.has_section("llama_server"):
                parser.add_section("llama_server")
            parser.set("llama_server", "models_dir", str(Path(new_path).resolve()))
            with open(str(ini_path), "w", encoding="utf-8") as f:
                parser.write(f)
            return True
        except Exception as e:
            logger.error(f"[ServerConfigResolver] Ошибка сохранения models_dir: {e}")
            return False

    @staticmethod
    def resolve_mmproj_for_model(model_path: Path) -> Optional[Path]:
        """
        Locates multimodal projection adapter (*mmproj*.gguf) strictly beside model_path.
        Verifies family compatibility to avoid attaching incompatible mmproj files.
        """
        if not model_path or not model_path.exists():
            return None

        model_folder = model_path.parent
        if not model_folder.exists() or not model_folder.is_dir():
            return None

        candidates = [
            f.resolve() for f in model_folder.glob("*.gguf")
            if f.is_file() and "mmproj" in f.name.lower()
        ]
        if not candidates:
            return None

        base_models = [
            f.resolve() for f in model_folder.glob("*.gguf")
            if f.is_file() and "mmproj" not in f.name.lower()
        ]

        vision_keywords = ("vl", "vision", "llava", "pixtral", "minicpm", "internvl", "cogvlm", "moondream", "clip", "ocr")
        model_name_lower = f"{model_folder.name} {model_path.stem}".lower()

        # Case 1: Exactly 1 base model in folder and folder/model has vision indicators
        if len(base_models) == 1 and any(kw in model_name_lower for kw in vision_keywords):
            return candidates[0]

        # Case 2: Specific token/slug match between candidate mmproj and model_path
        norm_model = re.sub(r"[^a-z0-9]", "", model_path.stem.lower())
        for cand in candidates:
            norm_cand = re.sub(r"[^a-z0-9]", "", cand.stem.lower())
            # Check for shared key vision identifiers (e.g. qwen25vl, llava, etc.)
            for kw in vision_keywords:
                if (kw in model_name_lower or kw in norm_model) and kw in norm_cand:
                    return cand

        return None

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


if __name__ == "__main__":
    print("=" * 70)
    print("NeonRoute ServerConfigResolver Verification")
    print("=" * 70)

    # 1. Load INI
    ini_config = ServerConfigResolver.load_ini()
    print("[1] server_config.ini Configuration:")
    for k, v in ini_config.items():
        print(f"    - {k}: {v}")

    # 2. Resolve Server Exe
    server_exe = ServerConfigResolver.resolve_server_exe()
    print(f"\n[2] llama-server.exe Path: {server_exe}")

    # 3. Resolve CUDA Dir
    cuda_dir = ServerConfigResolver.resolve_cuda_dir(server_exe)
    print(f"\n[3] CUDA DLL Directory: {cuda_dir}")

    # 4. Discover Models
    models = ServerConfigResolver.discover_gguf_models()
    print(f"\n[4] Discovered GGUF Models ({len(models)} found):")
    for key, path in models.items():
        mmproj = ServerConfigResolver.resolve_mmproj_for_model(path)
        mm_str = f" [mmproj: {mmproj.name}]" if mmproj else ""
        print(f"    - '{key}' -> {path}{mm_str}")

    # 5. Resolve Model Path test
    active_test = "qwen2.5-coder-7b-instruct"
    resolved_active = ServerConfigResolver.resolve_model_path(active_test)
    print(f"\n[5] Test Resolve Model '{active_test}':\n    -> {resolved_active}")
    print("=" * 70)
