#!/usr/bin/env python3
"""Create a small RDShape Stage-1 venue from a Phase-0 geodata package."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[1]
CONVERTER_DIR = SOURCE_ROOT / "converter" if (SOURCE_ROOT / "converter").exists() else SOURCE_ROOT
sys.path.insert(0, str(CONVERTER_DIR))

from converteur import (  # noqa: E402
    Basemap,
    Surface,
    apply_presentations,
    build_tcl,
    is_rectangular_quad,
    occt_environment,
    orient_trapezium_points,
    polygon_normal,
    run_draw_script,
)


def numeric_tag(tags: dict[str, Any], key: str) -> float | None:
    value = tags.get(key)
    if value is None:
        return None
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    return float(match.group().replace(",", "."))


def building_heights(tags: dict[str, Any]) -> tuple[float, float]:
    base = numeric_tag(tags, "min_height")
    if base is None:
        base = (numeric_tag(tags, "building:min_level") or 0.0) * 3.0
    top = numeric_tag(tags, "height")
    if top is None:
        levels = numeric_tag(tags, "building:levels")
        top = base + levels * 3.0 if levels is not None else 8.0
    return max(0.0, base), max(base + 0.5, top)


def clean_ring(ring: list[list[float]]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for raw in ring:
        point = (float(raw[0]), float(raw[1]))
        if not points or math.dist(point, points[-1]) > 1e-6:
            points.append(point)
    if len(points) > 1 and math.dist(points[0], points[-1]) <= 1e-6:
        points.pop()
    return points


def signed_area(ring: list[tuple[float, float]]) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1])) / 2.0


def point_in_triangle(point: tuple[float, float], triangle: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]) -> bool:
    (px, py), ((ax, ay), (bx, by), (cx, cy)) = point, triangle
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
    d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
    return not ((d1 < -1e-9 or d2 < -1e-9 or d3 < -1e-9) and (d1 > 1e-9 or d2 > 1e-9 or d3 > 1e-9))


def triangulate_ring(ring: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Triangulate a simple polygon using deterministic ear clipping."""
    if len(ring) < 3 or abs(signed_area(ring)) <= 1e-8:
        return []
    points = ring if signed_area(ring) > 0 else list(reversed(ring))
    remaining = list(range(len(points)))
    triangles: list[list[tuple[float, float]]] = []
    while len(remaining) > 3:
        clipped = False
        for position, current in enumerate(remaining):
            previous = remaining[position - 1]
            following = remaining[(position + 1) % len(remaining)]
            a, b, c = points[previous], points[current], points[following]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if cross <= 1e-9:
                continue
            triangle = (a, b, c)
            if any(point_in_triangle(points[index], triangle) for index in remaining if index not in (previous, current, following)):
                continue
            triangles.append([a, b, c])
            del remaining[position]
            clipped = True
            break
        if not clipped:
            return []
    triangles.append([points[index] for index in remaining])
    return triangles


def is_convex_ring(ring: list[tuple[float, float]]) -> bool:
    if len(ring) < 3:
        return False
    signs = []
    for index in range(len(ring)):
        a, b, c = ring[index - 1], ring[index], ring[(index + 1) % len(ring)]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) > 1e-9:
            signs.append(cross > 0)
    return bool(signs) and all(sign == signs[0] for sign in signs)


