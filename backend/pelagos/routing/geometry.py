"""Pelagos Maritime Routing - Geodesic Geometry and Distance Utilities.

Provides explicit, consistent geodesic calculations along the WGS 84 ellipsoid,
strictly conforming to the international nautical mile standard:
    1 Nautical Mile (NM) = 1,852 meters = 1.852 kilometers (exact).
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple, Optional

try:
    import pyproj
    _GEOD = pyproj.Geod(ellps="WGS84")
except ImportError:  # pragma: no cover
    _GEOD = None

METERS_PER_NAUTICAL_MILE: float = 1852.0
KM_PER_NAUTICAL_MILE: float = 1.852
WGS84_EQUATORIAL_RADIUS_M: float = 6378137.0

NAV_SAFETY_DISCLAIMER: str = (
    "Coastline intersection checks and sea-lane graph paths reflect macro navigational "
    "topology only and do not constitute certified navigational safety, hydrographic soundings, "
    "or ECDIS passage approval."
)


def validate_coordinates(longitude: float, latitude: float) -> Tuple[bool, Optional[str]]:
    """Validate that longitude and latitude represent valid, finite WGS 84 coordinates.

    Args:
        longitude: Longitude in degrees, expected within [-180.0, 180.0].
        latitude: Latitude in degrees, expected within [-90.0, 90.0].

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not isinstance(longitude, (int, float)) or not isinstance(latitude, (int, float)):
        return False, f"Coordinates must be numeric; got types ({type(longitude).__name__}, {type(latitude).__name__})"

    if math.isnan(longitude) or math.isnan(latitude) or math.isinf(longitude) or math.isinf(latitude):
        return False, "Coordinates must be finite real numbers; NaN or Inf encountered"

    if not (-180.0 <= longitude <= 180.0):
        return False, f"Longitude {longitude} out of valid WGS 84 range [-180.0, 180.0]"

    if not (-90.0 <= latitude <= 90.0):
        return False, f"Latitude {latitude} out of valid WGS 84 range [-90.0, 90.0]"

    return True, None


def geodesic_distance_nm(
    lon1: float,
    lat1: float,
    lon2: float,
    lat2: float,
) -> float:
    """Calculate the geodesic distance between two points in Nautical Miles (NM).

    Uses the WGS 84 ellipsoidal inverse geodesic via pyproj.Geod. If pyproj is unavailable,
    falls back to the great-circle Haversine formula on a mean spherical radius.

    Args:
        lon1: Longitude of point 1 in degrees.
        lat1: Latitude of point 1 in degrees.
        lon2: Longitude of point 2 in degrees.
        lat2: Latitude of point 2 in degrees.

    Returns:
        Distance in nautical miles (float).
    """
    # Identical points have 0 distance
    if lon1 == lon2 and lat1 == lat2:
        return 0.0

    if _GEOD is not None:
        _, _, distance_m = _GEOD.inv(lon1, lat1, lon2, lat2)
        return float(distance_m) / METERS_PER_NAUTICAL_MILE

    # Fallback Haversine spherical formula
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    distance_m = WGS84_EQUATORIAL_RADIUS_M * c
    return distance_m / METERS_PER_NAUTICAL_MILE


def nm_to_km(distance_nm: float) -> float:
    """Convert nautical miles to kilometers using the exact 1.852 standard ratio.

    Args:
        distance_nm: Distance in nautical miles.

    Returns:
        Distance in kilometers rounded to 3 decimal places.
    """
    return round(distance_nm * KM_PER_NAUTICAL_MILE, 3)


def km_to_nm(distance_km: float) -> float:
    """Convert kilometers to nautical miles using the exact 1.852 standard ratio.

    Args:
        distance_km: Distance in kilometers.

    Returns:
        Distance in nautical miles rounded to 3 decimal places.
    """
    return round(distance_km / KM_PER_NAUTICAL_MILE, 3)


def path_cumulative_distance_nm(coordinates: Sequence[Sequence[float]]) -> float:
    """Calculate the cumulative geodesic distance along an ordered sequence of [lon, lat] pairs.

    Args:
        coordinates: Ordered sequence of [longitude, latitude] pairs.

    Returns:
        Total distance in nautical miles.
    """
    if len(coordinates) < 2:
        return 0.0

    total_nm = 0.0
    for i in range(len(coordinates) - 1):
        p1, p2 = coordinates[i], coordinates[i + 1]
        total_nm += geodesic_distance_nm(p1[0], p1[1], p2[0], p2[1])

    return round(total_nm, 3)
