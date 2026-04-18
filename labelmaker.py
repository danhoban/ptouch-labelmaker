#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Flask web app for designing, previewing, and printing Brother P-Touch labels.

Routes:
  GET  /                            — main UI
  GET  /api/printer_status          — live printer status (polled by frontend)
  GET  /api/icons                   — browse icon directory tree
  GET  /api/icons/search            — search icon files by name
  GET  /api/iconify/search          — search Iconify CDN
  POST /api/iconify/download        — download and cache an Iconify SVG
  POST /api/preview                 — render label PNG, return file_id
  POST /api/print                   — send previously-rendered PNG to printer
  GET  /preview/<file_id>.png       — serve a rendered preview image
  GET  /api/history                 — list saved label history (starred + recent)
  POST /api/history/<id>/star       — star/unstar a label and optionally rename it
  DELETE /api/history/<id>          — delete a label from history
  GET  /api/homebox/print           — Homebox webhook: render + print in one step
"""

import io
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
import uuid

from flask import Flask, jsonify, render_template, request, send_file, url_for
from PIL import Image

from fonts import DEFAULT_FONT_KEY, FONT_LIBRARY, get_font_options, resolve_font_path
from homebox_client import fetch_item_vars
from printer import PT_CMD, get_printer_info, run_cmd
from rendering import (
    BORDER_DEFAULT,
    BORDER_STYLES,
    ICON_ALLOWED_EXTS,
    ICON_DEFAULT,
    ICON_DIR_ABS,
    ICON_MIN_HEIGHT,
    QR_MIN_SIZE,
    SUPPORTS_SVG,
    apply_border,
    build_icon_breadcrumbs,
    clamp_icon_height,
    clamp_qr_size,
    get_border_options,
    mm_to_px,
    render_label_png,
    resolve_icon_path,
    PRINTER_DPI,
)

STATIC_DIR = os.path.join("/tmp", "ptouch_web")
os.makedirs(STATIC_DIR, exist_ok=True)

LABEL_STORE_DIR = os.environ.get(
    "LABEL_STORE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "label_store"),
)
try:
    os.makedirs(LABEL_STORE_DIR, exist_ok=True)
except OSError as _exc:
    raise SystemExit(
        f"ERROR: Cannot create label store directory '{LABEL_STORE_DIR}': {_exc}\n"
        f"Ensure the parent directory exists and is writable, or set LABEL_STORE_DIR "
        f"to a writable path."
    ) from _exc
if not os.access(LABEL_STORE_DIR, os.R_OK | os.W_OK):
    raise SystemExit(
        f"ERROR: Label store directory '{LABEL_STORE_DIR}' is not readable/writable.\n"
        f"Fix with: chmod 755 '{LABEL_STORE_DIR}'\n"
        f"Or set LABEL_STORE_DIR to a writable path."
    )

HISTORY_MAX_UNSTARRED = 15

_FILE_ID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_ICONIFY_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')
_TEMPLATE_VAR_RE = re.compile(r'\{\{([^}]+)\}\}')

HOMEBOX_TEMPLATE_NAME = "Homebox Template"

# Default Homebox template — placeholders are substituted at render time.
# Users can edit this entry in the library to customise font, layout, etc.
_HOMEBOX_DEFAULT_TEMPLATE = {
    "text": "**{{TitleText}}**\n{{DescriptionText}}\n[-4]{{AdditionalInformation}}",
    "url": "{{URL}}",
    "font_size": 24,
    "font": DEFAULT_FONT_KEY,
    "border_style": BORDER_DEFAULT,
    "icon": "",
    "icon_size": None,
    "qr_size": 96,
    "element_order": ["qr", "text"],
    "label_width_mm": None,
}


# ---------------------------------------------------------------------------
# Label store helpers
# ---------------------------------------------------------------------------

def _entry_dir(entry_id: str) -> str:
    """Absolute path to the label store directory for one entry."""
    return os.path.join(LABEL_STORE_DIR, entry_id)


def _load_meta(entry_id: str):
    """Load and return meta.json for one entry, or None on any error."""
    try:
        with open(os.path.join(_entry_dir(entry_id), "meta.json"), "r") as fh:
            return json.load(fh)
    except Exception:
        return None


def _save_label_entry(entry_id: str, meta: dict, png_src: str) -> None:
    """Persist meta.json and preview.png into label_store/{entry_id}/."""
    dest = _entry_dir(entry_id)
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    shutil.copy2(png_src, os.path.join(dest, "preview.png"))


def _cleanup_label_store() -> None:
    """Delete oldest unstarred entries beyond HISTORY_MAX_UNSTARRED."""
    try:
        entry_ids = [
            d for d in os.listdir(LABEL_STORE_DIR)
            if _FILE_ID_RE.match(d) and os.path.isdir(_entry_dir(d))
        ]
    except OSError:
        return
    unstarred = []
    for eid in entry_ids:
        meta = _load_meta(eid)
        if meta and not meta.get("starred"):
            unstarred.append((eid, meta.get("created_at", 0)))
    unstarred.sort(key=lambda x: x[1])  # oldest first
    for eid, _ in unstarred[:-HISTORY_MAX_UNSTARRED] if len(unstarred) > HISTORY_MAX_UNSTARRED else []:
        shutil.rmtree(_entry_dir(eid), ignore_errors=True)

def _interpolate(text: str, vars_dict: dict) -> str:
    """Replace {{KEY}} placeholders with values from vars_dict."""
    return _TEMPLATE_VAR_RE.sub(lambda m: vars_dict.get(m.group(1), ''), text)


def _find_label_by_name(name: str):
    """Return (entry_id, meta) for the first starred entry with the given name."""
    try:
        entry_ids = [
            d for d in os.listdir(LABEL_STORE_DIR)
            if _FILE_ID_RE.match(d) and os.path.isdir(_entry_dir(d))
        ]
    except OSError:
        return None, None
    for eid in entry_ids:
        meta = _load_meta(eid)
        if meta and meta.get("starred") and meta.get("name") == name:
            return eid, meta
    return None, None


def _get_or_create_homebox_template(vars_dict: dict, max_h: int) -> dict:
    """Return the Homebox Template library entry, creating it on first use."""
    _, meta = _find_label_by_name(HOMEBOX_TEMPLATE_NAME)
    if meta is not None:
        return meta

    entry_id = str(uuid.uuid4())

    # Render a preview using real data (with sensible fallbacks)
    preview_vars = {
        "URL":                   vars_dict.get("URL") or "https://homebox.example.com/i/1",
        "TitleText":             vars_dict.get("TitleText") or "Asset Name",
        "DescriptionText":       vars_dict.get("DescriptionText") or "Description",
        "AdditionalInformation": vars_dict.get("AdditionalInformation") or "",
    }
    img, _ = render_label_png(
        text=_interpolate(_HOMEBOX_DEFAULT_TEMPLATE["text"], preview_vars),
        url=_interpolate(_HOMEBOX_DEFAULT_TEMPLATE["url"], preview_vars) or None,
        max_height=max_h,
        font_size=_HOMEBOX_DEFAULT_TEMPLATE["font_size"],
        font_key=_HOMEBOX_DEFAULT_TEMPLATE["font"],
        border_style=_HOMEBOX_DEFAULT_TEMPLATE["border_style"],
        icon_key=_HOMEBOX_DEFAULT_TEMPLATE["icon"],
        icon_size=_HOMEBOX_DEFAULT_TEMPLATE["icon_size"],
        qr_size=_HOMEBOX_DEFAULT_TEMPLATE["qr_size"],
        element_order=_HOMEBOX_DEFAULT_TEMPLATE["element_order"],
    )

    meta = {
        "id":             entry_id,
        "created_at":     time.time(),
        "starred":        True,
        "name":           HOMEBOX_TEMPLATE_NAME,
        **_HOMEBOX_DEFAULT_TEMPLATE,
        "rendered_width":  img.width,
        "rendered_height": img.height,
    }

    tmp_path = os.path.join(STATIC_DIR, f"label_{entry_id}.png")
    img.save(tmp_path, format="PNG", optimize=True)
    try:
        _save_label_entry(entry_id, meta, tmp_path)
    except OSError:
        pass

    return meta


app = Flask(__name__)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    info = get_printer_info()
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
        supports_svg=SUPPORTS_SVG,
    )


# ---------------------------------------------------------------------------
# Printer status
# ---------------------------------------------------------------------------

@app.route('/api/printer_status')
def api_printer_status():
    info = get_printer_info()
    return jsonify({
        "available":    info.available,
        "raw":          info.raw,
        "model":        info.model,
        "max_printer_px": info.max_printer_px,
        "max_tape_px":  info.max_tape_px,
        "media_type":   info.media_type,
        "media_width":  info.media_width,
        "tape_color":   info.tape_color,
        "text_color":   info.text_color,
        "error_code":   info.error_code,
        "error_message": info.error_message,
        "has_error":    info.has_error,
    })


# ---------------------------------------------------------------------------
# Icon browsing
# ---------------------------------------------------------------------------

@app.route('/api/icons')
def api_icons():
    path = request.args.get('path', '')
    rel_path, abs_path = resolve_icon_path(path, allow_directory=True)
    if abs_path is None:
        return jsonify({"error": "Path not found"}), 404

    dirs, icons = [], []
    try:
        entries = sorted(os.listdir(abs_path), key=lambda s: s.lower())
    except OSError as exc:
        return jsonify({"error": f"Unable to read directory: {exc}"}), 500

    for entry in entries:
        if entry.startswith('.'):
            continue
        full = os.path.join(abs_path, entry)
        rel_entry = (os.path.join(rel_path, entry) if rel_path else entry).replace("\\", "/")
        if os.path.isdir(full):
            dirs.append({"name": entry, "path": rel_entry})
        elif os.path.isfile(full):
            ext = os.path.splitext(entry)[1].lower()
            if ext in ICON_ALLOWED_EXTS:
                icons.append({
                    "name": os.path.splitext(entry)[0],
                    "path": rel_entry,
                    "url":  url_for('static', filename=f"icons/{rel_entry}"),
                    "ext":  ext,
                })

    return jsonify({
        "path":        rel_path,
        "breadcrumbs": build_icon_breadcrumbs(rel_path),
        "dirs":        dirs,
        "icons":       icons,
        "supports_svg": SUPPORTS_SVG,
    })


@app.route('/api/icons/search')
def api_icons_search():
    q = request.args.get('q', '').strip().lower()
    if len(q) < 2:
        return jsonify({"icons": [], "total": 0})

    results = []
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
                "url":  url_for('static', filename=f"icons/{rel}"),
                "ext":  ext,
            })
            if len(results) >= 120:
                break
        if len(results) >= 120:
            break

    return jsonify({"icons": results, "total": len(results)})


# ---------------------------------------------------------------------------
# Iconify integration
# ---------------------------------------------------------------------------

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

    icons = [
        {"ref": ref, "prefix": ref.split(":")[0], "name": ref.split(":")[1]}
        for ref in data.get("icons", [])
        if ":" in ref
    ]
    return jsonify({"icons": icons, "total": data.get("total", len(icons))})


@app.route('/api/iconify/download', methods=['POST'])
def api_iconify_download():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    prefix = (data.get("prefix") or "").strip()
    name   = (data.get("name")   or "").strip()
    if not _ICONIFY_NAME_RE.match(prefix) or not _ICONIFY_NAME_RE.match(name):
        return jsonify({"error": "Invalid icon reference"}), 400

    svg_url = f"https://api.iconify.design/{prefix}/{name}.svg"
    try:
        req = urllib.request.Request(svg_url, headers={"User-Agent": "ptouch-labelmaker/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            svg_bytes = resp.read()
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch icon: {exc}"}), 502

    try:
        save_dir = os.path.join(ICON_DIR_ABS, "iconify", prefix)
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, f"{name}.svg")
        with open(save_path, "wb") as fh:
            fh.write(svg_bytes)
    except OSError as exc:
        return jsonify({"error": f"Failed to save icon: {exc}"}), 500

    rel = f"iconify/{prefix}/{name}.svg"
    return jsonify({"path": rel, "url": url_for("static", filename=f"icons/{rel}")})


# ---------------------------------------------------------------------------
# Label preview + print (GUI flow)
# ---------------------------------------------------------------------------

@app.route('/api/preview', methods=['POST'])
def api_preview():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400

    text = data.get('text', '')
    url  = data.get('url', '').strip() or None
    try:
        font_size = int(data.get('font_size', 24))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid font_size"}), 400

    qr_size_raw = data.get('qr_size')
    try:
        qr_size_val = int(qr_size_raw) if qr_size_raw is not None else None
    except (TypeError, ValueError):
        qr_size_val = None

    font_key     = data.get('font', DEFAULT_FONT_KEY)
    border_style = data.get('border_style', BORDER_DEFAULT)
    icon_key     = data.get('icon', ICON_DEFAULT)

    icon_size_raw = data.get('icon_size')
    try:
        icon_size_val = int(icon_size_raw) if icon_size_raw is not None else None
    except (TypeError, ValueError):
        icon_size_val = None

    label_width_mm_raw = data.get('label_width_mm')
    try:
        label_width_mm = float(label_width_mm_raw) if label_width_mm_raw is not None else None
    except (TypeError, ValueError):
        label_width_mm = None
    if label_width_mm is not None and label_width_mm <= 0:
        return jsonify({"error": "label_width_mm must be greater than 0"}), 400

    _valid_elements = {'icon', 'qr', 'text'}
    element_order_raw = data.get('element_order')
    if isinstance(element_order_raw, list):
        element_order = [e for e in element_order_raw if isinstance(e, str) and e in _valid_elements]
    else:
        element_order = None

    if font_key not in FONT_LIBRARY:
        return jsonify({"error": f"Unknown font selection '{font_key}'."}), 400
    if border_style not in BORDER_STYLES:
        return jsonify({"error": f"Unknown border style '{border_style}'."}), 400
    if icon_key not in (None, "", "none"):
        resolved_rel, _abs = resolve_icon_path(icon_key, allow_directory=False)
        if not _abs:
            return jsonify({"error": f"Unknown icon selection '{icon_key}'."}), 400
        sanitized_icon = resolved_rel
    else:
        sanitized_icon = ICON_DEFAULT

    info = get_printer_info()
    if not info.available:
        return jsonify({"error": "Printer not available", "raw": info.raw}), 400
    if info.has_error:
        return jsonify({"error": f"Printer error: {info.error_message}"}), 400

    max_h   = info.max_height_px or 128
    padding = 12
    resolved_font_key  = font_key if resolve_font_path(font_key) else DEFAULT_FONT_KEY
    resolved_border    = border_style if border_style in BORDER_STYLES else BORDER_DEFAULT
    resolved_icon      = sanitized_icon
    resolved_icon_size = clamp_icon_height(icon_size_val, max_h, padding) if resolved_icon else 0
    resolved_qr_size   = clamp_qr_size(qr_size_val, max_h, padding)

    max_width_px = mm_to_px(label_width_mm) if label_width_mm is not None else None

    img, actual_font_size = render_label_png(
        text=text,
        url=url,
        max_height=max_h,
        font_size=font_size,
        qr_size=resolved_qr_size,
        font_key=resolved_font_key,
        border_style=resolved_border,
        icon_key=resolved_icon,
        icon_size=resolved_icon_size,
        max_width=max_width_px,
        element_order=element_order,
    )

    file_id = str(uuid.uuid4())
    path = os.path.join(STATIC_DIR, f"label_{file_id}.png")
    img.save(path, format="PNG", optimize=True)

    # Write a sidecar JSON so /api/print can persist settings without the
    # frontend having to resend them.
    sidecar = {
        "id":              file_id,
        "created_at":      time.time(),
        "starred":         False,
        "name":            None,
        "text":            text or "",
        "url":             url or "",
        "font_size":       actual_font_size,
        "font":            resolved_font_key,
        "border_style":    resolved_border,
        "icon":            resolved_icon or "",
        "icon_size":       resolved_icon_size,
        "qr_size":         resolved_qr_size,
        "label_width_mm":  label_width_mm,
        "element_order":   element_order or ['icon', 'qr', 'text'],
        "rendered_width":  img.width,
        "rendered_height": img.height,
    }
    sidecar_path = os.path.join(STATIC_DIR, f"label_{file_id}_meta.json")
    try:
        with open(sidecar_path, "w") as fh:
            json.dump(sidecar, fh)
    except OSError:
        pass

    return jsonify({
        "file_id":         file_id,
        "height":          img.height,
        "width":           img.width,
        "width_mm":        round(img.width  * 25.4 / PRINTER_DPI, 1),
        "height_mm":       round(img.height * 25.4 / PRINTER_DPI, 1),
        "path":            path,
        "font_key":        resolved_font_key,
        "font_size":       actual_font_size,
        "border_style":    resolved_border,
        "icon":            resolved_icon,
        "qr_size":         resolved_qr_size,
        "icon_size":       resolved_icon_size,
        "label_width_mm":  label_width_mm,
        "element_order":   element_order or ['icon', 'qr', 'text'],
    })


@app.route('/api/print', methods=['POST'])
def api_print():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    file_id = data.get('file_id')
    if not file_id:
        return jsonify({"error": "Missing file_id"}), 400
    if not _FILE_ID_RE.match(str(file_id)):
        return jsonify({"error": "Invalid file_id"}), 400

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
    if ok:
        sidecar_path = os.path.join(STATIC_DIR, f"label_{file_id}_meta.json")
        try:
            with open(sidecar_path, "r") as fh:
                meta = json.load(fh)
        except Exception:
            meta = {"id": file_id, "created_at": time.time(), "starred": False, "name": None}
        _save_label_entry(file_id, meta, path)
        _cleanup_label_store()
    return jsonify({"ok": ok, "returncode": code, "stdout": out, "stderr": err}), (200 if ok else 500)


@app.route('/preview/<file_id>.png')
def serve_preview(file_id: str):
    if not _FILE_ID_RE.match(file_id):
        return "Not found", 404
    path = os.path.join(STATIC_DIR, f"label_{file_id}.png")
    if os.path.exists(path):
        return send_file(path, mimetype='image/png', as_attachment=False)
    store_path = os.path.join(_entry_dir(file_id), "preview.png")
    if os.path.exists(store_path):
        return send_file(store_path, mimetype='image/png', as_attachment=False)
    return "Not found", 404


# ---------------------------------------------------------------------------
# Label history API
# ---------------------------------------------------------------------------

@app.route('/api/history')
def api_history():
    try:
        entry_ids = [
            d for d in os.listdir(LABEL_STORE_DIR)
            if _FILE_ID_RE.match(d) and os.path.isdir(_entry_dir(d))
        ]
    except OSError:
        entry_ids = []

    entries = []
    for eid in entry_ids:
        meta = _load_meta(eid)
        if meta:
            meta["preview_url"] = f"/preview/{eid}.png"
            entries.append(meta)

    entries.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    starred = [e for e in entries if e.get("starred")]
    recent  = [e for e in entries if not e.get("starred")]
    return jsonify({"starred": starred, "recent": recent})


@app.route('/api/history/<entry_id>/star', methods=['POST'])
def api_history_star(entry_id: str):
    if not _FILE_ID_RE.match(entry_id):
        return jsonify({"error": "Invalid id"}), 400
    meta = _load_meta(entry_id)
    if meta is None:
        return jsonify({"error": "Entry not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    meta["starred"] = bool(data.get("starred", meta.get("starred", False)))
    name = data.get("name")
    if name is not None:
        meta["name"] = str(name).strip() or None
    try:
        with open(os.path.join(_entry_dir(entry_id), "meta.json"), "w") as fh:
            json.dump(meta, fh, indent=2)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    meta["preview_url"] = f"/preview/{entry_id}.png"
    return jsonify(meta)


@app.route('/api/history/<entry_id>', methods=['DELETE'])
def api_history_delete(entry_id: str):
    if not _FILE_ID_RE.match(entry_id):
        return jsonify({"error": "Invalid id"}), 400
    shutil.rmtree(_entry_dir(entry_id), ignore_errors=True)
    return jsonify({"ok": True})


@app.route('/api/history/<entry_id>/overwrite', methods=['POST'])
def api_history_overwrite(entry_id: str):
    """Replace a starred library entry's preview and metadata with a freshly-generated preview."""
    if not _FILE_ID_RE.match(entry_id):
        return jsonify({"error": "Invalid id"}), 400
    meta = _load_meta(entry_id)
    if meta is None:
        return jsonify({"error": "Entry not found"}), 404
    if not meta.get('starred'):
        return jsonify({"error": "Can only overwrite starred entries"}), 400
    req = request.get_json(force=True, silent=True) or {}
    file_id = str(req.get('file_id', '')).strip()
    if not file_id or not _FILE_ID_RE.match(file_id):
        return jsonify({"error": "Invalid file_id"}), 400
    sidecar_path = os.path.join(STATIC_DIR, f'label_{file_id}_meta.json')
    png_path     = os.path.join(STATIC_DIR, f'label_{file_id}.png')
    if not os.path.exists(sidecar_path) or not os.path.exists(png_path):
        return jsonify({"error": "Preview not found; generate again"}), 404
    with open(sidecar_path) as fh:
        new_meta = json.load(fh)
    # Preserve identity and library metadata
    new_meta['id']         = entry_id
    new_meta['starred']    = True
    new_meta['name']       = meta.get('name')
    new_meta['created_at'] = meta.get('created_at', time.time())
    _save_label_entry(entry_id, new_meta, png_path)
    new_meta['preview_url'] = f'/preview/{entry_id}.png'
    return jsonify(new_meta)


