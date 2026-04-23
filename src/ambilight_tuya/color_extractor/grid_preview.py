from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ambilight_tuya.models import RGBColor


@dataclass(frozen=True)
class GridPreviewCell:
    index: int
    row: int
    col: int
    rgb: RGBColor

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "row": self.row,
            "col": self.col,
            "rgb": list(self.rgb.as_tuple()),
            "hex": f"#{self.rgb.r:02x}{self.rgb.g:02x}{self.rgb.b:02x}",
        }


class ScreenGridPreviewExtractor:
    def __init__(self, rows: int = 4, cols: int = 4) -> None:
        self.rows = rows
        self.cols = cols

    def extract(self, frame: np.ndarray) -> list[GridPreviewCell]:
        height, width, _ = frame.shape
        cells: list[GridPreviewCell] = []
        for row in range(self.rows):
            for col in range(self.cols):
                x0 = int(width * col / self.cols)
                x1 = max(x0 + 1, int(width * (col + 1) / self.cols))
                y0 = int(height * row / self.rows)
                y1 = max(y0 + 1, int(height * (row + 1) / self.rows))
                region = frame[y0:y1, x0:x1]
                cells.append(
                    GridPreviewCell(
                        index=(row * self.cols) + col,
                        row=row,
                        col=col,
                        rgb=self._representative_color(region),
                    )
                )
        return cells

    def _representative_color(self, region: np.ndarray) -> RGBColor:
        if region.size == 0:
            return RGBColor(0, 0, 0)

        height, width, _ = region.shape
        row_stride = max(1, height // 18)
        col_stride = max(1, width // 18)
        sampled_region = region[::row_stride, ::col_stride]
        mean_rgb = sampled_region.reshape(-1, 3).mean(axis=0)
        return RGBColor(*mean_rgb)
