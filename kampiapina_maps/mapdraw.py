"""Stylized base map: land, landcover, water and roads from OpenMapTiles-schema vector tiles."""

import math

import cairocffi as cairo
from cairocffi import cairo as _lib, ffi as _ffi  # raw C API, for native path copy/append

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
TILE_UNITS = 4096  # cached tile paths are stored in these units (cairo keeps paths in 24.8 fixed point)
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
        self._paths: dict[tuple[int, int, int], dict] = {}
        self._scratch = cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1))  # for building paths

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

    def tile_paths(self, key) -> dict:
        """Outlines of one tile, grouped by style, as native cairo paths in tile units (0..4096).

        Built once per tile and replayed each frame with a single C call; issuing every
        point from Python per frame is far too slow once the camera never stops moving.
        """
        if key in self._paths:
            return self._paths[key]
        data = self.tiles.get(*key)
        ctx = self._scratch
        groups = {}

        def build(name, layer, add, pred=lambda p: True):
            lyr = data.get(layer)
            if not lyr:
                return
            a = TILE_UNITS / lyr.get("extent", 4096)
            ctx.new_path()
            hit = False
            for f in lyr["features"]:
                if pred(f["properties"]):
                    hit = add(ctx, f["geometry"], a, 0.0, 0.0) or hit
            if hit:
                groups[name] = _ffi.gc(_lib.cairo_copy_path(ctx._pointer), _lib.cairo_path_destroy)

        build("field", "landcover", _add_polygons, lambda p: p.get("class") in FIELD_CLASSES)
        build("park", "park", _add_polygons)
        build("forest", "landcover", _add_polygons, lambda p: p.get("class") in FOREST_CLASSES)
        build("water", "water", _add_polygons)
        for cls in WATERWAY_WIDTH:
            build(f"waterway:{cls}", "waterway", _add_lines, lambda p, c=cls: p.get("class") == c)
        for g in ROAD_ORDER:
            build(f"road:{g}", "transportation", _add_lines, lambda p, g=g: ROAD_GROUPS.get(p.get("class")) == g)
        ctx.new_path()
        self._paths[key] = groups
        return groups

    def _draw_level(self, ctx, cam: Camera, z: int):
        m = self.theme["map"]
        n = 2**z
        s = cam.scale
        a = s / (n * TILE_UNITS)
        placed = []  # (paths, offset_x, offset_y)
        for key in self.tiles.tiles_for(cam, z):
            _, tx, ty = key
            placed.append((self.tile_paths(key),
                           (tx / n - cam.cx) * s + cam.width / 2,
                           (ty / n - cam.cy) * s + cam.height / 2))

        def path(name) -> bool:
            """Set the current path to group `name` across all visible tiles, in screen space."""
            ctx.new_path()
            hit = False
            for groups, bx, by in placed:
                p = groups.get(name)
                if p is not None:
                    ctx.save()
                    ctx.translate(bx, by)
                    ctx.scale(a, a)
                    _lib.cairo_append_path(ctx._pointer, p)
                    ctx.restore()  # back to identity, so line widths are in screen pixels
                    hit = True
            return hit

        def fill(name, color):
            if path(name):
                ctx.set_source_rgb(*rgb(color))
                ctx.fill()

        def stroke(name, color, width):
            if width >= 0.3 and path(name):
                ctx.set_source_rgb(*rgb(color))
                ctx.set_line_width(width)
                ctx.stroke()

        ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)

        fill("field", m["field"])
        fill("park", m["field"])
        fill("forest", m["forest"])
        fill("water", m["water"])

        growth = ROAD_GROWTH ** (cam.zoom - 13)
        for cls, w in WATERWAY_WIDTH.items():
            if w * growth >= 0.4:
                stroke(f"waterway:{cls}", m["water"], w * growth)

        # Roads: all casings first, then fills, from minor to major so big roads sit on top.
        styles = self.theme["roads"]
        casing_px = min(max((cam.zoom - 10) * 0.6, 0.0), 1.8)
        for g in ROAD_ORDER:
            st = styles[g]
            if "casing" in st and casing_px > 0:
                stroke(f"road:{g}", st["casing"], st["width"] * growth + 2 * casing_px)
        for g in ROAD_ORDER:
            stroke(f"road:{g}", styles[g]["color"], styles[g]["width"] * growth)


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
    added = False
    for poly in polys:
        for ring in poly:
            if len(ring) > 2:
                _add_ring(ctx, ring, a, bx, by, True)
                added = True
    return added


def _add_lines(ctx, geom, a, bx, by):
    t, coords = geom["type"], geom["coordinates"]
    lines = [coords] if t == "LineString" else coords if t == "MultiLineString" else []
    added = False
    for line in lines:
        if len(line) > 1:
            _add_ring(ctx, line, a, bx, by, False)
            added = True
    return added
