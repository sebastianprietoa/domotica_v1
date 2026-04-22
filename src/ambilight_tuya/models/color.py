from __future__ import annotations

from dataclasses import dataclass
import colorsys
import math


def _clamp_channel(value: float) -> int:
    return max(0, min(255, int(round(value))))


@dataclass(frozen=True)
class RGBColor:
    r: int
    g: int
    b: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "r", _clamp_channel(self.r))
        object.__setattr__(self, "g", _clamp_channel(self.g))
        object.__setattr__(self, "b", _clamp_channel(self.b))

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.r, self.g, self.b)

    def distance(self, other: "RGBColor") -> float:
        return math.dist(self.as_tuple(), other.as_tuple())

    def blend(self, other: "RGBColor", alpha: float) -> "RGBColor":
        return RGBColor(
            r=(1 - alpha) * self.r + alpha * other.r,
            g=(1 - alpha) * self.g + alpha * other.g,
            b=(1 - alpha) * self.b + alpha * other.b,
        )

    def to_hsv(self) -> "HSVColor":
        hue, saturation, value = colorsys.rgb_to_hsv(
            self.r / 255.0, self.g / 255.0, self.b / 255.0
        )
        return HSVColor(
            h=int(round(hue * 360)) % 360,
            s=int(round(saturation * 1000)),
            v=int(round(value * 1000)),
        )


@dataclass(frozen=True)
class HSVColor:
    h: int
    s: int
    v: int

    def as_dict(self) -> dict[str, int]:
        return {"h": self.h, "s": self.s, "v": self.v}
