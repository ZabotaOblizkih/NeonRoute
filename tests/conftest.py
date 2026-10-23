import pytest
from services.sandbox_manager import SandboxManager

@pytest.fixture(autouse=True)
def check_sandbox_state_leak(request):
    # До теста: принудительно очищаем состояние
    SandboxManager.set_active_session_id(None)
    
    yield  # Выполняется сам тест
    
    # После теста: проверяем, убрал ли тест за собой
    active_sid = SandboxManager.get_active_session_id()
    if active_sid is not None:
        # Сбрасываем, чтобы не ломать следующие тесты, но валим текущий
        SandboxManager.set_active_session_id(None)
        pytest.fail(
            f"УТЕЧКА СОСТОЯНИЯ: Тест '{request.node.name}' оставил незакрытую сессию '{active_sid}'!"
        )
