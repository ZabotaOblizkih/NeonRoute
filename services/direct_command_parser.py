"""
NeonRoute - Direct Command Parser
Парсер параметров и флагов командной строки для ручного режима (!арт / !art).
"""

import re
from typing import Dict, Any, Tuple


def parse_direct_command_params(text: str) -> tuple[str, str, Dict[str, Any]]:
    """
    Парсит директ-команду (!арт / !art):
    Извлекает CLI-флаги (--steps, --cfg, --denoise, --scale, --hires, --ad, --seed, --model, --w, --h, --size).
    Надежно отделяет негативный промпт.
    """
    params: Dict[str, Any] = {}
    working_text = text

    m = re.search(r'--steps\s+(\d+)', working_text, re.IGNORECASE)
    if m:
        params['steps'] = int(m.group(1))
        working_text = working_text[:m.start()] + " " + working_text[m.end():]

    m = re.search(r'--cfg\s+([\d\.]+)', working_text, re.IGNORECASE)
    if m:
        params['cfg_scale'] = float(m.group(1))
        working_text = working_text[:m.start()] + " " + working_text[m.end():]

    m = re.search(r'--(?:denoise|dn)\s+([\d\.]+)', working_text, re.IGNORECASE)
    if m:
        params['denoising_strength'] = float(m.group(1))
        working_text = working_text[:m.start()] + " " + working_text[m.end():]

    m = re.search(r'--(?:hires|hr)\s+(on|off|true|false|[\d\.]+)', working_text, re.IGNORECASE)
    if m:
        val = m.group(1).lower()
        if val in ('off', 'false'):
            params['enable_hr'] = False
        elif val in ('on', 'true'):
            params['enable_hr'] = True
        else:
            params['enable_hr'] = True
            params['hr_scale'] = float(val)
        working_text = working_text[:m.start()] + " " + working_text[m.end():]

    m = re.search(r'--scale\s+([\d\.]+)', working_text, re.IGNORECASE)
    if m:
        params['hr_scale'] = float(m.group(1))
        params['enable_hr'] = True
        working_text = working_text[:m.start()] + " " + working_text[m.end():]

    m = re.search(r'--(?:adetailer|ad)\s+(on|off|true|false)', working_text, re.IGNORECASE)
    if m:
        params['enable_adetailer'] = m.group(1).lower() in ('on', 'true')
        working_text = working_text[:m.start()] + " " + working_text[m.end():]

    m = re.search(r'--seed\s+(-?\d+)', working_text, re.IGNORECASE)
    if m:
        params['seed'] = int(m.group(1))
        working_text = working_text[:m.start()] + " " + working_text[m.end():]

    m = re.search(r'--(?:model|style|ckpt)\s+([a-zA-Z0-9_\-\.]+)', working_text, re.IGNORECASE)
    if m:
        params['model_override'] = m.group(1).lower()
        working_text = working_text[:m.start()] + " " + working_text[m.end():]

    m_ar = re.search(r'--ar\s+([a-zA-Z0-9:]+)', working_text, re.IGNORECASE)
    if m_ar:
        ar_val = m_ar.group(1).lower()
        if ar_val in ("wide", "16:9", "3:2"):
            params['width'], params['height'] = 768, 512
        elif ar_val in ("tall", "9:16", "2:3"):
            params['width'], params['height'] = 512, 768
        elif ar_val in ("square", "1:1"):
            params['width'], params['height'] = 512, 512
        working_text = working_text[:m_ar.start()] + " " + working_text[m_ar.end():]

    m_size = re.search(r'--size\s+(\d+)x(\d+)', working_text, re.IGNORECASE)
    if m_size:
        params['width'] = int(m_size.group(1))
        params['height'] = int(m_size.group(2))
        working_text = working_text[:m_size.start()] + " " + working_text[m_size.end():]

    m_w = re.search(r'--w\s+(\d+)', working_text, re.IGNORECASE)
    if m_w:
        params['width'] = int(m_w.group(1))
        working_text = working_text[:m_w.start()] + " " + working_text[m_w.end():]

    m_h = re.search(r'--h\s+(\d+)', working_text, re.IGNORECASE)
    if m_h:
        params['height'] = int(m_h.group(1))
        working_text = working_text[:m_h.start()] + " " + working_text[m_h.end():]

    neg_pattern = re.compile(
        r'(?:--negative|--neg|(?:negative|негативный\s+промп?т|негатив)\s*:)\s*',
        re.IGNORECASE
    )
    parts = neg_pattern.split(working_text, maxsplit=1)
    pos = parts[0].strip(' ,')
    neg = parts[1].strip(' ,') if len(parts) > 1 else ""

    return pos, neg, params


def parse_user_negative_trigger(text: str) -> tuple[bool, str, str]:
    """Вспомогательная функция для парсинга триггера negative: в запросе (обратная совместимость)."""
    pos, neg, _ = parse_direct_command_params(text)
    return bool(neg), pos, neg
