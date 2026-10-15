"""Web Mercator helpers.

"World" coordinates are normalized Web Mercator: x and y both in [0, 1],
origin at the top-left (lon -180, lat ~85), y growing southwards.
"""

import math

import numpy as np

EARTH_RADIUS_M = 6_371_008.8
EARTH_CIRCUMFERENCE_M = 2 * math.pi * 6_378_137.0
TILE_SIZE = 256


def lonlat_to_world(lon, lat):
    """Works on scalars and numpy arrays."""
    lon = np.asarray(lon, dtype=float)
    lat = np.clip(np.asarray(lat, dtype=float), -85.05112878, 85.05112878)
    x = (lon + 180.0) / 360.0
    s = np.sin(np.radians(lat))
    y = 0.5 - np.log((1 + s) / (1 - s)) / (4 * math.pi)
    return x, y


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in meters. Works on numpy arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


def meters_per_world_unit(lat):
    """Ground meters covered by one world unit horizontally at a latitude."""
    return EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat))
