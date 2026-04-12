# P-Touch Label Maker

A self-hosted web app for designing and printing labels on a Brother P-Touch label printer. Run it on a Raspberry Pi (or any machine with the printer attached), open it in any browser on your network, and print.

## Features

- **Text labels** with automatic font-size scaling to fit the tape width
- **QR codes** generated from any URL, composited alongside the text
- **Icons** — 2,800+ bundled Font Awesome SVGs (solid, regular, brands) plus searchable access to 200,000+ icons via [Iconify](https://iconify.design/)
- **Font selection** — discovers and lists all fonts installed on the host system
- **Border styles** — none, thin, thick, double, dashed
- **Live printer status** — shows tape width, media type, and error codes
- Black-and-white 1-bit PNG output matched to your tape's exact pixel height

## Requirements

### ptouch-print

The app shells out to [`ptouch-print`](https://github.com/philpem/ptouch-print) to communicate with the printer. Build it and place the binary at:

```
/opt/ptouch-print/build/ptouch-print
```

### Python dependencies

```bash
pip install flask pillow qrcode[pil] cairosvg
```

`cairosvg` is optional but required for SVG icons to render on labels. Without it, SVG icons are visible in the picker but won't be composited onto the printed label.

## Running

```bash
python labelmaker.py
```

The app listens on `0.0.0.0:5000` by default. Set the `PORT` environment variable to change it:

```bash
PORT=8080 python labelmaker.py
```

## Icons

### Bundled library

The `static/icons/` directory ships with 2,800+ Font Awesome Free SVGs organised into three subdirectories: `solid/`, `regular/`, and `brands/`. Use the search box in the icon picker to find them by name.

### Iconify (online search)

The **Iconify** tab in the icon picker searches the Iconify API in real time. Clicking an icon downloads it to `static/icons/iconify/{set}/` on the server and selects it — it then becomes part of your local library. Requires internet access from the host machine.

### Adding your own icons

Drop any PNG, SVG, JPG, BMP, or GIF into a subdirectory under `static/icons/` and it will appear in the picker immediately.

## Homebox integration

The app exposes a webhook endpoint that [Homebox](https://homebox.software/) can call to automatically print a label for any asset.

```
GET /api/homebox/print
```

| Parameter | Required | Description |
|---|---|---|
| `URL` | Yes | Encoded into the QR code |
| `TitleText` | No | Large bold heading |
| `DescriptionText` | No | Body text (word-wrapped, first line only) |
| `AdditionalInformation` | No | Small info line at the bottom |

Point your Homebox label-printer webhook at:

```
http://<host>:5000/api/homebox/print?URL={url}&TitleText={name}&DescriptionText={description}&AdditionalInformation={location}
```

The endpoint renders the label and sends it to the printer immediately, returning JSON:

```json
{"ok": true, "returncode": 0, "width": 350, "height": 128}
```

Also accepts `POST` with a JSON body using the same field names.

## Code layout

| File | Responsibility |
|---|---|
| `labelmaker.py` | Flask app and all routes |
| `printer.py` | Printer communication (`ptouch-print` wrapper, status parsing, error codes) |
| `fonts.py` | Font discovery, cataloguing, and loading |
| `rendering.py` | Label image rendering (borders, icons, QR codes, both label renderers) |

## Configuration

### Error codes

Printer error codes are mapped to human-readable messages in `error_codes.json`:

```json
{
  "0000": "OK",
  "1000": "Printer door open",
  "0001": "No tape loaded"
}
```

Add entries here to handle any error codes your printer reports.

## Printer setup (Brother P-Touch)

The mode switch on the printer must be set to **E** (Editor mode) for USB communication to work. The app will display a reminder if the printer is not detected.
