# kampiapina-maps

Animated 12-second 1920×1080 map intros from a GPX file, in Kampiapina brand colors.

1. 0.0 s – full route on a stylized map (land, forest, water, roads)
2. 1.2 s – city names type in
3. 3.4 s – smooth zoom to the chosen kilometer (~3 km of map across the frame)
4. 5.0 s – pulsing marker appears at that kilometer and rides slowly along the track until the end (12 s)

## Setup

```sh
uv venv -p 3.13 .venv
uv pip install -p .venv -e .
```

Needs the system `libcairo2` (already present on most Linux desktops). ffmpeg is bundled via `imageio-ffmpeg`.

## Usage

```sh
.venv/bin/kampiapina-maps ride.gpx --km 30 --cities Tampere Lempäälä Valkeakoski -o intro.mp4
```

| Option | |
|---|---|
| `--km` | kilometer to zoom into (required) |
| `--cities` | names to highlight; a wrong geocode can be fixed with `"Name@lat,lon"` |
| `--span` | km of map across the frame when zoomed in (default 3) |
| `--theme` | `paper` (default), `light`, `clean`, `nordic`, `sand`, or a path to a theme `.toml` |
| `--duration` | video length in seconds (default 12); extra time holds on the zoomed view |
| `--vertical` | 1080×1920 portrait video, full frame, for placing at the edge of the edit or for Shorts |
| `--fps` | default 30 |
| `--preview [TIMES]` | write PNG stills instead of video (default 0.5,2.6,4.4,7.0 s) |

Tiles and geocoding results are cached in `~/.cache/kampiapina-maps`.

## Tweaking

- Colors, line widths, label font/size: `kampiapina_maps/themes/light.toml`. Other themes start with `extends = "light"` and override only what differs.
- Timings (typing speed, zoom start/end, marker speed): `kampiapina_maps/timeline.py`

Map data © OpenStreetMap contributors via [OpenFreeMap](https://openfreemap.org); the credit is drawn in the corner of every frame. Font: Montserrat (OFL).