def footprint_key(ring: list[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    """Return an order-independent key for coincident OSM footprint vertices."""
    return tuple(sorted((round(x, 5), round(y, 5)) for x, y in ring))


def surfaces_for_building(feature: dict[str, Any], ground_offset: float = 0.0) -> list[Surface]:
    ring = clean_ring(feature["geometry"]["coordinates"][0])
    roof_parts = [ring] if len(ring) == 4 and is_convex_ring(ring) else triangulate_ring(ring)
    if not roof_parts:
        return []
    tags = feature.get("properties", {})
    base, top = building_heights(tags)
    base += ground_offset
    top += ground_offset
    label = tags.get("name") or feature.get("id", "Building")
    kind = f'building={tags["building"]}' if "building" in tags else f'building:part={tags.get("building:part", "yes")}'
    notes = "; ".join([f'OpenStreetMap {feature.get("id", "")}', kind, f'ground offset={ground_offset:g} m', f'local base={base:g} m', f'local top={top:g} m'])
    result = [Surface(
        f"{label} - Roof {index}" if len(roof_parts) > 1 else f"{label} - Roof",
        [(x, y, top) for x, y in triangle], None, True, "Buildings", (0.65, 0.65, 0.62, 1.0), notes=notes,
    ) for index, triangle in enumerate(roof_parts, 1)]
    for index, ((x1, y1), (x2, y2)) in enumerate(zip(ring, ring[1:] + ring[:1]), 1):
        result.append(Surface(
            f"{label} - Wall {index}",
            [(x1, y1, base), (x2, y2, base), (x2, y2, top), (x1, y1, top)],
            None, True, "Buildings", (0.72, 0.70, 0.65, 1.0), notes=notes,
        ))
    return result


def select_buildings(document: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    by_footprint: dict[tuple[tuple[float, float], ...], dict[str, Any]] = {}
    for feature in document.get("features", []):
        ring = clean_ring(feature["geometry"]["coordinates"][0])
        if not triangulate_ring(ring):
            continue
        key = footprint_key(ring)
        previous = by_footprint.get(key)
        is_part = "building:part" in feature.get("properties", {})
        previous_is_part = previous is not None and "building:part" in previous.get("properties", {})
        if previous is None or (is_part and not previous_is_part):
            by_footprint[key] = feature
    candidates = []
    for feature in by_footprint.values():
        ring = clean_ring(feature["geometry"]["coordinates"][0])
        cx = sum(point[0] for point in ring) / len(ring)
        cy = sum(point[1] for point in ring) / len(ring)
        candidates.append((math.hypot(cx, cy), feature))
    ordered = [feature for _, feature in sorted(candidates, key=lambda item: item[0])]
    return ordered[:limit] if limit > 0 else ordered


def terrain_height(terrain: dict[str, Any], x: float, y: float) -> float:
    rows = terrain["samples"]
    valid_rows = [row for row in rows if row and all(point is not None for point in row)]
    if len(valid_rows) < 2 or len(valid_rows[0]) < 2:
        raise ValueError("Terrain grid does not contain enough valid samples")
    xs = [float(point[0]) for point in valid_rows[0]]
    ys = [float(row[0][1]) for row in valid_rows]
    x = min(max(x, xs[0]), xs[-1])
    y = min(max(y, min(ys)), max(ys))
    column = next((index for index in range(len(xs) - 1) if xs[index] <= x <= xs[index + 1]), len(xs) - 2)
    row_index = next((index for index in range(len(ys) - 1) if ys[index] >= y >= ys[index + 1]), len(ys) - 2)
    x0, x1 = xs[column], xs[column + 1]
    y0, y1 = ys[row_index], ys[row_index + 1]
    tx = 0.0 if math.isclose(x0, x1) else (x - x0) / (x1 - x0)
    ty = 0.0 if math.isclose(y0, y1) else (y0 - y) / (y0 - y1)
    z00 = float(valid_rows[row_index][column][2])
    z10 = float(valid_rows[row_index][column + 1][2])
    z01 = float(valid_rows[row_index + 1][column][2])
    z11 = float(valid_rows[row_index + 1][column + 1][2])
    return (1 - ty) * ((1 - tx) * z00 + tx * z10) + ty * ((1 - tx) * z01 + tx * z11)


def terrain_surfaces(terrain: dict[str, Any], width: float, height: float, step: float) -> list[Surface]:
    columns = max(1, math.ceil(width / step))
    rows = max(1, math.ceil(height / step))
    xs = [-width / 2 + width * index / columns for index in range(columns + 1)]
    ys = [-height / 2 + height * index / rows for index in range(rows + 1)]
    notes = f'{terrain["provider"]} {terrain["dataset"]}; {terrain.get("surfaceModel", "DEM")}; source grid {terrain["columns"]}x{terrain["rows"]}; reference elevation {terrain["referenceElevation"]:g} m'
    result: list[Surface] = []
    for row in range(rows):
        for column in range(columns):
            p00 = (xs[column], ys[row], terrain_height(terrain, xs[column], ys[row]))
            p10 = (xs[column + 1], ys[row], terrain_height(terrain, xs[column + 1], ys[row]))
            p11 = (xs[column + 1], ys[row + 1], terrain_height(terrain, xs[column + 1], ys[row + 1]))
            p01 = (xs[column], ys[row + 1], terrain_height(terrain, xs[column], ys[row + 1]))
            result.append(Surface(f"Terrain {row + 1}-{column + 1} A", [p00, p10, p11], None, True, "Terrain", (0.45, 0.55, 0.38, 1.0), 0.72, notes=notes))
            result.append(Surface(f"Terrain {row + 1}-{column + 1} B", [p00, p11, p01], None, True, "Terrain", (0.45, 0.55, 0.38, 1.0), 0.72, notes=notes))
    return result


def rotate_xy(point: tuple[float, float, float], heading_degrees: float) -> tuple[float, float, float]:
    radians = math.radians(heading_degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    x, y, z = point
    return cosine * x - sine * y, sine * x + cosine * y, z


def rotate_surfaces(items: list[Surface], heading_degrees: float) -> None:
    if math.isclose(heading_degrees % 360.0, 0.0, abs_tol=1e-9):
        return
    for item in items:
        item.points = [rotate_xy(point, heading_degrees) for point in item.points]


def prepare_surfaces(document: dict[str, Any], limit: int, terrain: dict[str, Any] | None = None) -> list[Surface]:
    selected = select_buildings(document, limit)
    items = []
    for feature in selected:
        ring = clean_ring(feature["geometry"]["coordinates"][0])
        center_x = sum(point[0] for point in ring) / len(ring)
        center_y = sum(point[1] for point in ring) / len(ring)
        offset = terrain_height(terrain, center_x, center_y) if terrain is not None else 0.0
        items.extend(surfaces_for_building(feature, offset))
    for item in items:
        polygon_normal(item.points)
        if len(item.points) == 4 and not is_rectangular_quad(item.points):
            item.points = orient_trapezium_points(item.points)
            item.native_kind = "trapezium"
    items.sort(key=lambda item: item.native_kind is not None)
    return items


def write_s3d(package: Path, output: Path, occt: Path, templates: Path, limit: int, terrain_step: float, heading: float = 0.0) -> None:
    project = json.loads((package / "project.json").read_text(encoding="utf-8"))
    local_buildings = json.loads((package / project["buildings"]["localFile"]).read_text(encoding="utf-8"))
    terrain = json.loads((package / project["terrain"]["file"]).read_text(encoding="utf-8")) if "terrain" in project else None
    building_items = prepare_surfaces(local_buildings, limit, terrain)
    terrain_items = terrain_surfaces(terrain, float(project["extentMetres"]["width"]), float(project["extentMetres"]["height"]), terrain_step) if terrain is not None else []
    items = building_items + terrain_items
    rotate_surfaces(items, heading)
    extent = project["extentMetres"]
    basemap_z = min((point[2] for item in terrain_items for point in item.points), default=0.0) - 0.05
    radians = math.radians(heading)
    cosine, sine = math.cos(radians), math.sin(radians)
    basemap = Basemap(
        "Satellite imagery",
        (package / project["imagery"]["file"]).read_bytes(),
        (0.0, 0.0, basemap_z),
        (cosine, sine, 0.0),
        (-sine, cosine, 0.0),
        float(extent["width"]),
        float(extent["height"]),
        1.0,
    )
    template = templates / "20_20_20.s3d"
    group_template = templates / "group_template.s3d"
    trapezium_template = templates / "trapezium_template.s3d"
    basemap_template = templates / "basemap_template.s3d"
    output.parent.mkdir(parents=True, exist_ok=True)
    draw, environment = occt_environment(occt)
    script = build_tcl(template, output, items, group_template=group_template, trapezium_template=trapezium_template, basemaps=[basemap], basemap_template=basemap_template)
    environment["THIRDPARTY_DIR"] = str(occt / "3rdparty-vc14-64")
    code = run_draw_script(draw, environment, output.parent, script)
    appended = Path(str(output) + ".cbf")
    if not output.exists() and appended.exists():
        appended.replace(output)
    if code != 0 or not output.exists():
        raise RuntimeError(f"OCCT could not create the S3D file (exit code {code})")
    apply_presentations(output, items, draw, environment, occt, [basemap], [])
    building_count = len(select_buildings(local_buildings, limit))
    print(f"Created {output} with {len(building_items)} building surfaces, {len(terrain_items)} terrain surfaces from {building_count} buildings, one basemap and heading {heading:g} degrees")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--occt", type=Path, required=True)
    parser.add_argument("--templates", type=Path, default=Path("templates/rdshape"))
    parser.add_argument("--limit", type=int, default=3, help="number of nearest usable footprints; 0 imports all")
    parser.add_argument("--terrain-step", type=float, default=25.0, help="terrain output grid spacing in metres")
    parser.add_argument("--heading", type=float, default=0.0, help="clockwise direction from geographic north that becomes RDShape +Y")
    args = parser.parse_args()
    write_s3d(args.package.resolve(), args.output.resolve(), args.occt.resolve(), args.templates.resolve(), args.limit, args.terrain_step, args.heading)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
