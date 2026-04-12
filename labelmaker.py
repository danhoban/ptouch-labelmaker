#!/usr/bin/env python3
"""
Flask web app for designing, previewing, and printing Brother P-Touch labels.

Routes:
  GET  /                       — main UI
  GET  /api/printer_status     — live printer status (polled by frontend)
  GET  /api/icons              — browse icon directory tree
  GET  /api/icons/search       — search icon files by name
  GET  /api/iconify/search     — search Iconify CDN
  POST /api/iconify/download   — download and cache an Iconify SVG
  POST /api/preview            — render label PNG, return file_id
  POST /api/print              — send previously-rendered PNG to printer
  GET  /preview/<file_id>.png  — serve a rendered preview image
  GET  /api/homebox/print      — Homebox webhook: render + print in one step
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request
import uuid

from flask import Flask, jsonify, render_template, request, send_file, url_for
from PIL import Image

from fonts import DEFAULT_FONT_KEY, FONT_LIBRARY, get_font_options, resolve_font_path
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
    render_homebox_label,
    render_label_png,
    resolve_icon_path,
)

STATIC_DIR = os.path.join("/tmp", "ptouch_web")
os.makedirs(STATIC_DIR, exist_ok=True)

_FILE_ID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_ICONIFY_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')

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
    )

    file_id = str(uuid.uuid4())
    path = os.path.join(STATIC_DIR, f"label_{file_id}.png")
    img.save(path, format="PNG", optimize=True)

    return jsonify({
        "file_id":      file_id,
        "height":       img.height,
        "width":        img.width,
        "path":         path,
        "font_key":     resolved_font_key,
        "font_size":    actual_font_size,
        "border_style": resolved_border,
        "icon":         resolved_icon,
        "qr_size":      resolved_qr_size,
        "icon_size":    resolved_icon_size,
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
    return jsonify({"ok": ok, "returncode": code, "stdout": out, "stderr": err}), (200 if ok else 500)


@app.route('/preview/<file_id>.png')
def serve_preview(file_id: str):
    if not _FILE_ID_RE.match(file_id):
        return "Not found", 404
    path = os.path.join(STATIC_DIR, f"label_{file_id}.png")
    if not os.path.exists(path):
        return "Not found", 404
    return send_file(path, mimetype='image/png', as_attachment=False)


# ---------------------------------------------------------------------------
# Homebox webhook endpoint
# ---------------------------------------------------------------------------

@app.route('/api/homebox/print', methods=['GET', 'POST'])
def api_homebox_print():
    """
    Accepts a Homebox label-print webhook.  Renders a structured label
    (QR code + title / description / additional info) and sends it directly
    to the printer.

    Query parameters (GET) or JSON body (POST):
      URL                  — required; encoded into the QR code
      TitleText            — optional; large bold heading
      DescriptionText      — optional; body text (word-wrapped, first line only)
      AdditionalInformation — optional; small info line at the bottom
    """
    if request.method == 'POST':
        body = request.get_json(force=True, silent=True) or {}
        get_param = lambda key: (body.get(key) or '').strip()
    else:
        get_param = lambda key: (request.args.get(key) or '').strip()

    url = get_param('URL')
    if not url:
        return jsonify({"error": "Missing required parameter: URL"}), 400

    title           = get_param('TitleText')
    description     = get_param('DescriptionText')
    additional_info = get_param('AdditionalInformation')

    info = get_printer_info()
    if not info.available:
        return jsonify({"error": "Printer not available", "raw": info.raw}), 503
    if info.has_error:
        return jsonify({"error": f"Printer error: {info.error_message}"}), 503

    max_h = info.max_height_px or 128
    img = render_homebox_label(url, title, description, additional_info, max_height=max_h)

    file_id = str(uuid.uuid4())
    path = os.path.join(STATIC_DIR, f"label_{file_id}.png")
    img.save(path, format="PNG")

    code, out, err = run_cmd([PT_CMD, f"--image={path}"])
    ok = (code == 0)
    return jsonify({
        "ok":         ok,
        "returncode": code,
        "stdout":     out,
        "stderr":     err,
        "width":      img.width,
        "height":     img.height,
    }), (200 if ok else 500)


# ---------------------------------------------------------------------------

if __name__ == '__main__':
    port  = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
    app.run(host='0.0.0.0', port=port, debug=debug)
