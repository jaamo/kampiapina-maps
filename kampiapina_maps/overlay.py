"""Everything drawn on top of the map: route, city labels, km marker, attribution."""

import math

import cairocffi as cairo
from cairocffi import cairo as _lib, ffi as _ffi  # raw C API, for native path copy/append

from .camera import Camera
from .theme import font_path, rgb
from .text import load_font
from .timeline import clamp01, ease_out_back, ease_out_cubic

LABEL_GAP = 20  # px between city dot and label text
EDGE_MARGIN = 40


class RoutePath:
    """The track as a native cairo path, built once and replayed every frame.

    Cairo stores paths in 24.8 fixed point, so points are kept relative to the track's
    corner and scaled to span ~4M units: precise enough at any zoom, safely in range.
    """

    def __init__(self, wx, wy):
        self.ox, self.oy = float(wx.min()), float(wy.min())
        extent = max(float(wx.max()) - self.ox, float(wy.max()) - self.oy, 1e-12)
        self.units = 4_000_000 / extent
        ctx = cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1))
        xs = ((wx - self.ox) * self.units).tolist()
        ys = ((wy - self.oy) * self.units).tolist()
        ctx.move_to(xs[0], ys[0])
        for x, y in zip(xs[1:], ys[1:]):
            ctx.line_to(x, y)
        self.path = _ffi.gc(_lib.cairo_copy_path(ctx._pointer), _lib.cairo_path_destroy)

    def draw(self, ctx, cam: Camera, style: dict):
        ctx.new_path()
        ctx.save()
        ctx.translate(*cam.to_screen(self.ox, self.oy))
        k = cam.scale / self.units
        ctx.scale(k, k)
        _lib.cairo_append_path(ctx._pointer, self.path)
        ctx.restore()  # identity again, so line widths are in screen pixels
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        if style.get("outline_width", 0) > 0:
            ctx.set_source_rgb(*rgb(style["outline"]))
            ctx.set_line_width(style["width"] + 2 * style["outline_width"])
            ctx.stroke_preserve()
        ctx.set_source_rgb(*rgb(style["color"]))
        ctx.set_line_width(style["width"])
        ctx.stroke()


class CityLabel:
    def __init__(self, city, style: dict):
        self.city = city
        self.style = style
        self.font = load_font(font_path(style["font"]))
        self.text = city.name.upper() if style.get("uppercase") else city.name
        size = style["size"]
        self.cap = self.font.cap(size)
        self.box_w = self.font.measure(self.text, size) + 2 * style["box_padding_x"]
        self.box_h = self.cap + 2 * style["box_padding_y"]
        self.offset = (LABEL_GAP, -self.box_h / 2)  # box top-left relative to the dot; see place_labels

    def candidates(self):
        """Box top-left offsets around the dot, in order of preference."""
        w, h, g = self.box_w, self.box_h, LABEL_GAP
        return [
            (g, -h / 2), (-g - w, -h / 2),            # right, left
            (g * 0.5, -g - h), (g * 0.5, g),          # above-right, below-right
            (-g * 0.5 - w, -g - h), (-g * 0.5 - w, g),  # above-left, below-left
            (-w / 2, -g - h), (-w / 2, g),            # above, below
        ]

    def draw(self, ctx, cam: Camera, wx: float, wy: float, t_local: float, char_time: float,
             dot_pop: float, alpha: float):
        """t_local: seconds since this label started."""
        if t_local < 0 or alpha <= 0:
            return
        st = self.style
        x, y = cam.to_screen(wx, wy)
        size = st["size"]
        ctx.push_group()

        # Dot
        r = st["dot_radius"] * ease_out_back(t_local / dot_pop)
        if r > 0:
            ctx.arc(x, y, r, 0, 2 * math.pi)
            ctx.set_source_rgb(*rgb(st["dot_outline"]))
            ctx.set_line_width(3)
            ctx.stroke_preserve()
            ctx.set_source_rgb(*rgb(st["dot"]))
            ctx.fill()

        # Text in a box, typed one letter at a time; the box grows with the text.
        n_total = len(self.text)
        n = min(n_total, int(t_local / char_time) + 1)
        px, py = st["box_padding_x"], st["box_padding_y"]
        bx, by = x + self.offset[0], y + self.offset[1]
        shown = self.text[:n]
        ctx.rectangle(bx, by, self.font.measure(shown, size) + 2 * px, self.box_h)
        ctx.set_source_rgb(*rgb(st["box"]))
        ctx.fill()

        ctx.new_path()
        self.font.path(ctx, shown, bx + px, by + py + self.cap, size)
        ctx.set_source_rgb(*rgb(st["color"]))
        ctx.fill()

        ctx.pop_group_to_source()
        ctx.paint_with_alpha(alpha)


