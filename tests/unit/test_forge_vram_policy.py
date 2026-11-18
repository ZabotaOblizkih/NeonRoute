"""
Unit tests for adaptive Forge VRAM offload policy, RAM-guarded --pin-shared-memory,
and launch argument assembly.
"""

from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

from services.process_manager import ProcessManager
from services.art_director.vram_policy import (
    VAE_FP32_OVERHEAD_MB,
    resolve_forge_offload_policy,
    resolve_forge_startup_flags,
    should_enable_pin_shared_memory,
    format_vram_planner_log,
    calculate_max_hires_canvas_area,
    is_hires_safe,
    resolve_safe_hires_scale,
    resolve_hires_second_pass_steps,
)


def test_forge_launch_cmd_adaptive_on_8gb(tmp_path):
    """
    При запуске Forge на системе с 8 ГБ VRAM не навязывается жёсткий --always-offload-from-vram
    (режим переключается динамически перед каждым txt2img через /sdapi/v1/free-vram),
    а при достаточной RAM добавляется --pin-shared-memory.
    """
    QApplication.instance() or QApplication([])
    fake_launcher = tmp_path / "fake_forge" / "forge_launcher.bat"
    fake_launcher.parent.mkdir(parents=True, exist_ok=True)
    fake_launcher.write_text("@echo off\nexit /b 0\n", encoding="utf-8")

    pm = ProcessManager()
    pm.is_port_open = MagicMock(side_effect=[False, True])

    mock_proc = MagicMock()
    mock_proc.pid = 8888
    mock_proc.poll.return_value = None
    mock_proc.stdout.readline.return_value = ""

    fake_vm = MagicMock(total=32 * 1024 ** 3, available=20 * 1024 ** 3)
    with patch("services.process_manager.FORGE_LAUNCHER_PATH", fake_launcher), \
         patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)), \
         patch("psutil.virtual_memory", return_value=fake_vm), \
         patch("services.asset_validator.AssetValidator.log_validation_summary"), \
         patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
         patch("builtins.open", MagicMock()):

        pm._run_forge_start()

        assert mock_popen.called
        forge_calls = [
            c for c in mock_popen.call_args_list
            if c.args and isinstance(c.args[0], list) and any("forge_launcher.bat" in str(x) for x in c.args[0])
        ]
        assert forge_calls, f"Вызов forge_launcher.bat не найден среди вызовов Popen: {mock_popen.call_args_list}"
        cmd = forge_calls[0].args[0]
        assert "--always-offload-from-vram" not in cmd
        assert "--pin-shared-memory" in cmd
        assert ("--vae-in-bf16" in cmd) or ("--vae-in-fp32" in cmd)
        assert "--upcast-sampling" not in cmd
        assert "--api" in cmd
        assert "--port" in cmd
        assert "7860" in cmd


def test_pin_shared_memory_ram_guard():
    """На <= 16.5 ГБ RAM с активной LLM или нехваткой свободной ОЗУ --pin-shared-memory отключается во избежание Host RAM OOM."""
    # 1. При активной LLM на 16 ГБ RAM (15.7 ГБ) - безусловное отключение pin-shared-memory
    ok_llm_active, reason_llm = should_enable_pin_shared_memory(
        llm_active=True, sdxl_fp8_enabled=True, total_ram_gb=15.7, avail_ram_mb=8500
    )
    assert ok_llm_active is False
    assert "активной LLM" in reason_llm

    # 2. При неактивной LLM, но свободная RAM < 9000 MB - также отключение
    ok_low_no_llm, reason_low = should_enable_pin_shared_memory(
        llm_active=False, sdxl_fp8_enabled=True, total_ram_gb=15.7, avail_ram_mb=7500
    )
    assert ok_low_no_llm is False
    assert "< 9000MB" in reason_low

    # 3. При неактивной LLM и достаточной RAM >= 9000 MB на 16 ГБ - включение разрешено
    ok_avail_no_llm, _ = should_enable_pin_shared_memory(
        llm_active=False, sdxl_fp8_enabled=True, total_ram_gb=16.0, avail_ram_mb=9500
    )
    assert ok_avail_no_llm is True

    # 4. На системе 32 ГБ RAM при свободных 18 ГБ - включение разрешено даже с активной LLM
    ok_high, _ = should_enable_pin_shared_memory(
        llm_active=True, sdxl_fp8_enabled=True, total_ram_gb=32.0, avail_ram_mb=18000
    )
    assert ok_high is True

    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(4.0, 700)):
        flags_4gb, meta_4gb = resolve_forge_startup_flags(total_ram_gb=16.0, avail_ram_mb=4500)
        assert "--always-offload-from-vram" in flags_4gb
        assert "--pin-shared-memory" not in flags_4gb
        assert meta_4gb["pin_shared_memory"] is False


