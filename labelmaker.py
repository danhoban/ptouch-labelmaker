#!/usr/bin/env python3
"""
Minimal Flask web app to design + print black/white PNG labels for Brother P‑Touch via
/opt/ptouch-print/build/ptouch-print.

Now includes improved error handling for printer error codes:
- 0000 = OK
- 1000 = Door open
- 0001 = No tape loaded
More can easily be added to ERROR_CODES.
"""

import io
import os
import json
import re
import time
import uuid
import functools
import subprocess
import urllib.request
import urllib.parse
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from flask import Flask, render_template, request, jsonify, send_file, url_for
from PIL import Image, ImageDraw, ImageFont
import qrcode

try:
    import cairosvg  # type: ignore
except ImportError:
    cairosvg = None

PT_CMD = "/opt/ptouch-print/build/ptouch-print"
STATIC_DIR = os.path.join("/tmp", "ptouch_web")
os.makedirs(STATIC_DIR, exist_ok=True)

app = Flask(__name__)

PRINTER_INFO_RE = {
    "model": re.compile(r"^(?P<model>.*) found on USB"),
    "max_printer": re.compile(r"maximum printing width for this printer is (\d+)px"),
    "max_tape": re.compile(r"maximum printing width for this tape is (\d+)px"),
    "media_type": re.compile(r"media type = (\S+)"),
    "media_width": re.compile(r"media width = (.+)$"),
    "tape_color": re.compile(r"tape color = (.+)$"),
    "text_color": re.compile(r"text color = (.+)$"),
    "error": re.compile(r"error = (\S+)")
}

def _load_default_error_codes() -> Dict[str, str]:
    return {
        "0000": "OK",
        "1000": "Printer door open",
        "0001": "No tape loaded",
    }


ERROR_CODES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error_codes.json")

BORDER_STYLES = {
    "none": {"label": "None", "type": "none"},
    "thin": {"label": "Thin line", "type": "solid", "width": 2, "margin": 2},
    "thick": {"label": "Thick line", "type": "solid", "width": 4, "margin": 3},
    "double": {"label": "Double line", "type": "double", "width": 1, "margin": 2, "gap": 3},
    "dashed": {"label": "Dashed line", "type": "dashed", "width": 2, "margin": 2, "dash": 6, "space": 4},
}
BORDER_DEFAULT = "none"

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

@dataclass
class PrinterInfo:
    available: bool
    raw: str
    model: Optional[str] = None
    max_printer_px: Optional[int] = None
    max_tape_px: Optional[int] = None
    media_type: Optional[str] = None
    media_width: Optional[str] = None
    tape_color: Optional[str] = None
    text_color: Optional[str] = None
    error_code: Optional[str] = None

    @property
    def max_height_px(self) -> Optional[int]:
        return self.max_tape_px or self.max_printer_px

    @property
    def error_message(self) -> Optional[str]:
        if not self.error_code:
            return None
        return ERROR_CODES.get(self.error_code, f"Unknown error ({self.error_code})")

    @property
    def has_error(self) -> bool:
        return self.error_code not in (None, "0000")


def run_cmd(args: list, timeout: int = 8) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, text=True)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"Command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out"


def get_printer_info() -> PrinterInfo:
    code, out, err = run_cmd([PT_CMD, "--info"])
    if "No P-Touch printer found" in out or code not in (0,):
        return PrinterInfo(available=False, raw=(out or err or f"exit {code}"))

    info = PrinterInfo(available=True, raw=out)
    for line in out.splitlines():
        for key, regex in PRINTER_INFO_RE.items():
            m = regex.search(line)
            if m:
                val = m.group(1) if m.groups() else m.groupdict().get("model")
                if key == "model":
                    info.model = m.group("model")
                elif key == "max_printer":
                    info.max_printer_px = int(m.group(1))
                elif key == "max_tape":
                    info.max_tape_px = int(m.group(1))
                elif key == "media_type":
                    info.media_type = val
                elif key == "media_width":
                    info.media_width = val
                elif key == "tape_color":
                    info.tape_color = val
                elif key == "text_color":
                    info.text_color = val
                elif key == "error":
                    info.error_code = val
    return info


