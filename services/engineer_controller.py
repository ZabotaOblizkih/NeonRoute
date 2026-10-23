"""
Compatibility shim for EngineerControllerMixin.
Single Source of Truth is located in ui.controllers.engineer_controller.
"""
from ui.controllers.engineer_controller import EngineerControllerMixin, DOC_TASK_MARKERS

__all__ = ["EngineerControllerMixin", "DOC_TASK_MARKERS"]
