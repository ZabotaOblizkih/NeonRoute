"""
Icon utilities for safe FluentIcon access across NeonRoute UI.
"""

from qfluentwidgets import FluentIcon as FIF


def safe_fif(icon_name: str, fallback=FIF.DOCUMENT):
    """Безопасное получение иконки по строковому имени без риска AttributeError."""
    if icon_name == "BOOK_SHELF":
        return getattr(FIF, "LIBRARY", getattr(FIF, "DICTIONARY", fallback))
    return getattr(FIF, icon_name, fallback)


class SafeFIFMeta(type):
    """Прокси-метакласс: перехватывает любое обращение к SafeFIF.<NAME>."""
    def __getattr__(cls, name):
        if name == "BOOK_SHELF":
            return getattr(FIF, "LIBRARY", getattr(FIF, "DICTIONARY", FIF.DOCUMENT))
        return getattr(FIF, name, FIF.DOCUMENT)


class SafeFIF(metaclass=SafeFIFMeta):
    BOOK_SHELF = getattr(FIF, "LIBRARY", getattr(FIF, "DICTIONARY", FIF.DOCUMENT))
