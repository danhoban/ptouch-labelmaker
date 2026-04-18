# SPDX-License-Identifier: GPL-3.0-or-later
"""
Label image rendering: borders, icons, QR codes, and the two label renderers
(GUI-driven and Homebox webhook-driven).
"""

import io
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
import qrcode

try:
    import cairosvg  # type: ignore
except ImportError:
    cairosvg = None

from fonts import load_font, load_variant, DEFAULT_FONT_KEY, resolve_font_path

SUPPORTS_SVG = bool(cairosvg)

# ---------------------------------------------------------------------------
# Border styles
# ---------------------------------------------------------------------------

BORDER_STYLES: Dict[str, Dict] = {
    "none":   {"label": "None",        "type": "none"},
    "thin":   {"label": "Thin line",   "type": "solid",  "width": 2, "margin": 2},
    "thick":  {"label": "Thick line",  "type": "solid",  "width": 4, "margin": 3},
    "double": {"label": "Double line", "type": "double", "width": 1, "margin": 2, "gap": 3},
    "dashed": {"label": "Dashed line", "type": "dashed", "width": 2, "margin": 2, "dash": 6, "space": 4},
}
BORDER_DEFAULT = "none"


def get_border_options() -> List[Dict]:
    opts = [
        {"key": key, "label": meta["label"], "type": meta.get("type", "solid")}
        for key, meta in BORDER_STYLES.items()
    ]
    opts.sort(key=lambda item: (item["key"] != BORDER_DEFAULT, item["label"].lower()))
    return opts


def _apply_solid_border(draw: ImageDraw.ImageDraw, bbox: Tuple[int, int, int, int], width: int):
    left, top, right, bottom = bbox
    for offset in range(width):
        draw.rectangle((left + offset, top + offset, right - offset, bottom - offset), outline=0)


def _apply_double_border(draw: ImageDraw.ImageDraw, bbox: Tuple[int, int, int, int], width: int, gap: int):
    _apply_solid_border(draw, bbox, width)
    inner = (bbox[0] + gap, bbox[1] + gap, bbox[2] - gap, bbox[3] - gap)
    if inner[2] > inner[0] and inner[3] > inner[1]:
        _apply_solid_border(draw, inner, width)


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: Tuple[int, int],
    end: Tuple[int, int],
    dash: int,
    space: int,
    width: int,
):
    x0, y0 = start
    x1, y1 = end
    if y0 == y1:  # horizontal
        length = abs(x1 - x0)
        direction = 1 if x1 >= x0 else -1
        for offset in range(0, length + 1, dash + space):
            seg_end = x0 + direction * min(offset + dash, length)
            draw.line((x0 + direction * offset, y0, seg_end, y1), fill=0, width=width)
    else:  # vertical
        length = abs(y1 - y0)
        direction = 1 if y1 >= y0 else -1
        for offset in range(0, length + 1, dash + space):
            seg_end = y0 + direction * min(offset + dash, length)
            draw.line((x0, y0 + direction * offset, x1, seg_end), fill=0, width=width)


def _apply_dashed_border(
    draw: ImageDraw.ImageDraw,
    bbox: Tuple[int, int, int, int],
    width: int,
    dash: int,
    space: int,
):
    left, top, right, bottom = bbox
    _draw_dashed_line(draw, (left, top),   (right, top),    dash, space, width)
    _draw_dashed_line(draw, (right, top),  (right, bottom), dash, space, width)
    _draw_dashed_line(draw, (right, bottom), (left, bottom), dash, space, width)
    _draw_dashed_line(draw, (left, bottom), (left, top),   dash, space, width)


def apply_border(img: Image.Image, style_key: str) -> Image.Image:
    style = BORDER_STYLES.get(style_key, BORDER_STYLES[BORDER_DEFAULT])
    if style.get("type") == "none":
        return img
    draw = ImageDraw.Draw(img)
    width = max(1, int(style.get("width", 2)))
    margin = max(1, int(style.get("margin", width)))
    right = img.width - margin - 1
    bottom = img.height - margin - 1
    if right <= margin or bottom <= margin:
        return img
    bbox = (margin, margin, right, bottom)
    typ = style.get("type", "solid")
    if typ == "solid":
        _apply_solid_border(draw, bbox, width)
    elif typ == "double":
        gap = max(width + 1, int(style.get("gap", width + 2)))
        _apply_double_border(draw, bbox, width, gap)
    elif typ == "dashed":
        dash = max(1, int(style.get("dash", 6)))
        space = max(1, int(style.get("space", 4)))
        _apply_dashed_border(draw, bbox, width, dash, space)
    else:
        _apply_solid_border(draw, bbox, width)
    return img


