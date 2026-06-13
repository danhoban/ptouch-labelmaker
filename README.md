# P-Touch Label Maker

A self-hosted web app for designing and printing labels on a Brother P-Touch label printer. Run it on a Raspberry Pi (or any machine with the printer attached), open it in any browser on your network, and print.

![Desktop UI with label preview](docs/screenshots/ui-preview.png)

<details>
<summary>More screenshots</summary>

**Desktop — empty state**
![Desktop UI empty state](docs/screenshots/ui-desktop.png)

**Mobile**
![Mobile UI](docs/screenshots/ui-mobile.png)

</details>

## Features

- **Text labels** with automatic font-size scaling to fit the tape width
- **QR codes** generated from any URL, composited alongside the text
- **Icons** — 2,800+ bundled Font Awesome SVGs (solid, regular, brands) plus searchable access to 200,000+ icons via [Iconify](https://iconify.design/)
- **Font selection** — discovers and lists all fonts installed on the host system
- **Border styles** — none, thin, thick, double, dashed
- **Element ordering** — reorder icon, QR code, and text left-to-right using up/down buttons
- **Label library** — save named label configurations and reload them in one click
- **Recent history** — last-used labels are remembered and can be reloaded or reprinted
- **Live printer status** — shows tape width, media type, and error codes
- **Mobile-friendly** — responsive layout works on phones and tablets
- Black-and-white 1-bit PNG output matched to your tape's exact pixel height

## Requirements

### ptouch-print

The app shells out to [`ptouch-print`](https://github.com/philpem/ptouch-print) to communicate with the printer. Build it and place the binary at:

```
/opt/ptouch-print/build/ptouch-print
```

### Python dependencies

Requires Python 3.9 or later.

```bash
pip install -r requirements.txt
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

## Label library

The **Library** section lets you save the current label configuration under a name. Saved entries can be loaded, overwritten, reprinted, or deleted. The library persists across sessions.

## Homebox integration

Requires **Homebox v0.26 or later**.

The app exposes a webhook endpoint that [Homebox](https://homebox.software/) can call to generate a label image for any asset.

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

The endpoint returns a PNG image directly. Homebox handles sending it to the printer. The endpoint works even when the printer is off — it falls back to a 128 px tape height if the printer is unavailable.

Also accepts `POST` with a JSON body using the same field names.

### API enrichment (optional)

When configured with a Homebox API key, the webhook fetches full item data and makes every field available as a `{{placeholder}}` in label templates. Set these environment variables (or add them to `.env`):

```
HOMEBOX_URL=http://your-homebox-instance:7745
HOMEBOX_API_KEY=hb_your_api_key_here
```

Generate an API key from your Homebox profile page (Profile → API Keys). Keys use the `hb_` prefix.

Available template variables when API enrichment is enabled:

| Variable | Source |
|---|---|
| `{{name}}` | Item name |
| `{{description}}` | Item description |
| `{{assetId}}` | Asset ID |
| `{{location}}` | Location name |
| `{{tags}}` | Comma-separated tag names |
| `{{collection}}` | Derived from a `Label-*` tag (e.g. tag `Label-Electronics` → `Electronics`) |
| `{{serialNumber}}` | Serial number |
| `{{modelNumber}}` | Model number |
| `{{manufacturer}}` | Manufacturer |
| `{{notes}}` | Notes |
| `{{purchaseFrom}}` | Purchase source |
| `{{purchasePrice}}` | Purchase price |
| `{{purchaseDate}}` | Purchase date (YYYY-MM-DD) |
| `{{soldDate}}` | Sale date (YYYY-MM-DD) |
| `{{soldPrice}}` | Sale price |
| `{{warrantyExpires}}` | Warranty expiry date (YYYY-MM-DD) |
| `{{warrantyDetails}}` | Warranty details |
| `{{quantity}}` | Quantity |
| `{{<custom field name>}}` | Any custom field defined on the item |

Webhook parameters (`TitleText`, `URL`, etc.) take priority over API fields with the same name.

### Font requirements for Homebox labels

The Homebox label renderer uses **Arial Black** and **Verdana Bold** for the title and body text. On Debian/Ubuntu/Raspberry Pi OS these come from the `ttf-mscorefonts-installer` package:

```bash
sudo apt install ttf-mscorefonts-installer
```

Without them the renderer falls back to a plain bitmap font, which will look noticeably worse.

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

## Security note

The web interface has no authentication. It is intended for use on a trusted local network. Do not expose it directly to the internet.

## Third-party content

The bundled icons in `static/icons/` are from [Font Awesome Free](https://fontawesome.com) and are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Font Awesome is a trademark of Fonticons, Inc.

## Licence

Copyright (C) 2025 Dan Hoban

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

See [LICENSE](LICENSE) for the full text.