def test_resolve_forge_startup_flags_vae_precision_by_compute_cap():
    """Проверка динамического выбора VAE флагов в зависимости от Compute Capability GPU."""
    # 1. Ada Lovelace (8, 9) -> --vae-in-bf16 без легаси --no-half-vae и --vae-in-fp32
    hw_ada = {"vendor": "nvidia", "compute_cap": (8, 9), "vram_total_mb": 12288}
    with patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=hw_ada), \
         patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(12.0, 700)):
        flags, meta = resolve_forge_startup_flags(total_ram_gb=32.0, avail_ram_mb=20000)
        assert "--vae-in-bf16" in flags
        assert "--no-half-vae" not in flags
        assert "--vae-in-fp32" not in flags
        assert meta["vae_precision"] == "bf16"

    # 2. Blackwell (12, 0) -> --vae-in-bf16
    hw_blackwell = {"vendor": "nvidia", "compute_cap": (12, 0), "vram_total_mb": 8192}
    with patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=hw_blackwell), \
         patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):
        flags, meta = resolve_forge_startup_flags(total_ram_gb=32.0, avail_ram_mb=20000)
        assert "--vae-in-bf16" in flags
        assert "--no-half-vae" not in flags
        assert "--vae-in-fp32" not in flags
        assert meta["vae_precision"] == "bf16"

    # 3. Turing (7, 5) -> откат на безопасную пару [--no-half-vae, --vae-in-fp32]
    hw_turing = {"vendor": "nvidia", "compute_cap": (7, 5), "vram_total_mb": 8192}
    with patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=hw_turing), \
         patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):
        flags, meta = resolve_forge_startup_flags(total_ram_gb=32.0, avail_ram_mb=20000)
        assert "--vae-in-bf16" not in flags
        assert "--no-half-vae" in flags
        assert "--vae-in-fp32" in flags
        assert meta["vae_precision"] == "fp32"

    # 4. Не-NVIDIA / CPU -> безопасная пара [--no-half-vae, --vae-in-fp32]
    hw_cpu = {"vendor": "cpu", "compute_cap": "N/A", "vram_total_mb": 0}
    with patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=hw_cpu), \
         patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(0.0, 0)):
        flags, meta = resolve_forge_startup_flags(total_ram_gb=32.0, avail_ram_mb=20000)
        assert "--vae-in-bf16" not in flags
        assert "--no-half-vae" in flags
        assert "--vae-in-fp32" in flags
        assert meta["vae_precision"] == "fp32"


