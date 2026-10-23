"""
Tool Parser for Mini-Antigravity in NeonRoute.
Provides data structures (ToolCall, ToolResult) and extraction/parsing logic
for tool execution syntax (<<TOOL:...>>).
"""

import re
import html
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple

__all__ = ["ToolCall", "ToolResult", "ToolParser"]


@dataclass
class ToolCall:
    name: str
    params: Dict[str, Any]
    raw_tag: str = ""
    body: Optional[str] = None

    @property
    def tool_name(self) -> str:
        return self.name

    @property
    def content(self) -> str:
        return self.body or self.params.get("content", "")


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    output: str
    artifact_path: Optional[Path] = None
    is_script_run: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    @property
    def return_code(self) -> int:
        return self.exit_code

    @property
    def error(self) -> str:
        return self.stderr or ("" if self.success else self.output)

    @property
    def artifact(self) -> Optional[str]:
        return str(self.artifact_path.resolve()) if self.artifact_path else None


class ToolParser:
    """
    Parser and extractor for tool calls and tags used in NeonRoute.
    Handles <<TOOL:...>> patterns and legacy syntax normalization.
    """

    @staticmethod
    def _parse_attributes(attr_str: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if not attr_str:
            return params

        clean_attr = html.unescape(attr_str)
        kv_pattern = r'(\w+)\s*=\s*(?:["\']([^"\']*)["\']|([^\s>"\']+))'
        for match in re.finditer(kv_pattern, clean_attr):
            key = match.group(1).strip()
            val = match.group(2) if match.group(2) is not None else match.group(3)
            params[key] = html.unescape(val.strip())

        return params

    @staticmethod
    def normalize_tool_text(text: str) -> str:
        if not text:
            return ""
        norm = re.sub(r'(`+|<+)TOOL:', '<<TOOL:', text, flags=re.IGNORECASE)
        norm = re.sub(r'(<<TOOL:[^\n\r>]+?)(?:>>|>\'?|>`+|`+)(?=\s|\$|\r|\n)', r'\1>>', norm)
        return norm

    @classmethod
    def parse_tool_calls(cls, text: str) -> List[ToolCall]:
        if not text:
            return []

        norm = ToolParser.normalize_tool_text(text)
        calls_with_pos = []
        seen_spans = []

        write_pattern = r'<<TOOL:write_file\s+(?:filename|path|file)=["\'](?P<filename>[^"\']+)["\']\s*>>?[\r\n]*(?P<content>.*?)(?:<<?/+TOOL(?::write_file)?>>?|`+/+TOOL(?::write_file)?`+)'
        for match in re.finditer(write_pattern, norm, flags=re.DOTALL | re.IGNORECASE):
            fn = html.unescape(match.group('filename')).strip().strip("'\"`")
            cnt = match.group('content').strip()
            seen_spans.append(match.span())
            calls_with_pos.append((match.start(), ToolCall(
                name="write_file",
                params={"filename": fn, "content": cnt},
                raw_tag=match.group(0),
                body=cnt
            )))

        if not any(c[1].name == "write_file" for c in calls_with_pos) and '<<TOOL:write_file' in norm:
            open_match = re.search(r'<<TOOL:write_file\s+(?:filename|path|file)=["\'](?P<filename>[^"\']+)["\']\s*>>?', norm, flags=re.IGNORECASE)
            if open_match:
                fn = html.unescape(open_match.group('filename')).strip().strip("'\"`")
                after = norm[open_match.end():]
                close_m = re.search(r'(?:<<?/+TOOL(?::write_file)?>>?|`+/+TOOL(?::write_file)?`+)', after, flags=re.IGNORECASE)
                next_tool = re.search(r'<<TOOL:\w+', after, flags=re.IGNORECASE)
                if close_m and (not next_tool or close_m.start() < next_tool.start()):
                    cnt = after[:close_m.start()].strip()
                    end_pos = open_match.end() + close_m.end()
                elif next_tool:
                    cnt = after[:next_tool.start()].strip()
                    end_pos = open_match.end() + next_tool.start()
                else:
                    cnt = after.strip()
                    end_pos = len(norm)
                cnt = re.sub(r'(?:<<?/+TOOL(?::write_file)?>>?|`+/+TOOL(?::write_file)?`+)\$', '', cnt, flags=re.IGNORECASE).strip()

                if not cnt:
                    code_block = re.search(r'```(?:python|py)?\s*[\r\n]+(.*?)(?:```|\$)', norm, flags=re.DOTALL | re.IGNORECASE)
                    if code_block:
                        cnt = code_block.group(1).strip()

                if cnt:
                    seen_spans.append((open_match.start(), end_pos))
                    calls_with_pos.append((open_match.start(), ToolCall(
                        name="write_file",
                        params={"filename": fn, "content": cnt},
                        raw_tag=norm[open_match.start():end_pos],
                        body=cnt
                    )))

        patch_pattern = r'<<TOOL:patch_file\s+(?:filename|path|file)=["\'](?P<filename>[^"\']+)["\'](?:\s+[^>\r\n]+?)?\s*>>?[\r\n]*(?P<body>.*?)(?:<<?/+TOOL(?::patch_file)?>>?|`+/+TOOL(?::patch_file)?`+)'
        for match in re.finditer(patch_pattern, norm, flags=re.DOTALL | re.IGNORECASE):
            fn = html.unescape(match.group('filename')).strip().strip("'\"`")
            body = match.group('body') or ""
            m_sr = re.search(r'<{3,}SEARCH[\r\n]*(?P<search>.*?)[\r\n]*={3,}[\r\n]*(?P<replace>.*?)[\r\n]*>{3,}', body, flags=re.DOTALL)
            if m_sr:
                search_block = m_sr.group('search')
                replace_block = m_sr.group('replace')
            else:
                tag_header = match.group(0).split('>>', 1)[0]
                attrs = ToolParser._parse_attributes(tag_header)
                search_block = attrs.get('search', '')
                replace_block = attrs.get('replace', '')

            seen_spans.append(match.span())
            calls_with_pos.append((match.start(), ToolCall(
                name="patch_file",
                params={"filename": fn, "search": search_block, "replace": replace_block},
                raw_tag=match.group(0),
                body=body
            )))

        if not any(c[1].name == "patch_file" for c in calls_with_pos) and '<<TOOL:patch_file' in norm:
            open_match = re.search(r'<<TOOL:patch_file\s+(?:filename|path|file)=["\'](?P<filename>[^"\']+)["\'](?:\s+[^>\r\n]+?)?\s*>>?', norm, flags=re.IGNORECASE)
            if open_match:
                fn = html.unescape(open_match.group('filename')).strip().strip("'\"`")
                after = norm[open_match.end():]
                close_m = re.search(r'(?:<<?/+TOOL(?::patch_file)?>>?|`+/+TOOL(?::patch_file)?`+)', after, flags=re.IGNORECASE)
                next_tool = re.search(r'<<TOOL:\w+', after, flags=re.IGNORECASE)
                if close_m and (not next_tool or close_m.start() < next_tool.start()):
                    body = after[:close_m.start()].strip()
                    end_pos = open_match.end() + close_m.end()
                elif next_tool:
                    body = after[:next_tool.start()].strip()
                    end_pos = open_match.end() + next_tool.start()
                else:
                    body = after.strip()
                    end_pos = len(norm)

                m_sr = re.search(r'<{3,}SEARCH[\r\n]*(?P<search>.*?)[\r\n]*={3,}[\r\n]*(?P<replace>.*?)[\r\n]*>{3,}', body, flags=re.DOTALL)
                if m_sr:
                    search_block = m_sr.group('search')
                    replace_block = m_sr.group('replace')
                else:
                    attrs = ToolParser._parse_attributes(open_match.group(0))
                    search_block = attrs.get('search', '')
                    replace_block = attrs.get('replace', '')

                if search_block or replace_block or body:
                    seen_spans.append((open_match.start(), end_pos))
                    calls_with_pos.append((open_match.start(), ToolCall(
                        name="patch_file",
                        params={"filename": fn, "search": search_block, "replace": replace_block},
                        raw_tag=norm[open_match.start():end_pos],
                        body=body
                    )))

        inline_pattern = r'<<TOOL:(?P<tool_name>\w+)(?:\s+(?P<params>[^>\r\n]+?))?>>'
        for match in re.finditer(inline_pattern, norm, flags=re.IGNORECASE):
            if any(s <= match.start() < e for s, e in seen_spans):
                continue
            t_name = match.group('tool_name').strip()
            if t_name.lower() in ('write_file', 'patch_file'):
                continue
            params_str = (match.group('params') or "").strip()
            params = ToolParser._parse_attributes(params_str)
            if not params and params_str:
                clean_arg = params_str.strip("'\"`")
                if clean_arg:
                    params["filename"] = clean_arg
            calls_with_pos.append((match.start(), ToolCall(
                name=t_name,
                params=params,
                raw_tag=match.group(0)
            )))

        calls_with_pos.sort(key=lambda x: x[0])
        return [c[1] for c in calls_with_pos]

    @classmethod
    def truncate_after_tool_call(cls, text: str) -> str:
        if not text:
            return ""

        norm = ToolParser.normalize_tool_text(text)
        last_end = 0

        write_pattern = r'<<TOOL:write_file\s+(?:filename|path|file)=["\'](?P<filename>[^"\']+)["\']\s*>>?[\r\n]*(?P<content>.*?)(?:<<?/+TOOL(?::write_file)?>>?|`+/+TOOL(?::write_file)?`+)'
        for m in re.finditer(write_pattern, norm, flags=re.DOTALL | re.IGNORECASE):
            if m.end() > last_end:
                last_end = m.end()

        patch_pattern = r'<<TOOL:patch_file\s+(?:filename|path|file)=["\'](?P<filename>[^"\']+)["\'](?:\s+[^>\r\n]+?)?\s*>>?[\r\n]*(?P<body>.*?)(?:<<?/+TOOL(?::patch_file)?>>?|`+/+TOOL(?::patch_file)?`+)'
        for m in re.finditer(patch_pattern, norm, flags=re.DOTALL | re.IGNORECASE):
            if m.end() > last_end:
                last_end = m.end()

        inline_pattern = r'<<TOOL:(?P<tool_name>\w+)(?:\s+[^>\r\n]+?)?>>'
        for m in re.finditer(inline_pattern, norm, flags=re.IGNORECASE):
            if m.end() > last_end:
                last_end = m.end()

        if last_end > 0:
            return norm[:last_end].strip()

        return text

    @classmethod
    def parse_tool_call(cls, text: str) -> Optional[ToolCall]:
        calls = cls.parse_tool_calls(text)
        return calls[0] if calls else None

    @classmethod
    def strip_tool_tags(cls, text: str) -> str:
        if not text:
            return ""

        norm = ToolParser.normalize_tool_text(text)
        cleaned = re.sub(r'<<TOOL:write_file\s+(?:filename|path|file)=["\'][^"\']+["\']\s*>>?[\r\n]*.*?(?:<<?/+TOOL(?::write_file)?>>?|`+/+TOOL(?::write_file)?`+)', '', norm, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<<TOOL:write_file\s+(?:filename|path|file)=["\'][^"\']+["\']\s*>>?[\r\n]*.*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<<TOOL:patch_file\s+(?:filename|path|file)=["\'][^"\']+["\']\s*>>?[\r\n]*.*?(?:<<?/+TOOL(?::patch_file)?>>?|`+/+TOOL(?::patch_file)?`+)', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<<TOOL:patch_file\s+(?:filename|path|file)=["\'][^"\']+["\']\s*>>?[\r\n]*.*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<<TOOL:\w+\s+.*?>>', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'(`+|<+)TOOL:.*?(?:>>|>\'?|>`*|`+|$)', '', cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    @staticmethod
    def has_tool_call(text: str) -> bool:
        if not text:
            return False
        return bool(re.search(r'(`+|<+)TOOL:\w+', text, flags=re.IGNORECASE))