def place_labels(labels: list[CityLabel], dots: list[tuple[float, float]], route_pts, width, height):
    """Greedy placement: each label takes the candidate slot with the lowest cost.

    dots: screen positions of the city dots. route_pts: (N, 2) array of screen points
    sampled evenly along the route, used to keep boxes off the track where possible.
    """
    placed = []  # (x0, y0, x1, y1)
    dot_hard = LABEL_GAP * 0.6  # another city's dot under a box: never acceptable
    dot_soft = LABEL_GAP * 2  # near another city's dot: the box might read as that city's label
    for i, (label, (x, y)) in enumerate(zip(labels, dots)):
        best = None
        for rank, (dx, dy) in enumerate(label.candidates()):
            x0, y0 = x + dx, y + dy
            x1, y1 = x0 + label.box_w, y0 + label.box_h
            cost = rank
            if x0 < EDGE_MARGIN or y0 < EDGE_MARGIN or x1 > width - EDGE_MARGIN or y1 > height - EDGE_MARGIN:
                cost += 1000
            cost += 1000 * sum(x0 < bx1 and bx0 < x1 and y0 < by1 and by0 < y1 for bx0, by0, bx1, by1 in placed)
            for j, (ox, oy) in enumerate(dots):
                if j == i:
                    continue
                if x0 - dot_hard < ox < x1 + dot_hard and y0 - dot_hard < oy < y1 + dot_hard:
                    cost += 1000
                elif x0 - dot_soft < ox < x1 + dot_soft and y0 - dot_soft < oy < y1 + dot_soft:
                    cost += 100
            inside = ((route_pts[:, 0] > x0) & (route_pts[:, 0] < x1)
                      & (route_pts[:, 1] > y0) & (route_pts[:, 1] < y1))
            cost += 0.05 * int(inside.sum())
            if best is None or cost < best[0]:
                best = (cost, (dx, dy), (x0, y0, x1, y1))
        label.offset = best[1]
        placed.append(best[2])


def draw_marker(ctx, x: float, y: float, t_local: float, pop: float, period: float, style: dict):
    """Pulsing km marker. t_local: seconds since it appeared."""
    if t_local < 0:
        return
    r0 = style["radius"]
    # Two expanding rings, half a period apart.
    for k in (0.0, 0.5):
        phase = (t_local / period + k) % 1.0
        if t_local / period + k < 1.0 and k > 0:
            continue  # second ring waits for its first cycle
        r = r0 + (style["ring_radius"] - r0) * ease_out_cubic(phase)
        a = (1 - phase) ** 1.5 * clamp01(t_local / pop)
        ctx.arc(x, y, r, 0, 2 * math.pi)
        ctx.set_source_rgba(*rgb(style["ring"]), a)
        ctx.set_line_width(5 * (1 - phase) + 1.5)
        ctx.stroke()

    r = r0 * ease_out_back(t_local / pop)
    if r > 0:
        ctx.arc(x, y, r, 0, 2 * math.pi)
        ctx.set_source_rgb(*rgb(style["outline"]))
        ctx.set_line_width(4)
        ctx.stroke_preserve()
        ctx.set_source_rgb(*rgb(style["color"]))
        ctx.fill()


def draw_attribution(ctx, cam: Camera, style: dict):
    font = load_font(font_path(style["font"]))
    text = "© OpenStreetMap"
    size = style["size"]
    x = cam.width - EDGE_MARGIN * 0.6 - font.measure(text, size)
    y = cam.height - EDGE_MARGIN * 0.6
    ctx.new_path()
    font.path(ctx, text, x, y, size)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.set_source_rgba(*rgb(style["halo"]), style["alpha"])
    ctx.set_line_width(4)
    ctx.stroke_preserve()
    ctx.set_source_rgba(*rgb(style["color"]), style["alpha"])
    ctx.fill()
