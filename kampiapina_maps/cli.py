"""kampiapina-maps: animated route map intro from a GPX file."""

import argparse
import os
import sys
import time
from pathlib import Path

from .encode import VideoWriter
from .geocode import Geocoder
from .gpx import load_gpx
from .render import Renderer, Scene, check_timing, render_frames
from .theme import load_theme
from .tiles import TileSource
from .timeline import Timeline

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "kampiapina-maps"


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="kampiapina-maps",
        description="Render a 1920x1080 (or 1080x1920) map intro: full route, city names, zoom to a kilometer.",
    )
    p.add_argument("gpx", help="GPX file")
    p.add_argument("--km", type=float, required=True, help="kilometer to zoom into, e.g. 42.5")
    p.add_argument("--cities", nargs="*", default=[], metavar="CITY",
                   help='city names to highlight, e.g. Tampere Lempäälä "Valkeakoski@61.264,24.031"')
    p.add_argument("-o", "--output", help="output .mp4 (default: <gpx name>.mp4)")
    p.add_argument("--theme", default="paper", help="theme name or path to a .toml (default: paper)")
    p.add_argument("--vertical", action="store_true", help="1080x1920 portrait video instead of 1920x1080")
    p.add_argument("--span", type=float, default=3.0,
                   help="km of map across the frame width when zoomed in (default: 3)")
    p.add_argument("--duration", type=float, default=Timeline.duration,
                   help=f"video length in seconds; extra time holds on the zoomed view (default: {Timeline.duration:g})")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--preview", nargs="?", const="0.5,2.6,4.4,7.0", metavar="TIMES",
                   help="write PNG stills instead of a video, at comma-separated seconds")
    return p.parse_args(argv)


def split_cities(items):
    # Accept both `--cities A B C` and `--cities "A,B,C"`; "Name@lat,lon" stays intact.
    out = []
    for item in items:
        out.extend([item] if "@" in item else [c for c in item.split(",") if c.strip()])
    return out


def main(argv=None):
    args = parse_args(argv)
    track = load_gpx(args.gpx)
    if not 0 <= args.km <= track.length_km:
        sys.exit(f"--km {args.km} is outside the track (0 - {track.length_km:.1f} km)")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    geocoder = Geocoder(CACHE_DIR)
    bbox = (track.lon.min(), track.lat.min(), track.lon.max(), track.lat.max())
    try:
        cities = [geocoder.resolve(c, bbox) for c in split_cities(args.cities)]
    except ValueError as e:
        sys.exit(str(e))
    for c in cities:
        print(f"{c.name}: {c.lat:.5f}, {c.lon:.5f}", file=sys.stderr)

    scene = Scene(track=track, km=args.km, cities=cities, theme=load_theme(args.theme),
                  timeline=Timeline(duration=args.duration), span_km=args.span,
                  width=1080 if args.vertical else 1920, height=1920 if args.vertical else 1080)
    if args.duration <= scene.timeline.zoom_end:
        sys.exit(f"--duration must be longer than the zoom ({scene.timeline.zoom_end:g} s)")
    check_timing(scene)
    renderer = Renderer(scene, TileSource(CACHE_DIR))
    out = Path(args.output or Path(args.gpx).with_suffix(".mp4").name)

    if args.preview:
        times = [float(t) for t in args.preview.split(",")]
        renderer.prefetch(times)
        for t in times:
            path = out.with_name(f"{out.stem}_{t:05.2f}s.png")
            renderer.render(t, args.fps).write_to_png(str(path))
            print(path)
        return

    times = renderer.frame_times(args.fps)
    renderer.prefetch(times)
    writer = VideoWriter(str(out), scene.width, scene.height, args.fps)
    start = time.monotonic()
    try:
        for i, frame in enumerate(render_frames(renderer, args.fps), 1):
            writer.write(frame)
            print(f"\rframe {i}/{len(times)}", end="", file=sys.stderr)
    finally:
        writer.close()
    print(f"\n{out} ({time.monotonic() - start:.0f}s)", file=sys.stderr)


if __name__ == "__main__":
    main()
