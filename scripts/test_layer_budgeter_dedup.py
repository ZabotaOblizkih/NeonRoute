from pathlib import Path

pm_path = Path("services/process_manager.py")
text = pm_path.read_text(encoding="utf-8")

duplicate_block = """                    ngl, can_run, reason, audit_data = LayerBudgeter.calculate_layer_offload(
                        model_path=model_path,
                        target_context=effective_ctx,
                        current_running_ngl=current_running_ngl,
                        cache_type=kv_cache_type
                    )
                    LayerBudgeter.log_audit_report(audit_data)"""

assert duplicate_block in text, "Блок с дублирующим вызовом log_audit_report найден!"
print("[PASS] Подтверждено: в services/process_manager.py присутствует дублирующий вызов LayerBudgeter.log_audit_report(audit_data).")
