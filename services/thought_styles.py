"""
Thought Styles Resolver for NeonRoute.
Provides archetype-specific visual presets and configurations for the thought spoiler widget.
Pure logic module (zero PySide/PyQt/UI imports to prevent circular dependencies).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Any, Dict


@dataclass(frozen=True)
class ThoughtStyleConfig:
    icon: str
    active_label: str
    collapsed_label: str
    accent_color: str
    is_rp: bool


def _extract_thought_style_override(card: Any) -> Optional[Dict[str, Any]]:
    """Извлекает кастомный словарь thought_style из raw_data карточки."""
    if not card or not hasattr(card, "raw_data") or not isinstance(card.raw_data, dict):
        return None

    raw = card.raw_data
    # 1. data.extensions.neonroute.thought_style
    data_dict = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    ext1 = data_dict.get("extensions") if isinstance(data_dict.get("extensions"), dict) else {}
    nr1 = ext1.get("neonroute") if isinstance(ext1.get("neonroute"), dict) else {}
    if isinstance(nr1.get("thought_style"), dict):
        return nr1["thought_style"]

    # 2. extensions.neonroute.thought_style
    ext2 = raw.get("extensions") if isinstance(raw.get("extensions"), dict) else {}
    nr2 = ext2.get("neonroute") if isinstance(ext2.get("neonroute"), dict) else {}
    if isinstance(nr2.get("thought_style"), dict):
        return nr2["thought_style"]

    # 3. Прямой thought_style в raw
    if isinstance(raw.get("thought_style"), dict):
        return raw["thought_style"]

    return None


def resolve_thought_style(card: Optional[Any] = None) -> ThoughtStyleConfig:
    """
    Резолвит визуальную конфигурацию блока рассуждений:
    - Если card is None -> дефолт режима Ассистента (is_rp=False).
    - Для ролевых карточек -> выбор по архетипу карточки (is_rp=True).
    - Поддержка кастомных переопределений из data.extensions.neonroute.thought_style.
    """
    if card is None:
        return ThoughtStyleConfig(
            icon="🧠",
            active_label="Анализ данных...",
            collapsed_label="Ход мыслей",
            accent_color="#38BDF8",
            is_rp=False
        )

    name = (getattr(card, "name", None) or "Персонаж").strip() or "Персонаж"

    # Выбор пресета по порядку приоритета флагов RoleplayPromptBuilder._resolve_archetype_block
    if getattr(card, "is_system", False):
        icon = "⚙️"
        active_label = "Обработка параметров Системы..."
        collapsed_label = "Лог Системы"
        accent_color = "#38BDF8"
    elif getattr(card, "is_dating", False):
        icon = "✨"
        active_label = f"{name} прислушивается к чувствам..."
        collapsed_label = "Невысказанное"
        accent_color = "#B47EB3"
    elif getattr(card, "is_horror", False):
        icon = "👁️"
        active_label = "Тьма сгущается вокруг..."
        collapsed_label = "Шёпот за гранью"
        accent_color = "#B94A5A"
    elif getattr(card, "is_cyoa", False):
        icon = "🧭"
        active_label = "Нити сюжета сплетаются..."
        collapsed_label = "Ветвление сцены"
        accent_color = "#6B9AC4"
    elif getattr(card, "is_reverse_rp", False):
        icon = "⚖️"
        active_label = f"{name} ищет выход из положения..."
        collapsed_label = "Скрытый расчёт"
        accent_color = "#6366F1"
    elif getattr(card, "is_detective", False):
        icon = "🔍"
        active_label = "Сопоставляет улики и мотивы..."
        collapsed_label = "Ход расследования"
        accent_color = "#6B8CAE"
    elif getattr(card, "is_dm", False):
        icon = "🎲"
        active_label = "Мастер сверяется с хроникой..."
        collapsed_label = "Замысел Мастера"
        accent_color = "#8B5CF6"
    elif getattr(card, "is_scenario", False):
        icon = "☢️"
        active_label = "Оценка обстановки и угроз..."
        collapsed_label = "Сводка среды"
        accent_color = "#B87A56"
    elif getattr(card, "is_group", False):
        icon = "👥"
        active_label = "Реакция спутников..."
        collapsed_label = "За кадром диалога"
        accent_color = "#569DAA"
    elif getattr(card, "is_lorebook", False):
        icon = "📜"
        active_label = "Поиск в архивах мира..."
        collapsed_label = "Заметки летописца"
        accent_color = "#94A3B8"
    else:  # Solo / fallback
        icon = "💭"
        active_label = f"{name} погружается в раздумья..."
        collapsed_label = f"Внутренний монолог • {name}"
        accent_color = "#818CF8"

    is_rp = True

    # Проверка кастомного переопределения в raw_data карточки
    custom_override = _extract_thought_style_override(card)
    if custom_override:
        if "icon" in custom_override and custom_override["icon"]:
            icon = str(custom_override["icon"])
        if "active_label" in custom_override and custom_override["active_label"]:
            active_label = str(custom_override["active_label"]).replace("{name}", name)
        if "collapsed_label" in custom_override and custom_override["collapsed_label"]:
            collapsed_label = str(custom_override["collapsed_label"]).replace("{name}", name)
        if "accent_color" in custom_override and custom_override["accent_color"]:
            accent_color = str(custom_override["accent_color"])
        if "is_rp" in custom_override:
            is_rp = bool(custom_override["is_rp"])

    return ThoughtStyleConfig(
        icon=icon,
        active_label=active_label,
        collapsed_label=collapsed_label,
        accent_color=accent_color,
        is_rp=is_rp
    )