def load_error_codes(path: str = ERROR_CODES_PATH) -> Dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("error_codes data must be a mapping")
        normalized = {}
        for key, val in data.items():
            key_str = str(key)
            val_str = str(val) if val is not None else ""
            normalized[key_str] = val_str
        return normalized or _load_default_error_codes()
    except (OSError, ValueError, json.JSONDecodeError):
        return _load_default_error_codes()


ERROR_CODES = load_error_codes()


class FontCatalog:
    DEFAULT_FONT_KEY = "auto"
    DEFAULT_FONT_PATHS = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    DEFAULT_FONT_LABEL = "Automatic Sans (default)"

    FONT_SCAN_DIRS = [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.local/share/fonts"),
        os.path.expanduser("~/.fonts"),
    ]

    DISCOVERY_MAX = 200
    STYLE_PREF_WORDS = ("regular", "book", "normal", "plain", "roman")

    def __init__(self):
        self._library = self._build_font_library()

    @staticmethod
    def _slugify(name: str) -> str:
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        return slug or "font"

    @classmethod
    def _style_rank(cls, style: Optional[str]) -> int:
        if not style:
            return 0
        style_lower = style.strip().lower()
        if not style_lower:
            return 0
        for idx, word in enumerate(cls.STYLE_PREF_WORDS):
            if style_lower == word or word in style_lower:
                return idx
        if "medium" in style_lower:
            return len(cls.STYLE_PREF_WORDS)
        if "light" in style_lower:
            return len(cls.STYLE_PREF_WORDS) + 1
        if "semi" in style_lower or "demi" in style_lower:
            return len(cls.STYLE_PREF_WORDS) + 2
        if "bold" in style_lower:
            return len(cls.STYLE_PREF_WORDS) + 3
        if "italic" in style_lower or "oblique" in style_lower:
            return len(cls.STYLE_PREF_WORDS) + 4
        return len(cls.STYLE_PREF_WORDS) + 5

    @classmethod
    def _label_from_metadata(cls, family: str, style: Optional[str]) -> str:
        if style:
            style_lower = style.lower()
            if any(word in style_lower for word in cls.STYLE_PREF_WORDS):
                return family
            return f"{family} {style}"
        return family

    @classmethod
    def _extract_family_style(cls, path: str) -> Optional[Tuple[str, Optional[str]]]:
        try:
            font = ImageFont.truetype(path, size=24)
        except OSError:
            return None
        family, style = font.getname()
        family = (family or "").strip()
        style = (style or "").strip() or None
        if not family:
            return None
        return family, style

    @classmethod
    @functools.lru_cache(maxsize=1)
    def _discover_system_fonts(cls) -> List[Dict[str, str]]:
        best_by_family: Dict[str, Dict[str, object]] = {}

        def consider_font(family: str, style: Optional[str], path: str):
            if not path or not os.path.exists(path):
                return
            ext = os.path.splitext(path)[1].lower()
            if ext not in (".ttf", ".otf"):
                return
            family_name = (family or "").strip()
            if not family_name:
                family_name = os.path.splitext(os.path.basename(path))[0]
            family_key = family_name.lower()
            style_clean = (style or "").strip() or None
            rank = cls._style_rank(style_clean)
            label = cls._label_from_metadata(family_name, style_clean)
            entry = best_by_family.get(family_key)
            if entry is None or rank < entry["rank"]:
                best_by_family[family_key] = {
                    "family": family_name,
                    "label": label,
                    "path": path,
                    "rank": rank,
                }

        code, out, _ = run_cmd(["fc-list", "--format=%{file}\\t%{family}\\t%{style}\\n"], timeout=6)
        if code == 0 and out:
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                path = parts[0].strip()
                family = parts[1].split(",")[0].strip()
                style = parts[2].split(",")[0].strip()
                consider_font(family, style, path)

        if not best_by_family:
            for directory in cls.FONT_SCAN_DIRS:
                if not directory or not os.path.isdir(directory):
                    continue
                for root, _, files in os.walk(directory):
                    for filename in files:
                        path = os.path.join(root, filename)
                        metadata = cls._extract_family_style(path)
                        if not metadata:
                            continue
                        family, style = metadata
                        consider_font(family, style, path)

        discovered: List[Dict[str, str]] = []
        existing_keys = set()
        for entry in sorted(best_by_family.values(), key=lambda val: val["label"].lower()):
            key_base = cls._slugify(entry["label"])
            key = key_base
            counter = 2
            while key in existing_keys:
                key = f"{key_base}-{counter}"
                counter += 1
            existing_keys.add(key)
            discovered.append({"key": key, "label": entry["label"], "path": entry["path"]})
            if len(discovered) >= cls.DISCOVERY_MAX:
                break
        return discovered

    def _build_font_library(self) -> Dict[str, Dict[str, List[str]]]:
        library: Dict[str, Dict[str, List[str]]] = {
            self.DEFAULT_FONT_KEY: {
                "label": self.DEFAULT_FONT_LABEL,
                "paths": self.DEFAULT_FONT_PATHS.copy(),
            }
        }
        for entry in self._discover_system_fonts():
            key = entry["key"]
            if key in library:
                continue
            library[key] = {
                "label": entry["label"],
                "paths": [entry["path"]],
            }
        return library

    @property
    def library(self) -> Dict[str, Dict[str, List[str]]]:
        return self._library

    def resolve_font_path(self, font_key: str) -> Optional[str]:
        entry = self._library.get(font_key)
        if not entry:
            return None
        for path in entry["paths"]:
            if os.path.exists(path):
                return path
        return None

    def load_font(self, size: int, font_key: str) -> ImageFont.ImageFont:
        requested_key = font_key if font_key in self._library else self.DEFAULT_FONT_KEY
        path = self.resolve_font_path(requested_key)
        if not path and requested_key != self.DEFAULT_FONT_KEY:
            path = self.resolve_font_path(self.DEFAULT_FONT_KEY)
        if not path:
            for fallback_key in self._library:
                path = self.resolve_font_path(fallback_key)
                if path:
                    break
        if path:
            return ImageFont.truetype(path, size=size)
        return ImageFont.load_default()

    def options(self) -> List[Dict[str, object]]:
        options = []
        for key, meta in self._library.items():
            available = bool(self.resolve_font_path(key))
            if key == self.DEFAULT_FONT_KEY:
                available = True
            options.append({
                "key": key,
                "label": meta["label"],
                "available": available,
            })
        options.sort(key=lambda item: (item["key"] != self.DEFAULT_FONT_KEY, item["label"].lower()))
        return options


