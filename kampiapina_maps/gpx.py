"""GPX loading and distance lookups along the track."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np

from .geo import haversine_m, lonlat_to_world


@dataclass
class Track:
    lat: np.ndarray
    lon: np.ndarray
    km: np.ndarray  # cumulative distance at each point
    wx: np.ndarray  # world coordinates
    wy: np.ndarray

    @property
    def length_km(self) -> float:
        return float(self.km[-1])

    def point_at(self, km: float) -> tuple[float, float]:
        """Interpolated (lat, lon) at a distance along the track."""
        km = min(max(km, 0.0), self.length_km)
        return float(np.interp(km, self.km, self.lat)), float(np.interp(km, self.km, self.lon))

    def world_at(self, km: float) -> tuple[float, float]:
        lat, lon = self.point_at(km)
        x, y = lonlat_to_world(lon, lat)
        return float(x), float(y)


def load_gpx(path: str) -> Track:
    root = ET.parse(path).getroot()
    pts = []
    # Namespace-agnostic: accept track points, falling back to route points.
    for tag in ("trkpt", "rtept"):
        pts = [(float(e.get("lat")), float(e.get("lon"))) for e in root.iter() if e.tag.rsplit("}", 1)[-1] == tag]
        if pts:
            break
    if len(pts) < 2:
        raise ValueError(f"{path}: no track found (need at least 2 trkpt/rtept points)")

    arr = np.array(pts)
    lat, lon = arr[:, 0], arr[:, 1]
    seg = haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:]) / 1000.0
    km = np.concatenate([[0.0], np.cumsum(seg)])
    wx, wy = lonlat_to_world(lon, lat)
    return Track(lat=lat, lon=lon, km=km, wx=wx, wy=wy)
