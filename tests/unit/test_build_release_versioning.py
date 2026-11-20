"""
tests/unit/test_build_release_versioning.py
============================================
Тесты для интерактивного управления версией и ведения hotfix.md
в конвейере релизной сборки (scripts/build_release.py).
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scripts.build_release import (
    RELEASE_COMMIT_MARKER,
    collect_git_commits,
    create_release_git_commit,
    validate_version_string,
    read_app_version,
    update_app_version_in_config,
    update_hotfix_md,
    rollback_versioning,
    prompt_interactive_versioning,
)


def test_validate_version_string_arbitrary_format():
    """
    Проверяет, что разрешён произвольный формат строки (SemVer, буквенно-цифровые суффиксы),
    но отсекаются пустые строки, запрещённые символы Windows (<>:"/\\|?*) и управляющие символы.
    """
    # 1. Валидные версии
    valid_versions = [
        "0.9.9-beta",
        "0.9.8.123.2321ABSJDLKAD",
        "1.0_rc2",
        "2.0.0-final",
        "v3.1.4.159",
        "2026.11.17-hotfix1",
    ]
    for ver in valid_versions:
        ok, err = validate_version_string(ver)
        assert ok is True, f"Версия {ver} должна быть валидной, но получена ошибка: {err}"
        assert err == ""

    # 2. Невалидные: пустые строки и пробелы
    for empty in ["", "   ", "\t", "\n"]:
        ok, err = validate_version_string(empty)
        assert ok is False
        assert "не может быть пустой" in err

    # 3. Невалидные: запрещенные символы Windows (< > : " / \ | ? *)
    forbidden_samples = [
        "0.9.8:beta",
        "0.9.8/beta",
        "0.9.8\\beta",
        "0.9.8?beta",
        "0.9.8*beta",
        "0.9.8<beta>",
        "0.9.8|beta",
        '0.9.8"beta',
    ]
    for bad in forbidden_samples:
        ok, err = validate_version_string(bad)
        assert ok is False, f"Версия {bad} должна отклоняться!"
        assert "запрещен в именах файлов Windows" in err

    # 4. Невалидные: управляющие символы
    ok, err = validate_version_string("0.9.8\x07beta")
    assert ok is False
    assert "Управляющие символы запрещены" in err


def test_create_hotfix_md_from_scratch(tmp_path):
    """
    Проверяет создание hotfix.md с нуля при сборке релиза
    с произвольным номером версии вида 0.9.8.123.2321ABSJDLKAD.
    """
    hotfix_file = tmp_path / "hotfix.md"
    assert not hotfix_file.exists()

    new_version = "0.9.8.123.2321ABSJDLKAD"
    changes = "- Добавлена поддержка произвольных версий\n- Оптимизирован конвейер сборки"

    update_hotfix_md(hotfix_file, new_version, changes, date_str="2026-11-17")

    assert hotfix_file.is_file()
    content = hotfix_file.read_text(encoding="utf-8")

    assert "# История изменений и релизов NeonRoute" in content
    assert f"## v{new_version} (2026-11-17)" in content
    assert "- Добавлена поддержка произвольных версий" in content
    assert "- Оптимизирован конвейер сборки" in content


def test_update_hotfix_md_prepends_without_overwriting_history(tmp_path):
    """
    Проверяет, что новая запись вставляется поверх существующих версий,
    полностью сохраняя всю предыдущую историю изменений.
    """
    hotfix_file = tmp_path / "hotfix.md"
    initial_content = (
        "# История изменений и релизов NeonRoute\n\n"
        "## v0.9.8-beta (2026-11-16)\n\n"
        "- Исправлен запуск llama-server (--load-mode none)\n"
        "- Исправлена докачка библиотек CUDA 12.4\n"
    )
    hotfix_file.write_text(initial_content, encoding="utf-8")

    new_version = "0.9.9-beta"
    new_changes = "- Новые возможности интерактивной сборки\n- Защита отката версий"

    update_hotfix_md(hotfix_file, new_version, new_changes, date_str="2026-11-17")

    updated = hotfix_file.read_text(encoding="utf-8")

    # Обе версии должны присутствовать
    assert f"## v{new_version} (2026-11-17)" in updated
    assert "## v0.9.8-beta (2026-11-16)" in updated

    # Новая версия должна быть выше старой
    pos_new = updated.find(f"## v{new_version}")
    pos_old = updated.find("## v0.9.8-beta")
    assert pos_new != -1 and pos_old != -1
    assert pos_new < pos_old, "Новая версия должна быть вставлена выше старой!"

    # Старые записи не затерты
    assert "- Исправлен запуск llama-server (--load-mode none)" in updated
    assert "- Новые возможности интерактивной сборки" in updated


def test_update_hotfix_md_updates_existing_version_in_place(tmp_path):
    """
    Проверяет, что если запись для текущей версии уже была создана ранее,
    повторный вызов обновляет этот блок без дублирования заголовка.
    """
    hotfix_file = tmp_path / "hotfix.md"
    initial_content = (
        "# История изменений и релизов NeonRoute\n\n"
        "## v0.9.9-beta (2026-11-17)\n\n"
        "- Черновое описание\n\n"
        "## v0.9.8-beta (2026-11-16)\n\n"
        "- Старый релиз\n"
    )
    hotfix_file.write_text(initial_content, encoding="utf-8")

    final_changes = "- Финальное описание изменений v0.9.9"
    update_hotfix_md(hotfix_file, "0.9.9-beta", final_changes, date_str="2026-11-17")

    updated = hotfix_file.read_text(encoding="utf-8")
    assert updated.count("## v0.9.9-beta") == 1, "Заголовок версии не должен дублироваться!"
    assert "- Финальное описание изменений v0.9.9" in updated
    assert "- Черновое описание" not in updated
    assert "## v0.9.8-beta (2026-11-16)" in updated


def test_no_changes_when_version_unchanged(tmp_path):
    """
    Проверяет, что если пользователь нажимает Enter или вводит текущую версию,
    файлы config.py и hotfix.md не изменяются, а rollback_state равен None.
    """
    config_file = tmp_path / "config.py"
    config_file.write_text('APP_NAME = "NeonRoute"\nAPP_VERSION = "0.9.8-beta"\n', encoding="utf-8")
    hotfix_file = tmp_path / "hotfix.md"
    hotfix_file.write_text("# Old Hotfix\n", encoding="utf-8")

    config_before = config_file.read_text(encoding="utf-8")
    hotfix_before = hotfix_file.read_text(encoding="utf-8")

    # Имитируем ввод Enter (пустая строка)
    with patch("builtins.input", return_value=""), \
         patch("sys.stdin.isatty", return_value=True):
        ver, rollback_state = prompt_interactive_versioning(tmp_path, non_interactive=False)

    assert ver == "0.9.8-beta"
    assert rollback_state is None
    assert config_file.read_text(encoding="utf-8") == config_before
    assert hotfix_file.read_text(encoding="utf-8") == hotfix_before


def test_rollback_on_failure_when_hotfix_did_not_exist(tmp_path):
    """
    Проверяет сценарий сбоя на Stage A/B:
    Если hotfix.md изначально не существовал, после rollback_versioning()
    он удаляется, а config.py возвращается к исходному состоянию.
    """
    config_file = tmp_path / "config.py"
    config_file.write_text('APP_VERSION = "0.9.8-beta"\n', encoding="utf-8")
    hotfix_file = tmp_path / "hotfix.md"
    assert not hotfix_file.exists()

    inputs = ["0.9.9-beta", "- First change", "END"]
    input_gen = iter(inputs)

    with patch("builtins.input", side_effect=lambda *args: next(input_gen)), \
         patch("sys.stdin.isatty", return_value=True):
        new_ver, rollback_state = prompt_interactive_versioning(tmp_path, non_interactive=False)

    assert new_ver == "0.9.9-beta"
    assert rollback_state is not None
    assert read_app_version(config_file) == "0.9.9-beta"
    assert hotfix_file.is_file()

    # Имитируем падение проверки Stage A или B и вызываем откат
    rollback_versioning(rollback_state)

    assert read_app_version(config_file) == "0.9.8-beta"
    assert not hotfix_file.exists(), "hotfix.md должен быть удалён, так как его не было до сборки!"


def test_rollback_on_failure_when_hotfix_existed(tmp_path):
    """
    Проверяет откат при сбое:
    Если hotfix.md существовал до сборки, его исходное содержимое полностью восстанавливается.
    """
    config_file = tmp_path / "config.py"
    config_file.write_text('APP_VERSION = "0.9.8-beta"\n', encoding="utf-8")
    hotfix_file = tmp_path / "hotfix.md"
    original_hotfix = "# Original Hotfix\n\n## v0.9.8-beta (2026-11-16)\n- Original notes\n"
    hotfix_file.write_text(original_hotfix, encoding="utf-8")

    inputs = ["1.0.0", "- New v1 notes", "END"]
    input_gen = iter(inputs)

    with patch("builtins.input", side_effect=lambda *args: next(input_gen)), \
         patch("sys.stdin.isatty", return_value=True):
        new_ver, rollback_state = prompt_interactive_versioning(tmp_path, non_interactive=False)

    assert new_ver == "1.0.0"
    assert read_app_version(config_file) == "1.0.0"
    assert "## v1.0.0" in hotfix_file.read_text(encoding="utf-8")

    # Откат
    rollback_versioning(rollback_state)

    assert read_app_version(config_file) == "0.9.8-beta"
    assert hotfix_file.read_text(encoding="utf-8") == original_hotfix


def test_collect_git_commits_stops_at_release_head_marker(tmp_path):
    """
    Проверяет, что сбор коммитов останавливается ровно на коммите с маркером [release-head],
    и ни сам маркерный коммит, ни более старые коммиты не попадают в результирующий список.
    """
    mock_git_output = (
        "1111111111111111111111111111111111111111\x1ffix: new feature fix\x1fBody 1\x1e"
        "2222222222222222222222222222222222222222\x1ffea: another improvement\x1fBody 2\x1e"
        f"3333333333333333333333333333333333333333\x1fv0.9.8: Release commit\x1fRelease notes...\n\n{RELEASE_COMMIT_MARKER}\x1e"
        "4444444444444444444444444444444444444444\x1fold: ancient commit\x1fOld body\x1e"
    )

    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = mock_git_output

    with patch("shutil.which", return_value="git"), \
         patch("subprocess.run", return_value=mock_res):
        relevant, ignored, all_commits = collect_git_commits(tmp_path)

    # Должны быть собраны только 1 и 2 коммиты в чистом формате "- <subject>"
    assert len(all_commits) == 2
    assert relevant == ["- fix: new feature fix", "- fea: another improvement"]
    assert all(c.startswith("- ") for c in all_commits)
    assert not any(c.startswith("* ") for c in all_commits)

    # Коммит с [release-head] и более старый не должны попасть никуда
    for c in all_commits:
        assert "Release commit" not in c
        assert "ancient commit" not in c
        assert RELEASE_COMMIT_MARKER not in c


def test_collect_git_commits_filters_noise_and_duplicates(tmp_path):
    """
    Проверяет отсев служебных коммитов (wip, test:, ci:, fixup!, дубликаты)
    и правильный подсчёт скрытых коммитов.
    """
    mock_git_output = (
        "aaa1111111111111111111111111111111111111\x1ffeat(core): vital feature\x1fBody\x1e"
        "aaa2222222222222222222222222222222222222\x1fwip: unfinished work\x1fBody\x1e"
        "aaa3333333333333333333333333333333333333\x1ftest: add unit tests\x1fBody\x1e"
        "aaa4444444444444444444444444444444444444\x1fci: update github action\x1fBody\x1e"
        "aaa5555555555555555555555555555555555555\x1ffixup! fix previous typo\x1fBody\x1e"
        "aaa6666666666666666666666666666666666666\x1ffeat(core): vital feature\x1fDuplicate subject\x1e"
        "aaa7777777777777777777777777777777777777\x1ffix(ui): fix button margin\x1fBody\x1e"
    )

    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = mock_git_output

    with patch("shutil.which", return_value="git"), \
         patch("subprocess.run", return_value=mock_res):
        relevant, ignored, all_commits = collect_git_commits(tmp_path)

    assert len(all_commits) == 7
    # Релевантные: feat(core): vital feature (первое вхождение) и fix(ui): fix button margin
    assert len(relevant) == 2
    assert any("feat(core): vital feature" in c for c in relevant)
    assert any("fix(ui): fix button margin" in c for c in relevant)

    # Игнорированные: wip, test:, ci:, fixup!, дубликат
    assert len(ignored) == 5
    assert any("wip: unfinished work" in c for c in ignored)
    assert any("test: add unit tests" in c for c in ignored)
    assert any("ci: update github action" in c for c in ignored)
    assert any("fixup! fix previous typo" in c for c in ignored)


def test_create_release_git_commit_structure(tmp_path):
    """
    Проверяет формирование релизного коммита через git commit --only -- config.py hotfix.md:
    наличие текста пользователя и маркера [release-head] в сообщении коммита,
    а также строгое отсутствие [release-head] в самом файле hotfix.md.
    """
    config_file = tmp_path / "config.py"
    config_file.write_text('APP_VERSION = "0.9.9-beta"\n', encoding="utf-8")
    hotfix_file = tmp_path / "hotfix.md"
    user_changes = "- Оптимизация инференса\n- Фикс утечки памяти"

    update_hotfix_md(hotfix_file, "0.9.9-beta", user_changes, date_str="2026-11-17")

    # Проверяем, что в hotfix.md маркера нет
    hotfix_text = hotfix_file.read_text(encoding="utf-8")
    assert RELEASE_COMMIT_MARKER not in hotfix_text, "RELEASE_COMMIT_MARKER никогда не должен записываться в hotfix.md!"

    git_calls = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        git_calls.append(cmd)
        mock_ret = MagicMock()
        mock_ret.returncode = 0
        mock_ret.stdout = "[main 1234567] v0.9.9-beta"
        mock_ret.stderr = ""
        return mock_ret

    with patch("shutil.which", return_value="git"), \
         patch("subprocess.run", side_effect=fake_subprocess_run):
        ok = create_release_git_commit(tmp_path, "0.9.9-beta", user_changes)
        assert ok is True

    # Проверяем последовательность команд git
    assert len(git_calls) == 2
    assert git_calls[0] == ["git", "add", "--", "config.py", "hotfix.md"]

    commit_cmd = git_calls[1]
    assert commit_cmd[:2] == ["git", "commit"]
    assert "-m" in commit_cmd

    # Проверяем subject и body в аргументах -m
    idx_m1 = commit_cmd.index("-m")
    subject_arg = commit_cmd[idx_m1 + 1]
    idx_m2 = commit_cmd.index("-m", idx_m1 + 2)
    body_arg = commit_cmd[idx_m2 + 1]

    assert "v0.9.9-beta: Оптимизация инференса" in subject_arg
    assert "Оптимизация инференса" in body_arg
    assert "Фикс утечки памяти" in body_arg
    assert RELEASE_COMMIT_MARKER in body_arg, "Маркер [release-head] должен присутствовать в теле git-коммита!"


def test_auto_commit_not_created_when_version_unchanged(tmp_path):
    """
    Проверяет, что при version_changed=False (версия не менялась)
    автоматический релизный коммит не вызывается.
    """
    config_file = tmp_path / "config.py"
    config_file.write_text('APP_VERSION = "0.9.8-beta"\n', encoding="utf-8")

    # Имитируем ввод Enter при запросе версии (версия не изменилась)
    with patch("builtins.input", return_value=""), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("scripts.build_release.create_release_git_commit") as mock_commit:
        ver, rollback_state = prompt_interactive_versioning(tmp_path, non_interactive=False)

        assert ver == "0.9.8-beta"
        assert rollback_state is None
        version_changed = (rollback_state is not None and ver != rollback_state.get("old_version"))
        assert version_changed is False
        mock_commit.assert_not_called()


def test_prompt_interactive_single_enter_substitutes_all_commits(tmp_path):
    """
    Проверяет, что при запросе описания изменений нажатие одного Enter на пустой первой строке:
    1. Немедленно завершает ввод без ожидания второго Enter.
    2. Автоматически подставляет все новые отфильтрованные коммиты в формате '- <subject>'.
    3. В hotfix.md и rollback_state попадают только чистые строки без 'commit ', 'Author:', 'Date:'.
    """
    config_file = tmp_path / "config.py"
    config_file.write_text('APP_VERSION = "0.9.8-beta"\n', encoding="utf-8")
    hotfix_file = tmp_path / "hotfix.md"

    mock_git_output = (
        "1111111111111111111111111111111111111111\x1ffix(engine): resolve mmap load mode\x1fBody 1\x1e"
        "2222222222222222222222222222222222222222\x1ffea(cuda): download cudart runtime\x1fBody 2\x1e"
        f"3333333333333333333333333333333333333333\x1fv0.9.8: release commit\x1fNote\n\n{RELEASE_COMMIT_MARKER}\x1e"
    )

    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = mock_git_output

    # Ровно два значения: 1) новая версия, 2) пустая строка (один Enter)
    # Если скрипт попытается запросить вторую строку, iter выбросит StopIteration и тест упадет!
    input_iter = iter(["0.9.9-beta", ""])

    with patch("shutil.which", return_value="git"), \
         patch("subprocess.run", return_value=mock_res), \
         patch("builtins.input", side_effect=lambda *args: next(input_iter)), \
         patch("sys.stdin.isatty", return_value=True):
        new_ver, rollback_state = prompt_interactive_versioning(tmp_path, non_interactive=False)

    assert new_ver == "0.9.9-beta"
    assert rollback_state is not None

    changes_text = rollback_state.get("changes_text", "")
    assert "- fix(engine): resolve mmap load mode" in changes_text
    assert "- fea(cuda): download cudart runtime" in changes_text

    # Проверяем отсутствие шума машинного git log
    for noise in ["commit ", "Author:", "Date:", RELEASE_COMMIT_MARKER]:
        assert noise not in changes_text

    hotfix_content = hotfix_file.read_text(encoding="utf-8")
    assert "## v0.9.9-beta" in hotfix_content
    assert "- fix(engine): resolve mmap load mode" in hotfix_content
    assert "- fea(cuda): download cudart runtime" in hotfix_content
    for noise in ["commit ", "Author:", "Date:", RELEASE_COMMIT_MARKER]:
        assert noise not in hotfix_content


def test_prompt_interactive_all_keyword_displays_ignored_and_reprompts(tmp_path):
    """
    Проверяет, что ввод 'all' на первой строке описания показывает все коммиты
    (включая скрытые служебные) и перезапрашивает описание.
    """
    config_file = tmp_path / "config.py"
    config_file.write_text('APP_VERSION = "0.9.8-beta"\n', encoding="utf-8")
    hotfix_file = tmp_path / "hotfix.md"

    mock_git_output = (
        "1111111111111111111111111111111111111111\x1ffix(core): real change\x1fBody 1\x1e"
        "2222222222222222222222222222222222222222\x1fwip: ignored commit\x1fBody 2\x1e"
        f"3333333333333333333333333333333333333333\x1fv0.9.8: release\x1f\n\n{RELEASE_COMMIT_MARKER}\x1e"
    )

    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = mock_git_output

    # 1) Новая версия, 2) "all" -> вывод всех, 3) "" -> завершение подстановкой
    input_iter = iter(["0.9.9-beta", "all", ""])

    with patch("shutil.which", return_value="git"), \
         patch("subprocess.run", return_value=mock_res), \
         patch("builtins.input", side_effect=lambda *args: next(input_iter)), \
         patch("sys.stdin.isatty", return_value=True):
        new_ver, rollback_state = prompt_interactive_versioning(tmp_path, non_interactive=False)

    assert new_ver == "0.9.9-beta"
    changes_text = rollback_state.get("changes_text", "")
    assert "- fix(core): real change" in changes_text
    # Игнорированный commit 'wip:' не должен попасть в итоговый changes_text при автоподстановке
    assert "wip: ignored commit" not in changes_text


def test_prompt_interactive_single_enter_when_zero_commits_warns_and_prompts_manual(tmp_path):
    """
    Проверяет, что если новых коммитов 0, нажатие Enter выводит предупреждение
    и запрашивает ручной ввод хотя бы одной строки описания.
    """
    config_file = tmp_path / "config.py"
    config_file.write_text('APP_VERSION = "0.9.8-beta"\n', encoding="utf-8")
    hotfix_file = tmp_path / "hotfix.md"

    # Сразу первый коммит содержит RELEASE_COMMIT_MARKER -> новых коммитов 0
    mock_git_output = (
        f"3333333333333333333333333333333333333333\x1fv0.9.8: release\x1f\n\n{RELEASE_COMMIT_MARKER}\x1e"
    )

    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = mock_git_output

    # 1) Новая версия
    # 2) "" (Enter на первой строке - коммитов 0, поэтому выдается предупреждение)
    # 3) "- Ручное описание релиза"
    # 4) "" (пустая строка для завершения ручного ввода)
    input_iter = iter(["0.9.9-beta", "", "- Ручное описание релиза", ""])

    with patch("shutil.which", return_value="git"), \
         patch("subprocess.run", return_value=mock_res), \
         patch("builtins.input", side_effect=lambda *args: next(input_iter)), \
         patch("sys.stdin.isatty", return_value=True):
        new_ver, rollback_state = prompt_interactive_versioning(tmp_path, non_interactive=False)

    assert new_ver == "0.9.9-beta"
    changes_text = rollback_state.get("changes_text", "")
    assert "- Ручное описание релиза" in changes_text
