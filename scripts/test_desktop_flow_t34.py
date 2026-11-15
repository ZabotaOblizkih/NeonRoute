import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.memory_db import memory_db
from services.assistant_flow import assemble_assistant_context, run_assistant_inference_deferred

print("=== 1. Direct search_relevant_facts ===")
query = "Ты знаешь что-нибудь про Т-34?"
facts = memory_db.search_relevant_facts(query, top_k=5)
print(f"Query: '{query}'")
print(f"Facts found ({len(facts)}): {facts}")

print("\n=== 2. assemble_assistant_context ===")
dummy_controller = MagicMock()
dummy_controller.current_session = {
    "id": "test_session_123",
    "messages": [
        {"role": "user", "content": query, "is_user": True}
    ],
    "chat_history": [
        {"role": "user", "content": query}
    ]
}
dummy_controller.chat_history = [
    {"role": "user", "content": query}
]
dummy_controller._pending_knowledge_sources = None

assembled_msgs, matched_docs, kb_block = assemble_assistant_context(
    actual_prompt=query,
    session_id="test_session_123",
    curr_sess=dummy_controller.current_session,
    controller=dummy_controller,
    include_memory=True
)

sys_prompt = assembled_msgs[0]["content"]
print("\n--- System Prompt contains <PassiveMemory>? ---")
print("<PassiveMemory> in sys_prompt:", "<PassiveMemory>" in sys_prompt)
if "<PassiveMemory>" in sys_prompt:
    # Print the PassiveMemory block
    start = sys_prompt.find("<PassiveMemory>")
    end = sys_prompt.find("</PassiveMemory>") + len("</PassiveMemory>")
    print("PassiveMemory snippet:")
    print(sys_prompt[start:end])

print("\n--- controller._pending_knowledge_sources ---")
sources = dummy_controller._pending_knowledge_sources
print("Pending sources:", sources)
