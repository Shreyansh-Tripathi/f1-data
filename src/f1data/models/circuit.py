from __future__ import annotations

from typing import Literal

from f1data.models.base import Model, OutputFile


class Corner(Model):
    number: int
    letter: str | None
    x: int
    y: int
    angle: float


class Circuit(OutputFile):
    """Coordinates are in the OpenF1 location frame; the app applies `rotation`."""

    id: str
    name: str
    season: int
    polyline: list[tuple[int, int]]
    corners: list[Corner]
    rotation: float
    length_m: int | None
    source: Literal["official", "derived"]