FONT_CATALOG = FontCatalog()
DEFAULT_FONT_KEY = FontCatalog.DEFAULT_FONT_KEY
FONT_LIBRARY = FONT_CATALOG.library


def resolve_font_path(font_key: str) -> Optional[str]:
    return FONT_CATALOG.resolve_font_path(font_key)


def load_font(size: int, font_key: str = DEFAULT_FONT_KEY) -> ImageFont.ImageFont:
    return FONT_CATALOG.load_font(size, font_key)


def get_font_options() -> List[Dict[str, object]]:
    return FONT_CATALOG.options()


def get_border_options() -> List[Dict[str, object]]:
    options = []
    for key, meta in BORDER_STYLES.items():
        options.append({
            "key": key,
            "label": meta["label"],
            "type": meta.get("type", "solid"),
        })
    options.sort(key=lambda item: (item["key"] != BORDER_DEFAULT, item["label"].lower()))
    return options


def _apply_solid_border(draw: ImageDraw.ImageDraw, bbox: Tuple[int, int, int, int], width: int):
    left, top, right, bottom = bbox
    for offset in range(width):
        draw.rectangle((left + offset, top + offset, right - offset, bottom - offset), outline=0)


def _apply_double_border(draw: ImageDraw.ImageDraw, bbox: Tuple[int, int, int, int], width: int, gap: int):
    _apply_solid_border(draw, bbox, width)
    inner_bbox = (
        bbox[0] + gap,
        bbox[1] + gap,
        bbox[2] - gap,
        bbox[3] - gap,
    )
    if inner_bbox[2] > inner_bbox[0] and inner_bbox[3] > inner_bbox[1]:
        _apply_solid_border(draw, inner_bbox, width)


def _draw_dashed_line(draw: ImageDraw.ImageDraw, start: Tuple[int, int], end: Tuple[int, int], dash: int, space: int, width: int):
    x0, y0 = start
    x1, y1 = end
    is_horizontal = y0 == y1
    if is_horizontal:
        length = abs(x1 - x0)
        step = dash + space
        direction = 1 if x1 >= x0 else -1
        for offset in range(0, length + 1, step):
            seg_start = x0 + direction * offset
            seg_end = x0 + direction * min(offset + dash, length)
            draw.line((seg_start, y0, seg_end, y1), fill=0, width=width)
    else:
        length = abs(y1 - y0)
        step = dash + space
        direction = 1 if y1 >= y0 else -1
        for offset in range(0, length + 1, step):
            seg_start = y0 + direction * offset
            seg_end = y0 + direction * min(offset + dash, length)
            draw.line((x0, seg_start, x1, seg_end), fill=0, width=width)