def test_resolve_forge_offload_policy_calibrated_8gb():
    """Проверка калиброванных режимов FAST_KEEP_VRAM / SMART_SWAP / STAGE_OFFLOAD на 8 ГБ VRAM."""
    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):
        # 1. SD 1.5 + Hires + ADetailer (Face+Hands) -> FAST_KEEP_VRAM (без лишних выгрузок)
        pol_sd15 = resolve_forge_offload_policy(
            width=512, height=768, architecture="sd15", is_fp8=False,
            enable_hr=True, hr_scale=1.5, enable_adetailer=True, enable_hand_adetailer=True,
        )
        assert pol_sd15["mode"] == "FAST_KEEP_VRAM"
        assert pol_sd15["always_offload"] is False
        assert 3000 <= pol_sd15["est_peak_mb"] <= 5000

        # 2. SDXL FP8 + ADetailer (без Hires, 1024x1024) -> FAST_KEEP_VRAM + VAETile + inf_mem=512
        pol_xl_fp8 = resolve_forge_offload_policy(
            width=1024, height=1024, architecture="sdxl", is_fp8=True,
            enable_hr=False, enable_adetailer=True, enable_hand_adetailer=False,
        )
        assert pol_xl_fp8["mode"] == "FAST_KEEP_VRAM"
        assert pol_xl_fp8["always_offload"] is False
        assert pol_xl_fp8["enable_vae_tiling"] is True
        assert pol_xl_fp8["inference_memory_mb"] == 512

        # 3. SDXL FP8 + Hires + ADetailer (Face+Hands) -> FAST_KEEP_VRAM / SMART_SWAP
        pol_xl_fp8_heavy = resolve_forge_offload_policy(
            width=896, height=1152, architecture="sdxl", is_fp8=True,
            enable_hr=True, hr_scale=1.25, enable_adetailer=True, enable_hand_adetailer=True,
        )
        assert pol_xl_fp8_heavy["mode"] in ("FAST_KEEP_VRAM", "SMART_SWAP")
        assert pol_xl_fp8_heavy["always_offload"] is False
        assert pol_xl_fp8_heavy["can_run"] is True

        # 4. SDXL FP16 без Hires и без ADetailer -> помещается без STAGE_OFFLOAD (SMART_SWAP на dGPU)
        pol_xl_fp16_clean = resolve_forge_offload_policy(
            width=896, height=1152, architecture="sdxl", is_fp8=False,
            enable_hr=False, enable_adetailer=False,
        )
        assert pol_xl_fp16_clean["mode"] in ("FAST_KEEP_VRAM", "SMART_SWAP")
        assert pol_xl_fp16_clean["always_offload"] is False

        # 5. SDXL FP16 с ADetailer или Hires -> сразу STAGE_OFFLOAD (без тормозов PCIe-свопа на каждом шаге)
        pol_xl_fp16_ad = resolve_forge_offload_policy(
            width=896, height=1152, architecture="sdxl", is_fp8=False,
            enable_hr=False, enable_adetailer=True,
        )
        assert pol_xl_fp16_ad["mode"] == "STAGE_OFFLOAD"
        assert pol_xl_fp16_ad["always_offload"] is True

        # 6. Защита от ложного заниженного free_vram_mb при forge_holds_vram=True
        pol_held = resolve_forge_offload_policy(
            width=512, height=768, architecture="sd15", is_fp8=False,
            enable_hr=True, hr_scale=1.5, enable_adetailer=True,
            free_vram_mb=2900, forge_holds_vram=True,
        )
        assert pol_held["mode"] == "FAST_KEEP_VRAM"
        assert pol_held["always_offload"] is False

        log_line = format_vram_planner_log(pol_sd15)
        assert "[VRAM-Planner]" in log_line
        assert "Mode=FAST_KEEP_VRAM" in log_line
        assert "AD=Face+Hands" in log_line


def test_hardware_vram_budget_ttl_cache():
    """resolve_hardware_vram_budget кэширует топологию LayerBudgeter на одном кадре (без тройного вызова)."""
    import services.art_director.vram_policy as vp
    from services.layer_budgeter import LayerBudgeter

    vp._hardware_budget_cache = None
    call_count = 0
    orig_detect = LayerBudgeter.detect_gpu_topology

    def _counting_detect():
        nonlocal call_count
        call_count += 1
        return (True, 150)

    LayerBudgeter.detect_gpu_topology = staticmethod(_counting_detect)
    try:
        b1 = vp.resolve_hardware_vram_budget()
        b2 = vp.resolve_hardware_vram_budget()
        b3 = vp.resolve_hardware_vram_budget()
        assert b1 == b2 == b3
        assert b1[1] == 150
        assert call_count == 1
    finally:
        LayerBudgeter.detect_gpu_topology = orig_detect
        vp._hardware_budget_cache = None


