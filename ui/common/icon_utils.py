"""
Icon utilities for safe FluentIcon access and lightweight QR rendering in NeonRoute UI.
"""

from typing import List
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
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


# ─── Compact Pure-Python ISO/IEC 18004 QR Code Generator (Byte Mode, ECC-M) ───

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]

# (version, max_bytes_M, ec_per_block, blocks_group1, data_per_b1, blocks_group2, data_per_b2, alignment_centers)
_QR_SPECS_M = [
    (1, 14, 10, 1, 16, 0, 0, []),
    (2, 26, 16, 1, 28, 0, 0, [6, 18]),
    (3, 42, 26, 1, 44, 0, 0, [6, 22]),
    (4, 62, 18, 2, 32, 0, 0, [6, 26]),
    (5, 84, 24, 2, 43, 0, 0, [6, 30]),
    (6, 106, 16, 4, 27, 0, 0, [6, 34]),
]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_encode(data: List[int], ec_len: int) -> List[int]:
    gen = [1]
    for i in range(ec_len):
        nxt = [0] * (len(gen) + 1)
        factor = _GF_EXP[i]
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= _gf_mul(g, factor)
        gen = nxt

    res = list(data) + [0] * ec_len
    for i in range(len(data)):
        coef = res[i]
        if coef != 0:
            for j in range(1, len(gen)):
                res[i + j] ^= _gf_mul(gen[j], coef)
    return res[len(data):]


def _format_bits_m(mask: int) -> int:
    # ECC Level M = 0b00
    data = mask & 0x07
    rem = data << 10
    poly = 0x537
    for i in range(14, 9, -1):
        if rem & (1 << i):
            rem ^= poly << (i - 10)
    return ((data << 10) | rem) ^ 0x5412


def _mask_condition(mask: int, r: int, c: int) -> bool:
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask == 5:
        return ((r * c) % 2 + (r * c) % 3) == 0
    if mask == 6:
        return (((r * c) % 2 + (r * c) % 3) % 2) == 0
    return (((r + c) % 2 + (r * c) % 3) % 2) == 0


