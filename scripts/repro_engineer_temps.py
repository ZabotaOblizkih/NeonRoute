import inspect
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from services.subagent_engineer import SubagentEngineer
from services.sandbox_manager import SandboxManager
from services.session_manager import SessionManager


class TempSpyClient:
    def __init__(self):
        self.calls = []  # (phase, temperature)

    def chat_completion(self, messages, **kwargs):
        temp = kwargs.get("temperature")
        sys_txt = (messages[0].get("content") or "") if messages else ""
        if "системный архитектор" in sys_txt.lower():
            self.calls.append(("architect", temp))
            return """{
  "task_type": "NEW_MODULE",
  "target_file": "hello.py",
  "target_artifact": "hello.py",
  "format": "cli_automation",
  "intent": "new",
  "interface_spec": {"classes": [], "functions": [{"name": "main", "args": [], "return_type": "None"}]},
  "plan": "Тестовый вывод"
}"""
        if "ведущий инженер" in sys_txt.lower() or kwargs.get("stream"):
            self.calls.append(("worker", temp))
            return """<<TOOL:write_file filename="hello.py">
\"\"\"
CLI Hello module with full implementation.
Contains greeting logic, error handling and execution entry point.
\"\"\"
import sys


def print_greeting(target: str = "World") -> str:
    \"\"\"Prints a greeting message to stdout with full formatting.\"\"\"
    msg = f"Hello, {target}! Everything is configured and working perfectly OK."
    print(msg)
    return msg


def main():
    print_greeting("NeonRoute")


if __name__ == '__main__':
    main()
<</TOOL:write_file>>
<<TOOL:run_script filename="hello.py">>"""
        self.calls.append(("other", temp))
        return "Готово."


def run_probe(user_temp: float):
    spy = TempSpyClient()
    orig_get_ws = SandboxManager.get_workspace_dir
    orig_get_sid = SandboxManager.get_active_session_id
    orig_load_sess = SessionManager.load_session
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        SandboxManager.get_workspace_dir = classmethod(lambda cls, _p=tmp_ws: _p)
        SandboxManager.get_active_session_id = classmethod(lambda cls: "spy-sess")
        SessionManager.load_session = staticmethod(lambda sid: {"sampling_params": {"temperature": user_temp}, "metadata": {"active_engineer_skill": "cli_automation"}})
        try:
            eng = SubagentEngineer()
            fn = getattr(eng, "execute_worker", getattr(eng, "run_worker", getattr(eng, "run", None)))
            sig = inspect.signature(fn)
            known = {
                "task_prompt": "Напиши hello.py с выводом OK",
                "lm_client": spy,
                "client": spy,
                "target_model": "fake-coder",
                "model_id": "fake-coder",
                "session_id": "spy-sess",
                "ws_dir": tmp_ws,
            }
            kwargs = {k: v for k, v in known.items() if k in sig.parameters}
            fn(**kwargs)
        finally:
            SandboxManager.get_workspace_dir = orig_get_ws
            SandboxManager.get_active_session_id = orig_get_sid
            SessionManager.load_session = orig_load_sess
    return spy.calls


def main():
    print("=" * 80)
    print("T3-7 BASELINE STAND: Architect Temp (User) vs Worker Temp (0.20)")
    print("=" * 80)
    bugs = 0

    calls_85 = run_probe(0.85)
    arch_temps = [t for p, t in calls_85 if p == "architect"]
    work_temps = [t for p, t in calls_85 if p == "worker"]
    print(f"\n[Case 1: user_temp=0.85] calls={calls_85}")
    print(f"  architect={arch_temps} (ожидали [0.85]), worker={work_temps} (ожидали [0.2])")
    if not arch_temps or arch_temps[0] != 0.85 or not work_temps or work_temps[0] > 0.20:
        print(">>> [REPRODUCED BUG-T7-1] Работяга использует высокую пользовательскую температуру (0.85) вместо <= 0.20!")
        bugs += 1
    else:
        print(">>> [PASS T7-1] Архитектор=0.85, Работяга=0.20.")

    calls_15 = run_probe(0.15)
    arch_15 = [t for p, t in calls_15 if p == "architect"]
    work_15 = [t for p, t in calls_15 if p == "worker"]
    print(f"\n[Case 2: user_temp=0.15] calls={calls_15}")
    print(f"  architect={arch_15} (ожидали [0.15]), worker={work_15} (ожидали [0.15], не завышать до 0.20)")
    if not work_15 or work_15[0] > 0.15:
        print(">>> [REPRODUCED BUG-T7-2] Работяга завысил ручную температуру 0.15 до 0.20!")
        bugs += 1
    else:
        print(">>> [PASS T7-2] При user_temp=0.15 сохраняется 0.15.")

    print("\n" + "=" * 80)
    print(f"ИТОГ СТЕНДА ТЗ №7: провалов {bugs}/2")
    print("=" * 80)


if __name__ == "__main__":
    main()