def test_resolve_safe_hires_scale_sdxl_8gb():
    """На 8 ГБ VRAM SDXL FP8 запрос 1.50x (1344x1728) пропускается без ложного урезания до 1.35x/1.25x."""
    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):
        res = resolve_safe_hires_scale(
            width=896,
            height=1152,
            requested_hr_scale=1.50,
            architecture="sdxl",
            is_fp8=True,
            enable_adetailer=True,
            enable_hand_adetailer=True,
            vae_tiling=True,
            hr_upscaler="Latent",
            ad_inpaint_size=512,
        )
        assert res["allowed"] is True
        assert res["effective_scale"] == 1.50
        assert res["hr_width"] == 1344
        assert res["hr_height"] == 1728
        assert res["hr_width"] % 64 == 0
        assert res["hr_height"] % 64 == 0
        assert res["policy"]["can_run"] is True
        assert res["policy"]["est_peak_mb"] <= 8192


def test_resolve_safe_hires_scale_sd15_8gb_full_scale():
    """Для SD 1.5 (512x768, AD=Face+Hands, R-ESRGAN 4x+, 8 ГБ VRAM) запросы 1.5x и 2.0x не срезаются."""
    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):
        # 1. Запрос 1.5x проходит без снижения
        res_15 = resolve_safe_hires_scale(
            width=512,
            height=768,
            requested_hr_scale=1.50,
            architecture="sd15",
            is_fp8=False,
            enable_adetailer=True,
            enable_hand_adetailer=True,
            hr_upscaler="R-ESRGAN 4x+",
            vae_tiling=True,
            ad_inpaint_size=512,
        )
        assert res_15["allowed"] is True
        assert res_15["effective_scale"] == 1.50
        assert res_15["hr_width"] == 768
        assert res_15["hr_height"] == 1152
        assert res_15["policy"]["mode"] == "FAST_KEEP_VRAM"

        # 2. Запрос 2.0x проходит без снижения
        res_20 = resolve_safe_hires_scale(
            width=512,
            height=768,
            requested_hr_scale=2.0,
            architecture="sd15",
            is_fp8=False,
            enable_adetailer=True,
            enable_hand_adetailer=True,
            hr_upscaler="R-ESRGAN 4x+",
            vae_tiling=True,
            ad_inpaint_size=512,
        )
        assert res_20["allowed"] is True
        assert res_20["effective_scale"] == 2.0
        assert res_20["hr_width"] == 1024
        assert res_20["hr_height"] == 1536
        assert res_20["policy"]["mode"] == "FAST_KEEP_VRAM"


def test_hires_safe_wide_formats_no_longer_blocked():
    """Широкоформатные соотношения 16:9 (1344x768) и 21:9 (1536x640) не отсекаются искусственно по max(w,h) > 1280."""
    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):
        # 1. 1344x768 (16:9)
        safe_16_9 = is_hires_safe(
            width=1344,
            height=768,
            architecture="sdxl",
            is_fp8=True,
            hr_scale=1.20,
            vae_tiling=True,
        )
        assert safe_16_9 is True

        res_16_9 = resolve_safe_hires_scale(
            width=1344,
            height=768,
            requested_hr_scale=1.50,
            architecture="sdxl",
            is_fp8=True,
            vae_tiling=True,
        )
        assert res_16_9["allowed"] is True
        assert res_16_9["hr_width"] % 64 == 0
        assert res_16_9["hr_height"] % 64 == 0

        # 2. 1536x640 (21:9 ultrawide)
        safe_21_9 = is_hires_safe(
            width=1536,
            height=640,
            architecture="sdxl",
            is_fp8=True,
            hr_scale=1.20,
            vae_tiling=True,
        )
        assert safe_21_9 is True

        res_21_9 = resolve_safe_hires_scale(
            width=1536,
            height=640,
            requested_hr_scale=1.50,
            architecture="sdxl",
            is_fp8=True,
            vae_tiling=True,
        )
        assert res_21_9["allowed"] is True
        assert res_21_9["hr_width"] % 64 == 0
        assert res_21_9["hr_height"] % 64 == 0


def test_sdxl_fp16_on_8gb_blocked():
    """SDXL FP16 (6.5 ГБ весов) на картах <= 8.5 ГБ блокирует Hires.fix для предотвращения STAGE_OFFLOAD / OOM."""
    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):
        max_area = calculate_max_hires_canvas_area("sdxl", is_fp8=False, total_vram_gb=8.0)
        assert max_area == 0

        res = resolve_safe_hires_scale(
            width=896,
            height=1152,
            requested_hr_scale=1.25,
            architecture="sdxl",
            is_fp8=False,
            vae_tiling=True,
        )
        assert res["allowed"] is False
        assert res["effective_scale"] == 1.0
        assert "SDXL FP16" in res["reason"]


