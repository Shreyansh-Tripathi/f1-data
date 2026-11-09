"""Circuit outline, corners and rotation, in the OpenF1 location frame (units: decimetres)."""

from __future__ import annotations

import itertools
import math
from datetime import datetime
from typing import Any

from f1data.models.circuit import Circuit, Corner

MAX_DERIVED_POINTS = 500
UNITS_PER_METRE = 10


def from_multiviewer(circuit_id: str, name: str, season: int, mv: dict[str, Any], generated_at: datetime) -> Circuit:
    polyline = _dedupe([(round(x), round(y)) for x, y in zip(mv["x"], mv["y"], strict=True)])
    corners = [
        Corner(
            number=c["number"],
            letter=c.get("letter") or None,
            x=round(c["trackPosition"]["x"]),
            y=round(c["trackPosition"]["y"]),
            angle=round(c["angle"], 2),
        )
        for c in sorted(mv["corners"], key=lambda c: (c["number"], c.get("letter") or ""))
    ]
    return Circuit(
        generated_at=generated_at,
        id=circuit_id,
        name=name,
        season=season,
        polyline=polyline,
        corners=corners,
        rotation=float(mv["rotation"]),
        length_m=_length_m(polyline),
        source="official",
    )


def from_location(
    circuit_id: str, name: str, season: int, samples: list[dict[str, Any]], generated_at: datetime
) -> Circuit:
    """Fallback outline from one lap of OpenF1 location samples. No corners, no rotation."""
    points = _dedupe([(round(s["x"]), round(s["y"])) for s in sorted(samples, key=lambda s: s["date"])])
    step = max(1, math.ceil(len(points) / MAX_DERIVED_POINTS))
    polyline = points[::step]
    return Circuit(
        generated_at=generated_at,
        id=circuit_id,
        name=name,
        season=season,
        polyline=polyline,
        corners=[],
        rotation=0.0,
        length_m=_length_m(polyline),
        source="derived",
    )


def _dedupe(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    return [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]


def _length_m(polyline: list[tuple[int, int]]) -> int | None:
    """Closed-outline perimeter; approximate (slightly short of the official length)."""
    if len(polyline) < 3:
        return None
    closed = [*polyline, polyline[0]]
    return round(sum(math.dist(a, b) for a, b in itertools.pairwise(closed)) / UNITS_PER_METRE)
