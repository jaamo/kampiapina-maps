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

    def trim(self, start_km: float, end_km: float) -> "Track":
        """Drop `start_km` from the beginning and `end_km` from the end (privacy: hide home/work).

        The cut points are interpolated exactly at the requested distances and the
        returned track's `km` restarts from 0.
        """
        start_km, end_km = max(start_km, 0.0), max(end_km, 0.0)
        lo, hi = start_km, self.length_km - end_km
        if hi - lo < 0.1:
            raise ValueError(
                f"track is {self.length_km:.1f} km; trimming {start_km:g} + {end_km:g} km leaves nothing")
        inner = (self.km > lo) & (self.km < hi)
        km = np.concatenate([[lo], self.km[inner], [hi]])
        lat = np.interp(km, self.km, self.lat)
        lon = np.interp(km, self.km, self.lon)
        wx, wy = lonlat_to_world(lon, lat)
        return Track(lat=lat, lon=lon, km=km - lo, wx=wx, wy=wy)


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