def test_art_director_plan_adaptive_hires():
    """ArtDirector адаптирует hr_scale при формировании плана генерации на 8 ГБ VRAM."""
    from services.art_director import ArtDirector
    from services.art_director.contracts import VisualIntent

    director = ArtDirector()
    fake_intent = VisualIntent(
        prompt="1girl, solo, looking at viewer",
        raw_user_query="girl portrait",
        aspect_ratio="tall",
        mode="chat",
    )

    with patch("services.art_director.director.resolve_hardware_vram_budget", return_value=(8.0, 700)), \
         patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)), \
         patch("utils.config_manager.ConfigManager.load_config", return_value={"forge_sdxl_fp8_enabled": True}):

        mock_profile = {
            "id": "test_sdxl",
            "name": "Test SDXL",
            "checkpoint": "ponyRealism_v21Main.safetensors",
            "architecture": "sdxl",
            "pipeline": {
                "enable_hr": True,
                "hr_scale": 1.5,
                "steps": 25,
            }
        }
        router_mock = patch.object(director.router, "get_profile", return_value=mock_profile) if hasattr(director, "router") and director.router else None
        if router_mock:
            with router_mock:
                plan = director.plan_from_visual_intent(fake_intent, preferred_style="test_sdxl")
        else:
            plan = director.plan_from_visual_intent(fake_intent, preferred_style="test_sdxl")

        assert plan.enable_hr is True
        assert plan.hr_scale == 1.5
        assert (plan.width * plan.hr_scale) > plan.width


def test_resolve_forge_startup_flags_includes_no_half_vae():
    """Флаги запуска Forge содержат безопасный флаг VAE (--vae-in-bf16 для compute_cap >= 8.0 или --no-half-vae)."""
    flags, meta = resolve_forge_startup_flags()
    assert ("--vae-in-bf16" in flags) or ("--no-half-vae" in flags)


def test_sd15_hires_no_forced_vae_tiling_below_threshold():
    """Для SD 1.5 на 8 ГБ VRAM VAE Tiling не включается принудительно для портретов <= 1.3M px (например, 704x1088)."""
    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):
        # 1. SD 1.5: 512x768 с Hires 1.38x (площадь ~750k px <= 1.3M) -> VAE Tiling выключен
        pol_small = resolve_forge_offload_policy(
            width=512,
            height=768,
            architecture="sd15",
            is_fp8=False,
            enable_hr=True,
            hr_scale=1.38,
            vae_tiling=False,
        )
        assert pol_small["enable_vae_tiling"] is False

        # 2. SD 1.5: Крупный Hires (> 1.3M px, например 768x1152 с Hires 1.35x = 1.6M px) -> VAE Tiling принудительно включен
        pol_large = resolve_forge_offload_policy(
            width=768,
            height=1152,
            architecture="sd15",
            is_fp8=False,
            enable_hr=True,
            hr_scale=1.35,
            vae_tiling=False,
        )
        assert pol_large["enable_vae_tiling"] is True

        # 3. SDXL: на >= 1024px VAE Tiling включен
        pol_xl = resolve_forge_offload_policy(
            width=896,
            height=1152,
            architecture="sdxl",
            is_fp8=True,
            enable_hr=True,
            hr_scale=1.20,
            vae_tiling=False,
        )
        assert pol_xl["enable_vae_tiling"] is True


