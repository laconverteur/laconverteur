#!/usr/bin/env python3
"""Download a georeferenced Phase-0 venue package from a metric map extent."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EARTH_RADIUS = 6_378_137.0
TILE_SIZE = 256
USER_AGENT = "La-Converteur/0.1 (mailto:laconverteur@proton.me)"
DEFAULT_IMAGERY = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
DEFAULT_OVERPASS = "https://overpass-api.de/api/interpreter"
FALLBACK_OVERPASS = "https://overpass.private.coffee/api/interpreter"
SECONDARY_FALLBACK_OVERPASS = "https://overpass.kumi.systems/api/interpreter"
OVERPASS_REQUEST_TIMEOUT = 12


def mercator_from_lon_lat(lon: float, lat: float) -> tuple[float, float]:
    lat = max(-85.05112878, min(85.05112878, lat))
    return EARTH_RADIUS * math.radians(lon), EARTH_RADIUS * math.asinh(math.tan(math.radians(lat)))


def lon_lat_from_mercator(x: float, y: float) -> tuple[float, float]:
    return math.degrees(x / EARTH_RADIUS), math.degrees(math.atan(math.sinh(y / EARTH_RADIUS)))


def local_from_lon_lat(lon: float, lat: float, center_lon: float, center_lat: float) -> tuple[float, float]:
    """Return local conformal east/north metres centred on the origin."""
    x, y = mercator_from_lon_lat(lon, lat)
    cx, cy = mercator_from_lon_lat(center_lon, center_lat)
    scale = math.cos(math.radians(center_lat))
    return (x - cx) * scale, (y - cy) * scale


def bounds_for_metric_extent(lat: float, lon: float, width: float, height: float) -> dict[str, float]:
    cx, cy = mercator_from_lon_lat(lon, lat)
    scale = math.cos(math.radians(lat))
    west, south = lon_lat_from_mercator(cx - width / (2 * scale), cy - height / (2 * scale))
    east, north = lon_lat_from_mercator(cx + width / (2 * scale), cy + height / (2 * scale))
    return {"south": south, "west": west, "north": north, "east": east}


def global_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    size = 2**zoom * TILE_SIZE
    lat_rad = math.radians(max(-85.05112878, min(85.05112878, lat)))
    return ((lon + 180.0) / 360.0 * size, (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * size)


def request_bytes(url: str, *, data: bytes | None = None, content_type: str | None = None, attempts: int = 3, timeout: float = 60) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=headers)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt + 1 == attempts:
                raise
            time.sleep(1 << attempt)
    raise RuntimeError("Network request failed")


def cached_request(cache: Path, key: str, url: str, *, data: bytes | None = None, content_type: str | None = None, attempts: int = 3, timeout: float = 60) -> bytes:
    cache.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((key + "\n" + url + "\n").encode() + (data or b"")).hexdigest()
    target = cache / digest
    if not target.exists():
        target.write_bytes(request_bytes(url, data=data, content_type=content_type, attempts=attempts, timeout=timeout))
    return target.read_bytes()


def download_imagery(bounds: dict[str, float], zoom: int, cache: Path, output: Path, endpoint: str) -> dict[str, Any]:
    left, top = global_pixel(bounds["west"], bounds["north"], zoom)
    right, bottom = global_pixel(bounds["east"], bounds["south"], zoom)
    min_x, max_x = math.floor(left / TILE_SIZE), math.floor((right - 1e-9) / TILE_SIZE)
    min_y, max_y = math.floor(top / TILE_SIZE), math.floor((bottom - 1e-9) / TILE_SIZE)
    tiles: list[dict[str, Any]] = []
    tile_dir = cache / "imagery"
    tile_dir.mkdir(parents=True, exist_ok=True)
    for tile_y in range(min_y, max_y + 1):
        for tile_x in range(min_x, max_x + 1):
            url = endpoint.format(z=zoom, x=tile_x, y=tile_y)
            tile_path = tile_dir / f"{zoom}-{tile_x}-{tile_y}.jpg"
            if not tile_path.exists():
                tile_path.write_bytes(request_bytes(url))
            tiles.append({"path": str(tile_path.resolve()), "globalX": tile_x * TILE_SIZE, "globalY": tile_y * TILE_SIZE})
    manifest = {"left": left, "top": top, "pixelWidth": right - left, "pixelHeight": bottom - top, "tiles": tiles}
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
        json.dump(manifest, handle)
        manifest_path = Path(handle.name)
    try:
        helper = Path(__file__).with_name("stitch_tiles.ps1")
        powershell = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
        subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper), "-Manifest", str(manifest_path), "-Output", str(output)], check=True)
    finally:
        manifest_path.unlink(missing_ok=True)
    return {"zoom": zoom, "pixelWidth": math.ceil(right - left), "pixelHeight": math.ceil(bottom - top), "tileCount": len(tiles)}


def overpass_query(bounds: dict[str, float]) -> str:
    bbox = f'{bounds["south"]:.8f},{bounds["west"]:.8f},{bounds["north"]:.8f},{bounds["east"]:.8f}'
    return (
        f'[out:json][timeout:25];('
        f'way["building"]({bbox});relation["building"]({bbox});'
        f'way["building:part"]({bbox});relation["building:part"]({bbox});'
        f');out body;>;out skel qt;'
    )


def way_polygon(way: dict[str, Any], nodes: dict[int, tuple[float, float]]) -> list[list[float]] | None:
    coordinates = [nodes[node_id] for node_id in way.get("nodes", []) if node_id in nodes]
    if len(coordinates) < 3:
        return None
    if coordinates[0] != coordinates[-1]:
        coordinates.append(coordinates[0])
    if len(set(coordinates[:-1])) < 3:
        return None
    return [[lon, lat] for lon, lat in coordinates]


def download_buildings(bounds: dict[str, float], cache: Path, endpoint: str) -> tuple[dict[str, Any], str]:
    payload = urllib.parse.urlencode({"data": overpass_query(bounds)}).encode()
    endpoints = [endpoint]
    if endpoint == DEFAULT_OVERPASS:
        endpoints.extend((FALLBACK_OVERPASS, SECONDARY_FALLBACK_OVERPASS))
    last_error: Exception | None = None
    for candidate in endpoints:
        print(f"Trying Overpass instance: {urllib.parse.urlsplit(candidate).netloc}", file=sys.stderr)
        try:
            # Building selections are deliberately small. Retrying the same
            # overloaded public endpoint for minutes only freezes the GUI;
            # fail over once after a short, bounded request instead.
            raw = cached_request(
                cache, "overpass-buildings", candidate, data=payload,
                content_type="application/x-www-form-urlencoded",
                attempts=1, timeout=OVERPASS_REQUEST_TIMEOUT,
            )
            used_endpoint = candidate
            break
        except urllib.error.HTTPError as error:
            last_error = error
            detail = error.read().decode("utf-8", errors="replace")
            detail = re.sub(r"<[^>]+>", " ", detail)
            detail = " ".join(detail.split())
            if candidate == endpoints[-1]:
                raise RuntimeError(f"Overpass returned HTTP {error.code}: {detail[:500]}") from None
            print(f"Overpass returned HTTP {error.code}; trying fallback instance ...", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if candidate == endpoints[-1]:
                raise RuntimeError(f"Overpass request failed: {error}") from None
            print("Overpass request timed out; trying fallback instance ...", file=sys.stderr)
    else:
        raise RuntimeError("No Overpass endpoint returned data") from last_error
    elements = json.loads(raw).get("elements", [])
    nodes = {item["id"]: (item["lon"], item["lat"]) for item in elements if item.get("type") == "node"}
    ways = {item["id"]: item for item in elements if item.get("type") == "way"}
    relation_outer_ids = {member["ref"] for relation in elements if relation.get("type") == "relation" and ({"building", "building:part"} & relation.get("tags", {}).keys()) for member in relation.get("members", []) if member.get("type") == "way" and member.get("role", "") in ("", "outer")}
    features: list[dict[str, Any]] = []
    for way_id, way in ways.items():
        tags = way.get("tags", {})
        if not ({"building", "building:part"} & tags.keys()) and way_id not in relation_outer_ids:
            continue
        polygon = way_polygon(way, nodes)
        if polygon is not None:
            features.append({"type": "Feature", "id": f"way/{way_id}", "properties": {"osmType": "way", "osmId": way_id, **tags}, "geometry": {"type": "Polygon", "coordinates": [polygon]}})
    return {"type": "FeatureCollection", "features": features}, used_endpoint


def buildings_to_local(buildings: dict[str, Any], center_lon: float, center_lat: float) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for feature in buildings["features"]:
        rings = []
        for ring in feature["geometry"]["coordinates"]:
            rings.append([[*local_from_lon_lat(lon, lat, center_lon, center_lat)] for lon, lat in ring])
        features.append({
            "id": feature["id"],
            "properties": feature["properties"],
            "geometry": {"type": "Polygon", "coordinates": rings},
        })
    return {"coordinateSystem": "local-conformal-ENU", "unit": "metre", "features": features}


def write_project(output: Path, lat: float, lon: float, width: float, height: float, zoom: int, imagery_endpoint: str, overpass_endpoint: str, elevation_dataset: str | None = None, imagery_only: bool = False, skip_buildings: bool = False) -> None:
    if width <= 0 or height <= 0 or width > 5000 or height > 5000:
        raise ValueError("Width and height must be greater than zero and at most 5000 metres")
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "cache"
    bounds = bounds_for_metric_extent(lat, lon, width, height)
    imagery = download_imagery(bounds, zoom, cache, output / "imagery.png", imagery_endpoint)
    if imagery_only or skip_buildings:
        buildings = {"type": "FeatureCollection", "features": []}
        used_overpass_endpoint = None
    else:
        buildings, used_overpass_endpoint = download_buildings(bounds, cache, overpass_endpoint)
        local_buildings = buildings_to_local(buildings, lon, lat)
    local_buildings = buildings_to_local(buildings, lon, lat)
    (output / "buildings.geojson").write_text(json.dumps(buildings, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "buildings-local.json").write_text(json.dumps(local_buildings, indent=2, ensure_ascii=False), encoding="utf-8")
    project = {
        "schemaVersion": 1,
        "origin": {"latitude": lat, "longitude": lon, "altitude": 0.0},
        "localCoordinateSystem": {"type": "local-conformal-ENU", "x": "east", "y": "north", "z": "up", "unit": "metre"},
        "extentMetres": {"width": width, "height": height},
        "boundsWgs84": bounds,
        "imagery": {"file": "imagery.png", **imagery, "provider": "Esri World Imagery"},
        "buildings": {"file": "buildings.geojson", "localFile": "buildings-local.json", "featureCount": len(buildings["features"]), "provider": "OpenStreetMap/Overpass", "endpoint": used_overpass_endpoint},
    }
    if elevation_dataset:
        from geo.elevation import add_elevation_to_package
        project["terrain"] = add_elevation_to_package(output, bounds, lon, lat, elevation_dataset)
    (output / "project.json").write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "attribution.txt").write_text("Imagery: Esri World Imagery and its listed data providers.\nBuilding data: © OpenStreetMap contributors, ODbL 1.0.\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--width", type=float, default=200.0)
    parser.add_argument("--height", type=float, default=200.0)
    parser.add_argument("--zoom", type=int, default=18, choices=range(0, 21))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--imagery-endpoint", default=os.environ.get("LACONVERTEUR_IMAGERY_ENDPOINT", DEFAULT_IMAGERY))
    parser.add_argument("--overpass-endpoint", default=os.environ.get("LACONVERTEUR_OVERPASS_ENDPOINT", DEFAULT_OVERPASS))
    parser.add_argument("--elevation", choices=("GEDTM30", "COP30", "COP90", "SRTMGL1", "NASADEM", "EU_DTM"), help="optional OpenTopography elevation dataset; GEDTM30 is the global terrain default")
    parser.add_argument("--imagery-only", action="store_true", help="download only satellite imagery for a fast GUI preview")
    parser.add_argument("--skip-buildings", action="store_true", help="omit OpenStreetMap buildings while retaining optional terrain")
    args = parser.parse_args()
    write_project(args.output.resolve(), args.lat, args.lon, args.width, args.height, args.zoom, args.imagery_endpoint, args.overpass_endpoint, args.elevation, args.imagery_only, args.skip_buildings)
    print(f"Wrote Phase-0 geo package to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
