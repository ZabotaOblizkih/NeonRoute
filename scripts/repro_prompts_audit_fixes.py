import sys
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.engineer_prompts import get_architect_prompt, get_engineer_system_prompt, get_rca_prompt
from services.engineer_skill_router import ENGINEER_SKILLS

def main():
    print("=" * 80)
    print("T3-10 BASELINE STAND: Prompts Hygiene, Contradictions & Archetype Rules")
    print("=" * 80)
    bugs = 0

    se_src = (ROOT / "services" / "subagent_engineer.py").read_text(encoding="utf-8", errors="replace")
    tdd_src = (ROOT / "services" / "engineer_tdd.py").read_text(encoding="utf-8", errors="replace")
    rev_src = (ROOT / "services" / "engineer_review.py").read_text(encoding="utf-8", errors="replace")

    # 1. Противоречие patch_file + no_patch_notice в diag_msg (subagent_engineer.py)
    has_patch_contradiction = (
        'либо через <<TOOL:patch_file' in se_src
        and 'no_patch_notice' in se_src
        and 'if not show_patch' not in se_src
        and 'if is_maintenance' not in se_src[se_src.find('else:', se_src.find('ИмяError') if False else se_src.find('[СИНТАКСИЧЕСКАЯ ОШИБКА')):se_src.find('no_patch_notice')]
    )
    # Проще: проверяем, что в ветке else перед no_patch_notice нет проверки на отсутствие файла на диске
    idx_else = se_src.find("[СИНТАКСИЧЕСКАЯ ОШИБКА В КОДЕ]")
    idx_npn = se_src.find("no_patch_notice =", idx_else) if idx_else != -1 else -1
    block_mid = se_src[idx_else:idx_npn] if (idx_else != -1 and idx_npn != -1) else ""
    contradict_patch = ("patch_file" in block_mid and "if " not in block_mid)
    print(f"\n[P0-1] Противоречивый совет patch_file + запрет patch_file: {contradict_patch}")
    if contradict_patch:
        bugs += 1

    # 2. Обрезка last_error[:600] с начала вместо хвоста в retry_msg
    cuts_head = "last_error[:600]" in se_src
    print(f"[P0-2] Обрезка last_error[:600] с начала (теряет строку Exception): {cuts_head}")
    if cuts_head:
        bugs += 1

    # 3. service_backend требует uvicorn.run() вместо TestClient
    sb_worker = ENGINEER_SKILLS["service_backend"]["worker_rules"]
    bad_uvicorn = "uvicorn.run()" in sb_worker and "TestClient" not in sb_worker
    print(f"[P0-3] service_backend толкает в uvicorn.run() вместо TestClient: {bad_uvicorn}")
    if bad_uvicorn:
        bugs += 1

    # 4. interactive_loop смешивает Tkinter/PyQt и не имеет ### секций + физики/контрастности Pygame
    il_worker = ENGINEER_SKILLS["interactive_loop"]["worker_rules"]
    il_arch = ENGINEER_SKILLS["interactive_loop"]["architect_rules"]
    bad_il = ("###" not in il_worker) or ("PyQt: root.mainloop" in il_worker or "Tkinter/PyQt: root.mainloop" in il_worker) or ("Test" == "" )
    has_physics = ("gravity" in il_worker.lower() or "гравитац" in il_worker.lower()) and ("контраст" in il_worker.lower())
    print(f"[P1-1] interactive_loop без ### / смешан Tkinter+PyQt / без физики+контраста: bad_mix={bad_il}, has_physics={has_physics}")
    if bad_il or not has_physics:
        bugs += 1

    # 5. TDD: запрет tmp_path и wildcard import *
    bad_tdd = ("ЗАПРЕЩЕНО указывать в аргументах функций внешние фикстуры" in tdd_src and "tmp_path" not in tdd_src) or ("import *" in tdd_src)
    print(f"[P1-2] TDD запрещает tmp_path или содержит 'import *': {bad_tdd}")
    if bad_tdd:
        bugs += 1

    # 6. Структура ### в базовых промптах + Review лимит 3500 + RCA max_tokens=400
    arch_p = get_architect_prompt()
    eng_p = get_engineer_system_prompt()
    rca_p = get_rca_prompt()
    missing_headings = ("###" not in arch_p) or ("###" not in eng_p) or ("###" not in rca_p)
    small_rev_or_rca = ("file_code[:3500]" in rev_src) or ("max_tokens=400" in se_src)
    print(f"[P1-3] Отсутствуют ### заголовки в core-промптах={missing_headings}, малый буфер Review/RCA={small_rev_or_rca}")
    if missing_headings or small_rev_or_rca:
        bugs += 1

    print("\n" + "=" * 80)
    print(f"ИТОГ СТЕНДА ТЗ №10: проблем воспроизведено {bugs}/6")
    print("=" * 80)

if __name__ == "__main__":
    main()