# ---------------------------------------------------------------------------
# Homebox webhook endpoint
# ---------------------------------------------------------------------------

@app.route('/api/homebox/print', methods=['GET', 'POST'])
def api_homebox_print():
    """
    Homebox label webhook.  On first call, creates a "Homebox Template" entry
    in the label library with {{TitleText}}, {{URL}} etc. placeholders.
    Subsequent calls use that template (load it in the UI to customise it).

    Query parameters (GET) or JSON body (POST):
      URL                   — required; encoded into the QR code
      TitleText             — optional
      DescriptionText       — optional
      AdditionalInformation — optional

    Available template variables (use as {{name}} in the template text/url):
      Webhook:  URL, TitleText, DescriptionText, AdditionalInformation
      API item: name, description, assetId, location, tags, serialNumber,
                modelNumber, manufacturer, notes, purchaseFrom, purchasePrice,
                quantity, <any custom field name e.g. {{Warranty Code}}>
      (API fields require HOMEBOX_URL, HOMEBOX_USER, HOMEBOX_PASSWORD env vars)
    """
    if request.method == 'POST':
        body = request.get_json(force=True, silent=True) or {}
        get_param = lambda key: (body.get(key) or '').strip()
    else:
        get_param = lambda key: (request.args.get(key) or '').strip()

    url = get_param('URL')
    if not url:
        return jsonify({"error": "Missing required parameter: URL"}), 400

    webhook_vars = {
        "URL":                   url,
        "TitleText":             get_param('TitleText'),
        "DescriptionText":       get_param('DescriptionText'),
        "AdditionalInformation": get_param('AdditionalInformation'),
    }

    # Enrich with full item data from Homebox API (if configured).
    # Webhook fields take priority over API fields with the same name.
    api_vars = fetch_item_vars(url)
    vars_dict = {**api_vars, **webhook_vars}

    info = get_printer_info()
    max_h = (info.max_height_px or 128) if info.available else 128

    template = _get_or_create_homebox_template(vars_dict, max_h)

    img, _ = render_label_png(
        text=_interpolate(template.get("text", ""), vars_dict),
        url=_interpolate(template.get("url", ""), vars_dict) or None,
        max_height=max_h,
        font_size=template.get("font_size", 24),
        font_key=template.get("font", DEFAULT_FONT_KEY),
        border_style=template.get("border_style", BORDER_DEFAULT),
        icon_key=template.get("icon", ""),
        icon_size=template.get("icon_size"),
        qr_size=template.get("qr_size", 96),
        element_order=template.get("element_order"),
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ---------------------------------------------------------------------------

if __name__ == '__main__':
    port  = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
    app.run(host='0.0.0.0', port=port, debug=debug)