def test_resolve_hires_second_pass_steps():
    """Тестирование адаптивной формулы расчёта hr_second_pass_steps."""
    # 1. Быстрые модели (base_steps <= 10): 6 шагов -> 6
    assert resolve_hires_second_pass_steps(base_steps=6, configured_steps=None) == 6
    assert resolve_hires_second_pass_steps(base_steps=8, configured_steps=None) == 8
    assert resolve_hires_second_pass_steps(base_steps=10, configured_steps=None) == 10
    # Нижний порог для быстрых моделей = 4
    assert resolve_hires_second_pass_steps(base_steps=2, configured_steps=None) == 4

    # 2. Стандартные модели (base_steps > 10):
    # 25 шагов, denoise=0.35 -> 15 (round(25 * 0.60) = 15, min_for_denoise = ceil(3/0.35) = 9 -> max(15, 9) = 15)
    assert resolve_hires_second_pass_steps(base_steps=25, denoising_strength=0.35, configured_steps=None) == 15
    # 20 шагов -> 12
    assert resolve_hires_second_pass_steps(base_steps=20, denoising_strength=0.35, configured_steps=None) == 12
    # 30 шагов -> 18
    assert resolve_hires_second_pass_steps(base_steps=30, denoising_strength=0.35, configured_steps=None) == 18
    # 40 шагов -> 20 (ограничение base_hr <= 20)
    assert resolve_hires_second_pass_steps(base_steps=40, denoising_strength=0.35, configured_steps=None) == 20
    # Малый denoise=0.15 требует больше шагов: min_for_denoise = ceil(3/0.15) = 20
    assert resolve_hires_second_pass_steps(base_steps=15, denoising_strength=0.15, configured_steps=None) == 20

    # 3. Ручной оверрайд (configured_steps > 0):
    assert resolve_hires_second_pass_steps(base_steps=6, configured_steps=12) == 12
    assert resolve_hires_second_pass_steps(base_steps=25, configured_steps=18) == 18

    # 4. Некорректный или нулевой configured_steps -> рассчитывается адаптивно
    assert resolve_hires_second_pass_steps(base_steps=25, denoising_strength=0.35, configured_steps=0) == 15
    assert resolve_hires_second_pass_steps(base_steps=25, denoising_strength=0.35, configured_steps=-1) == 15


def test_art_director_plan_adaptive_hires_second_pass_steps():
    """ArtDirector применяет resolve_hires_second_pass_steps при формировании плана."""
    from services.art_director import ArtDirector
    from services.art_director.contracts import VisualIntent

    director = ArtDirector()
    fake_intent = VisualIntent(
        prompt="1girl, solo, looking at viewer",
        raw_user_query="girl portrait",
        aspect_ratio="portrait",
        mode="chat",
    )

    with patch("services.art_director.director.resolve_hardware_vram_budget", return_value=(8.0, 700)), \
         patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):

        # Модель с 6 базовыми шагами без configured hr_second_pass_steps
        fast_profile = {
            "id": "fast_sd15",
            "name": "Fast SD15",
            "checkpoint": "fast_model.safetensors",
            "architecture": "sd15",
            "pipeline": {
                "enable_hr": True,
                "hr_scale": 1.5,
                "steps": 6,
                "hires_denoise": 0.35,
            }
        }
        with patch.object(director.router, "get_profile", return_value=fast_profile):
            plan = director.plan_from_visual_intent(fake_intent, preferred_style="fast_sd15")
            assert plan.enable_hr is True
            assert plan.hr_second_pass_steps == 6

        # Модель с явным configured hr_second_pass_steps=14
        custom_profile = {
            "id": "custom_sd15",
            "name": "Custom SD15",
            "checkpoint": "custom_model.safetensors",
            "architecture": "sd15",
            "pipeline": {
                "enable_hr": True,
                "hr_scale": 1.5,
                "steps": 6,
                "hr_second_pass_steps": 14,
            }
        }
        with patch.object(director.router, "get_profile", return_value=custom_profile):
            plan2 = director.plan_from_visual_intent(fake_intent, preferred_style="custom_sd15")
            assert plan2.enable_hr is True
            assert plan2.hr_second_pass_steps == 14


def test_resolve_forge_startup_flags_vae_fp32_and_no_duplicates():
    """Флаги запуска на архитектуре < 8.0 (Turing 7.5) включают --vae-in-fp32, --no-half-vae, исключают --upcast-sampling и не дублируются."""
    hw_turing = {"vendor": "nvidia", "compute_cap": (7, 5), "vram_total_mb": 8192}
    with patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=hw_turing):
        flags, meta = resolve_forge_startup_flags(total_ram_gb=32.0, avail_ram_mb=18000)
        assert "--vae-in-fp32" in flags
        assert "--no-half-vae" in flags
        assert "--upcast-sampling" not in flags
        assert len(flags) == len(set(flags))


