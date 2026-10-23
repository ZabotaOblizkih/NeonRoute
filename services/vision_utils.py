"""
Vision Utilities for Multimodal Analysis & Resolution Optimization (NeonRoute)
Provides image pre-processing, resolution downscaling (to protect context token budget),
and base64 payload formatting for Qwen2.5-VL / LM Studio.
"""

import io
import base64
import re
from pathlib import Path
from typing import Union, List, Dict, Any, Optional
from PIL import Image


class VisionUtils:
    @staticmethod
    def prepare_image_payload(image_path: Union[str, Path], max_dim: int = 768) -> dict:
        """
        Подготавливает payload изображения для Vision-моделей (Qwen2.5-VL):
        1. Открывает изображение через PIL.Image.
        2. Конвертирует в RGB (если RGBA или P).
        3. Если max(width, height) > max_dim, пропорционально уменьшает размер с использованием Image.Resampling.LANCZOS.
        4. Сохраняет в BytesIO как JPEG (качество 85).
        5. Кодирует в base64 и возвращает словарь:
           {
               "type": "image_url",
               "image_url": {"url": f"data:image/jpeg;base64,{b64_str}"}
           }
        """
        img_p = Path(image_path)
        with Image.open(img_p) as img:
            # Конвертирует в RGB (если RGBA или P)
            if img.mode in ("RGBA", "P") or img.mode != "RGB":
                img = img.convert("RGB")
            else:
                img = img.copy()

            # Если max(width, height) > max_dim, пропорционально уменьшает размер
            w, h = img.size
            if max(w, h) > max_dim:
                scale = max_dim / max(w, h)
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
                img = img.resize((new_w, new_h), resample=resample)

            # Сохраняет в BytesIO как JPEG (качество 85)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            buf.seek(0)
            b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")

        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64_str}"}
        }

    @staticmethod
    def sanitize_messages_for_logging(messages: list) -> list:
        """
        Создает поверхностную копию списка сообщений, заменяя длинные base64 строки
        на "[IMAGE_DATA: {len} chars]", чтобы не перегружать логи терминала и UI.
        """
        if not isinstance(messages, list):
            return messages

        sanitized = []
        for msg in messages:
            if not isinstance(msg, dict):
                sanitized.append(msg)
                continue

            msg_copy = dict(msg)
            content = msg_copy.get("content")

            if isinstance(content, list):
                new_content = []
                for part in content:
                    if isinstance(part, dict):
                        part_copy = dict(part)
                        if part_copy.get("type") == "image_url":
                            img_info = part_copy.get("image_url")
                            if isinstance(img_info, dict) and "url" in img_info:
                                url_val = str(img_info["url"])
                                if url_val.startswith("data:image"):
                                    part_copy["image_url"] = {"url": f"[IMAGE_DATA: {len(url_val)} chars]"}
                            elif isinstance(img_info, str) and img_info.startswith("data:image"):
                                part_copy["image_url"] = f"[IMAGE_DATA: {len(img_info)} chars]"
                        new_content.append(part_copy)
                    else:
                        new_content.append(part)
                msg_copy["content"] = new_content
            elif isinstance(content, str) and "data:image" in content and len(content) > 500:
                msg_copy["content"] = re.sub(
                    r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+',
                    lambda m: f"[IMAGE_DATA: {len(m.group(0))} chars]",
                    content
                )

            sanitized.append(msg_copy)

        return sanitized
