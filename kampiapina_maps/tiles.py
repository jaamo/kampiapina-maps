"""OpenFreeMap vector tile fetching, disk caching and decoding."""

import gzip
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mapbox_vector_tile
import requests

from .camera import Camera

TILEJSON_URL = "https://tiles.openfreemap.org/planet"
USER_AGENT = "kampiapina-maps/0.1"


class TileSource:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir) / "tiles"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._template = None
        self.maxzoom = 14
        self._decoded: dict[tuple[int, int, int], dict] = {}

    def _load_tilejson(self):
        if self._template is not None:
            return
        meta_path = self.cache_dir / "tilejson.json"
        try:
            r = self.session.get(TILEJSON_URL, timeout=15)
            r.raise_for_status()
            meta = r.json()
            meta_path.write_text(json.dumps(meta))
        except requests.RequestException:
            if not meta_path.exists():
                raise
            meta = json.loads(meta_path.read_text())
        self._template = meta["tiles"][0]
        self.maxzoom = int(meta.get("maxzoom", 14))

    def _path(self, z, x, y) -> Path:
        return self.cache_dir / str(z) / str(x) / f"{y}.pbf"

    def _fetch(self, key) -> bytes:
        z, x, y = key
        path = self._path(z, x, y)
        if path.exists():
            return path.read_bytes()
        self._load_tilejson()
        url = self._template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
        r = self.session.get(url, timeout=30)
        if r.status_code in (204, 404):
            data = b""
        else:
            r.raise_for_status()
            data = r.content
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    def prefetch(self, keys):
        missing = [k for k in set(keys) if not self._path(*k).exists()]
        if not missing:
            return
        self._load_tilejson()
        print(f"Downloading {len(missing)} map tiles...", file=sys.stderr)
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(self._fetch, missing))

    def get(self, z, x, y) -> dict:
        key = (z, x, y)
        if key not in self._decoded:
            data = self._fetch(key)
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            self._decoded[key] = (
                mapbox_vector_tile.decode(data, default_options={"y_coord_down": True}) if data else {}
            )
        return self._decoded[key]

    def tiles_for(self, cam: Camera, z: int) -> list[tuple[int, int, int]]:
        n = 2**z
        minx, miny, maxx, maxy = cam.world_bounds()
        x0, x1 = math.floor(minx * n), math.floor(maxx * n)
        y0, y1 = max(math.floor(miny * n), 0), min(math.floor(maxy * n), n - 1)
        return [(z, x % n, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
