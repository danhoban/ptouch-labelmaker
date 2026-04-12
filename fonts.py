"""
Font discovery, cataloguing, and loading.
"""

import functools
import os
import re
from typing import Dict, List, Optional, Tuple

from PIL import ImageFont

from printer import run_cmd


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
        best_by_family: Dict[str, Dict] = {}

        def consider_font(family: str, style: Optional[str], path: str):
            if not path or not os.path.exists(path):
                return
            if os.path.splitext(path)[1].lower() not in (".ttf", ".otf"):
                return
            family_name = (family or "").strip() or os.path.splitext(os.path.basename(path))[0]
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
                        consider_font(*metadata, path)

        discovered: List[Dict[str, str]] = []
        existing_keys: set = set()
        for entry in sorted(best_by_family.values(), key=lambda v: v["label"].lower()):
            key_base = cls._slugify(entry["label"])
            key, counter = key_base, 2
            while key in existing_keys:
                key = f"{key_base}-{counter}"
                counter += 1
            existing_keys.add(key)
            discovered.append({"key": key, "label": entry["label"], "path": entry["path"]})
            if len(discovered) >= cls.DISCOVERY_MAX:
                break
        return discovered

    def _build_font_library(self) -> Dict[str, Dict]:
        library: Dict[str, Dict] = {
            self.DEFAULT_FONT_KEY: {
                "label": self.DEFAULT_FONT_LABEL,
                "paths": self.DEFAULT_FONT_PATHS.copy(),
            }
        }
        for entry in self._discover_system_fonts():
            key = entry["key"]
            if key not in library:
                library[key] = {"label": entry["label"], "paths": [entry["path"]]}
        return library

    @property
    def library(self) -> Dict[str, Dict]:
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

    def options(self) -> List[Dict]:
        opts = [
            {
                "key": key,
                "label": meta["label"],
                "available": bool(self.resolve_font_path(key)) or key == self.DEFAULT_FONT_KEY,
            }
            for key, meta in self._library.items()
        ]
        opts.sort(key=lambda item: (item["key"] != self.DEFAULT_FONT_KEY, item["label"].lower()))
        return opts


FONT_CATALOG = FontCatalog()
DEFAULT_FONT_KEY = FontCatalog.DEFAULT_FONT_KEY
FONT_LIBRARY = FONT_CATALOG.library


def resolve_font_path(font_key: str) -> Optional[str]:
    return FONT_CATALOG.resolve_font_path(font_key)


def load_font(size: int, font_key: str = DEFAULT_FONT_KEY) -> ImageFont.ImageFont:
    return FONT_CATALOG.load_font(size, font_key)


def get_font_options() -> List[Dict]:
    return FONT_CATALOG.options()