def _penalty_score(mat: List[List[bool]]) -> int:
    n = len(mat)
    score = 0
    # N1: runs of 5+ same color
    for r in range(n):
        run = 1
        for c in range(1, n):
            if mat[r][c] == mat[r][c - 1]:
                run += 1
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            else:
                run = 1
    for c in range(n):
        run = 1
        for r in range(1, n):
            if mat[r][c] == mat[r - 1][c]:
                run += 1
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            else:
                run = 1
    # N2: 2x2 blocks
    for r in range(n - 1):
        for c in range(n - 1):
            v = mat[r][c]
            if mat[r][c + 1] == v and mat[r + 1][c] == v and mat[r + 1][c + 1] == v:
                score += 3
    # N4: dark ratio balance
    dark = sum(1 for row in mat for cell in row if cell)
    pct = (dark * 100) / (n * n)
    score += int(abs(pct - 50) // 5) * 10
    return score


def encode_qr_matrix(text: str) -> List[List[bool]]:
    """Encodes a UTF-8 string (up to 106 bytes) into an ISO/IEC 18004 QR boolean matrix."""
    payload = (text or "").encode("utf-8")
    spec = None
    for row in _QR_SPECS_M:
        if len(payload) <= row[1]:
            spec = row
            break
    if spec is None:
        raise ValueError(f"QR payload too long ({len(payload)} bytes > 106 bytes)")

    version, _, ec_len, b1, d1, b2, d2, align_pts = spec
    total_data_cw = b1 * d1 + b2 * d2

    # Build bitstream (Mode=0100 Byte, length=8 bits)
    bits: List[int] = []

    def _push(val: int, width: int):
        for sh in range(width - 1, -1, -1):
            bits.append((val >> sh) & 1)

    _push(0b0100, 4)
    _push(len(payload), 8)
    for byte in payload:
        _push(byte, 8)
    term = min(4, total_data_cw * 8 - len(bits))
    _push(0, term)
    while len(bits) % 8 != 0:
        bits.append(0)

    data_cw = [0] * (len(bits) // 8)
    for idx, bit in enumerate(bits):
        data_cw[idx // 8] = (data_cw[idx // 8] << 1) | bit
    pad_bytes = (0xEC, 0x11)
    p_idx = 0
    while len(data_cw) < total_data_cw:
        data_cw.append(pad_bytes[p_idx % 2])
        p_idx += 1

    # Split into blocks and interleave data + ECC
    blocks_data: List[List[int]] = []
    blocks_ec: List[List[int]] = []
    pos = 0
    for count, d_len in ((b1, d1), (b2, d2)):
        for _ in range(count):
            blk = data_cw[pos:pos + d_len]
            pos += d_len
            blocks_data.append(blk)
            blocks_ec.append(_rs_encode(blk, ec_len))

    final_cw: List[int] = []
    for i in range(max(len(b) for b in blocks_data)):
        for blk in blocks_data:
            if i < len(blk):
                final_cw.append(blk[i])
    for i in range(ec_len):
        for ec in blocks_ec:
            final_cw.append(ec[i])

    all_bits: List[int] = []
    for cw in final_cw:
        for sh in range(7, -1, -1):
            all_bits.append((cw >> sh) & 1)

    size = 17 + 4 * version
    base = [[False] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def _set_func(r: int, c: int, val: bool):
        base[r][c] = val
        reserved[r][c] = True

    # Finder patterns + separators
    for fr, fc in ((0, 0), (0, size - 7), (size - 7, 0)):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = fr + dr, fc + dc
                if 0 <= rr < size and 0 <= cc < size:
                    in_outer = (0 <= dr <= 6 and 0 <= dc <= 6) and (dr in (0, 6) or dc in (0, 6))
                    in_inner = 2 <= dr <= 4 and 2 <= dc <= 4
                    _set_func(rr, cc, in_outer or in_inner)

    # Alignment patterns
    for ar in align_pts:
        for ac in align_pts:
            if reserved[ar][ac]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    is_dark = (dr in (-2, 2) or dc in (-2, 2) or (dr == 0 and dc == 0))
                    _set_func(ar + dr, ac + dc, is_dark)

    # Timing patterns & dark module
    for i in range(8, size - 8):
        _set_func(6, i, i % 2 == 0)
        _set_func(i, 6, i % 2 == 0)
    _set_func(4 * version + 9, 8, True)

    # Reserve format info strips
    for i in range(9):
        if not reserved[8][i]:
            reserved[8][i] = True
        if not reserved[i][8]:
            reserved[i][8] = True
    for i in range(size - 8, size):
        reserved[8][i] = True
        reserved[i][8] = True

    # Place data bits in zigzag
    b_idx = 0
    col = size - 1
    going_up = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if going_up else range(size)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if not reserved[r][c]:
                    if b_idx < len(all_bits):
                        base[r][c] = bool(all_bits[b_idx])
                        b_idx += 1
        col -= 2
        going_up = not going_up

    best_mat = None
    best_score = None
    for mask in range(8):
        cand = [list(row) for row in base]
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and _mask_condition(mask, r, c):
                    cand[r][c] = not cand[r][c]

        fmt = _format_bits_m(mask)
        for i in range(15):
            bit = bool((fmt >> i) & 1)
            # Copy 1: top-left (MSB i=14 at (8,0) .. LSB i=0 at (0,8))
            if i >= 9:
                cand[8][14 - i] = bit
            elif i == 8:
                cand[8][7] = bit
            elif i == 7:
                cand[8][8] = bit
            elif i == 6:
                cand[7][8] = bit
            else:
                cand[i][8] = bit

            # Copy 2: bottom-left (MSB i=14 at (size-1,8)) + top-right (LSB i=0 at (8,size-1))
            if i >= 8:
                cand[size - 15 + i][8] = bit
            else:
                cand[8][size - 1 - i] = bit

        sc = _penalty_score(cand)
        if best_score is None or sc < best_score:
            best_score = sc
            best_mat = cand

    return best_mat or base


def render_qr_pixmap(text: str, module_px: int = 7, quiet_modules: int = 3) -> QPixmap:
    """Renders a high-contrast QR code QPixmap ready for mobile camera scanning."""
    matrix = encode_qr_matrix(text)
    modules = len(matrix)
    total_px = (modules + 2 * quiet_modules) * module_px

    pix = QPixmap(total_px, total_px)
    pix.fill(QColor("#FFFFFF"))

    painter = QPainter(pix)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0B1120"))

    for r, row in enumerate(matrix):
        y = (r + quiet_modules) * module_px
        for c, is_dark in enumerate(row):
            if is_dark:
                x = (c + quiet_modules) * module_px
                painter.drawRect(x, y, module_px, module_px)

    painter.end()
    return pix