def test_resolve_forge_offload_policy_applies_fp32_overhead_and_early_tiling():
    """
    resolve_forge_offload_policy корректно учитывает VAE_FP32_OVERHEAD_MB = 260
    и ранний VAE-tiling (tiling_factor = 0.65) при Hires.fix на SDXL canvas >= 1024.
    """
    assert VAE_FP32_OVERHEAD_MB == 260

    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):
        # SDXL FP8 + Hires: canvas 896x1152 -> hr 1254x1613 (max >= 1024)
        pol = resolve_forge_offload_policy(
            width=896,
            height=1152,
            architecture="sdxl",
            is_fp8=True,
            enable_hr=True,
            hr_scale=1.4,
            enable_adetailer=False,
            enable_hand_adetailer=False,
        )
        assert pol["enable_vae_tiling"] is True
        # С ранним тайлингом (0.65) и оверхедом 260 МБ пик равен ~6042 МБ (без тайлинга был бы ~6817 МБ)
        # и надежно укладывается в safe_budget_mb (FAST_KEEP_VRAM)
        assert pol["mode"] == "FAST_KEEP_VRAM"
        assert pol["always_offload"] is False
        assert 5900 <= pol["est_peak_mb"] <= 6200

        # Без Hires оверхед VAE_FP32_OVERHEAD_MB не прибавляется
        pol_no_hr = resolve_forge_offload_policy(
            width=896,
            height=1152,
            architecture="sdxl",
            is_fp8=True,
            enable_hr=False,
            enable_adetailer=False,
        )
        assert pol_no_hr["est_peak_mb"] < pol["est_peak_mb"]


def test_art_director_logs_denoise_na_when_hr_disabled(caplog):
    """ArtDirector выводит Denoise=N/A в лог [PARAMS], если enable_hr=False."""
    import logging
    from services.art_director import ArtDirector
    from services.art_director.contracts import VisualIntent

    director = ArtDirector()
    fake_intent = VisualIntent(
        prompt="beautiful cybernetic city at night",
        raw_user_query="cyberpunk city",
        aspect_ratio="landscape",
        mode="chat",
    )

    profile_no_hr = {
        "id": "test_no_hr_model",
        "name": "Test No HR Model",
        "checkpoint": "test_model.safetensors",
        "architecture": "sdxl",
        "pipeline": {
            "enable_hr": False,
            "hires_denoise": 0.45,
            "steps": 25,
            "cfg_scale": 5.0,
        },
    }

    with patch.object(director.router, "get_profile", return_value=profile_no_hr), \
         patch("services.art_director.director.resolve_hardware_vram_budget", return_value=(8.0, 700)), \
         patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)), \
         caplog.at_level(logging.INFO):

        director.plan_from_visual_intent(fake_intent, preferred_style="test_no_hr_model")
        params_logs = [record.message for record in caplog.records if "[PARAMS]" in record.message]
        assert len(params_logs) >= 1
        assert "Denoise=N/A" in params_logs[-1]


def test_adetailer_steps_contract_for_light_models():
    """Для моделей с малым числом шагов (steps=6) ADetailer получает ad_use_steps=True и ad_steps >= 14."""
    from services.art_director.plan import GenerationPlan

    plan = GenerationPlan(
        prompt="beautiful young woman on sunny beach",
        negative_prompt="ugly, deformed, bad hands",
        steps=6,
        enable_adetailer=True,
        enable_hand_adetailer=True,
        adetailer_face_denoise=0.30,
        adetailer_hands_denoise=0.36,
        adetailer_hands_enabled=True,
    )
    kwargs = plan.to_forge_kwargs()
    alwayson = kwargs.get("alwayson_scripts", {})
    assert "ADetailer" in alwayson
    ad_args = alwayson["ADetailer"]["args"]
    assert ad_args[0] is True  # master enable
    assert ad_args[1] is False  # skip img2img

    face_slot = ad_args[2]
    hand_slot = ad_args[3]

    assert face_slot["ad_use_steps"] is True
    assert face_slot["ad_steps"] >= 14
    assert face_slot["ad_prompt"] == "beautiful young woman on sunny beach"
    assert face_slot["ad_negative_prompt"] == "ugly, deformed, bad hands"

    assert hand_slot["ad_use_steps"] is True
    assert hand_slot["ad_steps"] >= 14
    assert hand_slot["ad_prompt"] == "beautiful young woman on sunny beach"
    assert hand_slot["ad_negative_prompt"] == "ugly, deformed, bad hands"