def _apply_dashed_border(draw: ImageDraw.ImageDraw, bbox: Tuple[int, int, int, int], width: int, dash: int, space: int):
    left, top, right, bottom = bbox
    _draw_dashed_line(draw, (left, top), (right, top), dash, space, width)
    _draw_dashed_line(draw, (right, top), (right, bottom), dash, space, width)
    _draw_dashed_line(draw, (right, bottom), (left, bottom), dash, space, width)
    _draw_dashed_line(draw, (left, bottom), (left, top), dash, space, width)


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


def resolve_icon_path(rel_path: Optional[str], allow_directory: bool = False) -> Tuple[Optional[str], Optional[str]]:
    base = ICON_DIR_ABS
    if not os.path.isdir(base):
        return None, None
    raw = (rel_path or "").strip()
    raw = raw.strip("\\/")  # normalize user input
    target = os.path.normpath(os.path.join(base, raw)) if raw else base
    if not (target == base or target.startswith(base + os.sep)):
        return None, None
    if not os.path.exists(target):
        return None, None
    if os.path.isdir(target):
        if not allow_directory:
            return None, None
        rel = os.path.relpath(target, base)
        if rel == ".":
            rel = ""
        return rel.replace("\\", "/"), target
    rel = os.path.relpath(target, base).replace("\\", "/")
    return rel, target


def build_icon_breadcrumbs(rel_path: Optional[str]) -> List[Dict[str, str]]:
    rel = (rel_path or "").strip()
    crumbs = [{"name": "Icons", "path": ""}]
    if not rel:
        return crumbs
    parts = [part for part in rel.split("/") if part]
    acc: List[str] = []
    for part in parts:
        acc.append(part)
        crumbs.append({"name": part, "path": "/".join(acc)})
    return crumbs


def compute_default_icon_height(max_height: int, padding: int = 12) -> int:
    available = max_height - 2 * padding
    if available <= 0:
        return ICON_MIN_HEIGHT
    target = max_height * ICON_DEFAULT_RATIO
    suggested = int(max(ICON_MIN_HEIGHT, target))
    return min(available, suggested)


def compute_default_qr_size(max_height: int, padding: int = 12) -> int:
    available = max_height - 2 * max(2, padding // 3)
    if available <= 0:
        return QR_MIN_SIZE
    target = max_height * QR_DEFAULT_RATIO
    suggested = int(max(QR_MIN_SIZE, target))
    return min(available, suggested)


def load_icon_image(icon_key: str, max_height: int, target_height: Optional[int] = None) -> Optional[Image.Image]:
    if not icon_key or icon_key in ("none", ICON_DEFAULT):
        return None
    rel, path = resolve_icon_path(icon_key, allow_directory=False)
    if not path:
        return None
    if target_height is not None and target_height <= 0:
        return None
    def prepare_icon(icon: Image.Image) -> Image.Image:
        img = icon.convert("RGBA")
        if img.mode == "RGBA":
            background = Image.new("RGBA", img.size, (255, 255, 255, 255))
            background.alpha_composite(img)
            img = background
        img = img.convert("L")
        target = max_height
        if target_height:
            target = max(1, min(max_height, target_height))
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
            output_height = max_height if max_height > 0 else None
            png_bytes = cairosvg.svg2png(url=path, output_height=output_height)
            with Image.open(io.BytesIO(png_bytes)) as icon:
                return prepare_icon(icon)
        with Image.open(path) as icon:
            return prepare_icon(icon)
    except (OSError, ValueError):
        return None
    except OSError:
        return None
    except (AttributeError, TypeError):
        return None


def measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    if hasattr(draw, "textbbox"):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    return draw.textsize(text, font=font)


def make_qr(data: str, box_size: int) -> Image.Image:
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=2)
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
    requested = max(ICON_MIN_HEIGHT, requested)
    return min(available, requested)


