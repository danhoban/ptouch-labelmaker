"""
Printer communication: info queries, error codes, subprocess wrapper.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

PT_CMD = "/opt/ptouch-print/build/ptouch-print"

PRINTER_INFO_RE = {
    "model":       re.compile(r"^(?P<model>.*) found on USB"),
    "max_printer": re.compile(r"maximum printing width for this printer is (\d+)px"),
    "max_tape":    re.compile(r"maximum printing width for this tape is (\d+)px"),
    "media_type":  re.compile(r"media type = (\S+)"),
    "media_width": re.compile(r"media width = (.+)$"),
    "tape_color":  re.compile(r"tape color = (.+)$"),
    "text_color":  re.compile(r"text color = (.+)$"),
    "error":       re.compile(r"error = (\S+)"),
}


def _load_default_error_codes() -> Dict[str, str]:
    return {
        "0000": "OK",
        "1000": "Printer door open",
        "0001": "No tape loaded",
    }


ERROR_CODES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error_codes.json")


def load_error_codes(path: str = ERROR_CODES_PATH) -> Dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("error_codes data must be a mapping")
        normalized = {str(k): str(v) if v is not None else "" for k, v in data.items()}
        return normalized or _load_default_error_codes()
    except (OSError, ValueError, json.JSONDecodeError):
        return _load_default_error_codes()


ERROR_CODES = load_error_codes()


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
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
        )
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
            if not m:
                continue
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
