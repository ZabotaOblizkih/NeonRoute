"""Models Manager package."""
from ui.models_manager.adetailer_dialog import ADetailerSettingsDialog
from ui.models_manager.widgets import (
    SamplingSlider,
    SamplingDoubleSpinBox,
    SamplingSpinBox,
    FluentEditableComboBox,
    FluentTextEdit,
    RoutingResultBadge,
    MODEL_PRESETS,
    normalize_checkpoint_stem,
    get_default_negative_prompt,
    create_active_checkbox_cell,
    create_delete_button_cell,
)
from ui.models_manager.workers import (
    RoutingTestWorker,
    ForgeScanWorker,
    CivitaiParamsWorker,
    FP8ConvertWorker,
    register_orphaned_worker,
    resolve_converter_script,
    verify_python,
    resolve_torch_python,
    resolve_checkpoint_path,
    _MODELS_MANAGER_ORPHANED_WORKERS,
)

from ui.models_manager.mixins import (
    ModelPipelineFormMixin,
    ForgeCivitaiSyncMixin,
    FP8ConversionMixin,
)

__all__ = [
    "ADetailerSettingsDialog",
    "ModelPipelineFormMixin",
    "ForgeCivitaiSyncMixin",
    "FP8ConversionMixin",
    "SamplingSlider",
    "SamplingDoubleSpinBox",
    "SamplingSpinBox",
    "FluentEditableComboBox",
    "FluentTextEdit",
    "RoutingResultBadge",
    "MODEL_PRESETS",
    "normalize_checkpoint_stem",
    "get_default_negative_prompt",
    "create_active_checkbox_cell",
    "create_delete_button_cell",
    "RoutingTestWorker",
    "ForgeScanWorker",
    "CivitaiParamsWorker",
    "FP8ConvertWorker",
    "register_orphaned_worker",
    "resolve_converter_script",
    "verify_python",
    "resolve_torch_python",
    "resolve_checkpoint_path",
    "_MODELS_MANAGER_ORPHANED_WORKERS",
]