def clamp_qr_size(requested: Optional[int], max_height: int, padding: int) -> int:
    available = max_height - 2 * max(2, padding // 3)
    if available <= 0:
        return QR_MIN_SIZE
    if available <= QR_MIN_SIZE:
        return available
    if not requested or requested <= 0:
        return compute_default_qr_size(max_height, padding)
    requested = max(QR_MIN_SIZE, requested)
    return min(available, requested)


def render_label_png(text: str, url: Optional[str], max_height: int, font_size: int = 24,
                      qr_size: int = 96, padding: int = 12, line_spacing: int = 4,
                      font_key: str = DEFAULT_FONT_KEY, border_style: str = BORDER_DEFAULT,
                      icon_key: str = ICON_DEFAULT, icon_size: Optional[int] = None) -> Image.Image:
    height = max(24, max_height)
    qr_actual_size = clamp_qr_size(qr_size, height, padding)
    qr_img = None
    if url and url.strip():
        qr_img = make_qr(url.strip(), qr_actual_size)

    desired_icon_height = clamp_icon_height(icon_size, height, padding) if icon_key and icon_key not in ("", ICON_DEFAULT) else 0
    available_icon_height = max(1, height - 2 * padding)
    icon_img = load_icon_image(icon_key, max_height=available_icon_height, target_height=desired_icon_height) if desired_icon_height else None

    draw_tmp = ImageDraw.Draw(Image.new("L", (1, 1)))

    font = load_font(font_size, font_key=font_key)
    lines = []
    for raw_line in (text or "").splitlines() or [""]:
        line = raw_line.strip("\r")
        if not line:
            lines.append("")
            continue
        target_px = 9999
        words = line.split(" ")
        cur = []
        for w in words:
            test = (" ".join(cur + [w])).strip()
            w_px, _ = measure_text(draw_tmp, test, font)
            if w_px > target_px:
                lines.append(" ".join(cur))
                cur = [w]
            else:
                cur.append(w)
        lines.append(" ".join(cur))

    def compute_layout(current_font: ImageFont.ImageFont):
        line_heights_local = []
        text_width_local = 0
        for ln in lines:
            w, h = measure_text(draw_tmp, ln if ln else " ", current_font)
            text_width_local = max(text_width_local, w)
            line_heights_local.append(h)
        total_height_local = sum(line_heights_local) + line_spacing * (len(line_heights_local) - 1)
        return text_width_local, line_heights_local, total_height_local

    text_width, line_heights, total_text_height = compute_layout(font)

    while total_text_height + 2 * padding > height and font_size > 8:
        font_size -= 1
        font = load_font(font_size, font_key=font_key)
        text_width, line_heights, total_text_height = compute_layout(font)

    icon_w = icon_img.width if icon_img is not None else 0
    qr_w = qr_img.width if qr_img is not None else 0
    width = padding
    if icon_w:
        width += icon_w
        if qr_w:
            width += padding
        elif text_width > 0:
            width += padding
    if qr_w:
        width += qr_w
        if text_width > 0:
            width += padding
    if text_width > 0:
        width += text_width
    width += padding
    width = max(1, width)

    img = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)

    x = padding
    if icon_img is not None:
        iy = (height - icon_img.height) // 2
        img.paste(icon_img, (x, iy))
        x += icon_img.width
        if qr_img is not None:
            x += padding
        elif text_width > 0:
            x += padding

    if qr_img is not None:
        qy = (height - qr_img.height) // 2
        img.paste(qr_img, (x, qy))
        x += qr_img.width
        if text_width > 0:
            x += padding

    y = (height - total_text_height) // 2
    for i, ln in enumerate(lines):
        draw.text((x, y), ln, font=font, fill=0)
        y += line_heights[i] + line_spacing

    img = apply_border(img, border_style)
    return img.convert('1', dither=Image.NONE)


@app.route('/')
def index():
    info = get_printer_info()
    max_h = info.max_height_px or 128
    return render_template(
        "index.html",
        info=info,
        ts=int(time.time()),
        fonts=get_font_options(),
        default_font=DEFAULT_FONT_KEY,
        border_styles=get_border_options(),
        default_border=BORDER_DEFAULT,
        default_icon=ICON_DEFAULT,
        icon_min_height=ICON_MIN_HEIGHT,
        qr_min_size=QR_MIN_SIZE,
        supports_svg=bool(cairosvg),
    )