# ---------------------------------------------------------------------------
# Icon resolution and browsing
# ---------------------------------------------------------------------------

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "icons")
ICON_ALLOWED_EXTS = {".png", ".svg", ".jpg", ".jpeg", ".bmp", ".gif"}
ICON_DEFAULT = ""
ICON_DIR_ABS = os.path.abspath(ICON_DIR)
os.makedirs(ICON_DIR_ABS, exist_ok=True)

ICON_DEFAULT_RATIO = 0.85
ICON_MIN_HEIGHT = 16
ICON_RESAMPLE = Image.NEAREST
QR_MIN_SIZE = 24
QR_DEFAULT_RATIO = 0.85

# ---------------------------------------------------------------------------
# Printer geometry
# ---------------------------------------------------------------------------

PRINTER_DPI = 180  # P-Touch standard print resolution


def mm_to_px(mm: float) -> int:
    """Convert millimetres to pixels at PRINTER_DPI."""
    return max(1, round(mm * PRINTER_DPI / 25.4))


def resolve_icon_path(
    rel_path: Optional[str], allow_directory: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    base = ICON_DIR_ABS
    if not os.path.isdir(base):
        return None, None
    raw = (rel_path or "").strip().strip("\\/")
    target = os.path.normpath(os.path.join(base, raw)) if raw else base
    if not (target == base or target.startswith(base + os.sep)):
        return None, None
    if not os.path.exists(target):
        return None, None
    if os.path.isdir(target):
        if not allow_directory:
            return None, None
        rel = os.path.relpath(target, base)
        return ("" if rel == "." else rel.replace("\\", "/")), target
    rel = os.path.relpath(target, base).replace("\\", "/")
    return rel, target


def build_icon_breadcrumbs(rel_path: Optional[str]) -> List[Dict[str, str]]:
    rel = (rel_path or "").strip()
    crumbs = [{"name": "Icons", "path": ""}]
    if not rel:
        return crumbs
    acc: List[str] = []
    for part in (p for p in rel.split("/") if p):
        acc.append(part)
        crumbs.append({"name": part, "path": "/".join(acc)})
    return crumbs


def compute_default_icon_height(max_height: int, padding: int = 12) -> int:
    available = max_height - 2 * padding
    if available <= 0:
        return ICON_MIN_HEIGHT
    return min(available, int(max(ICON_MIN_HEIGHT, max_height * ICON_DEFAULT_RATIO)))


def compute_default_qr_size(max_height: int, padding: int = 12) -> int:
    available = max_height - 2 * max(2, padding // 3)
    if available <= 0:
        return QR_MIN_SIZE
    return min(available, int(max(QR_MIN_SIZE, max_height * QR_DEFAULT_RATIO)))


def load_icon_image(
    icon_key: str, max_height: int, target_height: Optional[int] = None
) -> Optional[Image.Image]:
    if not icon_key or icon_key in ("none", ICON_DEFAULT):
        return None
    _, path = resolve_icon_path(icon_key, allow_directory=False)
    if not path:
        return None
    if target_height is not None and target_height <= 0:
        return None

    def prepare(icon: Image.Image) -> Image.Image:
        img = icon.convert("RGBA")
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        background.alpha_composite(img)
        img = background.convert("L")
        target = max(1, min(max_height, target_height)) if target_height else max_height
        if target and img.height > target:
            scale = target / img.height
            new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
            img = img.resize(new_size, ICON_RESAMPLE)
        return img

    try:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".svg":
            if cairosvg is None:
                return None
            png_bytes = cairosvg.svg2png(url=path, output_height=max_height or None)
            with Image.open(io.BytesIO(png_bytes)) as icon:
                return prepare(icon)
        with Image.open(path) as icon:
            return prepare(icon)
    except (OSError, ValueError, AttributeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Shared rendering primitives
# ---------------------------------------------------------------------------

def measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    if hasattr(draw, "textbbox"):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    return draw.textsize(text, font=font)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Formatted text: data structures and parser
# ---------------------------------------------------------------------------

@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False


@dataclass
class ParsedLine:
    spans: List[Span]
    size_delta: int = 0


_SIZE_PREFIX_RE = re.compile(r'^\[([+-]\d+)\]')


def _tokenize_spans(text: str) -> List[Span]:
    """Parse inline formatting markers into a list of Spans."""
    spans: List[Span] = []
    i = 0
    n = len(text)
    while i < n:
        matched = False
        for marker, attrs in (("**", {"bold": True}), ("__", {"underline": True}), ("_", {"italic": True})):
            ml = len(marker)
            if text[i:i + ml] != marker:
                continue
            close = text.find(marker, i + ml)
            if close > i + ml:
                inner = text[i + ml:close]
                if inner:
                    spans.append(Span(text=inner, **attrs))
                    i = close + ml
                    matched = True
                    break
        if not matched:
            # Find next marker boundary and emit plain text
            j = i + 1
            while j < n:
                if text[j:j+2] in ("**", "__") or text[j] == "_":
                    break
                j += 1
            chunk = text[i:j]
            if spans and not spans[-1].bold and not spans[-1].italic and not spans[-1].underline:
                spans[-1] = Span(text=spans[-1].text + chunk)
            else:
                spans.append(Span(text=chunk))
            i = j
    return spans or [Span(text="")]


def parse_formatted_text(text: str) -> List[ParsedLine]:
    parsed: List[ParsedLine] = []
    for raw in (text or "").splitlines() or [""]:
        line = raw.strip("\r")
        size_delta = 0
        m = _SIZE_PREFIX_RE.match(line)
        if m:
            size_delta = int(m.group(1))
            line = line[m.end():]
        if not line:
            parsed.append(ParsedLine(spans=[Span(text="")], size_delta=size_delta))
        else:
            parsed.append(ParsedLine(spans=_tokenize_spans(line), size_delta=size_delta))
    return parsed


def measure_parsed_line(
    draw: ImageDraw.ImageDraw,
    parsed_line: "ParsedLine",
    font_key: str,
    font_size: int,
) -> Tuple[int, int]:
    total_w, max_h = 0, 0
    for span in parsed_line.spans:
        f = load_variant(font_size, font_key, bold=span.bold, italic=span.italic)
        w, h = measure_text(draw, span.text or " ", f)
        total_w += w
        max_h = max(max_h, h)
    return total_w, max_h


def make_qr(data: str, box_size: int) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    scale = box_size / max(img.size)
    new_size = (max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale)))
    return img.resize(new_size, Image.NEAREST)


def clamp_icon_height(requested: Optional[int], max_height: int, padding: int) -> int:
    available = max_height - 2 * padding
    if available <= 0:
        return 0
    if available <= ICON_MIN_HEIGHT:
        return available
    if not requested or requested <= 0:
        return compute_default_icon_height(max_height, padding)
    return min(available, max(ICON_MIN_HEIGHT, requested))


def clamp_qr_size(requested: Optional[int], max_height: int, padding: int) -> int:
    available = max_height - 2 * max(2, padding // 3)
    if available <= 0:
        return QR_MIN_SIZE
    if available <= QR_MIN_SIZE:
        return available
    if not requested or requested <= 0:
        return compute_default_qr_size(max_height, padding)
    return min(available, max(QR_MIN_SIZE, requested))


# ---------------------------------------------------------------------------
# GUI label renderer
# ---------------------------------------------------------------------------

_DEFAULT_ELEMENT_ORDER: List[str] = ['icon', 'qr', 'text']


def render_label_png(
    text: str,
    url: Optional[str],
    max_height: int,
    font_size: int = 24,
    qr_size: int = 96,
    padding: int = 12,
    line_spacing: int = 4,
    font_key: str = DEFAULT_FONT_KEY,
    border_style: str = BORDER_DEFAULT,
    icon_key: str = ICON_DEFAULT,
    icon_size: Optional[int] = None,
    max_width: Optional[int] = None,
    element_order: Optional[List[str]] = None,
    text_align: str = "middle",
) -> Tuple[Image.Image, int]:
    _SS = 2  # supersample scale: render at 2× then downscale for crisp 1-bit output
    _out_height = max(24, max_height)

    # Scale all pixel dimensions up for supersampled rendering
    height = _out_height * _SS
    padding = padding * _SS
    line_spacing = line_spacing * _SS
    font_size = font_size * _SS
    qr_size = qr_size * _SS
    if icon_size is not None:
        icon_size = icon_size * _SS
    if max_width is not None:
        max_width = max_width * _SS

    qr_actual_size = clamp_qr_size(qr_size, height, padding)
    qr_img = make_qr(url.strip(), qr_actual_size) if url and url.strip() else None

    desired_icon_height = (
        clamp_icon_height(icon_size, height, padding)
        if icon_key and icon_key not in ("", ICON_DEFAULT)
        else 0
    )
    icon_img = (
        load_icon_image(icon_key, max_height=max(1, height - 2 * padding), target_height=desired_icon_height)
        if desired_icon_height
        else None
    )

    draw_tmp = ImageDraw.Draw(Image.new("L", (1, 1)))

    if max_width is not None:
        icon_w_for_wrap = icon_img.width if icon_img is not None else 0
        qr_w_for_wrap = qr_img.width if qr_img is not None else 0
        fixed_cols = padding  # leading padding
        if icon_w_for_wrap:
            fixed_cols += icon_w_for_wrap + padding
        if qr_w_for_wrap:
            fixed_cols += qr_w_for_wrap + padding
        fixed_cols += padding  # trailing padding
        wrap_limit = max(1, max_width - fixed_cols)
    else:
        wrap_limit = 9999

    # Parse formatted text and word-wrap each line
    parsed_lines = parse_formatted_text(text)
    final_lines: List[ParsedLine] = []
    for pl in parsed_lines:
        line_size = max(8 * _SS, font_size + pl.size_delta * _SS)
        line_font = load_font(line_size, font_key=font_key)
        plain = "".join(s.text for s in pl.spans)
        if not plain:
            final_lines.append(pl)
            continue
        words = plain.split(" ")
        cur: List[str] = []
        sub_lines: List[str] = []
        for w in words:
            test = (" ".join(cur + [w])).strip()
            if measure_text(draw_tmp, test, line_font)[0] > wrap_limit:
                sub_lines.append(" ".join(cur))
                cur = [w]
            else:
                cur.append(w)
        sub_lines.append(" ".join(cur))
        if len(sub_lines) == 1:
            final_lines.append(pl)
        else:
            for sub in sub_lines:
                final_lines.append(ParsedLine(spans=[Span(text=sub)], size_delta=pl.size_delta))

    def compute_layout(base_size: int):
        widths, heights = [], []
        for pl in final_lines:
            ls = max(8 * _SS, base_size + pl.size_delta * _SS)
            w, h = measure_parsed_line(draw_tmp, pl, font_key, ls)
            widths.append(w)
            heights.append(h)
        return max(widths, default=0), heights, sum(heights) + line_spacing * (len(heights) - 1)

    text_width, line_heights, total_text_height = compute_layout(font_size)
    while total_text_height + 2 * padding > height and font_size > 8 * _SS:
        font_size -= 1
        text_width, line_heights, total_text_height = compute_layout(font_size)

    icon_w = icon_img.width if icon_img is not None else 0
    qr_w = qr_img.width if qr_img is not None else 0

    # Resolve element order; fill in any missing elements at the end
    _order: List[str] = list(element_order) if element_order else list(_DEFAULT_ELEMENT_ORDER)
    _order = [e for e in _order if e in {'icon', 'qr', 'text'}]
    for _e in _DEFAULT_ELEMENT_ORDER:
        if _e not in _order:
            _order.append(_e)

    _elem_w = {'icon': icon_w, 'qr': qr_w, 'text': text_width}
    _present = [e for e in _order if _elem_w[e] > 0]

    width = padding
    for _i, _e in enumerate(_present):
        width += _elem_w[_e]
        if _i < len(_present) - 1:
            width += padding
    width = max(1, width + padding)
    if max_width is not None:
        width = max(width, max_width)

    img = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)
    x = padding

    _last_e = _present[-1] if _present else None

    for _i, _e in enumerate(_present):
        # Right-align the last element (QR or icon) when a max_width is set
        is_last = _e == _last_e
        if is_last and max_width is not None and _e in ('qr', 'icon'):
            draw_x = width - padding - _elem_w[_e]
        else:
            draw_x = x

        if _e == 'icon' and icon_img is not None:
            img.paste(icon_img, (draw_x, (height - icon_img.height) // 2))
        elif _e == 'qr' and qr_img is not None:
            img.paste(qr_img, (draw_x, (height - qr_img.height) // 2))
        elif _e == 'text':
            # Height of the tallest co-present element (QR or icon) for edge alignment
            ref_h = max(
                (qr_img.height  if qr_img  is not None and 'qr'   in _present else 0),
                (icon_img.height if icon_img is not None and 'icon' in _present else 0),
            )
            if text_align == 'top':
                y = (height - ref_h) // 2 if ref_h else padding
            elif text_align == 'bottom':
                y = ((height + ref_h) // 2 - total_text_height) if ref_h else (height - total_text_height - padding)
            else:
                y = (height - total_text_height) // 2
            y = max(padding, min(y, height - total_text_height - padding))
            for _j, pl in enumerate(final_lines):
                line_size = max(8 * _SS, font_size + pl.size_delta * _SS)
                x_cursor = draw_x
                for span in pl.spans:
                    if not span.text:
                        continue
                    sf = load_variant(line_size, font_key, bold=span.bold, italic=span.italic)
                    sw, _ = measure_text(draw, span.text, sf)
                    draw.text((x_cursor, y), span.text, font=sf, fill=0)
                    if span.underline:
                        ul_bottom = draw.textbbox((0, 0), span.text, font=sf)[3]
                        uy = y + ul_bottom + _SS
                        draw.rectangle([x_cursor, uy, x_cursor + sw - 1, uy + _SS + 1], fill=0)
                    x_cursor += sw
                y += line_heights[_j] + line_spacing
        x += _elem_w[_e] + (padding if _i < len(_present) - 1 else 0)

    img = apply_border(img, border_style)
    # Downscale from 2× to final size, then threshold to crisp 1-bit
    out_w = max(1, img.width // _SS)
    img = img.resize((out_w, _out_height), Image.LANCZOS)
    img = img.point(lambda v: 0 if v < 180 else 255)
    return img.convert('1', dither=Image.NONE), font_size // _SS


# ---------------------------------------------------------------------------
# Homebox label renderer
# ---------------------------------------------------------------------------
# Renders a richer layout with separate title / description / additional-info
# sections, each in a distinct font size.  Intended for the /api/homebox/print
# webhook endpoint rather than the interactive GUI.

_HOMEBOX_TITLE_FONT_PATHS = [
    "/usr/share/fonts/truetype/msttcorefonts/Arial_Black.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Verdana_Bold.ttf",
]
_HOMEBOX_BODY_FONT_PATHS = [
    "/usr/share/fonts/truetype/msttcorefonts/Verdana_Bold.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
]
_HOMEBOX_PADDING = 10
_HOMEBOX_MAX_WIDTH = 500
_HOMEBOX_TITLE_SIZE = 36
_HOMEBOX_DESC_SIZE = 22
_HOMEBOX_INFO_SIZE = 18
_HOMEBOX_TITLE_BOTTOM_PAD = 20
_HOMEBOX_DESC_BOTTOM_PAD = 15
_HOMEBOX_DESC_LINE_GAP = 3
_HOMEBOX_DESC_MAX_LINES = 2


def _load_font_from_paths(paths: List[str], size: int) -> ImageFont.ImageFont:
    """Try each path in order; fall back to PIL default if none exist."""
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def render_homebox_label(
    url: str,
    title: str,
    description: str,
    additional_info: str,
    max_height: int = 128,
) -> Image.Image:
    """
    Render a Homebox-style label: QR code on the left, structured text on the
    right (bold title, word-wrapped description, smaller additional info line).
    Returns a 1-bit PIL image ready to send to ptouch-print.
    """
    height = max(24, max_height)

    title_font = _load_font_from_paths(_HOMEBOX_TITLE_FONT_PATHS, _HOMEBOX_TITLE_SIZE)
    desc_font = _load_font_from_paths(_HOMEBOX_BODY_FONT_PATHS, _HOMEBOX_DESC_SIZE)
    info_font = _load_font_from_paths(_HOMEBOX_BODY_FONT_PATHS, _HOMEBOX_INFO_SIZE)

    # QR code fills the full label height
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=0,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("L")
    qr_img = qr_img.resize((height, height), resample=Image.NEAREST)

    # ---- text measurement helpers (closures over a dummy draw surface) ----

    _dummy = Image.new("L", (10, 10), 255)
    _draw = ImageDraw.Draw(_dummy)

    def _tsize(text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
        if not text:
            return (0, 0)
        bbox = _draw.textbbox((0, 0), text, font=font)
        return (bbox[2] - bbox[0], bbox[3] - bbox[1])

    def _clip(text: str, font: ImageFont.ImageFont, max_w: int) -> Tuple[str, int, int]:
        """Return (clipped_text, width, height), truncating with '…' if needed."""
        w, h = _tsize(text, font)
        if w <= max_w:
            return text, w, h
        lo, hi, best = 0, len(text), ""
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = text[:mid] + "\u2026"
            if _tsize(cand, font)[0] <= max_w:
                best, lo = cand, mid + 1
            else:
                hi = mid - 1
        best = best or "\u2026"
        w2, h2 = _tsize(best, font)
        return best, w2, h2

    def _wrap(
        text: str, font: ImageFont.ImageFont, max_w: int, max_lines: int = 2
    ) -> Tuple[List[str], int, int]:
        """Word-wrap *text* into at most *max_lines* lines; truncates with '…'."""
        if not text:
            return [], 0, 0
        words = text.split()
        lines: List[str] = []
        cur = ""
        i = 0
        while i < len(words) and len(lines) < max_lines:
            word = words[i]
            candidate = (cur + " " + word).lstrip() if cur else word
            if _tsize(candidate, font)[0] <= max_w:
                cur, i = candidate, i + 1
            else:
                if not cur:
                    # Single word wider than available space — break it character by character
                    seg = ""
                    for ch in word:
                        if _tsize(seg + ch, font)[0] <= max_w:
                            seg += ch
                        else:
                            break
                    seg = seg or word[0]
                    lines.append(seg)
                    rem = word[len(seg):]
                    if rem:
                        words[i] = rem
                    else:
                        i += 1
                else:
                    lines.append(cur)
                    cur = ""
        if len(lines) < max_lines and cur:
            lines.append(cur)
        # If there are still remaining words, mark the last line with ellipsis
        if i < len(words) and lines:
            last, _, _ = _clip(lines[-1] + "\u2026", font, max_w)
            lines[-1] = last

        max_line_w = max((_tsize(ln, font)[0] for ln in lines), default=0)
        total_h = sum(_tsize(ln, font)[1] for ln in lines) + _HOMEBOX_DESC_LINE_GAP * (len(lines) - 1)
        return lines, max_line_w, total_h

    # ---- measure each text section ----

    desc_first_line = description.splitlines()[0] if description else ""
    has_text = any([title, desc_first_line, additional_info])
    gap = _HOMEBOX_PADDING if has_text else 0
    avail_w = max(0, _HOMEBOX_MAX_WIDTH - height - gap)

    title_r,   title_w,  title_h  = _clip(title,          title_font, avail_w) if title          else ("", 0, 0)
    desc_lines, desc_w,  desc_h   = _wrap(desc_first_line, desc_font,  avail_w)
    info_r,    info_w,   info_h   = _clip(additional_info, info_font,  avail_w) if additional_info else ("", 0, 0)

    text_w = max(title_w, desc_w, info_w)
    width = min(height + (gap if text_w > 0 else 0) + text_w, _HOMEBOX_MAX_WIDTH)

    # ---- compose image ----

    img = Image.new("L", (max(1, width), height), 255)
    d = ImageDraw.Draw(img)
    img.paste(qr_img, (0, 0))

    tx = height + gap
    cy = 0

    if title_r:
        d.text((tx, cy), title_r, font=title_font, fill=0)
        cy += title_h + _HOMEBOX_TITLE_BOTTOM_PAD

    for idx, ln in enumerate(desc_lines):
        d.text((tx, cy), ln, font=desc_font, fill=0)
        cy += _tsize(ln, desc_font)[1]
        cy += _HOMEBOX_DESC_LINE_GAP if idx < len(desc_lines) - 1 else _HOMEBOX_DESC_BOTTOM_PAD

    if info_r:
        d.text((tx, cy), info_r, font=info_font, fill=0)

    return img.convert('1', dither=Image.NONE)
