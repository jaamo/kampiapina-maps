"""Camera state and the zoom flight between two views."""

import math
from dataclasses import dataclass

from .geo import TILE_SIZE, meters_per_world_unit


@dataclass(frozen=True)
class Camera:
    cx: float  # center, world coordinates
    cy: float
    zoom: float
    width: int = 1920
    height: int = 1080

    @property
    def scale(self) -> float:
        """Screen pixels per world unit."""
        return TILE_SIZE * 2.0**self.zoom

    def to_screen(self, wx, wy):
        s = self.scale
        return (wx - self.cx) * s + self.width / 2, (wy - self.cy) * s + self.height / 2

    def world_bounds(self) -> tuple[float, float, float, float]:
        hw, hh = self.width / 2 / self.scale, self.height / 2 / self.scale
        return self.cx - hw, self.cy - hh, self.cx + hw, self.cy + hh


def fit_bounds(minx, miny, maxx, maxy, width, height, padding=0.12) -> Camera:
    dx, dy = max(maxx - minx, 1e-9), max(maxy - miny, 1e-9)
    fx = width * (1 - 2 * padding) / (dx * TILE_SIZE)
    fy = height * (1 - 2 * padding) / (dy * TILE_SIZE)
    zoom = math.log2(min(fx, fy))
    return Camera((minx + maxx) / 2, (miny + maxy) / 2, zoom, width, height)


def camera_for_span(wx, wy, lat, span_km, width, height) -> Camera:
    """Camera centered on a point showing `span_km` of ground across the frame width."""
    world_span = span_km * 1000.0 / meters_per_world_unit(lat)
    zoom = math.log2(width / (world_span * TILE_SIZE))
    return Camera(wx, wy, zoom, width, height)


def flight(a: Camera, b: Camera, rho: float = 1.2):
    """Smooth zoom-and-pan path (van Wijk & Nuij 2003, as used by map flyTo).

    Returns f(u) -> Camera for u in [0, 1] with perceptually constant speed;
    apply easing to u before calling.
    """
    w0 = a.width / a.scale
    w1 = b.width / b.scale
    ux, uy = b.cx - a.cx, b.cy - a.cy
    u1 = math.hypot(ux, uy)

    if u1 < 1e-12 * w0:
        k = 1.0 if w1 > w0 else -1.0
        total = abs(math.log(w1 / w0)) / rho

        def w_at(s):
            return w0 * math.exp(k * rho * s)

        def u_at(s):
            return 0.0
    else:
        def b_(i):
            wi = w0 if i == 0 else w1
            return (w1 * w1 - w0 * w0 + (-1) ** i * rho**4 * u1 * u1) / (2 * wi * rho**2 * u1)

        def r_(i):
            bi = b_(i)
            return math.log(math.sqrt(bi * bi + 1) - bi)

        r0, r1 = r_(0), r_(1)
        total = (r1 - r0) / rho

        def w_at(s):
            return w0 * math.cosh(r0) / math.cosh(rho * s + r0)

        def u_at(s):
            return w0 / rho**2 * (math.cosh(r0) * math.tanh(rho * s + r0) - math.sinh(r0))

    def at(u: float) -> Camera:
        if u <= 0:
            return a
        if u >= 1:
            return b
        s = u * total
        frac = u_at(s) / u1 if u1 > 0 else 0.0
        w = w_at(s)
        zoom = math.log2(a.width / (w * TILE_SIZE))
        return Camera(a.cx + ux * frac, a.cy + uy * frac, zoom, a.width, a.height)

    return at