@app.route('/api/printer_status')
def api_printer_status():
    info = get_printer_info()
    payload = {
        "available": info.available,
        "raw": info.raw,
        "model": info.model,
        "max_printer_px": info.max_printer_px,
        "max_tape_px": info.max_tape_px,
        "media_type": info.media_type,
        "media_width": info.media_width,
        "tape_color": info.tape_color,
        "text_color": info.text_color,
        "error_code": info.error_code,
        "error_message": info.error_message,
        "has_error": info.has_error,
    }
    return jsonify(payload)


@app.route('/api/icons')
def api_icons():
    path = request.args.get('path', '')
    rel_path, abs_path = resolve_icon_path(path, allow_directory=True)
    if abs_path is None:
        return jsonify({"error": "Path not found"}), 404

    dirs: List[Dict[str, str]] = []
    icons: List[Dict[str, str]] = []
    try:
        entries = sorted(os.listdir(abs_path), key=lambda s: s.lower())
    except OSError as exc:
        return jsonify({"error": f"Unable to read directory: {exc}"}), 500

    for entry in entries:
        if entry.startswith('.'):
            continue
        full = os.path.join(abs_path, entry)
        rel_entry = os.path.join(rel_path, entry) if rel_path else entry
        rel_entry = rel_entry.replace("\\", "/")
        if os.path.isdir(full):
            dirs.append({"name": entry, "path": rel_entry})
        elif os.path.isfile(full):
            ext = os.path.splitext(entry)[1].lower()
            if ext in ICON_ALLOWED_EXTS:
                icons.append({
                    "name": os.path.splitext(entry)[0],
                    "path": rel_entry,
                    "url": url_for('static', filename=f"icons/{rel_entry}"),
                    "ext": ext,
                })

    return jsonify({
        "path": rel_path,
        "breadcrumbs": build_icon_breadcrumbs(rel_path),
        "dirs": dirs,
        "icons": icons,
        "supports_svg": cairosvg is not None,
    })


@app.route('/api/icons/search')
def api_icons_search():
    q = request.args.get('q', '').strip().lower()
    if len(q) < 2:
        return jsonify({"icons": [], "total": 0})

    results: List[Dict[str, str]] = []
    for root, dirs, files in os.walk(ICON_DIR_ABS):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for filename in sorted(files):
            if filename.startswith('.'):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ICON_ALLOWED_EXTS:
                continue
            if q not in os.path.splitext(filename)[0].lower():
                continue
            full_path = os.path.join(root, filename)
            rel = os.path.relpath(full_path, ICON_DIR_ABS).replace("\\", "/")
            results.append({
                "name": os.path.splitext(filename)[0],
                "path": rel,
                "url": url_for('static', filename=f"icons/{rel}"),
                "ext": ext,
            })
            if len(results) >= 120:
                break
        if len(results) >= 120:
            break

    return jsonify({"icons": results, "total": len(results)})


_ICONIFY_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')


@app.route('/api/iconify/search')
def api_iconify_search():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({"icons": [], "total": 0})

    api_url = f"https://api.iconify.design/search?query={urllib.parse.quote(q)}&limit=80"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "ptouch-labelmaker/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    icons = []
    for ref in data.get("icons", []):
        if ":" not in ref:
            continue
        prefix, name = ref.split(":", 1)
        icons.append({"ref": ref, "prefix": prefix, "name": name})

    return jsonify({"icons": icons, "total": data.get("total", len(icons))})


