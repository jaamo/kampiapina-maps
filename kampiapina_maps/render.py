"""Frame generation: camera per frame, map + overlays composited per frame."""

import math
import multiprocessing as mp
import os
import sys
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from itertools import islice

import cairocffi as cairo
import numpy as np

from .camera import Camera, camera_for_span, fit_bounds, flight
from .geo import lonlat_to_world, meters_per_world_unit
from .geocode import City
from .gpx import Track
from .mapdraw import MapRenderer
from .overlay import CityLabel, RoutePath, draw_attribution, draw_marker, place_labels
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
        self.route = RoutePath(*self._simplified_route())

        tr = scene.track
        xs = np.concatenate([tr.wx, [p[0] for p in self.city_world]])
        ys = np.concatenate([tr.wy, [p[1] for p in self.city_world]])
        self.start_cam = fit_bounds(xs.min(), ys.min(), xs.max(), ys.max(), scene.width, scene.height)

        lat, _ = tr.point_at(scene.km)
        self.end_zoom = camera_for_span(0, 0, lat, scene.span_km, scene.width, scene.height).zoom
        # Labels are placed for the overview as it looks while they are on screen.
        self._place_labels(self.overview_cam(self.tl.zoom_start))

    def _place_labels(self, cam: Camera):
        tr = self.s.track
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

    def marker_km(self, t: float) -> float:
        return self.s.km + self.tl.marker_km_offset(t)

    def overview_cam(self, t: float) -> Camera:
        """Full route, slowly pushing in."""
        return replace(self.start_cam, zoom=self.start_cam.zoom + self.tl.overview_zoom_rate * t)

    def follow_cam(self, t: float) -> Camera:
        """Centered on the marker, slowly pushing in."""
        cx, cy = self.s.track.world_at(self.marker_km(t))
        zoom = self.end_zoom + self.tl.follow_zoom_rate * (t - self.tl.zoom_end)
        return replace(self.start_cam, cx=cx, cy=cy, zoom=zoom)

    def camera_at(self, t: float) -> Camera:
        # The flight runs between two cameras that are both moving, so the camera's motion
        # carries straight on from the overview drift and into following the marker.
        tl = self.tl
        u = ease_in_out_cubic((t - tl.zoom_start) / (tl.zoom_end - tl.zoom_start))
        if u <= 0:
            return self.overview_cam(t)
        if u >= 1:
            return self.follow_cam(t)
        return flight(self.overview_cam(t), self.follow_cam(t))(u)

    def frame_times(self, fps: int):
        return [i / fps for i in range(round(self.tl.duration * fps))]

    def _blur_margin(self) -> int:
        return math.ceil(self.tl.max_blur_px) + 4

    def _padded(self, cam: Camera) -> Camera:
        """Same view with a margin, so blur sub-frames shifted or zoomed out still have content."""
        m = self._blur_margin()
        return replace(cam, width=cam.width + 2 * m, height=cam.height + 2 * m)

    def prefetch(self, times):
        """Download tiles and build their paths up front; forked render workers inherit them."""
        keys = set()
        for t in times:
            keys.update(self.map.tile_keys(self._padded(self.camera_at(t))))
        self.tiles.prefetch(keys)
        for k in keys:
            self.map.tile_paths(k)

    def _motion_px(self, t: float, dt: float) -> float:
        """Roughly how far screen content moves between t and t + dt."""
        a, b = self.camera_at(t), self.camera_at(t + dt)
        pan = math.hypot(b.cx - a.cx, b.cy - a.cy) * a.scale
        half_diag = math.hypot(a.width, a.height) / 2
        return pan + abs(2.0 ** (b.zoom - a.zoom) - 1) * half_diag

    def render(self, t: float, fps: int = 30) -> cairo.ImageSurface:
        """Frame at time t, with motion blur.

        Within one shutter interval the scene barely changes apart from the camera, so the
        frame is drawn once with a margin and each blur sub-frame is that image re-projected
        to the sub-frame's camera. The sub-frames are then averaged.
        """
        tl, s = self.tl, self.s
        cam = self.camera_at(t)
        open_ = tl.shutter / fps
        motion = self._motion_px(t - open_ / 2, open_)
        if motion > tl.max_blur_px:
            open_ *= tl.max_blur_px / motion
            motion = tl.max_blur_px
        n = max(math.ceil(motion / tl.blur_px_per_sample), 1)

        if n == 1:
            out = self.render_scene(t, cam)
        else:
            big_cam = self._padded(cam)
            big = self.render_scene(t, big_cam)
            sub = cairo.ImageSurface(cairo.FORMAT_RGB24, s.width, s.height)
            sub_px = np.frombuffer(sub.get_data(), np.uint8)
            ctx = cairo.Context(sub)
            acc = None
            for k in range(n):
                c = self.camera_at(t - open_ / 2 + open_ * (k + 0.5) / n)
                # big-image pixel -> world (big_cam) -> sub-frame screen (c)
                k_scale = c.scale / big_cam.scale
                ctx.identity_matrix()
                ctx.translate(*c.to_screen(big_cam.cx, big_cam.cy))
                ctx.scale(k_scale, k_scale)
                ctx.translate(-big_cam.width / 2, -big_cam.height / 2)
                ctx.set_source_surface(big)
                ctx.get_source().set_filter(cairo.FILTER_GOOD)
                ctx.paint()
                sub.flush()
                acc = sub_px.astype(np.uint32) if acc is None else acc + sub_px
            out = cairo.ImageSurface(cairo.FORMAT_RGB24, s.width, s.height)
            np.frombuffer(out.get_data(), np.uint8)[:] = ((acc + n // 2) // n).astype(np.uint8)
            out.mark_dirty()

        # Credit stays sharp and fixed in the corner.
        draw_attribution(cairo.Context(out), cam, s.theme["attribution"])
        out.flush()
        return out

    def render_scene(self, t: float, cam: Camera) -> cairo.ImageSurface:
        """Map, route, labels and marker at time t, seen through `cam`."""
        s, tl, th = self.s, self.tl, self.s.theme
        surf = self.map.render(cam)
        ctx = cairo.Context(surf)

        self.route.draw(ctx, cam, th["route"])

        label_alpha = 1.0 - clamp01((t - tl.zoom_start) / tl.labels_fade)
        for i, (label, (wx, wy)) in enumerate(zip(self.labels, self.city_world)):
            label.draw(ctx, cam, wx, wy, t - tl.label_start(i), tl.char_time, tl.dot_pop,
                       label_alpha)

        t_marker = t - tl.marker_start
        if t_marker >= 0:
            mx, my = cam.to_screen(*s.track.world_at(self.marker_km(t)))
            draw_marker(ctx, mx, my, t_marker, tl.marker_pop, tl.pulse_period, th["marker"])

        surf.flush()
        return surf


# Frames are rendered in worker processes; each inherits the renderer when forked.
_worker_renderer: Renderer | None = None


def _render_bytes(t: float, fps: int) -> bytes:
    # Keep the surface referenced while copying: get_data() is a raw view into its memory.
    surface = _worker_renderer.render(t, fps)
    return bytes(surface.get_data())


def render_frames(renderer: Renderer, fps: int, workers: int | None = None):
    """Yields raw BGRx frames in order, rendered in parallel.

    Only a few frames are in flight at once, so finished frames never pile up in memory.
    If a worker dies (e.g. out of memory) this raises BrokenProcessPool instead of hanging.
    """
    global _worker_renderer
    _worker_renderer = renderer
    workers = workers or os.cpu_count() or 1
    times = iter(renderer.frame_times(fps))
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("fork")) as pool:
        pending = deque(pool.submit(_render_bytes, t, fps) for t in islice(times, 2 * workers))
        while pending:
            frame = pending.popleft().result()
            for t in islice(times, 1):
                pending.append(pool.submit(_render_bytes, t, fps))
            yield frame


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
