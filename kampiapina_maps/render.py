"""Frame generation: camera per frame, map + overlays composited per frame."""

import sys
from dataclasses import dataclass

import cairocffi as cairo
import numpy as np

from .camera import Camera, camera_for_span, fit_bounds, flight
from .geo import lonlat_to_world, meters_per_world_unit
from .geocode import City
from .gpx import Track
from .mapdraw import MapRenderer
from .overlay import CityLabel, draw_attribution, draw_marker, draw_route, place_labels
from .timeline import Timeline, clamp01, ease_in_out_cubic
from .tiles import TileSource


@dataclass
class Scene:
    track: Track
    km: float
    cities: list[City]
    theme: dict
    timeline: Timeline
    span_km: float = 3.0
    width: int = 1920
    height: int = 1080


class Renderer:
    def __init__(self, scene: Scene, tiles: TileSource):
        self.s = scene
        self.tl = scene.timeline
        self.map = MapRenderer(scene.theme, tiles)
        self.tiles = tiles
        self.labels = [CityLabel(c, scene.theme["labels"]) for c in scene.cities]
        self.city_world = [tuple(map(float, lonlat_to_world(c.lon, c.lat))) for c in scene.cities]
        self.route_wx, self.route_wy = self._simplified_route()

        tr = scene.track
        xs = np.concatenate([tr.wx, [p[0] for p in self.city_world]])
        ys = np.concatenate([tr.wy, [p[1] for p in self.city_world]])
        self.start_cam = fit_bounds(xs.min(), ys.min(), xs.max(), ys.max(), scene.width, scene.height)

        # Frame the end view on the middle of the marker's journey so it stays well inside.
        mid_km = scene.km + self.tl.marker_travel_km / 2
        lat, _ = tr.point_at(mid_km)
        cx, cy = tr.world_at(mid_km)
        self.end_cam = camera_for_span(cx, cy, lat, scene.span_km, scene.width, scene.height)
        self.fly = flight(self.start_cam, self.end_cam)
        self._map_cache: tuple[Camera, cairo.ImageSurface] | None = None
        self._place_labels()

    def _place_labels(self):
        cam, tr = self.start_cam, self.s.track
        # Route points every ~4 px on screen, so the overlap penalty measures length, not GPS density.
        km_per_px = meters_per_world_unit(float(tr.lat.mean())) / cam.scale / 1000.0
        kms = np.arange(0.0, tr.length_km, 4 * km_per_px)
        sx, sy = cam.to_screen(np.interp(kms, tr.km, tr.wx), np.interp(kms, tr.km, tr.wy))
        dots = [cam.to_screen(wx, wy) for wx, wy in self.city_world]
        place_labels(self.labels, dots, np.column_stack([sx, sy]), cam.width, cam.height)

    def _simplified_route(self):
        """Drop points closer than ~1 m apart; keeps path drawing fast for dense GPX files."""
        tr = self.s.track
        keep = np.concatenate([[True], np.diff(tr.km) > 0.001])
        keep[-1] = True
        return tr.wx[keep], tr.wy[keep]

    def camera_at(self, t: float) -> Camera:
        tl = self.tl
        u = ease_in_out_cubic((t - tl.zoom_start) / (tl.zoom_end - tl.zoom_start))
        return self.fly(u)

    def frame_times(self, fps: int):
        return [i / fps for i in range(round(self.tl.duration * fps))]

    def prefetch(self, times):
        keys = set()
        for t in times:
            keys.update(self.map.tile_keys(self.camera_at(t)))
        self.tiles.prefetch(keys)

    def _map_for(self, cam: Camera) -> cairo.ImageSurface:
        # Camera is static during the holds, so consecutive frames reuse the map.
        if self._map_cache is None or self._map_cache[0] != cam:
            self._map_cache = (cam, self.map.render(cam))
        return self._map_cache[1]

    def render(self, t: float) -> cairo.ImageSurface:
        s, tl, th = self.s, self.tl, self.s.theme
        cam = self.camera_at(t)
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, s.width, s.height)
        ctx = cairo.Context(surf)
        ctx.set_source_surface(self._map_for(cam))
        ctx.paint()

        draw_route(ctx, cam, self.route_wx, self.route_wy, th["route"], clamp01(t / tl.route_fade_in))

        label_alpha = 1.0 - clamp01((t - tl.zoom_start) / tl.labels_fade)
        for i, (label, (wx, wy)) in enumerate(zip(self.labels, self.city_world)):
            label.draw(ctx, cam, wx, wy, t - tl.label_start(i), tl.char_time, tl.dot_pop,
                       label_alpha)

        t_marker = t - tl.marker_start
        if t_marker >= 0:
            km = s.km + t_marker * tl.marker_speed_kmps
            mx, my = cam.to_screen(*s.track.world_at(km))
            draw_marker(ctx, mx, my, t_marker, tl.marker_pop, tl.pulse_period, th["marker"])

        draw_attribution(ctx, cam, th["attribution"])
        surf.flush()
        return surf


def check_timing(scene: Scene):
    tl = scene.timeline
    if not scene.cities:
        return
    last = len(scene.cities) - 1
    longest = max(len(c.name) for c in scene.cities)
    done = tl.label_start(last) + longest * tl.char_time
    if done > tl.zoom_start:
        print(f"warning: city labels finish typing at {done:.2f}s, after the zoom starts "
              f"({tl.zoom_start:.2f}s). Consider fewer cities.", file=sys.stderr)
