"""Stylized base map: land, landcover, water and roads from OpenMapTiles-schema vector tiles."""

import math

import cairocffi as cairo

from .camera import Camera
from .theme import rgb
from .tiles import TileSource

# Tiles are fetched at camera zoom + DETAIL_BIAS so the map carries a bit more detail
# than a standard web map at the same scale.
DETAIL_BIAS = 1.0
# While the camera zoom moves through the last part of a level, the next level's
# map fades in on top, so roads appearing or disappearing never pop.
CROSSFADE_START = 0.55

ROAD_GROUPS = {
    "motorway": "major", "trunk": "major", "primary": "major",
    "secondary": "mid", "tertiary": "mid",
    "minor": "minor", "service": "minor", "busway": "minor", "raceway": "minor",
    "track": "path", "path": "path",
}
ROAD_ORDER = ("path", "minor", "mid", "major")
ROAD_GROWTH = 1.45  # road width multiplier per zoom level
WATERWAY_WIDTH = {"river": 3.0, "canal": 2.4, "stream": 1.0, "ditch": 0.8, "drain": 0.8}
FOREST_CLASSES = {"wood", "forest"}
FIELD_CLASSES = {"grass", "farmland", "wetland", "park"}


def _smoothstep(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


class MapRenderer:
    def __init__(self, theme: dict, tiles: TileSource):
        self.theme = theme
        self.tiles = tiles

    def levels(self, cam: Camera) -> list[tuple[int, float]]:
        """Tile zoom levels to draw for a camera, with opacity for each."""
        maxz = self.tiles.maxzoom
        z = cam.zoom + DETAIL_BIAS
        lo = max(0, min(math.floor(z), maxz))
        if lo >= maxz:
            return [(maxz, 1.0)]
        fade = _smoothstep((z - lo - CROSSFADE_START) / (1 - CROSSFADE_START))
        return [(lo, 1.0)] + ([(lo + 1, fade)] if fade > 0 else [])

    def tile_keys(self, cam: Camera):
        return [k for z, _ in self.levels(cam) for k in self.tiles.tiles_for(cam, z)]

    def render(self, cam: Camera) -> cairo.ImageSurface:
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, cam.width, cam.height)
        ctx = cairo.Context(surf)
        ctx.set_source_rgb(*rgb(self.theme["map"]["land"]))
        ctx.paint()
        for z, alpha in self.levels(cam):
            if alpha >= 1.0:
                self._draw_level(ctx, cam, z)
            else:
                ctx.push_group()
                ctx.set_source_rgb(*rgb(self.theme["map"]["land"]))
                ctx.paint()
                self._draw_level(ctx, cam, z)
                ctx.pop_group_to_source()
                ctx.paint_with_alpha(alpha)
        return surf

    def _draw_level(self, ctx, cam: Camera, z: int):
        m = self.theme["map"]
        tiles = [(k, self.tiles.get(*k)) for k in self.tiles.tiles_for(cam, z)]
        n = 2**z
        s = cam.scale

        def features(layer, pred=lambda p: True):
            """Yields (geometry, properties, scale, offset_x, offset_y) mapping tile coords to screen."""
            for (_, tx, ty), data in tiles:
                lyr = data.get(layer)
                if not lyr:
                    continue
                a = s / (n * lyr.get("extent", 4096))
                bx = (tx / n - cam.cx) * s + cam.width / 2
                by = (ty / n - cam.cy) * s + cam.height / 2
                for f in lyr["features"]:
                    if pred(f["properties"]):
                        yield f["geometry"], f["properties"], a, bx, by

        def fill(layer, color, pred=lambda p: True):
            ctx.new_path()
            for geom, _, a, bx, by in features(layer, pred):
                _add_polygons(ctx, geom, a, bx, by)
            ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
            ctx.set_source_rgb(*rgb(color))
            ctx.fill()

        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)

        fill("landcover", m["field"], lambda p: p.get("class") in FIELD_CLASSES)
        fill("park", m["field"])
        fill("landcover", m["forest"], lambda p: p.get("class") in FOREST_CLASSES)
        fill("water", m["water"])

        # Rivers and streams, grouped by width.
        by_width = {}
        for geom, props, a, bx, by in features("waterway"):
            w = WATERWAY_WIDTH.get(props.get("class"), 1.0) * ROAD_GROWTH ** (cam.zoom - 13)
            if w >= 0.4:
                by_width.setdefault(round(w, 2), []).append((geom, a, bx, by))
        ctx.set_source_rgb(*rgb(m["water"]))
        for w, items in by_width.items():
            ctx.new_path()
            for geom, a, bx, by in items:
                _add_lines(ctx, geom, a, bx, by)
            ctx.set_line_width(w)
            ctx.stroke()

        # Roads: all casings first, then fills, from minor to major so big roads sit on top.
        roads = {g: [] for g in ROAD_ORDER}
        for geom, props, a, bx, by in features("transportation"):
            group = ROAD_GROUPS.get(props.get("class"))
            if group and geom["type"] in ("LineString", "MultiLineString"):
                roads[group].append((geom, a, bx, by))
        styles = self.theme["roads"]
        widths = {g: styles[g]["width"] * ROAD_GROWTH ** (cam.zoom - 13) for g in ROAD_ORDER}
        casing_px = min(max((cam.zoom - 10) * 0.6, 0.0), 1.8)
        for pass_ in ("casing", "fill"):
            for g in ROAD_ORDER:
                st = styles[g]
                if not roads[g] or widths[g] < 0.3:
                    continue
                if pass_ == "casing":
                    if "casing" not in st or casing_px <= 0:
                        continue
                    color, w = st["casing"], widths[g] + 2 * casing_px
                else:
                    color, w = st["color"], widths[g]
                ctx.new_path()
                for geom, a, bx, by in roads[g]:
                    _add_lines(ctx, geom, a, bx, by)
                ctx.set_source_rgb(*rgb(color))
                ctx.set_line_width(w)
                ctx.stroke()


def _add_ring(ctx, ring, a, bx, by, close):
    x, y = ring[0]
    ctx.move_to(bx + x * a, by + y * a)
    for x, y in ring[1:]:
        ctx.line_to(bx + x * a, by + y * a)
    if close:
        ctx.close_path()


def _add_polygons(ctx, geom, a, bx, by):
    t, coords = geom["type"], geom["coordinates"]
    polys = [coords] if t == "Polygon" else coords if t == "MultiPolygon" else []
    for poly in polys:
        for ring in poly:
            if len(ring) > 2:
                _add_ring(ctx, ring, a, bx, by, True)


def _add_lines(ctx, geom, a, bx, by):
    t, coords = geom["type"], geom["coordinates"]
    lines = [coords] if t == "LineString" else coords if t == "MultiLineString" else []
    for line in lines:
        if len(line) > 1:
            _add_ring(ctx, line, a, bx, by, False)
