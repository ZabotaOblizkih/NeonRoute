"""
tests/unit/test_gpu_detection_fallback.py
=========================================
Тестирование честного детектора видеокарт через реестр Windows
для видеокарт AMD, Intel и CPU-режима, а также расчета оффлоада в LayerBudgeter.
"""

from unittest.mock import patch, MagicMock
from utils.system_monitor import _detect_windows_gpu_via_registry, SystemMonitor
from services.layer_budgeter import LayerBudgeter


class MockRegistryKey:
    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def setup_winreg_mock(subkeys_data):
    def mock_open_key(hkey, subkey):
        return MockRegistryKey(str(subkey))

    def mock_enum_key(key, idx):
        keys = list(subkeys_data.keys())
        if idx < len(keys):
            return keys[idx]
        raise OSError("No more keys")

    def mock_query_val(key, val_name):
        k_name = getattr(key, "name", "")
        if k_name in subkeys_data and val_name in subkeys_data[k_name]:
            return subkeys_data[k_name][val_name], 1
        raise FileNotFoundError(f"Key {val_name} not found")

    return mock_open_key, mock_enum_key, mock_query_val


def test_registry_detection_amd_2gb():
    """Проверка определения AMD Radeon с 2 ГБ VRAM."""
    subkeys = {
        "0000": {
            "DriverDesc": "AMD Radeon RX 550",
            "HardwareInformation.qwMemorySize": 2048 * 1024 * 1024,
        }
    }
    open_k, enum_k, query_v = setup_winreg_mock(subkeys)
    with patch("winreg.OpenKey", side_effect=open_k), \
         patch("winreg.EnumKey", side_effect=enum_k), \
         patch("winreg.QueryValueEx", side_effect=query_v):

        info = _detect_windows_gpu_via_registry()
        assert info["vendor"] == "amd"
        assert info["vram_total_mb"] == 2048
        assert "AMD Radeon RX 550" in info["gpu_name"]


def test_registry_detection_amd_10gb():
    """Проверка определения AMD Radeon с 10 ГБ VRAM."""
    subkeys = {
        "0000": {
            "DriverDesc": "AMD Radeon RX 6700 XT",
            "HardwareInformation.qwMemorySize": 10240 * 1024 * 1024,
        }
    }
    open_k, enum_k, query_v = setup_winreg_mock(subkeys)
    with patch("winreg.OpenKey", side_effect=open_k), \
         patch("winreg.EnumKey", side_effect=enum_k), \
         patch("winreg.QueryValueEx", side_effect=query_v):

        info = _detect_windows_gpu_via_registry()
        assert info["vendor"] == "amd"
        assert info["vram_total_mb"] == 10240
        assert "AMD Radeon RX 6700 XT" in info["gpu_name"]


def test_registry_detection_basic_adapter_and_no_gpu():
    """Проверка фильтрации Basic Display Adapter и возврата 0 VRAM при отсутствии дискретного GPU."""
    subkeys = {
        "0000": {
            "DriverDesc": "Microsoft Basic Display Adapter",
            "HardwareInformation.qwMemorySize": 0,
        }
    }
    open_k, enum_k, query_v = setup_winreg_mock(subkeys)
    with patch("winreg.OpenKey", side_effect=open_k), \
         patch("winreg.EnumKey", side_effect=enum_k), \
         patch("winreg.QueryValueEx", side_effect=query_v):

        info = _detect_windows_gpu_via_registry()
        assert info["vram_total_mb"] == 0
        assert info["vendor"] == "unknown"


def test_system_monitor_returns_real_vram_without_nvml():
    """Проверка, что SystemMonitor без NVML возвращает реальный объем из реестра, а не 8192."""
    subkeys = {
        "0000": {
            "DriverDesc": "AMD Radeon RX 580",
            "HardwareInformation.qwMemorySize": 4096 * 1024 * 1024,
        }
    }
    open_k, enum_k, query_v = setup_winreg_mock(subkeys)
    with patch("winreg.OpenKey", side_effect=open_k), \
         patch("winreg.EnumKey", side_effect=enum_k), \
         patch("winreg.QueryValueEx", side_effect=query_v), \
         patch("utils.system_monitor.HAS_PYNVML", False):

        mon = SystemMonitor()
        mon.nvml_initialized = False
        total = mon.get_total_vram_mb()
        assert total == 4096


def test_layer_budgeter_amd_2gb_yields_zero_ngl(tmp_path):
    """Проверка, что для 2 ГБ AMD LayerBudgeter рассчитывает ngl=0 для модели 4.5 ГБ."""
    dummy_gguf = tmp_path / "model_4gb.gguf"
    dummy_gguf.write_bytes(b"GGUF" + b"\x00" * 1024)

    mock_meta = {
        "model_size_mb": 4500.0,
        "total_layers": 32,
        "vocab_size": 150000
    }
    with patch.object(LayerBudgeter, "parse_gguf_metadata", return_value=mock_meta):
        ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
            model_path=str(dummy_gguf),
            target_context=4096,
            total_vram_mb=2048,
            available_ram_mb=16384
        )
        assert ngl == 0
        assert can_run is True


def test_layer_budgeter_no_gpu_cpu_mode(tmp_path):
    """Проверка, что при total_vram_mb=0 LayerBudgeter переходит в безопасный CPU-режим (ngl=0)."""
    dummy_gguf = tmp_path / "model_cpu.gguf"
    dummy_gguf.write_bytes(b"GGUF" + b"\x00" * 1024)

    ngl, can_run, reason, audit = LayerBudgeter.calculate_layer_offload(
        model_path=str(dummy_gguf),
        target_context=2048,
        total_vram_mb=0,
        available_ram_mb=16384
    )
    assert ngl == 0
    assert can_run is True
    assert "CPU-режим" in reason
