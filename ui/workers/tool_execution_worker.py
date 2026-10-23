import logging
from typing import List, Dict, Optional, Any
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger("NeonRoute.ToolExecutionWorker")


class ToolExecutionWorker(QThread):
    """
    Background worker thread for executing Mini-Antigravity Tool Engine tasks asynchronously,
    preventing any GUI freezes during disk I/O, script execution, or file search.
    Supports executing a sequence of tool calls (e.g. write_file followed by run_script).
    """
    finished = pyqtSignal(object)

    def __init__(
        self,
        tool_name: str = "",
        params: Optional[dict] = None,
        content: str = "",
        target_widget: Any = None,
        tool_calls: Optional[List[Any]] = None,
        session_id: Optional[str] = None
    ):
        super().__init__()
        self.tool_calls = list(tool_calls) if tool_calls else []
        self.session_id = session_id
        if not self.tool_calls and tool_name:
            from services.tool_engine import ToolCall
            self.tool_calls.append(ToolCall(name=tool_name, params=params or {}, body=content))
        self.target_widget = target_widget

    def run(self):
        try:
            from services.tool_engine import ToolEngine
            from services.sandbox_manager import SandboxManager
            if self.session_id:
                SandboxManager.set_active_session_id(self.session_id)
                ToolEngine.set_active_session_id(self.session_id)

            results = []
            overall_success = True
            last_written_file = ""

            for call in self.tool_calls:
                call_name = call.name.lower()
                call_widget = getattr(call, "target_widget", self.target_widget)

                # Sequential prerequisite check: verify file existence and non-zero size before run_script
                if call_name == "run_script":
                    fn = (
                        call.params.get("filename")
                        or call.params.get("path")
                        or call.params.get("script")
                        or call.params.get("file")
                        or call.params.get("command")
                        or call.params.get("name")
                        or ""
                    )
                    if fn.lower().startswith("python "):
                        fn = fn[7:].strip()
                    elif fn.lower().startswith("python3 "):
                        fn = fn[8:].strip()
                    if not fn and last_written_file:
                        fn = last_written_file
                    call.params["filename"] = fn

                    clean_fn = SandboxManager.clean_workspace_filename(fn)
                    target_path = SandboxManager.get_workspace_dir() / clean_fn
                    if not target_path.exists():
                        err_msg = f"Ошибка: Скрипт не найден перед запуском: C:\\NeonRoute\\workspace\\{clean_fn}"
                        results.append({
                            "tool_name": call.name,
                            "result": err_msg,
                            "params": call.params,
                            "content": call.content,
                            "success": False,
                            "target_widget": call_widget
                        })
                        overall_success = False
                        continue
                    if target_path.stat().st_size == 0:
                        err_msg = f"Ошибка: Скрипт C:\\NeonRoute\\workspace\\{clean_fn} имеет нулевой размер (пустой файл)!"
                        results.append({
                            "tool_name": call.name,
                            "result": err_msg,
                            "params": call.params,
                            "content": call.content,
                            "success": False,
                            "target_widget": call_widget
                        })
                        overall_success = False
                        continue

                try:
                    res_str = ToolEngine.execute_tool(call.name, call.params, call.content)
                    is_err = (
                        res_str.startswith("Ошибка:")
                        or res_str.startswith("[Ошибка")
                        or res_str.startswith("PermissionError:")
                        or ("[Код возврата: " in res_str and "[Код возврата: 0]" not in res_str)
                    )

                    if call_name in ("write_file", "patch_file") and not is_err:
                        fn = (
                            call.params.get("filename")
                            or call.params.get("path")
                            or call.params.get("file")
                            or call.params.get("name")
                            or ""
                        )
                        clean_fn = SandboxManager.clean_workspace_filename(fn)
                        last_written_file = clean_fn

                    # Post-write verification: ensure file really exists on disk and is non-empty
                    if call_name == "write_file":
                        fn = (
                            call.params.get("filename")
                            or call.params.get("path")
                            or call.params.get("file")
                            or call.params.get("name")
                            or ""
                        )
                        clean_fn = SandboxManager.clean_workspace_filename(fn)
                        target_path = SandboxManager.get_workspace_dir() / clean_fn
                        if not target_path.exists() or (target_path.stat().st_size == 0 and len(call.content.strip()) > 0):
                            is_err = True
                            res_str = f"Ошибка: Файл C:\\NeonRoute\\workspace\\{clean_fn} не был сохранен или остался пустым!"
                        else:
                            last_written_file = clean_fn

                    if is_err:
                        overall_success = False

                    results.append({
                        "tool_name": call.name,
                        "result": res_str,
                        "params": call.params,
                        "content": call.content,
                        "success": not is_err,
                        "target_widget": call_widget
                    })
                except Exception as e:
                    logger.error(f"Error executing tool {call.name}: {e}")
                    results.append({
                        "tool_name": call.name,
                        "result": f"Ошибка выполнения инструмента {call.name}: {e}",
                        "params": call.params,
                        "content": call.content,
                        "success": False,
                        "target_widget": call_widget
                    })
                    overall_success = False

            first = results[0] if results else {}
            self.finished.emit({
                "results": results,
                "overall_success": overall_success,
                "target_widget": self.target_widget,
                "tool_name": first.get("tool_name", ""),
                "result": first.get("result", ""),
                "params": first.get("params", {}),
                "content": first.get("content", ""),
                "success": first.get("success", False)
            })
        except Exception as e:
            logger.error(f"Fatal error in ToolExecutionWorker.run: {e}", exc_info=True)
            self.finished.emit({
                "results": [{
                    "tool_name": "error",
                    "result": f"Критическая ошибка выполнения инструмента: {e}",
                    "params": {},
                    "content": "",
                    "success": False,
                    "target_widget": self.target_widget
                }],
                "overall_success": False,
                "target_widget": self.target_widget,
                "tool_name": "error",
                "result": f"Критическая ошибка: {e}",
                "params": {},
                "content": "",
                "success": False
            })
