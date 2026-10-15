"""Text as vector paths: glyph outlines from a TTF via fontTools, drawn with cairo.

Avoids depending on system fonts or cairo's FreeType bindings.
"""

from functools import lru_cache

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont


class _CairoPen(BasePen):
    def __init__(self, glyphset, ctx, x, y, scale):
        super().__init__(glyphset)
        self.ctx, self.x, self.y, self.s = ctx, x, y, scale

    def _pt(self, p):
        return self.x + p[0] * self.s, self.y - p[1] * self.s

    def _moveTo(self, p):
        self.ctx.move_to(*self._pt(p))

    def _lineTo(self, p):
        self.ctx.line_to(*self._pt(p))

    def _curveToOne(self, p1, p2, p3):
        self.ctx.curve_to(*self._pt(p1), *self._pt(p2), *self._pt(p3))

    def _closePath(self):
        self.ctx.close_path()


class Font:
    def __init__(self, path):
        self.tt = TTFont(str(path))
        self.glyphset = self.tt.getGlyphSet()
        self.cmap = self.tt.getBestCmap()
        self.hmtx = self.tt["hmtx"]
        self.upm = self.tt["head"].unitsPerEm
        self.cap_height = getattr(self.tt["OS/2"], "sCapHeight", 0) or self.upm * 0.7

    def _glyph(self, ch):
        return self.cmap.get(ord(ch), ".notdef")

    def measure(self, text: str, size: float) -> float:
        return sum(self.hmtx[self._glyph(c)][0] for c in text) * size / self.upm

    def cap(self, size: float) -> float:
        return self.cap_height * size / self.upm

    def path(self, ctx, text: str, x: float, baseline: float, size: float):
        """Append the outline of `text` to the current cairo path."""
        s = size / self.upm
        for ch in text:
            g = self._glyph(ch)
            self.glyphset[g].draw(_CairoPen(self.glyphset, ctx, x, baseline, s))
            x += self.hmtx[g][0] * s
        return x


@lru_cache(maxsize=None)
def load_font(path) -> Font:
    return Font(path)
