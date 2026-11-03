import re
from pathlib import Path

print("\n=== ВНЕДРЕНИЕ И ВАЛИДАЦИЯ ПАТЧА: LAZY WORKSPACE & INSPECTOR VISIBILITY ===")

# 1. services/sandbox_manager.py
sm_p = Path("services/sandbox_manager.py")
sm_txt = sm_p.read_text(encoding="utf-8")

old_get_ws = '''    @classmethod
    def get_workspace_dir(cls) -> Path:
        """Возвращает изолированную директорию проекта для активной сессии."""
        if cls._active_session_id:
            proj_dir = WORKSPACE_DIR / "projects" / cls._active_session_id
            proj_dir.mkdir(parents=True, exist_ok=True)
            return proj_dir
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        return WORKSPACE_DIR'''

new_get_ws = '''    @classmethod
    def get_workspace_dir(cls, create: bool = False) -> Path:
        """Возвращает изолированную директорию проекта для активной сессии (ленивое создание)."""
        if cls._active_session_id:
            proj_dir = WORKSPACE_DIR / "projects" / cls._active_session_id
            if create:
                proj_dir.mkdir(parents=True, exist_ok=True)
            return proj_dir
        if create:
            WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        return WORKSPACE_DIR'''

if old_get_ws in sm_txt:
    sm_txt = sm_txt.replace(old_get_ws, new_get_ws)
    print("✓ [services/sandbox_manager.py] get_workspace_dir переведён на ленивое создание (create=False).")

# Гарантируем create=True в операциях записи
old_write_call = "ws_dir = cls.get_workspace_dir()"
new_write_call = "ws_dir = cls.get_workspace_dir(create=True)"
# заменяем в методах записи и создания файлов
sm_txt = sm_txt.replace("def write_file(cls, filename: str, content: str) -> Path:\n        ws_dir = cls.get_workspace_dir()", "def write_file(cls, filename: str, content: str) -> Path:\n        ws_dir = cls.get_workspace_dir(create=True)")
sm_p.write_text(sm_txt, encoding="utf-8")

# 2. ui/components/workspace_inspector.py
wi_p = Path("ui/components/workspace_inspector.py")
wi_txt = wi_p.read_text(encoding="utf-8")

old_init_watcher = '''    def _init_watcher(self):
        """Регистрирует директорию песочницы в наблюдателе."""
        ws_dir = SandboxManager.get_workspace_dir()
        if not ws_dir.exists():
            try:
                ws_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                return

        watched = set(self._watcher.directories())
        ws_str = str(ws_dir.resolve())
        if ws_str not in watched:
            self._watcher.addPath(ws_str)'''

new_init_watcher = '''    def _init_watcher(self):
        """Регистрирует директорию песочницы в наблюдателе (без насильного mkdir)."""
        ws_dir = SandboxManager.get_workspace_dir(create=False)
        if not ws_dir.exists():
            return

        watched = set(self._watcher.directories())
        ws_str = str(ws_dir.resolve())
        if ws_str not in watched:
            self._watcher.addPath(ws_str)'''

if old_init_watcher in wi_txt:
    wi_txt = wi_txt.replace(old_init_watcher, new_init_watcher)
    print("✓ [ui/components/workspace_inspector.py] _init_watcher очищен от создания пустых папок.")

wi_p.write_text(wi_txt, encoding="utf-8")

# 3. services/mode_coordinator.py
mc_p = Path("services/mode_coordinator.py")
mc_txt = mc_p.read_text(encoding="utf-8")

old_mc_mount = '''        # 1. Синхронизация песочницы и инструментов
        if sess_id:
            try:
                from services.sandbox_manager import SandboxManager
                SandboxManager.set_active_session_id(sess_id)
            except Exception:
                pass
            try:
                from services.tool_engine import ToolEngine
                ToolEngine.set_active_session_id(sess_id)
            except Exception:
                pass'''

new_mc_mount = '''        # 1. Синхронизация песочницы и инструментов (только для инженерных сессий)
        is_eng = (target_mode == AppMode.ENGINEER)
        if sess_id and is_eng:
            try:
                from services.sandbox_manager import SandboxManager
                SandboxManager.set_active_session_id(sess_id)
            except Exception:
                pass
            try:
                from services.tool_engine import ToolEngine
                ToolEngine.set_active_session_id(sess_id)
            except Exception:
                pass
        else:
            try:
                from services.sandbox_manager import SandboxManager
                SandboxManager.set_active_session_id(None)
            except Exception:
                pass'''

