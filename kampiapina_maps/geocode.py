"""City name -> coordinates via OpenStreetMap Nominatim, cached on disk."""

import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .tiles import USER_AGENT

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@dataclass
class City:
    name: str
    lat: float
    lon: float


class Geocoder:
    def __init__(self, cache_dir: Path):
        self.cache_path = Path(cache_dir) / "geocode.json"
        self.cache = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self._last_request = 0.0

    def resolve(self, spec: str, bbox: tuple[float, float, float, float]) -> City:
        """`spec` is "Name" or "Name@lat,lon". bbox is (minlon, minlat, maxlon, maxlat) to bias results."""
        if "@" in spec:
            name, coords = spec.rsplit("@", 1)
            lat, lon = (float(v) for v in coords.split(","))
            return City(name.strip(), lat, lon)

        name = spec.strip()
        if name.lower() in self.cache:
            lat, lon = self.cache[name.lower()]
            return City(name, lat, lon)

        minlon, minlat, maxlon, maxlat = bbox
        m = 0.3  # degrees of slack around the route
        viewbox = f"{minlon - m},{maxlat + m},{maxlon + m},{minlat - m}"
        hit = self._search(name, viewbox, bounded=1) or self._search(name, viewbox, bounded=0)
        if not hit:
            raise ValueError(f'Could not find "{name}". Give coordinates instead: "{name}@lat,lon"')
        lat, lon = float(hit["lat"]), float(hit["lon"])
        self.cache[name.lower()] = [lat, lon]
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1))
        return City(name, lat, lon)

    def _search(self, name, viewbox, bounded):
        # Nominatim usage policy: max 1 request per second.
        wait = 1.0 - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        r = requests.get(
            NOMINATIM_URL,
            params={"q": name, "format": "json", "limit": 1, "viewbox": viewbox, "bounded": bounded},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json()
        return results[0] if results else None
