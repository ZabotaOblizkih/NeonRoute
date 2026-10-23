"""
Document Generation Service for NeonRoute
Builds professional Excel (.xlsx) and Word (.docx) documents from LLM responses.
Saves all generated documents directly into C:/NeonRoute/exports.
"""

import logging
import re
import time
from pathlib import Path
from typing import List, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

from config import EXPORTS_DIR

logger = logging.getLogger("NeonRoute.FileGenerator")


def _wait_for_file_ready(filepath: Path, timeout: float = 2.0) -> bool:
    """Ожидает завершения записи на диск и проверяет сигнатуру ZIP/Office."""
    start_time = time.time()
    target = Path(filepath)

    while time.time() - start_time < timeout:
        if target.exists() and target.stat().st_size > 0:
            try:
                # Пробуем открыть файл строго на чтение
                with open(target, "rb") as f:
                    header = f.read(4)
                    # Сигнатура ZIP архива (PK..) характерна для xlsx и docx
                    if header.startswith(b"PK"):
                        return True
            except (PermissionError, OSError):
                # Файл еще удерживается операционной системой, ждем
                time.sleep(0.05)
            else:
                time.sleep(0.05)
        else:
            time.sleep(0.05)

    return target.exists() and target.stat().st_size > 0


class FileGenerator:
    """Creates styled spreadsheets and documents from structured LLM outputs."""

    _wait_for_file_ready = staticmethod(_wait_for_file_ready)

    @staticmethod
    def create_excel(
        title: str,
        data_text: str,
        sheet_name: str = "Данные"
    ) -> Path:
        """
        Parses Markdown table or delimited text and creates an Excel spreadsheet.
        Saved into C:/NeonRoute/exports/<filename>.xlsx.
        """
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_name[:31]

        # Ensure gridlines are visible
        ws.views.sheetView[0].showGridLines = True

        # Styles
        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="00F0FF")
        title_font = Font(name="Calibri", size=14, bold=True, color="0F172A")
        cell_font = Font(name="Calibri", size=10, color="0F172A")
        thin_border = Border(
            left=Side(style='thin', color='CBD5E1'),
            right=Side(style='thin', color='CBD5E1'),
            top=Side(style='thin', color='CBD5E1'),
            bottom=Side(style='thin', color='CBD5E1')
        )
        center_align = Alignment(horizontal="center", vertical="center")
        left_align = Alignment(horizontal="left", vertical="center")

        # Add Title banner
        ws.cell(row=1, column=1, value=title).font = title_font
        ws.row_dimensions[1].height = 25

        rows = FileGenerator._parse_table_data(data_text)
        if not rows:
            # Fallback if no table found: write text line by line
            ws.cell(row=3, column=1, value="Содержимое").font = header_font
            ws.cell(row=3, column=1).fill = header_fill
            for idx, line in enumerate(data_text.splitlines(), start=4):
                if line.strip():
                    cell = ws.cell(row=idx, column=1, value=line.strip())
                    cell.font = cell_font
                    cell.border = thin_border
        else:
            start_row = 3
            # Write Header
            header = rows[0]
            ws.row_dimensions[start_row].height = 24
            for col_idx, col_name in enumerate(header, start=1):
                cell = ws.cell(row=start_row, column=col_idx, value=col_name)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = center_align
                cell.border = thin_border

            # Write Data Rows
            for r_idx, row in enumerate(rows[1:], start=start_row + 1):
                ws.row_dimensions[r_idx].height = 20
                for c_idx, val in enumerate(row, start=1):
                    # Try numeric conversion
                    cleaned = val.replace(" ", "").replace(",", ".")
                    cell_val: any = val
                    try:
                        if "." in cleaned:
                            cell_val = float(cleaned)
                        else:
                            cell_val = int(cleaned)
                    except ValueError:
                        cell_val = val

                    cell = ws.cell(row=r_idx, column=c_idx, value=cell_val)
                    cell.font = cell_font
                    cell.border = thin_border
                    cell.alignment = center_align if isinstance(cell_val, (int, float)) else left_align

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or "")
                max_len = max(max_len, len(val_str))
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        # Sanitize filename
        safe_name = re.sub(r'[\\/*?:"<>|]', "", title).strip() or f"Export_{int(time.time())}"
        safe_name = safe_name[:40].strip()
        filepath = EXPORTS_DIR / f"{safe_name}_{int(time.time())}.xlsx"
        wb.save(filepath)
        if not _wait_for_file_ready(Path(filepath)):
            logger.warning(f"Файл Excel {filepath} не подтвердил сигнатуру ZIP/Office за отведенный таймаут")
        return filepath

    @staticmethod
    def create_word(
        title: str,
        content_markdown: str
    ) -> Path:
        """
        Parses Markdown formatted text and builds a Microsoft Word (.docx) document.
        Saved into C:/NeonRoute/exports/<filename>.docx.
        """
        doc = docx.Document()

        # Set page margins
        sections = doc.sections
        for section in sections:
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)
            section.left_margin = Inches(0.8)
            section.right_margin = Inches(0.8)

        # Document Title
        p_title = doc.add_paragraph()
        run_title = p_title.add_run(title)
        run_title.font.name = "Calibri"
        run_title.font.size = Pt(22)
        run_title.font.bold = True
        run_title.font.color.rgb = RGBColor(15, 23, 42)
        p_title.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_title.paragraph_format.space_after = Pt(14)

        # Parse lines
        lines = content_markdown.splitlines()
        in_table = False
        table_lines: List[str] = []

        for line in lines:
            stripped = line.strip()

            # Handle Markdown Table blocks
            if "|" in stripped and stripped.startswith("|") and stripped.endswith("|"):
                in_table = True
                table_lines.append(stripped)
                continue
            elif in_table:
                # Flush table
                FileGenerator._render_docx_table(doc, table_lines)
                table_lines = []
                in_table = False

            if not stripped:
                continue

            # Heading 1
            if stripped.startswith("# "):
                h = doc.add_heading(level=1)
                run = h.add_run(stripped[2:])
                run.font.name = "Calibri"
                run.font.size = Pt(16)
                run.font.bold = True
                run.font.color.rgb = RGBColor(30, 41, 59)
                h.paragraph_format.space_before = Pt(12)
                h.paragraph_format.space_after = Pt(4)

            # Heading 2
            elif stripped.startswith("## "):
                h = doc.add_heading(level=2)
                run = h.add_run(stripped[3:])
                run.font.name = "Calibri"
                run.font.size = Pt(13)
                run.font.bold = True
                run.font.color.rgb = RGBColor(51, 65, 85)
                h.paragraph_format.space_before = Pt(8)
                h.paragraph_format.space_after = Pt(2)

            # Bullet points
            elif stripped.startswith(("- ", "* ", "• ")):
                p = doc.add_paragraph(style='List Bullet')
                FileGenerator._format_runs(p, stripped[2:])
                p.paragraph_format.space_after = Pt(2)

            # Numbered list
            elif re.match(r'^\d+\.\s+', stripped):
                match = re.match(r'^\d+\.\s+', stripped)
                p = doc.add_paragraph(style='List Number')
                FileGenerator._format_runs(p, stripped[match.end():])
                p.paragraph_format.space_after = Pt(2)

            # Normal paragraph
            else:
                p = doc.add_paragraph()
                FileGenerator._format_runs(p, stripped)
                p.paragraph_format.space_after = Pt(6)

        if in_table and table_lines:
            FileGenerator._render_docx_table(doc, table_lines)

        safe_name = re.sub(r'[\\/*?:"<>|]', "", title).strip() or f"Doc_{int(time.time())}"
        safe_name = safe_name[:40].strip()
        filepath = EXPORTS_DIR / f"{safe_name}_{int(time.time())}.docx"
        doc.save(filepath)
        if not _wait_for_file_ready(Path(filepath)):
            logger.warning(f"Файл Word {filepath} не подтвердил сигнатуру ZIP/Office за отведенный таймаут")
        return filepath

    @staticmethod
    def _format_runs(paragraph, text: str):
        """Applies basic bold/italic inline parsing to docx paragraph."""
        parts = re.split(r'(\*\*.*?\*\*|\*.*?\*)', text)
        for part in parts:
            if not part:
                continue
            if part.startswith("**") and part.endswith("**"):
                run = paragraph.add_run(part[2:-2])
                run.bold = True
            elif part.startswith("*") and part.endswith("*"):
                run = paragraph.add_run(part[1:-1])
                run.italic = True
            else:
                paragraph.add_run(part)

    @staticmethod
    def _parse_table_data(text: str) -> List[List[str]]:
        """Extracts rows and columns from Markdown tables or delimited lines."""
        rows = []
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if "|" in s:
                # Markdown row e.g. | Col 1 | Col 2 |
                if set(s.replace("|", "").replace(":", "").replace("-", " ")) == {" "}:
                    # Table separator row like |---|---|
                    continue
                parts = [p.strip() for p in s.split("|")]
                if parts and parts[0] == "":
                    parts.pop(0)
                if parts and parts[-1] == "":
                    parts.pop()
                if parts:
                    rows.append(parts)
            elif "\t" in s:
                rows.append([p.strip() for p in s.split("\t")])
            elif ";" in s and not s.startswith("#"):
                rows.append([p.strip() for p in s.split(";")])

        return rows

    @staticmethod
    def _render_docx_table(doc, table_lines: List[str]):
        """Renders parsed markdown table inside docx."""
        rows = FileGenerator._parse_table_data("\n".join(table_lines))
        if not rows:
            return

        table = doc.add_table(rows=len(rows), cols=len(rows[0]))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        for r_idx, row in enumerate(rows):
            for c_idx, val in enumerate(row):
                if c_idx < len(table.columns):
                    cell = table.cell(r_idx, c_idx)
                    cell.text = val
                    if r_idx == 0:
                        # Header style
                        for p in cell.paragraphs:
                            for r in p.runs:
                                r.font.bold = True
                                r.font.color.rgb = RGBColor(15, 23, 42)
        doc.add_paragraph().paragraph_format.space_after = Pt(6)