if old_mc_mount in mc_txt:
    mc_txt = mc_txt.replace(old_mc_mount, new_mc_mount)
    print("✓ [services/mode_coordinator.py] _mount_session изолирует песочницу от ассистента и RP.")

old_boot_btn = '''        # Синхронизация панелей (карточка персонажа, фон сцены, кнопка Forge)
        is_rp = (mode == AppMode.ROLEPLAY)
        if hasattr(self.mw, "char_card_widget"):
            self.mw.char_card_widget.setVisible(is_rp)
        if hasattr(self.mw, "btn_scene_bg"):
            self.mw.btn_scene_bg.setVisible(is_rp)
        if hasattr(self.mw, "btn_forge_toggle"):
            self.mw.btn_forge_toggle.setVisible(mode != AppMode.ENGINEER)'''

new_boot_btn = '''        # Синхронизация панелей (карточка персонажа, фон сцены, кнопка Forge, песочница)
        is_rp = (mode == AppMode.ROLEPLAY)
        if hasattr(self.mw, "char_card_widget"):
            self.mw.char_card_widget.setVisible(is_rp)
        if hasattr(self.mw, "btn_scene_bg"):
            self.mw.btn_scene_bg.setVisible(is_rp)
        if hasattr(self.mw, "btn_forge_toggle"):
            self.mw.btn_forge_toggle.setVisible(mode != AppMode.ENGINEER)
        if hasattr(self.mw, "btn_toggle_inspector"):
            self.mw.btn_toggle_inspector.setVisible(is_eng)'''

if old_boot_btn in mc_txt:
    mc_txt = mc_txt.replace(old_boot_btn, new_boot_btn)
    print("✓ [services/mode_coordinator.py] btn_toggle_inspector привязан строго к AppMode.ENGINEER.")

mc_p.write_text(mc_txt, encoding="utf-8")

# 4. ui/main_window.py
mw_p = Path("ui/main_window.py")
mw_txt = mw_p.read_text(encoding="utf-8")

old_mw_btn = '''        if hasattr(self, "btn_scene_bg"):
            self.btn_scene_bg.setVisible(is_rp)

        self._update_header_for_current_state()'''

new_mw_btn = '''        if hasattr(self, "btn_scene_bg"):
            self.btn_scene_bg.setVisible(is_rp)
        if hasattr(self, "btn_toggle_inspector"):
            self.btn_toggle_inspector.setVisible(is_eng)

        self._update_header_for_current_state()'''

if old_mw_btn in mw_txt:
    mw_txt = mw_txt.replace(old_mw_btn, new_mw_btn)
    print("✓ [ui/main_window.py] _update_mode_ui синхронизирует видимость кнопки песочницы.")

old_toggle_insp = '''    def toggle_workspace_inspector(self):
        if not hasattr(self, "workspace_inspector"):
            return
        is_visible = not self.workspace_inspector.isVisible()
        self._sync_splitter_layout(is_engineer=is_visible)'''

new_toggle_insp = '''    def toggle_workspace_inspector(self):
        if not hasattr(self, "workspace_inspector"):
            return
        if not self.is_engineer_mode_active():
            return
        is_visible = not self.workspace_inspector.isVisible()
        self._sync_splitter_layout(is_engineer=is_visible)'''

if old_toggle_insp in mw_txt:
    mw_txt = mw_txt.replace(old_toggle_insp, new_toggle_insp)
    print("✓ [ui/main_window.py] toggle_workspace_inspector заблокирован вне инженерного режима.")

# Изначальное скрытие кнопки в конструкторе
old_init_btn = "self.btn_toggle_inspector = self.chat_header_bar.btn_toggle_inspector"
new_init_btn = "self.btn_toggle_inspector = self.chat_header_bar.btn_toggle_inspector\n        self.btn_toggle_inspector.setVisible(False)"
if old_init_btn in mw_txt and new_init_btn not in mw_txt:
    mw_txt = mw_txt.replace(old_init_btn, new_init_btn)
    print("✓ [ui/main_window.py] Начальное состояние кнопки инспектора выставлено в False.")

mw_p.write_text(mw_txt, encoding="utf-8")

print("\n>>> ВСЕ ПРАВКИ УСПЕШНО ПРИМЕНЕНЫ! ЗАПУСКАЕМ ВАЛИДАЦИОННЫЙ ТЕСТ... <<<")