def test_est_peak_consistency_between_director_and_forge_client():
    """Значение Est.Peak совпадает в ArtDirector и ForgeClient без дельты в 220 МБ."""
    from services.art_director import ArtDirector
    from services.art_director.plan import GenerationPlan
    from services.forge_client import ForgeClient

    director = ArtDirector()
    client = ForgeClient()
    plan = GenerationPlan(
        target_checkpoint="Juggernaut_XI_Light_byRunDiffusion_fp8e4.safetensors",
        width=896,
        height=1152,
        steps=6,
        enable_hr=True,
        hr_scale=1.5,
        hr_upscaler="R-ESRGAN 4x+",
        enable_adetailer=True,
        enable_hand_adetailer=False,
    )

    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)), \
         patch.object(client, "get_upscalers", return_value=["R-ESRGAN 4x+", "Latent"]):

        # 1. ArtDirector passport logic
        safe_upscaler_d = str(getattr(plan, "hr_upscaler", None) or getattr(plan, "hires_upscaler", None) or "R-ESRGAN 4x+")
        pol_director = resolve_forge_offload_policy(
            width=plan.width,
            height=plan.height,
            architecture="sdxl",
            is_fp8=True,
            enable_adetailer=plan.enable_adetailer,
            enable_hr=plan.enable_hr,
            hr_scale=plan.hr_scale,
            hr_upscaler=safe_upscaler_d,
            enable_hand_adetailer=bool(getattr(plan, "adetailer_hands_enabled", False)),
        )

        # 2. ForgeClient txt2img logic
        safe_upscaler_c, _ = client.resolve_safe_hires_settings(
            requested_upscaler=plan.hr_upscaler,
            requested_denoising=plan.denoising_strength,
        )
        pol_forge = resolve_forge_offload_policy(
            width=plan.width,
            height=plan.height,
            architecture="sdxl",
            is_fp8=True,
            enable_adetailer=plan.enable_adetailer,
            enable_hr=plan.enable_hr,
            hr_scale=plan.hr_scale,
            hr_upscaler=str(safe_upscaler_c or "Latent"),
            enable_hand_adetailer=bool(getattr(plan, "adetailer_hands_enabled", False)),
        )

        assert pol_director["est_peak_mb"] == pol_forge["est_peak_mb"]
        assert pol_director["mode"] == pol_forge["mode"]


def test_fast_model_civitai_heuristic():
    """Эвристика CivitaiService автоматически настраивает denoise ADetailer для быстрых моделей."""
    from services.civitai_service import _extract_meta_into_params

    # Тест 1: имя файла с 'light'
    params_light = _extract_meta_into_params(
        ver_data={"images": [{"meta": {"steps": 6, "sampler": "DPM++ 2M Karras"}}]},
        model_tags=["photoreal"],
        model_title="Juggernaut XI",
        checkpoint_name="Juggernaut_XI_Light.safetensors",
    )
    assert params_light["adetailer_face_denoise"] == 0.30
    assert params_light["adetailer_hands_denoise"] == 0.36

    # Тест 2: имя файла без маркера, но steps <= 10
    params_steps = _extract_meta_into_params(
        ver_data={"images": [{"meta": {"steps": 8, "sampler": "LCM"}}]},
        model_tags=[],
        model_title="Fast Model",
        checkpoint_name="model.safetensors",
    )
    assert params_steps["adetailer_face_denoise"] == 0.30
    assert params_steps["adetailer_hands_denoise"] == 0.36

    # Тест 3: стандартная модель (25 шагов) не получает авто-оверрайд
    params_std = _extract_meta_into_params(
        ver_data={"images": [{"meta": {"steps": 25, "sampler": "DPM++ 2M Karras"}}]},
        model_tags=[],
        model_title="Classic SDXL",
        checkpoint_name="classic.safetensors",
    )
    assert "adetailer_face_denoise" not in params_std

