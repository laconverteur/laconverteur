"""Elevation provider abstractions for geodata packages."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .geo_phase0 import USER_AGENT, bounds_for_metric_extent, local_from_lon_lat


@dataclass
class AsciiGrid:
    columns: int
    rows: int
    x_origin: float
    y_origin: float
    cell_size: float
    nodata: float
    values: list[list[float]]


def parse_ascii_grid(text: str) -> AsciiGrid:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    header: dict[str, float] = {}
    data_start = 0
    for index, line in enumerate(lines):
        parts = line.split()
        key = parts[0].lower()
        if key not in {"ncols", "nrows", "xllcorner", "xllcenter", "yllcorner", "yllcenter", "cellsize", "nodata_value"}:
            data_start = index
            break
        header[key] = float(parts[1])
    required = {"ncols", "nrows", "cellsize"}
    missing = sorted(required - header.keys())
    if missing:
        preview = " ".join(text.split())[:300]
        raise ValueError(f"OpenTopography returned no valid AAIGrid (missing {', '.join(missing)}): {preview}")
    columns, rows = int(header["ncols"]), int(header["nrows"])
    values = [[float(value) for value in line.split()] for line in lines[data_start:]]
    if len(values) != rows or any(len(row) != columns for row in values):
        raise ValueError("Invalid AAIGrid dimensions")
    cell_size = header["cellsize"]
    x_origin = header.get("xllcenter", header.get("xllcorner", 0.0) + cell_size / 2)
    y_origin = header.get("yllcenter", header.get("yllcorner", 0.0) + cell_size / 2)
    return AsciiGrid(columns, rows, x_origin, y_origin, cell_size, header.get("nodata_value", -9999.0), values)


def nearest_valid_reference(grid: AsciiGrid, center_lon: float, center_lat: float) -> float:
    column = round((center_lon - grid.x_origin) / grid.cell_size)
    row_from_bottom = round((center_lat - grid.y_origin) / grid.cell_size)
    row = grid.rows - 1 - row_from_bottom
    candidates = []
    for radius in range(max(grid.columns, grid.rows)):
        for y in range(max(0, row - radius), min(grid.rows, row + radius + 1)):
            for x in range(max(0, column - radius), min(grid.columns, column + radius + 1)):
                value = grid.values[y][x]
                if not math.isclose(value, grid.nodata):
                    candidates.append((abs(x - column) + abs(y - row), value))
        if candidates:
            return min(candidates)[1]
    raise ValueError("Elevation grid contains no valid samples")


def grid_to_terrain(grid: AsciiGrid, center_lon: float, center_lat: float, provider: str, dataset: str) -> dict[str, Any]:
    reference = nearest_valid_reference(grid, center_lon, center_lat)
    samples = []
    for row_index, row in enumerate(grid.values):
        latitude = grid.y_origin + (grid.rows - 1 - row_index) * grid.cell_size
        sample_row = []
        for column_index, elevation in enumerate(row):
            longitude = grid.x_origin + column_index * grid.cell_size
            x, y = local_from_lon_lat(longitude, latitude, center_lon, center_lat)
            sample_row.append(None if math.isclose(elevation, grid.nodata) else [x, y, elevation - reference])
        samples.append(sample_row)
    return {
        "provider": provider,
        "dataset": dataset,
        "surfaceModel": "DSM" if dataset in {"COP30", "COP90", "AW3D30"} else "DTM",
        "coordinateSystem": "local-conformal-ENU",
        "unit": "metre",
        "referenceElevation": reference,
        "columns": grid.columns,
        "rows": grid.rows,
        "sourceCellSizeDegrees": grid.cell_size,
        "samples": samples,
    }


class OpenTopographyProvider:
    endpoint = "https://portal.opentopography.org/API/globaldem"

    def __init__(self, api_key: str, dataset: str = "COP30") -> None:
        if not api_key:
            raise ValueError("LACONVERTEUR_OPENTOPOGRAPHY_API_KEY is not set")
        self.api_key = api_key
        self.dataset = dataset

    def download(self, bounds: dict[str, float], cache: Path) -> AsciiGrid:
        cache.mkdir(parents=True, exist_ok=True)
        identity = "-".join(f"{bounds[key]:.8f}" for key in ("south", "north", "west", "east"))
        target = cache / f"opentopography-{self.dataset}-{identity}.asc"
        if not target.exists():
            parameters = {
                "demtype": self.dataset,
                "south": f'{bounds["south"]:.8f}',
                "north": f'{bounds["north"]:.8f}',
                "west": f'{bounds["west"]:.8f}',
                "east": f'{bounds["east"]:.8f}',
                "outputFormat": "AAIGrid",
                "API_Key": self.api_key,
            }
            url = self.endpoint + "?" + urllib.parse.urlencode(parameters)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = response.read()
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace").replace(self.api_key, "[redacted]")
                detail = " ".join(detail.split())[:500]
                raise RuntimeError(f"OpenTopography returned HTTP {error.code}: {detail}") from None
            except urllib.error.URLError as error:
                raise RuntimeError(f"OpenTopography request failed: {error.reason}") from None
            target.write_bytes(payload)
        try:
            return parse_ascii_grid(target.read_text(encoding="ascii", errors="replace"))
        except ValueError:
            target.unlink(missing_ok=True)
            raise


def add_elevation_to_package(output: Path, bounds: dict[str, float], center_lon: float, center_lat: float, dataset: str) -> dict[str, Any]:
    key = os.environ.get("LACONVERTEUR_OPENTOPOGRAPHY_API_KEY", "")
    provider = OpenTopographyProvider(key, dataset)
    west_x, _ = local_from_lon_lat(bounds["west"], center_lat, center_lon, center_lat)
    east_x, _ = local_from_lon_lat(bounds["east"], center_lat, center_lon, center_lat)
    _, south_y = local_from_lon_lat(center_lon, bounds["south"], center_lon, center_lat)
    _, north_y = local_from_lon_lat(center_lon, bounds["north"], center_lon, center_lat)
    request_width = max(300.0, east_x - west_x)
    request_height = max(300.0, north_y - south_y)
    request_bounds = bounds_for_metric_extent(center_lat, center_lon, request_width, request_height)
    grid = provider.download(request_bounds, output / "cache" / "elevation")
    terrain = grid_to_terrain(grid, center_lon, center_lat, "OpenTopography", dataset)
    (output / "terrain.json").write_text(json.dumps(terrain, indent=2), encoding="utf-8")
    return {
        "file": "terrain.json",
        "provider": terrain["provider"],
        "dataset": dataset,
        "surfaceModel": terrain["surfaceModel"],
        "columns": terrain["columns"],
        "rows": terrain["rows"],
        "referenceElevation": terrain["referenceElevation"],
        "sourceCellSizeDegrees": terrain["sourceCellSizeDegrees"],
        "requestExtentMetres": {"width": request_width, "height": request_height},
    }