@app.route('/api/iconify/download', methods=['POST'])
def api_iconify_download():
    data = request.get_json(force=True)
    prefix = (data.get("prefix") or "").strip()
    name = (data.get("name") or "").strip()

    if not _ICONIFY_NAME_RE.match(prefix) or not _ICONIFY_NAME_RE.match(name):
        return jsonify({"error": "Invalid icon reference"}), 400

    svg_url = f"https://api.iconify.design/{prefix}/{name}.svg"
    try:
        req = urllib.request.Request(svg_url, headers={"User-Agent": "ptouch-labelmaker/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            svg_bytes = resp.read()
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch icon: {exc}"}), 502

    save_dir = os.path.join(ICON_DIR_ABS, "iconify", prefix)
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, f"{name}.svg")
    with open(save_path, "wb") as fh:
        fh.write(svg_bytes)

    rel = f"iconify/{prefix}/{name}.svg"
    return jsonify({"path": rel, "url": url_for("static", filename=f"icons/{rel}")})


@app.route('/api/preview', methods=['POST'])
def api_preview():
    data = request.get_json(force=True)
    text = data.get('text', '')
    url = data.get('url', '').strip() or None
    font_size = int(data.get('font_size', 24))
    qr_size_raw = data.get('qr_size')
    try:
        qr_size_val = int(qr_size_raw) if qr_size_raw is not None else None
    except (TypeError, ValueError):
        qr_size_val = None
    font_key = data.get('font', DEFAULT_FONT_KEY)
    border_style = data.get('border_style', BORDER_DEFAULT)
    icon_key = data.get('icon', ICON_DEFAULT)
    icon_size_raw = data.get('icon_size')
    try:
        icon_size_val = int(icon_size_raw) if icon_size_raw is not None else None
    except (TypeError, ValueError):
        icon_size_val = None

    if font_key not in FONT_LIBRARY:
        return jsonify({"error": f"Unknown font selection '{font_key}'."}), 400
    if border_style not in BORDER_STYLES:
        return jsonify({"error": f"Unknown border style '{border_style}'."}), 400
    if icon_key not in (None, "", "none"):
        resolved_icon_path, _abs_icon = resolve_icon_path(icon_key, allow_directory=False)
        if not _abs_icon:
            return jsonify({"error": f"Unknown icon selection '{icon_key}'."}), 400
        sanitized_icon = resolved_icon_path
    else:
        sanitized_icon = ICON_DEFAULT

    info = get_printer_info()
    if not info.available:
        return jsonify({"error": "Printer not available", "raw": info.raw}), 400
    if info.has_error:
        return jsonify({"error": f"Printer error: {info.error_message}"}), 400

    max_h = info.max_height_px or 128
    resolved_font_key = font_key if resolve_font_path(font_key) else DEFAULT_FONT_KEY
    resolved_border = border_style if border_style in BORDER_STYLES else BORDER_DEFAULT
    resolved_icon = sanitized_icon
    padding = 12
    resolved_icon_size = clamp_icon_height(icon_size_val, max_h, padding=padding) if resolved_icon else 0
    resolved_qr_size = clamp_qr_size(qr_size_val, max_h, padding=padding)
    img = render_label_png(
        text=text,
        url=url,
        max_height=max_h,
        font_size=font_size,
        qr_size=resolved_qr_size,
        font_key=resolved_font_key,
        border_style=resolved_border,
        icon_key=resolved_icon,
        icon_size=resolved_icon_size,
    )

    file_id = str(uuid.uuid4())
    path = os.path.join(STATIC_DIR, f"label_{file_id}.png")
    img.save(path, format="PNG", optimize=True)

    return jsonify({
        "file_id": file_id,
        "height": img.height,
        "width": img.width,
        "path": path,
        "font_key": resolved_font_key,
        "border_style": resolved_border,
        "icon": resolved_icon,
        "qr_size": resolved_qr_size,
        "icon_size": resolved_icon_size,
    })


@app.route('/api/print', methods=['POST'])
def api_print():
    data = request.get_json(force=True)
    file_id = data.get('file_id')
    if not file_id:
        return jsonify({"error": "Missing file_id"}), 400
    path = os.path.join(STATIC_DIR, f"label_{file_id}.png")
    if not os.path.exists(path):
        return jsonify({"error": "Preview not found; generate again."}), 404

    info = get_printer_info()
    if not info.available:
        return jsonify({"error": "Printer not available"}), 400
    if info.has_error:
        return jsonify({"error": f"Printer error: {info.error_message}"}), 400

    max_h = info.max_height_px or 128
    with Image.open(path) as im:
        if im.height > max_h:
            return jsonify({"error": f"Image height {im.height}px exceeds max tape height {max_h}px"}), 400

    code, out, err = run_cmd([PT_CMD, f"--image={path}"])
    ok = (code == 0)
    return jsonify({"ok": ok, "returncode": code, "stdout": out, "stderr": err}), (200 if ok else 500)


@app.route('/preview/<file_id>.png')
def serve_preview(file_id: str):
    path = os.path.join(STATIC_DIR, f"label_{file_id}.png")
    if not os.path.exists(path):
        return "Not found", 404
    return send_file(path, mimetype='image/png', as_attachment=False)



if __name__ == '__main__':
    port = int(os.environ.get("PORT", "5000"))
    app.run(host='0.0.0.0', port=port, debug=True)
