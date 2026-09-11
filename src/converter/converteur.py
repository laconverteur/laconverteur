#!/usr/bin/env python3
"""Convert Soundvision, ArrayCalc and EASE Focus venues to RDShape S3D.

Encrypted inputs are decoded with Soundvision's AES-256-CBC parameters. Planar
The importer registry normalizes each supported source into common planar
surfaces; the OCCT writer then creates Shapes, Audiences, groups and colors in
the actual BinOcaf container.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import sqlite3
import struct

from e5_eprj_prototype import decode_eprj, decode_number


KEY_HEX = "3141333041364533444444333334343432363642453831414246433632373232"
IV_HEX = "32464343394231383639433131324146"
POINT_GUID = "4fbc89f8-cbf2-4503-910b-a599848f9de5"
NORMAL_GUID = "206e1d42-95e5-497f-8444-623684e663bd"
ORIGIN_GUID = "3adcb999-7f5f-4626-9f5e-f2d0bedb2448"
AXIS_GUID = "2214ab1a-7162-4ad0-8cb6-b760fd7294be"
DIRECTION_GUID = "ece5e8b8-f5f8-4c03-b601-be7ea6a40207"
RECEIVER_COLOR_INDICES = (407, 230, 22, 503, 313, 61, 78, 402)


@dataclass
class Surface:
    name: str
    points: list[tuple[float, float, float]]
    audience_height: float | None
    visible: bool
    group: str | None = None
    color_rgba: tuple[float, float, float, float] | None = None
    transparency: float = 0.5
    native_kind: str | None = None
    native_params: dict[str, float] | None = None
    shape_name: str | None = None
    notes: str | None = None


@dataclass
class Basemap:
    name: str
    image: bytes
    origin: tuple[float, float, float]
    x_direction: tuple[float, float, float]
    y_direction: tuple[float, float, float]
    width: float
    height: float
    opacity: float


@dataclass
class Receiver:
    name: str
    position: tuple[float, float, float]
    angle: float = 0.0
    keep_free: bool = False


def _ease5_argb(value) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    unsigned = int(value) & 0xFFFFFFFF
    return tuple(component / 255.0 for component in (
        (unsigned >> 16) & 0xFF, (unsigned >> 8) & 0xFF, unsigned & 0xFF, (unsigned >> 24) & 0xFF,
    ))


def _ease5_boundary(shape: dict) -> list[tuple[float, float, float]]:
    vertices = [tuple(decode_number(vertex[axis]) for axis in ("X", "Y", "Z"))
                for vertex in shape.get("VertexLocations", [])]
    triangles = [triangle.get("Indices", []) for triangle in shape.get("Triangles", [])]
    if not triangles:
        return vertices
    counts: dict[tuple[int, int], int] = {}
    for triangle in triangles:
        if len(triangle) != 3:
            continue
        for a, b in zip(triangle, triangle[1:] + triangle[:1]):
            edge = tuple(sorted((int(a), int(b))))
            counts[edge] = counts.get(edge, 0) + 1
    boundary_edges = [edge for edge, count in counts.items() if count == 1]
    adjacency: dict[int, list[int]] = {}
    for a, b in boundary_edges:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    if not adjacency or any(len(neighbors) != 2 for neighbors in adjacency.values()):
        return vertices
    start = min(adjacency)
    order = [start]
    previous = None
    current = start
    while True:
        candidates = [index for index in adjacency[current] if index != previous]
        if not candidates:
            break
        following = candidates[0]
        if following == start:
            break
        if following in order:
            return vertices
        order.append(following)
        previous, current = current, following
    return [vertices[index] for index in order]


def _ease5_is_planar(points: list[tuple[float, float, float]], tolerance: float = 5e-2) -> bool:
    if len(points) <= 3:
        return True
    ax, ay, az = points[0]
    bx, by, bz = points[1]
    cx, cy, cz = points[2]
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return False
    return all(abs(nx * (x - ax) + ny * (y - ay) + nz * (z - az)) / length <= tolerance
               for x, y, z in points[3:])


def _raster_as_png(image: bytes, source_name: str) -> bytes:
    """Normalize embedded raster formats to the PNG expected by RDShape."""
    if image[:8] == b"\x89PNG\r\n\x1a\n":
        return image
    with tempfile.TemporaryDirectory(prefix="venue-image-", dir=Path.cwd()) as directory:
        source = Path(directory) / "source.img"
        target = Path(directory) / "target.png"
        source.write_bytes(image)
        environment = os.environ.copy()
        environment["VENUE_IMAGE_SOURCE"] = str(source)
        environment["VENUE_IMAGE_TARGET"] = str(target)
        script = (
            "Add-Type -AssemblyName System.Drawing; "
            "$bytes=[System.IO.File]::ReadAllBytes($env:VENUE_IMAGE_SOURCE); "
            "$stream=[System.IO.MemoryStream]::new($bytes,$false); "
            "$image=[System.Drawing.Image]::FromStream($stream); "
            "try {$image.Save($env:VENUE_IMAGE_TARGET,[System.Drawing.Imaging.ImageFormat]::Png)} "
            "finally {$image.Dispose();$stream.Dispose()}"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if completed.returncode != 0 or not target.exists():
            detail = completed.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"Could not convert {source_name} to PNG: {detail}")
        return target.read_bytes()


def _orient_ease5_yz_png(image: bytes, source_name: str) -> bytes:
    """Orient Y/Z pixels without changing the basemap's world placement."""
    script = (
        "Add-Type -AssemblyName System.Drawing; "
        "$inputStream=[Console]::OpenStandardInput(); "
        "$inputBytes=[System.IO.MemoryStream]::new(); "
        "$inputStream.CopyTo($inputBytes);$inputBytes.Position=0; "
        "$sourceImage=[System.Drawing.Image]::FromStream($inputBytes); "
        "$bitmap=[System.Drawing.Bitmap]::new($sourceImage); "
        "$sourceImage.Dispose();$inputBytes.Dispose(); "
        "$bitmap.RotateFlip([System.Drawing.RotateFlipType]::Rotate180FlipX); "
        "$outputBytes=[System.IO.MemoryStream]::new(); "
        "$bitmap.Save($outputBytes,[System.Drawing.Imaging.ImageFormat]::Png);$bitmap.Dispose(); "
        "$data=$outputBytes.ToArray();$outputBytes.Dispose(); "
        "$stdout=[Console]::OpenStandardOutput();$stdout.Write($data,0,$data.Length)"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        input=image, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0 or not completed.stdout.startswith(b"\x89PNG"):
        detail = completed.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"Could not rotate {source_name}: {detail}")
    return completed.stdout


def _quaternion_axes(rotation: dict) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    w, x, y, z = (float(decode_number(rotation.get(axis, 1.0 if axis == "W" else 0.0)))
                  for axis in ("W", "X", "Y", "Z"))
    length = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / length, x / length, y / length, z / length
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)),
        (2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)),
    )


def read_ease5(path: Path) -> tuple[list[Surface], list[Basemap]]:
    """Read current encrypted and legacy Bond EASE 5 projects."""
    project = decode_eprj(path)
    room = project.get("SingleRoom") or {}
    materials = {material.get("Id"): material for material in room.get("Materials") or []}
    result: list[Surface] = []
    for index, source in enumerate(room.get("Surfaces") or [], 1):
        material = materials.get(source.get("MaterialId"), {})
        color = _ease5_argb(source.get("ColorARGB")) or _ease5_argb(
            material.get("ColorOverrideARGB", material.get("DefaultMaterialColorARGB")))
        material_name = (material.get("NameOverride") or material.get("Name") or "Surface").strip()
        shape = source.get("Shape") or {}
        points = _ease5_boundary(shape)
        base_name = f"{material_name} {source.get('Id', index)}"
        parts = [points]
        if not _ease5_is_planar(points):
            vertices = [tuple(decode_number(vertex[axis]) for axis in ("X", "Y", "Z"))
                        for vertex in shape.get("VertexLocations", [])]
            parts = [[vertices[int(i)] for i in triangle.get("Indices", [])]
                     for triangle in shape.get("Triangles", []) if len(triangle.get("Indices", [])) == 3]
        for part_index, part in enumerate(parts, 1):
            result.append(Surface(
                name=base_name if len(parts) == 1 else f"{base_name} [{part_index}]",
                points=part,
                audience_height=None,
            # RDShape only honors the stored transparency value when the
            # presentation itself remains displayed.  EASE Hidden therefore
            # maps to a displayed, fully transparent RDShape object.
            visible=True,
            group=None,
            color_rgba=color,
            transparency=1.0 if source.get("Hidden", False) else 0.5,
            notes=material_name,
            ))
    used_audience_names: dict[str, int] = {}
    for index, source in enumerate(room.get("AudienceAreas") or [], 1):
        raw_label = (source.get("Label") or f"Audience Area {index}").strip()
        used_audience_names[raw_label] = used_audience_names.get(raw_label, 0) + 1
        occurrence = used_audience_names[raw_label]
        label = raw_label if occurrence == 1 else f"{raw_label} [{occurrence}]"
        audience_points = _ease5_boundary(source.get("Shape") or {})

        def same_boundary(surface: Surface) -> bool:
            if len(surface.points) != len(audience_points):
                return False
            unmatched = list(surface.points)
            for point in audience_points:
                match = next((i for i, candidate in enumerate(unmatched)
                              if math.dist(point, candidate) <= 1e-4), None)
                if match is None:
                    return False
                unmatched.pop(match)
            return True

        supporting_surface = next((surface for surface in result
                                   if surface.audience_height is None and same_boundary(surface)), None)
        if supporting_surface is not None:
            # EASE stores the acoustic surface and its AudienceArea separately.
            # RDShape instead links one audience object to that existing shape.
            supporting_surface.shape_name = supporting_surface.name
            supporting_surface.name = label
            supporting_surface.audience_height = float(decode_number(source.get("EarHeight", 1.2)))
        else:
            result.append(Surface(
                name=label,
                points=audience_points,
                audience_height=float(decode_number(source.get("EarHeight", 1.2))),
                visible=True,
                group=None,
                color_rgba=_ease5_argb(source.get("ColorARGB")),
                transparency=1.0 if source.get("Hidden", False) else 0.5,
                shape_name=f"{label} - Surface",
            ))
    basemaps: list[Basemap] = []
    for index, source in enumerate(room.get("SectionPlans") or [], 1):
        encoded = source.get("Bitmap")
        if not encoded:
            continue
        try:
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError(f"EASE 5 basemap {index} contains invalid image data") from exc
        image = _raster_as_png(image, f"EASE 5 basemap {index}")
        width = float(decode_number(source.get("Width", 0.0)))
        height = float(decode_number(source.get("Height", 0.0)))
        if width <= 0 or height <= 0:
            continue
        transform = (source.get("Location") or {}).get("PositionOrientation") or {}
        position = transform.get("Position") or {}
        world_anchor = tuple(float(decode_number(position.get(axis, 0.0))) for axis in ("X", "Y", "Z"))
        x_direction, y_direction = _quaternion_axes(transform.get("Rotation") or {})
        anchor = source.get("AnchorPosition") or {}
        center_u = width / 2.0 - float(decode_number(anchor.get("X", width / 2.0)))
        center_v = height / 2.0 - float(decode_number(anchor.get("Y", height / 2.0)))
        center = tuple(world_anchor[i] + center_u * x_direction[i] + center_v * y_direction[i]
                       for i in range(3))
        normal = cross(x_direction, y_direction)
        if abs(normal[0]) > 0.5:
            # A manually aligned RDShape reference shows that its Y/Z basemap
            # maps the raster width onto EASE's vertical axis and its height
            # onto EASE's horizontal axis, while retaining the original width
            # and height values. This intentionally swaps only the axes, not
            # the dimensions; swapping both rotates the bitmap by 90 degrees.
            x_direction, y_direction = y_direction, x_direction
            # EASE 5 and RDShape use opposite signs for both coordinates in
            # this section plane. The manually aligned reference confirms
            # Y=+5.22/Z=-9.94 versus decoded Y=-5.14/Z=+10.20.
            center = (center[0], -center[1], -center[2])
            image = _orient_ease5_yz_png(image, f"EASE 5 Y/Z basemap {index}")
        basemaps.append(Basemap(
            (source.get("Label") or f"EASE 5 Basemap {index}").strip() or f"EASE 5 Basemap {index}",
            image, center, x_direction, y_direction, width, height,
            0.0 if source.get("Hidden", False) else 1.0,
        ))
    return result, basemaps


def read_easefocus(path: Path, properties_only: bool = False, include_basemaps: bool = False):
    """Read the GZip-compressed .NET SOAP hashtable stored in an EF3 FC3 file."""
    raw = path.read_bytes()
    gzip_offset = raw.find(b"\x1f\x8b")
    if gzip_offset < 0:
        raise ValueError("The FC3 file does not contain an EASE Focus project block.")
    try:
        decompressed = gzip.decompress(raw[gzip_offset:])
        # Real projects append one or more binary EASE_GLL loudspeaker-library
        # records to the serialized SOAP envelope inside the same GZip member.
        # They are not part of the venue property table and are not XML.
        closing_tag = b"</SOAP-ENV:Envelope>"
        xml_end = decompressed.find(closing_tag)
        if xml_end >= 0:
            xml_data = decompressed[:xml_end + len(closing_tag)]
            attachment_data = decompressed[xml_end + len(closing_tag):]
        else:
            xml_data, attachment_data = decompressed, b""
        root = ET.fromstring(xml_data)
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Invalid EASE Focus project data: {exc}") from exc

    by_id = {element.get("id"): element for element in root.iter() if element.get("id")}

    def dereference(element: ET.Element) -> ET.Element:
        seen = set()
        while (href := element.get("href")):
            reference = href.lstrip("#")
            if reference in seen or reference not in by_id:
                break
            seen.add(reference)
            element = by_id[reference]
        return element

    def array_items(reference: ET.Element) -> list[ET.Element]:
        return list(dereference(reference))

    hashtable = next((element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "Hashtable"), None)
    if hashtable is None:
        raise ValueError("The FC3 project has no property table.")
    children = {child.tag.rsplit("}", 1)[-1]: child for child in hashtable}
    keys = array_items(children["Keys"])
    values = array_items(children["Values"])
    if len(keys) != len(values):
        raise ValueError("The FC3 property table is inconsistent.")

    properties: dict[str, str | None] = {}
    for key_node, value_node in zip(keys, values):
        key_node = dereference(key_node)
        value_node = dereference(value_node)
        key = (key_node.text or "").strip()
        if not key:
            continue
        is_null = any(attribute.rsplit("}", 1)[-1] in ("null", "nil") and value == "1"
                      for attribute, value in value_node.attrib.items())
        properties[key] = None if is_null else (value_node.text or "").strip()

    if properties_only:
        return properties

    def text(key: str, default: str = "") -> str:
        value = properties.get(key)
        return default if value is None else value

    def real(key: str, default: float = 0.0) -> float:
        value = text(key)
        try:
            return float(value.replace(",", ".")) if value else default
        except ValueError:
            return default

    def integer(key: str, default: int = 0) -> int:
        try:
            return int(float(text(key)))
        except ValueError:
            return default

    def boolean(key: str, default: bool = True) -> bool:
        value = text(key)
        return default if not value else value.lower() not in ("false", "0", "no")

    result: list[Surface] = []
    receivers: list[Receiver] = []
    zone_count = integer("Project.AudienceZoneManager.Count")
    global_ear_height = real("Project.EarHeightSitting", 1.2)

    for zone_index in range(zone_count):
        prefix = f"Project.AudienceZoneManager.Zone[{zone_index}]"
        zone_type = text(prefix + ".Type", "Rectangle")
        zone_name = text(prefix + ".Label", f"Audience Zone {zone_index + 1}")
        center_x = real(prefix + ".X")
        center_y = real(prefix + ".Y")
        orientation = math.radians(real(prefix + ".Orientation"))
        cos_a, sin_a = math.cos(orientation), math.sin(orientation)
        depth = real(prefix + ".Depth")
        if zone_type == "Arc":
            inner_radius = 0.0
            outer_radius = real(prefix + ".Radius")
            depth = outer_radius
        elif zone_type == "Annulus":
            inner_radius = real(prefix + ".InnerRadius")
            outer_radius = real(prefix + ".Radius")
            depth = outer_radius - inner_radius
        else:
            inner_radius = outer_radius = 0.0

        def world(longitudinal: float, lateral: float, height: float) -> tuple[float, float, float]:
            return (
                center_x + longitudinal * cos_a - lateral * sin_a,
                center_y + longitudinal * sin_a + lateral * cos_a,
                height,
            )

        area_count = integer(prefix + ".AreaCount", 1)
        for area_index in range(area_count):
            area = prefix + f".Area[{area_index}]"
            area_label = text(area + ".Label", f"Audience Area {area_index + 1}")
            area_name = f"{zone_name} - {area_label}" if zone_name and area_label != zone_name else zone_name or area_label
            d1 = real(area + ".D1")
            d2 = real(area + ".D2", depth)
            z1 = real(area + ".Z1")
            z2 = real(area + ".Z2")
            ear_height = real(area + ".EarHeightValue", global_ear_height)
            if ear_height <= 0:
                ear_height = global_ear_height
            visible = boolean(area + ".Visible", boolean(prefix + ".Visible", True))

            receiver_count = integer(area + ".AnchoredReceiversCount")
            for receiver_index in range(receiver_count):
                receiver = area + f".AnchoredReceiver[{receiver_index}]"
                receiver_number = integer(receiver + ".Number", len(receivers) + 1)
                receivers.append(Receiver(
                    f"Receiver {receiver_number}",
                    (real(receiver + ".PositionX"), real(receiver + ".PositionY"),
                     real(receiver + ".PositionZ")),
                    real(receiver + ".Angle"),
                    boolean(receiver + ".KeepFree", False),
                ))

            faces: list[list[tuple[float, float, float]]] = []
            if zone_type in ("Arc", "Annulus"):
                sweep = real(prefix + ".SweepAngle", 360.0)
                segments = max(1, math.ceil(abs(sweep) / 7.5))
                # EF3's orientation describes the sector centre line.
                start = -math.radians(sweep) / 2.0
                r1, r2 = inner_radius + d1, inner_radius + d2
                angles = [start + math.radians(sweep) * segment / segments for segment in range(segments + 1)]
                if r1 <= 1e-9:
                    sector_points = [world(0, 0, z1)] + [
                        world(r2 * math.cos(angle), r2 * math.sin(angle), z2) for angle in reversed(angles)
                    ]
                else:
                    sector_points = [world(r1 * math.cos(angle), r1 * math.sin(angle), z1) for angle in angles]
                    sector_points += [world(r2 * math.cos(angle), r2 * math.sin(angle), z2) for angle in reversed(angles)]
                faces.append(sector_points)
            else:
                zone_depth = depth or max(d1, d2)

                def width_at(distance: float) -> float:
                    if zone_type == "Rectangle":
                        return real(prefix + ".Width")
                    front = real(prefix + ".FrontEdge", real(prefix + ".FrontWidth"))
                    back = real(prefix + ".BackEdge", real(prefix + ".BackWidth", front))
                    ratio = 0.0 if abs(zone_depth) < 1e-12 else distance / zone_depth
                    return front + (back - front) * ratio

                w1, w2 = width_at(d1), width_at(d2)
                right_angle_left = integer(prefix + ".RightAngleLeft", -1)
                if zone_type == "Right Trapezoid" and right_angle_left == 1:
                    left1, right1, left2, right2 = -w1 / 2, w1 / 2, -w2 + w1 / 2, w1 / 2
                elif zone_type == "Right Trapezoid" and right_angle_left == 0:
                    left1, right1, left2, right2 = -w1 / 2, w1 / 2, -w1 / 2, w2 - w1 / 2
                else:
                    left1, right1, left2, right2 = -w1 / 2, w1 / 2, -w2 / 2, w2 / 2
                x1, x2 = d1 - zone_depth / 2.0, d2 - zone_depth / 2.0
                faces.append([
                    world(x1, left1, z1), world(x2, left2, z2),
                    world(x2, right2, z2), world(x1, right1, z1),
                ])

            for face_index, points in enumerate(faces, 1):
                try:
                    polygon_normal(points)
                except ValueError:
                    continue
                name = area_name if len(faces) == 1 else f"{area_name} {face_index}"
                native_params = None
                native_kind = None
                if zone_type in ("Arc", "Annulus"):
                    native_kind = "sector"
                    native_params = {
                        "center_x": center_x, "center_y": center_y,
                        "orientation": math.degrees(orientation), "sweep": sweep,
                        "inner_radius": r1, "outer_radius": r2,
                        "inner_height": z1, "outer_height": z2,
                    }
                result.append(Surface(
                    name, points, ear_height, visible,
                    zone_name if area_count > 1 else None,
                    native_kind=native_kind, native_params=native_params,
                    shape_name=zone_name if area_count == 1 else name,
                ))
    free_receiver_count = integer("Project.ReceiverManager.FreeReceiversCount")
    for receiver_index in range(free_receiver_count):
        prefix = f"Project.ReceiverManager.FreeReceiver[{receiver_index}]"
        receiver_number = integer(prefix + ".Number", len(receivers) + 1)
        receivers.append(Receiver(
            f"Receiver {receiver_number}",
            (real(prefix + ".PositionX"), real(prefix + ".PositionY"), real(prefix + ".PositionZ")),
            real(prefix + ".Angle"), boolean(prefix + ".KeepFree", True),
        ))

    if not include_basemaps:
        return result

    def png_at(data: bytes, offset: int) -> bytes | None:
        if data[offset:offset + 8] != b"\x89PNG\r\n\x1a\n":
            return None
        cursor = offset + 8
        while cursor + 12 <= len(data):
            size = struct.unpack(">I", data[cursor:cursor + 4])[0]
            chunk_type = data[cursor + 4:cursor + 8]
            cursor += 12 + size
            if cursor > len(data):
                return None
            if chunk_type == b"IEND":
                return data[offset:cursor]
        return None

    def jpeg_at(data: bytes, offset: int) -> bytes | None:
        if data[offset:offset + 3] != b"\xff\xd8\xff":
            return None
        end = data.find(b"\xff\xd9", offset + 3)
        return None if end < 0 else data[offset:end + 2]

    def bmp_at(data: bytes, offset: int) -> bytes | None:
        if data[offset:offset + 2] != b"BM" or offset + 18 > len(data):
            return None
        size = int.from_bytes(data[offset + 2:offset + 6], "little")
        dib_size = int.from_bytes(data[offset + 14:offset + 18], "little")
        if dib_size not in (12, 16, 40, 52, 56, 64, 108, 124):
            return None
        return data[offset:offset + size] if 26 <= size <= len(data) - offset else None

    def image_dimensions(image: bytes) -> tuple[int, int] | None:
        if image[:8] == b"\x89PNG\r\n\x1a\n" and len(image) >= 24:
            return struct.unpack(">II", image[16:24])
        if image[:2] == b"BM" and len(image) >= 26:
            dib_size = int.from_bytes(image[14:18], "little")
            if dib_size == 12:
                return (int.from_bytes(image[18:20], "little"),
                        int.from_bytes(image[20:22], "little"))
            width = int.from_bytes(image[18:22], "little", signed=True)
            height = int.from_bytes(image[22:26], "little", signed=True)
            return abs(width), abs(height)
        if image[:3] == b"\xff\xd8\xff":
            cursor = 2
            sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                           0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
            while cursor + 4 <= len(image):
                if image[cursor] != 0xFF:
                    cursor += 1
                    continue
                while cursor < len(image) and image[cursor] == 0xFF:
                    cursor += 1
                if cursor >= len(image):
                    break
                marker = image[cursor]
                cursor += 1
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    continue
                if cursor + 2 > len(image):
                    break
                size = int.from_bytes(image[cursor:cursor + 2], "big")
                if size < 2 or cursor + size > len(image):
                    break
                if marker in sof_markers and size >= 7:
                    return (int.from_bytes(image[cursor + 5:cursor + 7], "big"),
                            int.from_bytes(image[cursor + 3:cursor + 5], "big"))
                cursor += size
        return None

    def image_as_png(image: bytes) -> bytes:
        if image[:8] == b"\x89PNG\r\n\x1a\n":
            return image
        # FC3 also accepts JPEG and BMP basemaps. RDShape files are kept
        # uniform by decoding those formats with Windows' built-in image
        # codec and storing PNG bytes in the document.
        # Stream the raster through PowerShell instead of using temporary
        # files. Embedded Python on locked-down Windows installations can
        # create private temp directories whose inherited ACL then prevents
        # even the creating process from reopening them.
        script = (
            "Add-Type -AssemblyName System.Drawing; "
            "$encoded=[Console]::In.ReadToEnd();$bytes=[Convert]::FromBase64String($encoded);"
            "$source=[System.IO.MemoryStream]::new($bytes,$false); "
            "$image=[System.Drawing.Image]::FromStream($source);$output=[System.IO.MemoryStream]::new(); "
            "try {$image.Save($output,[System.Drawing.Imaging.ImageFormat]::Png);$bytes=$output.ToArray();"
            "[Console]::OpenStandardOutput().Write($bytes,0,$bytes.Length)} "
            "finally {$output.Dispose();$image.Dispose();$source.Dispose()}"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            input=base64.b64encode(image), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode != 0 or not result.stdout.startswith(b"\x89PNG"):
            detail = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"Could not convert the FC3 basemap to PNG: {detail}")
        return result.stdout

    images: list[bytes] = []
    cursor = 0
    while (cursor := attachment_data.find(b"\x89PNG\r\n\x1a\n", cursor)) >= 0:
        image = png_at(attachment_data, cursor)
        if image is not None:
            images.append(image)
            cursor += len(image)
        else:
            cursor += 8
    for signature, extractor in ((b"\xff\xd8\xff", jpeg_at), (b"BM", bmp_at)):
        cursor = 0
        while (cursor := attachment_data.find(signature, cursor)) >= 0:
            image = extractor(attachment_data, cursor)
            if image is not None and image_dimensions(image) is not None:
                images.append(image)
                cursor += len(image)
            else:
                cursor += len(signature)
    if not images:
        try:
            decoded = base64.b64decode(b"".join(attachment_data.split()), validate=False)
            for extractor in (png_at, jpeg_at, bmp_at):
                image = extractor(decoded, 0)
                if image is not None and image_dimensions(image) is not None:
                    images.append(image)
                    break
        except (ValueError, base64.binascii.Error):
            pass
    images_by_md5 = {base64.b64encode(hashlib.md5(image).digest()).decode(): image for image in images}

    def make_basemap(prefix: str, name: str, plane: str, zone_prefix: str | None = None) -> Basemap | None:
        if boolean(prefix + ".IsEmpty", True):
            return None
        checksum = text(prefix + (".RawImage.EnclosureMD5" if plane == "xy" else ".Image.EnclosureMD5"))
        image = images_by_md5.get(checksum)
        if image is None and len(images) == 1:
            image = images[0]
        dimensions = image_dimensions(image) if image is not None else None
        if image is None or dimensions is None:
            return None
        image = image_as_png(image)
        pixel_width, pixel_height = dimensions
        ax, ay = real(prefix + ".PointA.X"), real(prefix + ".PointA.Y")
        bx, by = real(prefix + ".PointB.X"), real(prefix + ".PointB.Y")
        pixel_distance = math.hypot(bx - ax, by - ay)
        if pixel_distance < 1e-9:
            return None
        scale = real(prefix + ".Distance") / pixel_distance
        width, height = pixel_width * scale, pixel_height * scale
        real_x = real(prefix + ".RealPositionPointA.X")
        real_y = real(prefix + ".RealPositionPointA.Y")
        pixel_u, pixel_v = ax * scale, ay * scale
        qx, qy = (bx - ax) / pixel_distance, (by - ay) / pixel_distance
        if plane == "xy":
            # In EF3 the A-B line calibrates only the image scale. Point A
            # supplies its world position, but there is no world coordinate
            # for B and therefore no rotation constraint. Keep the raster's
            # original top-view orientation.
            x_direction = (1.0, 0.0, 0.0)
            y_direction = (0.0, 1.0, 0.0)
            point_a = (real_x, real_y, -0.05)
        else:
            zone_orientation = math.radians(real((zone_prefix or "") + ".Orientation"))
            longitudinal = (math.cos(zone_orientation), math.sin(zone_orientation), 0.0)
            vertical = (0.0, 0.0, 1.0)
            zone_depth = real((zone_prefix or "") + ".Depth")
            zone_x, zone_y = real((zone_prefix or "") + ".X"), real((zone_prefix or "") + ".Y")
            front = (zone_x - zone_depth / 2 * longitudinal[0], zone_y - zone_depth / 2 * longitudinal[1], 0.0)
            lateral = (-longitudinal[1], longitudinal[0], 0.0)
            point_a = (front[0] + real_x * longitudinal[0] + 0.05 * lateral[0],
                       front[1] + real_x * longitudinal[1] + 0.05 * lateral[1], real_y)
            x_direction = tuple(qx * longitudinal[i] - qy * vertical[i] for i in range(3))
            y_direction = tuple(qy * longitudinal[i] + qx * vertical[i] for i in range(3))
        center_u = (pixel_width / 2.0 - ax) * scale
        center_v = (pixel_height / 2.0 - ay) * scale
        center = tuple(point_a[i] + center_u * x_direction[i] + center_v * y_direction[i] for i in range(3))
        return Basemap(name, image, center, x_direction, y_direction, width, height,
                       max(0.0, min(1.0, real(prefix + ".Opacity", 100.0) / 100.0)))

    basemaps: list[Basemap] = []
    if top := make_basemap("Project.LayoutBitmap", "EASE Focus Top View", "xy"):
        basemaps.append(top)
    for zone_index in range(zone_count):
        zone_prefix = f"Project.AudienceZoneManager.Zone[{zone_index}]"
        prefix = zone_prefix + ".LayoutBitmapSide"
        if side := make_basemap(prefix, f"{text(zone_prefix + '.Label', f'Audience Zone {zone_index + 1}')} Side View",
                                "xz", zone_prefix):
            basemaps.append(side)
    return result, basemaps, receivers


def decrypt_aes(raw: bytes) -> bytes:
    openssl = shutil.which("openssl")
    if not openssl:
        candidate = Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe")
        if candidate.exists():
            openssl = str(candidate)
    if not openssl:
        raise RuntimeError("OpenSSL was not found; Git for Windows includes a suitable version.")
    result = subprocess.run(
        [openssl, "enc", "-d", "-aes-256-cbc", "-K", KEY_HEX, "-iv", IV_HEX],
        input=raw,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise ValueError("AES decryption failed: " + result.stderr.decode(errors="replace").strip())
    return result.stdout


def read_xmlp(path: Path) -> ET.Element:
    raw = path.read_bytes()
    stripped = raw.lstrip()
    is_plain_xml = stripped.startswith(b"<?xml") or stripped.startswith(b"<\x00?\x00x\x00m\x00l\x00") or stripped.startswith(b"\x00<\x00?\x00x\x00m\x00l")
    if not is_plain_xml:
        raw = decrypt_aes(raw)
    # Some Soundvision versions serialize XML as UTF-16 while incorrectly
    # declaring UTF-8. Normalize those files before handing them to ElementTree.
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"<\x00?\x00x\x00m\x00l\x00"):
        raw = raw.decode("utf-16-le").lstrip("\ufeff").encode("utf-8")
    elif raw.startswith(b"\xfe\xff") or raw.startswith(b"\x00<\x00?\x00x\x00m\x00l"):
        raw = raw.decode("utf-16-be").lstrip("\ufeff").encode("utf-8")
    start = raw.find(b"<?xml")
    if start < 0:
        raise ValueError("The decrypted file does not contain an XML document.")
    try:
        return ET.fromstring(raw[start:])
    except ET.ParseError as exc:
        raise ValueError(f"Invalid decrypted XML: {exc}") from exc


def surfaces(root: ET.Element) -> list[Surface]:
    result = []
    scene = root if root.tag == "scene" else root.find("./scene")
    if scene is None:
        raise ValueError("No Soundvision scene was found.")
    parents = {child: parent for parent in scene.iter() for child in parent}

    def rgba(colors: ET.Element | None, enabled: bool) -> tuple[float, float, float, float] | None:
        if colors is None:
            return None
        values = (colors.findtext("enabled" if enabled else "disabled") or "").split()
        return tuple(map(float, values)) if len(values) == 4 else None

    def style(element: ET.Element) -> tuple[str | None, tuple[float, float, float, float] | None]:
        enabled = element.findtext("enabled", "1") != "0"
        groups = []
        current = parents.get(element)
        color_node = None
        while current is not None and current is not scene:
            if current.tag == "group":
                group_name = (current.findtext("name") or "").strip()
                if group_name:
                    groups.append(group_name)
                if color_node is None:
                    color_node = current.find("colors")
            current = parents.get(current)
        if color_node is None:
            color_node = scene.find("colors")
        return (" / ".join(reversed(groups)) or None, rgba(color_node, enabled))

    for index, surface in enumerate(scene.findall(".//surface"), 1):
        name = (surface.findtext("name") or f"Surface {index}").strip()
        points = []
        for point in surface.findall("./points/point"):
            values = (point.findtext("position") or "").split()
            if len(values) != 3:
                raise ValueError(f"{name}: invalid point coordinate")
            internal_x, internal_y, internal_z = map(float, values)
            # Soundvision's XML coordinate basis differs from the coordinates
            # displayed in its UI (and from RDShape): UI/RDShape = (-X, Z, Y).
            points.append((-internal_x, internal_z, internal_y))
        # CAD exports occasionally repeat a vertex in the middle of a polygon
        # (not just as a closing point). OCCT cannot create a zero-length edge.
        points = [point for i, point in enumerate(points) if i == 0 or math.dist(point, points[i - 1]) >= 1e-8]
        if len(points) >= 2 and math.dist(points[-1], points[0]) < 1e-8:
            points.pop()
        # Scene groups and some legacy audience placeholders also use the tag
        # <surface>, but contain no polygon at all.
        if len(points) < 3:
            continue
        audience = surface.find("audience")
        audience_height = float(audience.findtext("height", "1")) if audience is not None else None
        legacy_audience_height = float(surface.findtext("audience_height", "0") or 0)
        if audience_height is None and legacy_audience_height > 0:
            audience_height = legacy_audience_height
        group, color = style(surface)
        # RDShape owns a dedicated top-level Audiences collection. Keeping a
        # Soundvision "Audience" material/group on the linked carrier shape
        # creates a second Audiences subtree below Other objects. In addition
        # to being misleading, equal group/object names can confuse RDShape's
        # unlink/delete path. Match the EASE 5 import: keep the carrier as an
        # ungrouped shape and give it an unambiguous display name.
        shape_name = f"{name} - Surface" if audience_height is not None else None
        if audience_height is not None:
            group = None
        result.append(Surface(
            name, points, audience_height, surface.findtext("visible", "1") != "0",
            group, color, shape_name=shape_name,
        ))

    # Older Soundvision scenes store floors, stages, galleries and similar
    # objects as "balconies": a 2D longitudinal profile extruded between a
    # front and rear width, then positioned and yawed in the 3D scene.
    for balcony_index, balcony in enumerate(scene.findall(".//balcony"), 1):
        name = (balcony.findtext("name") or f"Balcony {balcony_index}").strip()
        profile = []
        for point in balcony.findall("./points/point"):
            values = (point.findtext("position") or "").split()
            if len(values) == 2:
                profile.append(tuple(map(float, values)))
        if len(profile) < 2:
            continue
        init_values = list(map(float, (balcony.findtext("init_position") or "0 0 0").split()))
        if len(init_values) != 3:
            raise ValueError(f"{name}: invalid balcony start position")
        init_x, init_y, init_z = init_values
        angle = math.radians(float(balcony.findtext("init_angle", "0") or 0))
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        front_width = float(balcony.findtext("front_width", "0") or 0)
        rear_width = float(balcony.findtext("rear_width", str(front_width)) or front_width)
        audience_heights = [float(value) for value in (balcony.findtext("audience_heights") or "").split()]
        u_min = min(point[0] for point in profile)
        u_max = max(point[0] for point in profile)

        def width_at(u: float) -> float:
            ratio = 0.0 if abs(u_max - u_min) < 1e-12 else (u - u_min) / (u_max - u_min)
            return front_width + ratio * (rear_width - front_width)

        def rd_point(u: float, height: float, lateral: float) -> tuple[float, float, float]:
            # Local longitudinal axis u is Soundvision Z; lateral is X. Rotate
            # around Soundvision's vertical Y axis, then use UI/RD = (-X,Z,Y).
            internal_x = init_x + lateral * cos_a + u * sin_a
            internal_z = init_z - lateral * sin_a + u * cos_a
            internal_y = init_y + height
            return (-internal_x, internal_z, internal_y)

        for segment_index, ((u1, h1), (u2, h2)) in enumerate(zip(profile, profile[1:]), 1):
            half1, half2 = width_at(u1) / 2.0, width_at(u2) / 2.0
            points = [
                rd_point(u1, h1, -half1),
                rd_point(u2, h2, -half2),
                rd_point(u2, h2, half2),
                rd_point(u1, h1, half1),
            ]
            if min(math.dist(points[i], points[(i + 1) % 4]) for i in range(4)) < 1e-8:
                continue
            audience_height = audience_heights[segment_index - 1] if segment_index <= len(audience_heights) else 0.0
            group, color = style(balcony)
            item_name = name if len(profile) == 2 else f"{name} {segment_index}"
            is_audience = audience_height > 0
            result.append(
                Surface(
                    item_name,
                    points,
                    audience_height if is_audience else None,
                    balcony.findtext("visible", "1") != "0",
                    None if is_audience else group,
                    color,
                    shape_name=f"{item_name} - Surface" if is_audience else None,
                )
            )
    return result


def _arraycalc_surfaces(records: list[dict]) -> list[Surface]:
    """Convert normalized ArrayCalc VenueObject records to planar faces."""
    by_id = {record["id"]: record for record in records}

    def group_path(record: dict) -> str | None:
        names = []
        parent_id = record.get("parent", 0)
        seen = set()
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = by_id.get(parent_id)
            if parent is None:
                break
            if parent["shape"] == 5:
                raw = parent["name"]
                names.append(raw if not raw.startswith("RoomObjectGroup:") else f"Group {parent_id}")
            parent_id = parent.get("parent", 0)
        return " / ".join(reversed(names)) or None

    def transform(record: dict, point) -> tuple[float, float, float]:
        # Venue objects are stored in their parent's coordinate system.  Group
        # transforms therefore have to be composed from the leaf to the root.
        current = record
        x, y, z = point
        seen = set()
        while current is not None and current["id"] not in seen:
            seen.add(current["id"])
            x, y, z = (x * current["scale"][0], y * current["scale"][1], z * current["scale"][2])
            rx, ry, rz = (math.radians(value) for value in current["rotation"])
            x, y, z = x, y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
            x, y, z = x * math.cos(ry) + z * math.sin(ry), y, -x * math.sin(ry) + z * math.cos(ry)
            x, y, z = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz), z
            x, y, z = (x + current["origin"][0], y + current["origin"][1], z + current["origin"][2])
            current = by_id.get(current.get("parent", 0))
        return x, y, z

    result = []
    for record in records:
        if record["shape"] == 5:
            continue
        argb = record["color"] & 0xFFFFFFFF
        color = (((argb >> 16) & 255) / 255, ((argb >> 8) & 255) / 255, (argb & 255) / 255, ((argb >> 24) & 255) / 255)
        audience_height = record["listener_height"] if record["plane_type"] == 1 else None
        common = dict(
            audience_height=audience_height,
            visible=record["enabled"],
            group=group_path(record),
            color_rgba=color,
            transparency=0.5 if record["transparent"] else 0.0,
        )
        faces = []
        if record["shape"] in (1, 6):
            faces = [(record["name"], record["points"])]
        elif record["shape"] == 4 and len(record["points"]) == 8:
            p = record["points"]
            indices = ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0))
            faces = [(f"{record['name']} {i + 1}", [p[j] for j in face]) for i, face in enumerate(indices)]
        elif record["shape"] in (2, 3):
            curve = record.get("curve")
            if curve:
                span = curve["span_angle"]
                segments = max(1, math.ceil(abs(span) / 7.5))
                angles = [math.radians(curve["start_angle"] + span * i / segments) for i in range(segments + 1)]

                def radial(angle, outer):
                    a = curve["outer_a" if outer else "inner_a"]
                    b = curve["outer_b" if outer else "inner_b"]
                    n = curve.get("outer_n" if outer else "inner_n", 2.0) or 2.0
                    exponent = 2.0 / n
                    x = a * math.copysign(abs(math.cos(angle)) ** exponent, math.cos(angle))
                    y = b * math.copysign(abs(math.sin(angle)) ** exponent, math.sin(angle))
                    z = curve["outer_z" if outer else "inner_z"]
                    return (x, y, z)

                for i, (a1, a2) in enumerate(zip(angles, angles[1:]), 1):
                    quad = [radial(a1, False), radial(a2, False), radial(a2, True), radial(a1, True)]
                    candidates = (
                        ([quad[0], quad[1], quad[2]], [quad[0], quad[2], quad[3]]),
                        ([quad[0], quad[1], quad[3]], [quad[1], quad[2], quad[3]]),
                    )
                    def triangle_area(triangle):
                        return length(cross(subtract(triangle[1], triangle[0]), subtract(triangle[2], triangle[0])))
                    triangles = max(candidates, key=lambda pair: min(triangle_area(pair[0]), triangle_area(pair[1])))
                    for suffix, triangle in zip(("A", "B"), triangles):
                        if triangle_area(triangle) >= 1e-10:
                            faces.append((f"{record['name']} {i}{suffix}", triangle))
        for face_name, local_points in faces:
            if len(local_points) >= 3:
                world_points = [transform(record, p) for p in local_points]
                # ArrayCalc permits zero-thickness cuboids.  Their collapsed
                # side faces are not surfaces, while the remaining coincident
                # faces still represent the intended planar object.
                try:
                    polygon_normal(world_points)
                except ValueError:
                    continue
                result.append(Surface(face_name, world_points, **common))
    return result


def read_arraycalc_xml(path: Path) -> list[Surface]:
    root = ET.parse(path).getroot()
    elements = root.findall("./Venue/.//RoomObject")
    records = []
    for object_id, element in enumerate(elements, 1):
        def vector(tag, default):
            node = element.find(tag)
            return tuple(float(node.get(axis)) for axis in "xyz") if node is not None else default

        points = []
        index = 1
        while (point := element.find(f"P{index}")) is not None:
            points.append(tuple(float(point.get(axis)) for axis in "xyz"))
            index += 1
        shape = int(element.get("Shape", "0"))
        curve = None
        if shape in (2, 3):
            curve = {
                "start_angle": float(element.get("StartAngle", "0")), "span_angle": float(element.get("SpanAngle", "360")),
                "inner_z": float(element.get("InnerZ", "0")), "outer_z": float(element.get("OuterZ", "0")),
                "inner_a": float(element.get("InnerRadiusA", "0")), "outer_a": float(element.get("OuterRadiusA", "0")),
                "inner_b": float(element.get("InnerRadiusB", "0")), "outer_b": float(element.get("OuterRadiusB", "0")),
                "inner_n": float(element.get("InnerN", "2")), "outer_n": float(element.get("OuterN", "2")),
            }
        records.append({
            "id": object_id, "name": element.get("Name", f"Object {object_id}"), "shape": shape,
            "plane_type": int(element.get("PlaneType", "0")), "enabled": element.get("Enabled", "1") != "0",
            "transparent": element.get("Transparent", "0") != "0", "listener_height": float(element.get("ListenerHeight", "0")),
            "color": int(element.get("Color", "4290822336")), "origin": vector("Origin", (0.0, 0.0, 0.0)),
            "rotation": vector("Rotation", (0.0, 0.0, 0.0)), "scale": vector("Scaling", (1.0, 1.0, 1.0)),
            "parent": int(element.get("ParentVenueObjectId", "0")), "points": points, "curve": curve,
        })
    return _arraycalc_surfaces(records)


def read_arraycalc_db(path: Path) -> list[Surface]:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    records = []
    try:
        for row in connection.execute("select * from VenueObjects order by VenueObjectId"):
            points = [tuple(point) for point in connection.execute(
                "select X,Y,Z from VenueObjectPoints where VenueObjectId=? order by PointIndex", (row["VenueObjectId"],)
            )]
            curve = None
            if row["Shape"] in (2, 3):
                table = "VenueObjectsCircular" if row["Shape"] == 2 else "VenueObjectsEllipsoidal"
                extra = connection.execute(f"select * from {table} where VenueObjectId=?", (row["VenueObjectId"],)).fetchone()
                if extra:
                    curve = {"start_angle": extra["StartAngle"], "span_angle": extra["SpanAngle"],
                             "inner_z": extra["InnerZ"], "outer_z": extra["OuterZ"],
                             "inner_a": extra["InnerA"], "outer_a": extra["OuterA"],
                             "inner_b": extra["InnerB"], "outer_b": extra["OuterB"],
                             "inner_n": extra["InnerN"] if "InnerN" in extra.keys() else 2.0,
                             "outer_n": extra["OuterN"] if "OuterN" in extra.keys() else 2.0}
            records.append({"id": row["VenueObjectId"], "name": row["Name"], "shape": row["Shape"],
                            "plane_type": row["PlaneType"], "enabled": bool(row["Enabled"]),
                            "transparent": bool(row["Transparent"]), "listener_height": row["ListenerHeight"],
                            "color": row["Color"], "origin": (row["OriginX"], row["OriginY"], row["OriginZ"]),
                            "rotation": (row["RotationX"], row["RotationY"], row["RotationZ"]),
                            "scale": (row["ScaleX"], row["ScaleY"], row["ScaleZ"]),
                            "parent": row["ParentVenueObjectId"] or 0, "points": points, "curve": curve})
    finally:
        connection.close()
    return _arraycalc_surfaces(records)


def subtract(a, b):
    return tuple(x - y for x, y in zip(a, b))


def length(v):
    return math.sqrt(sum(x * x for x in v))


def normalize(v):
    size = length(v)
    if size < 1e-10:
        raise ValueError("The surface contains two identical adjacent points.")
    return tuple(x / size for x in v)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def polygon_normal(points):
    normal = [0.0, 0.0, 0.0]
    for current, following in zip(points, points[1:] + points[:1]):
        normal[0] += (current[1] - following[1]) * (current[2] + following[2])
        normal[1] += (current[2] - following[2]) * (current[0] + following[0])
        normal[2] += (current[0] - following[0]) * (current[1] + following[1])
    return normalize(tuple(normal))


def inclination(item: Surface) -> float:
    normal = polygon_normal(item.points)
    return math.degrees(math.acos(min(1.0, max(-1.0, abs(normal[2])))))


def number(value: float) -> str:
    if abs(value) < 1e-14:
        value = 0.0
    return format(value, ".17g")


def tcl_quote(value: str) -> str:
    return "{" + value.replace("}", "\\}") + "}"


def geometry_values(points):
    edge1 = subtract(points[1], points[0])
    edge2 = subtract(points[2], points[1])
    normal = polygon_normal(points)
    direction = normalize(edge1)
    opposite_normal = tuple(-x for x in normal)
    # RDShape's rectangle properties are width (front-edge length), depth
    # (horizontal projection of the side edge), and length (actual side-edge
    # length).  ``max(edge1, edge2)`` is not a valid substitute for length: it
    # makes a non-square rectangle report three identical dimensions whenever
    # its front edge happens to be the longer one.
    dimensions = (
        length(edge1),
        math.hypot(edge2[0], edge2[1]),
        length(edge2),
    )
    return normal, direction, opposite_normal, dimensions


def is_rectangular_quad(points, tolerance: float = 1e-4) -> bool:
    if len(points) != 4:
        return False
    edges = [tuple(points[(i + 1) % 4][j] - points[i][j] for j in range(3)) for i in range(4)]
    lengths = [length(edge) for edge in edges]
    if min(lengths) <= 1e-9:
        return False
    unit = [tuple(value / edge_length for value in edge) for edge, edge_length in zip(edges, lengths)]
    perpendicular = abs(sum(unit[0][j] * unit[1][j] for j in range(3))) <= tolerance
    opposite_parallel = (
        length(cross(unit[0], unit[2])) <= tolerance
        and length(cross(unit[1], unit[3])) <= tolerance
    )
    opposite_equal = (
        abs(lengths[0] - lengths[2]) <= tolerance * max(lengths[0], lengths[2], 1.0)
        and abs(lengths[1] - lengths[3]) <= tolerance * max(lengths[1], lengths[3], 1.0)
    )
    return perpendicular and opposite_parallel and opposite_equal


def remove_redundant_polygon_points(points, tolerance: float = 1e-8):
    """Remove duplicate and collinear vertices without changing the polygon."""
    cleaned = list(points)
    changed = True
    while changed and len(cleaned) >= 3:
        changed = False
        for index, current in enumerate(cleaned):
            previous = cleaned[index - 1]
            following = cleaned[(index + 1) % len(cleaned)]
            incoming = subtract(current, previous)
            outgoing = subtract(following, current)
            incoming_length = length(incoming)
            outgoing_length = length(outgoing)
            if incoming_length <= tolerance or outgoing_length <= tolerance:
                cleaned.pop(index)
                changed = True
                break
            # A point between two neighbours on the same straight edge carries
            # no shape information. Soundvision exports occasionally include
            # such points (for example Mur 045 in Uber Eats Arena); retaining
            # them makes the generic five-point splitter create a zero-area
            # second polygon.
            relative_cross = length(cross(incoming, outgoing)) / (incoming_length * outgoing_length)
            same_direction = sum(incoming[i] * outgoing[i] for i in range(3)) > 0.0
            if relative_cross <= tolerance and same_direction:
                cleaned.pop(index)
                changed = True
                break
    return cleaned


def orient_trapezium_points(points):
    """Put the most nearly parallel opposite edges first/last for RDShape."""
    if len(points) != 4:
        return points
    edges = [subtract(points[(index + 1) % 4], points[index]) for index in range(4)]

    def parallel_error(first: int, second: int) -> float:
        denominator = length(edges[first]) * length(edges[second])
        return float("inf") if denominator <= 1e-18 else length(cross(edges[first], edges[second])) / denominator

    # RDShape's native trapezium stores p3-p0 and p1-p2 as its two width edges
    # (the template order is start-left, end-left, end-right, start-right).
    # Rotate only when the other pair is more nearly parallel.
    if parallel_error(0, 2) < parallel_error(1, 3):
        return list(points[1:]) + [points[0]]
    return points


def triangulate_simple_polygon(points):
    """Ear-clip a planar simple polygon while preserving its winding."""
    normal = polygon_normal(points)
    drop_axis = max(range(3), key=lambda axis: abs(normal[axis]))
    projected = [tuple(value for axis, value in enumerate(point) if axis != drop_axis) for point in points]
    signed_area = sum(
        projected[i][0] * projected[(i + 1) % len(points)][1]
        - projected[(i + 1) % len(points)][0] * projected[i][1]
        for i in range(len(points))
    )
    winding = 1.0 if signed_area >= 0.0 else -1.0

    def turn(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def inside_triangle(point, a, b, c):
        values = (winding * turn(a, b, point), winding * turn(b, c, point), winding * turn(c, a, point))
        return min(values) >= -1e-10

    remaining = list(range(len(points)))
    triangles = []
    while len(remaining) > 3:
        for offset, current in enumerate(remaining):
            previous = remaining[offset - 1]
            following = remaining[(offset + 1) % len(remaining)]
            a, b, c = projected[previous], projected[current], projected[following]
            if winding * turn(a, b, c) <= 1e-10:
                continue
            if any(
                inside_triangle(projected[candidate], a, b, c)
                for candidate in remaining
                if candidate not in (previous, current, following)
            ):
                continue
            triangles.append([points[previous], points[current], points[following]])
            remaining.pop(offset)
            break
        else:
            raise ValueError("polygon cannot be decomposed into stable RDShape parts")
    triangles.append([points[index] for index in remaining])
    return triangles


def normalize_rdshape_polygons(items: list[Surface], split_complex: bool = False) -> list[Surface]:
    """Express simple 3/5-point polygons with RDShape's editable 4-point type."""
    def triangle_as_quad(a, b, c):
        # RDShape has no triangle primitive. Truncate the apex by a small but
        # numerically robust width. A duplicated/collinear fourth point is
        # accepted visually but later poisons the global audience delete path
        # with gp_Dir(0). Keep the truncation below 1% of the base and 10 mm.
        base_vector = subtract(b, a)
        base_length = length(base_vector)
        base = normalize(base_vector)
        half_tip = max(0.0005, min(0.005, base_length * 0.005))
        tip_right = tuple(c[i] + base[i] * half_tip for i in range(3))
        tip_left = tuple(c[i] - base[i] * half_tip for i in range(3))
        return [a, b, tip_right, tip_left]

    result: list[Surface] = []
    for item in items:
        cleaned_points = remove_redundant_polygon_points(item.points)
        if cleaned_points != item.points:
            item.points = cleaned_points
        point_sets: list[list[tuple[float, float, float]]]
        if item.native_kind is None and len(item.points) == 3:
            a, b, c = item.points
            point_sets = [triangle_as_quad(a, b, c)]
        elif item.native_kind is None and len(item.points) == 5:
            a, b, c, d, e = item.points
            point_sets = [[a, b, c, d], triangle_as_quad(a, d, e)]
        elif split_complex and item.native_kind is None and len(item.points) > 5:
            point_sets = [triangle_as_quad(*triangle) for triangle in triangulate_simple_polygon(item.points)]
        else:
            result.append(item)
            continue
        # Some Soundvision five-point records contain a valid four-point face
        # followed by a zero-area remainder along one of its edges (for example
        # Palladium's "Back wall R"). The remainder is not a surface and must
        # not become an RDShape object with gp_Dir(0).
        valid_point_sets = []
        for points in point_sets:
            try:
                polygon_normal(points)
            except ValueError:
                continue
            valid_point_sets.append(points)
        for part_index, points in enumerate(valid_point_sets, 1):
            suffix = f" [{part_index}]" if len(valid_point_sets) > 1 else ""
            result.append(Surface(
                item.name + suffix, points, item.audience_height, item.visible, item.group,
                item.color_rgba, item.transparency, "trapezium", item.native_params,
                (item.shape_name + suffix) if item.shape_name else None, item.notes,
            ))
    return result


def build_tcl(template: Path, output: Path, items: list[Surface], group_template: Path | None = None,
              sector_template: Path | None = None, trapezium_template: Path | None = None,
              basemaps: list[Basemap] | None = None,
              basemap_template: Path | None = None, receivers: list[Receiver] | None = None,
              receiver_template: Path | None = None) -> str:
    basemaps = basemaps or []
    receivers = receivers or []
    audience_items = [item for item in items if item.audience_height is not None]
    # Only load the command sets used by the converter.  Avoiding ``pload ALL``
    # keeps the portable distribution independent from visualization, VTK, Qt,
    # OpenVR and other unrelated OCCT third-party runtimes.
    lines = ["pload MODELING OCAF", f"Open {tcl_quote(template.as_posix())} S3D"]
    groups = list(dict.fromkeys(item.group for item in items if item.group))
    if groups and group_template is not None:
        lines.append(f"Open {tcl_quote(group_template.as_posix())} GROUPREF")
    if any(item.native_kind == "sector" for item in items) and sector_template is not None:
        lines.append(f"Open {tcl_quote(sector_template.as_posix())} SECTORREF")
    if any(item.native_kind == "trapezium" for item in items) and trapezium_template is not None:
        lines.append(f"Open {tcl_quote(trapezium_template.as_posix())} TRAPEZIUMREF")
    if basemaps and basemap_template is not None:
        lines.append(f"Open {tcl_quote(basemap_template.as_posix())} BASEMAPREF")
    if receivers and receiver_template is not None:
        lines.append(f"Open {tcl_quote(receiver_template.as_posix())} MICREF")

    # Allocate labels through TDF_TagSource before copying the two template
    # subtrees. This keeps RDShape's future New Object operations collision-free.
    lines += ["NewChild S3D 0:1:1:4" for _ in range(max(0, len(items) - 1))]
    lines += ["NewChild S3D 0:1:1:3" for _ in range(max(0, len(audience_items) - 1))]
    shape_counts = " ".join(map(str, [1, 1, len(items) + 1] + [1] * 13))
    lines.append(
        "SetIntArray S3D 0:1:1:4 0 -g 0e1c2caf-0af1-416f-8848-1b6b171b3bdd "
        f"1 16 {shape_counts}"
    )
    for tag, item in enumerate(items[1:], 3):
        # Linked audience carriers must use RDShape's rectangle function even
        # when their four vertices geometrically form a trapezoid.
        if item.audience_height is not None:
            source = "S3D 0:1:1:4:2"
        elif item.native_kind == "sector":
            source = "SECTORREF 0:1:1:4:3"
        elif item.native_kind == "trapezium":
            source = "TRAPEZIUMREF 0:1:1:4:4"
        else:
            source = "S3D 0:1:1:4:2"
        lines.append(f"CopyDF {source} S3D 0:1:1:4:{tag}")
    for tag in range(3, len(audience_items) + 2):
        lines.append(f"CopyDF S3D 0:1:1:3:2 S3D 0:1:1:3:{tag}")
    if basemaps:
        lines += ["NewChild S3D 0:1:1:6" for _ in basemaps]
        for tag in range(1, len(basemaps) + 1):
            lines.append(f"CopyDF BASEMAPREF 0:1:1:6:1 S3D 0:1:1:6:{tag}")
        lines.append(f"SetInteger S3D 0:1:1:6 {len(basemaps)} 0a31620a-2bf5-49ad-9b34-c870e2b20665")
    if receivers:
        lines += ["NewChild S3D 0:1:1:2" for _ in receivers]
        for tag in range(2, len(receivers) + 2):
            lines.append(f"CopyDF MICREF 0:1:1:2:2 S3D 0:1:1:2:{tag}")
        mic_counts = " ".join(map(str, [len(receivers) + 1] + [1] * 7))
        lines.append(
            "SetIntArray S3D 0:1:1:2 0 -g 0e1c2caf-0af1-416f-8848-1b6b171b3bdd "
            f"1 8 {mic_counts}"
        )
    if groups and group_template is not None:
        lines += ["NewChild S3D 0:1:1:4:1" for _ in groups]
        for group_index, group_name in enumerate(groups, 2):
            entry = f"0:1:1:4:1:{group_index}"
            lines.append(f"CopyDF GROUPREF 0:1:1:4:1:1 S3D {entry}")
            # CopyDF also preserves the group prototype's object id. Assign a
            # value beyond the shape and audience ranges so every RDShape tree
            # object remains globally unique in large grouped projects.
            lines.append(
                f"SetInteger S3D {entry} "
                f"{4326 + len(items) + len(audience_items) + group_index} "
                "65735cf8-a402-4f3e-8d4e-ae4c74763972"
            )
            lines.append(f"SetName S3D {entry} {tcl_quote(group_name)}")
            members = [f"0:1:1:4:{index}" for index, item in enumerate(items, 2) if item.group == group_name]
            lines.append(f"ForgetAll S3D {entry}:5")
            lines.append(f"SetInteger S3D {entry}:5 -1")
            lines.append(f"SetReferenceList S3D {entry}:5 {' '.join(members)}")

    def set_array(entry: str, guid: str, values) -> None:
        text = " ".join(number(x) for x in values)
        lines.append(f"SetRealArray S3D {entry} 0 -g {guid} 1 {len(values)} {text}")

    def configure_geometry(entry: str, item: Surface, shape_name: str, has_normal: bool,
                           display_name: str | None = None, brep_z_offset: float = 0.0) -> None:
        source_points = item.points
        normal, direction, opposite_normal, dimensions = geometry_values(source_points)
        # RDShape's venue shapes use a downward primary axis and an upward
        # placement axis. Audience zones use the upward axis for both. Source
        # formats do not agree on polygon winding, so derive those directions
        # explicitly instead of letting vertex order swap shape and audience.
        upward_normal = normal if normal[2] >= 0.0 else opposite_normal
        downward_normal = tuple(-value for value in upward_normal)
        # Soundvision stores small floating-point deviations even for surfaces
        # that are conceptually planar. Keep the original vertices as metadata,
        # but project the BRep wire onto the plane through the first 3 points.
        brep_points = []
        for point in source_points:
            distance = sum(normal[i] * (point[i] - source_points[0][i]) for i in range(3))
            brep_points.append(tuple(point[i] - distance * normal[i] for i in range(3)))
        # RDShape compares its point metadata with the stored BRep.  Feeding it
        # Soundvision's slightly non-planar source coordinates while the BRep is
        # planar can make the loader dereference an invalid result.  Store the
        # same projected coordinates in both representations.
        points = brep_points
        if has_normal and len(points) >= 3:
            origin = tuple((points[0][i] + points[-1][i]) / 2.0 for i in range(3))
        else:
            # Native RDShape audience objects store their main placement at
            # the polygon centre, while the nested placement below uses p0.
            # Reusing p0 for both is tolerated by some rectangles but leaves
            # linked inclined/trapezoidal audiences in an invalid state and
            # their delete path eventually constructs gp_Dir(0).
            origin = tuple(sum(point[i] for point in points) / len(points) for i in range(3))
        if item.native_kind == "trapezium" and len(points) == 4:
            start_mid = tuple((points[0][i] + points[3][i]) / 2.0 for i in range(3))
            end_mid = tuple((points[1][i] + points[2][i]) / 2.0 for i in range(3))
            direction = normalize(tuple(end_mid[i] - start_mid[i] for i in range(3)))
            if has_normal:
                origin = start_mid
        lines.append(f"SetName S3D {entry} {tcl_quote(display_name or item.name)}")
        if item.notes:
            lines.append(f"SetComment S3D {entry} {tcl_quote(item.notes)}")
        lines.append(f"SetInteger S3D {entry}:2 {len(points)} 22529e82-4956-4853-94ac-e717a2a7c40c")
        lines.append(
            f"SetIntArray S3D {entry}:2 0 -g 77baa734-fbad-44de-b978-9e6076d3ce34 "
            "1 3 0 0 0"
        )
        for index, point in enumerate(points, 1):
            set_array(f"{entry}:2:{index}", POINT_GUID, point)
        # The generic RDShape prototype contains four point child labels.
        for index in range(len(points) + 1, 5):
            lines.append(f"ForgetAll S3D {entry}:2:{index}")
        if has_normal:
            set_array(f"{entry}:2", NORMAL_GUID, upward_normal)
        set_array(f"{entry}:3", ORIGIN_GUID, origin)
        set_array(f"{entry}:3", AXIS_GUID, downward_normal if has_normal else upward_normal)
        set_array(f"{entry}:3", DIRECTION_GUID, direction)
        set_array(f"{entry}:3:1", ORIGIN_GUID, (0.0, 0.0, 0.0) if has_normal else points[0])
        set_array(f"{entry}:3:1", AXIS_GUID, upward_normal)
        set_array(f"{entry}:3:1", DIRECTION_GUID, direction)
        shape_points = [(x, y, z + brep_z_offset) for x, y, z in brep_points]
        for point_index, point in enumerate(shape_points, 1):
            coords = " ".join(number(value) for value in point)
            lines.append(f"vertex {shape_name}_v{point_index} {coords}")
        edge_names = []
        for point_index in range(1, len(points) + 1):
            next_index = point_index + 1 if point_index < len(points) else 1
            edge_name = f"{shape_name}_e{point_index}"
            edge_names.append(edge_name)
            lines.append(f"edge {edge_name} {shape_name}_v{point_index} {shape_name}_v{next_index}")
        lines.append(f"wire {shape_name}_wire {' '.join(edge_names)}")
        lines.append(f"mkplane {shape_name}_face {shape_name}_wire 0")
        lines.append(f"SetShape S3D {entry} {shape_name}_face")
        if has_normal:
            if item.native_kind == "trapezium" and len(points) == 4:
                start_width = length(tuple(points[3][i] - points[0][i] for i in range(3)))
                end_width = length(tuple(points[2][i] - points[1][i] for i in range(3)))
                start_mid = tuple((points[0][i] + points[3][i]) / 2.0 for i in range(3))
                end_mid = tuple((points[1][i] + points[2][i]) / 2.0 for i in range(3))
                depth = length(tuple(end_mid[i] - start_mid[i] for i in range(3)))
                dimensions = (start_width, end_width, depth)
            for guid, value in zip(
                ("2aa19bdb-c89a-44e2-9d46-e55b206dc1fb", "170195fe-58a8-4332-9ef0-99a73777c848", "05a16e19-a030-46c7-ba78-40127072baed"),
                dimensions,
            ):
                lines.append(f"SetReal S3D {entry} {number(value)} {guid}")

    def configure_sector(entry: str, item: Surface, shape_name: str, display_name: str | None = None) -> None:
        params = item.native_params or {}
        cx, cy = params["center_x"], params["center_y"]
        orientation = params["orientation"]
        sweep = params["sweep"]
        inner_radius, outer_radius = params["inner_radius"], params["outer_radius"]
        inner_height, outer_height = params["inner_height"], params["outer_height"]
        start = math.radians(orientation - sweep / 2.0)
        end = math.radians(orientation + sweep / 2.0)
        direction = (math.cos(math.radians(orientation)), math.sin(math.radians(orientation)), 0.0)
        lines.append(f"SetName S3D {entry} {tcl_quote(display_name or item.name)}")
        for guid, value in (
            ("868cb6c0-959d-449b-85d7-003062ca5af2", 90.0),
            ("90c880fe-a649-4dee-870e-3725f850f4a4", inner_radius),
            ("72a56384-8c18-47fa-aa01-8c0d71ab1563", inner_height),
            ("adbddf23-6277-42cd-8fee-4a27c6680d66", sweep),
            ("9d91f37b-0d8f-42be-b272-fbfdad4f7d49", outer_radius),
            ("5b5bbb88-b2ac-4b22-a2c1-eca17de401a3", outer_height),
        ):
            lines.append(f"SetReal S3D {entry}:2 {number(value)} {guid}")
        set_array(f"{entry}:2", NORMAL_GUID, (0, 0, 1))
        set_array(f"{entry}:3", ORIGIN_GUID, (cx, cy, inner_height))
        set_array(f"{entry}:3", AXIS_GUID, (0, 0, 1))
        set_array(f"{entry}:3", DIRECTION_GUID, direction)
        set_array(f"{entry}:3:1", ORIGIN_GUID, (cx, cy, inner_height))
        set_array(f"{entry}:3:1", AXIS_GUID, (0, 0, 1))
        set_array(f"{entry}:3:1", DIRECTION_GUID, direction)
        if inner_radius <= 1e-9:
            lines.append(f"vertex {shape_name}_inner {number(cx)} {number(cy)} {number(inner_height)}")
            inner_section = f"{shape_name}_inner"
        else:
            lines.append(f"circle {shape_name}_ci {number(cx)} {number(cy)} {number(inner_height)} 0 0 1 1 0 0 {number(inner_radius)}")
            lines.append(f"trim {shape_name}_cia {shape_name}_ci {number(start)} {number(end)}")
            lines.append(f"mkedge {shape_name}_cie {shape_name}_cia")
            lines.append(f"wire {shape_name}_ciw {shape_name}_cie")
            inner_section = f"{shape_name}_ciw"
        lines.append(f"circle {shape_name}_co {number(cx)} {number(cy)} {number(outer_height)} 0 0 1 1 0 0 {number(outer_radius)}")
        lines.append(f"trim {shape_name}_coa {shape_name}_co {number(start)} {number(end)}")
        lines.append(f"mkedge {shape_name}_coe {shape_name}_coa")
        lines.append(f"wire {shape_name}_cow {shape_name}_coe")
        lines.append(f"thrusections {shape_name}_face 0 1 {inner_section} {shape_name}_cow")
        lines.append(f"SetShape S3D {entry} {shape_name}_face")

    def configure_sector_audience(entry: str, item: Surface, shape_name: str) -> None:
        params = item.native_params or {}
        direction = (math.cos(math.radians(params["orientation"])), math.sin(math.radians(params["orientation"])), 0.0)
        ear_height = item.audience_height or 0.0
        audience_shape = f"{shape_name}_audience"
        lines.append(f"SetName S3D {entry} {tcl_quote(item.name)}")
        lines.append(f"SetInteger S3D {entry}:2 0 22529e82-4956-4853-94ac-e717a2a7c40c")
        set_array(f"{entry}:3", ORIGIN_GUID,
                  (params["center_x"], params["center_y"], params["inner_height"] + ear_height))
        set_array(f"{entry}:3", AXIS_GUID, (0, 0, 1))
        set_array(f"{entry}:3", DIRECTION_GUID, direction)
        set_array(f"{entry}:3:1", ORIGIN_GUID,
                  (params["center_x"], params["center_y"], params["inner_height"] + ear_height))
        set_array(f"{entry}:3:1", AXIS_GUID, (0, 0, -1))
        set_array(f"{entry}:3:1", DIRECTION_GUID, direction)
        lines.append(f"tcopy {shape_name}_face {audience_shape}")
        lines.append(f"ttranslate {audience_shape} 0 0 {number(ear_height)}")
        lines.append(f"SetShape S3D {entry} {audience_shape}")

    def configure_basemap(entry: str, basemap: Basemap, index: int) -> None:
        shape_name = f"basemap_{index}"
        xdir, ydir = basemap.x_direction, basemap.y_direction
        normal = cross(xdir, ydir)
        if abs(normal[2]) > 0.5 or abs(normal[0]) > 0.5:
            presentation_normal = normal
        else:
            # Native RDShape XZ basemaps (normal on Y) face toward the venue.
            # YZ basemaps (normal on X) retain their source normal; treating
            # both vertical planes alike turns the YZ raster upside down.
            presentation_normal = tuple(-v for v in normal)
        presentation_direction = xdir
        lines.append(f"SetName S3D {entry} {tcl_quote(basemap.name)}")
        for guid, value in (
            ("86f8e82b-9582-4e30-8803-df609e20fec6", basemap.width),
            ("e8515260-de8f-4cba-9cc7-8ce3d120e1e1", basemap.height),
            ("5fd13e0f-6bc1-4e27-94e8-af76ebfbcaf0", basemap.height / basemap.width),
        ):
            lines.append(f"SetReal S3D {entry} {number(value)} {guid}")
        byte_values = " ".join(str(value) for value in basemap.image)
        lines.append(
            f"SetByteArray S3D {entry} 0 -g ed8b2f1b-f784-4011-beab-f3776a730118 "
            f"1 {len(basemap.image)} {byte_values}"
        )
        set_array(f"{entry}:3", ORIGIN_GUID, basemap.origin)
        set_array(f"{entry}:3", AXIS_GUID, presentation_normal)
        set_array(f"{entry}:3", DIRECTION_GUID, presentation_direction)
        set_array(f"{entry}:3:1", ORIGIN_GUID, basemap.origin)
        set_array(f"{entry}:3:1", AXIS_GUID, presentation_normal)
        set_array(f"{entry}:3:1", DIRECTION_GUID, presentation_direction)
        lines.append(
            f"plane {shape_name}_surface {' '.join(number(v) for v in basemap.origin)} "
            f"{' '.join(number(v) for v in presentation_normal)} {' '.join(number(v) for v in xdir)}"
        )
        lines.append(
            f"mkface {shape_name}_face {shape_name}_surface "
            f"{number(-basemap.width / 2)} {number(basemap.width / 2)} "
            f"{number(-basemap.height / 2)} {number(basemap.height / 2)}"
        )
        lines.append(f"SetShape S3D {entry} {shape_name}_face")

    def configure_receiver(entry: str, receiver: Receiver, index: int) -> None:
        x, y, z = receiver.position
        angle = math.radians(receiver.angle)
        direction = (math.cos(angle), math.sin(angle), 0.0)
        lines.append(f"SetName S3D {entry} {tcl_quote(receiver.name)}")
        lines.append(f"SetInteger S3D {entry} {4328 + index} 65735cf8-a402-4f3e-8d4e-ae4c74763972")
        lines.append(
            f"SetInteger S3D {entry}:4 {RECEIVER_COLOR_INDICES[(index - 1) % len(RECEIVER_COLOR_INDICES)]} "
            "07918309-717b-4487-9031-0214c38bb1a3"
        )
        set_array(f"{entry}:3", ORIGIN_GUID, receiver.position)
        set_array(f"{entry}:3", AXIS_GUID, (0.0, 0.0, 1.0))
        set_array(f"{entry}:3", DIRECTION_GUID, direction)
        set_array(f"{entry}:3:1", ORIGIN_GUID, receiver.position)
        set_array(f"{entry}:3:1", AXIS_GUID, (0.0, 0.0, 1.0))
        set_array(f"{entry}:3:1", DIRECTION_GUID, direction)
        # The prototype microphone is located at (5, 0, 1.7).
        lines.append("GetShape MICREF 0:1:1:2:2 receiver_prototype")
        lines.append(f"tcopy receiver_prototype receiver_{index}")
        lines.append(f"ttranslate receiver_{index} {number(x - 5.0)} {number(y)} {number(z - 1.7)}")
        if abs(receiver.angle) > 1e-12:
            lines.append(f"trotate receiver_{index} {number(x)} {number(y)} {number(z)} 0 0 1 {number(receiver.angle)}")
        lines.append(f"SetShape S3D {entry} receiver_{index}")

    audience_index = 0
    for shape_index, item in enumerate(items, 2):
        shape_entry = f"0:1:1:4:{shape_index}"
        # 4328/4329 are RDShape's rectangle-carrier/audience type markers, not
        # globally unique object ids. Native and stable ArrayCalc documents
        # keep these values constant for every linked audience pair.
        shape_type_marker = 4328 if item.audience_height is not None else 4326 + shape_index
        lines.append(
            f"SetInteger S3D {shape_entry} {shape_type_marker} "
            "65735cf8-a402-4f3e-8d4e-ae4c74763972"
        )
        # The generic prototype is linked to its own template audience. That
        # reference must never be copied into ordinary shapes: after deleting
        # one shape RDShape otherwise follows a stale cross-reference when the
        # next shape is selected.
        lines.append(f"ForgetAll S3D {shape_entry}:1")
        lines.append(f"SetInteger S3D {shape_entry}:1 0")
        lines.append(
            f"SetInteger S3D {shape_entry}:1 0 "
            "59c99e6f-a3f4-4dd0-8b80-1cf774577d2a"
        )
        if item.native_kind == "sector" and item.audience_height is None:
            configure_sector(shape_entry, item, f"shape_{shape_index}", item.shape_name)
        else:
            configure_geometry(shape_entry, item, f"shape_{shape_index}", True, item.shape_name)
        if item.audience_height is not None:
            audience_index += 1
            audience_entry = f"0:1:1:3:{audience_index + 1}"
            # Keep the audience type marker from the native prototype. This
            # value deliberately repeats across audience objects.
            lines.append(
                f"SetInteger S3D {audience_entry} 4329 "
                "65735cf8-a402-4f3e-8d4e-ae4c74763972"
            )
            configure_geometry(
                audience_entry,
                item,
                f"audience_{audience_index + 1}",
                False,
                brep_z_offset=item.audience_height,
            )
            # RDShape stores ear height as a simulation property while the
            # generated audience BRep is located at that same vertical offset.
            # The mode enum is 1 = Sitting, 2 = Standing, 3 = Custom.
            if math.isclose(item.audience_height, 1.2, abs_tol=1e-6):
                audience_mode = 1
            elif math.isclose(item.audience_height, 1.7, abs_tol=1e-6):
                audience_mode = 2
            else:
                audience_mode = 3
            lines.append(
                f"SetInteger S3D {audience_entry}:1 {audience_mode} "
                "26f49e06-13b0-4d63-a625-c71480b4acca"
            )
            lines.append(
                f"SetReal S3D {audience_entry}:1 {number(item.audience_height)} cd0e687d-bd0d-4fb2-ba20-2c2b7ff5d4c2"
            )
            lines.append(f"SetReference S3D {audience_entry}:1 {shape_entry}")
            lines.append(f"SetReferenceList S3D {shape_entry}:1 {audience_entry}")

    for basemap_index, basemap in enumerate(basemaps, 1):
        configure_basemap(f"0:1:1:6:{basemap_index}", basemap, basemap_index)

    for receiver_index, receiver in enumerate(receivers, 1):
        configure_receiver(f"0:1:1:2:{receiver_index + 1}", receiver, receiver_index)

    lines += [
        *( ["Close GROUPREF"] if groups and group_template is not None else [] ),
        *( ["Close SECTORREF"] if any(item.native_kind == "sector" for item in items) and sector_template is not None else [] ),
        *( ["Close TRAPEZIUMREF"] if any(item.native_kind == "trapezium" for item in items) and trapezium_template is not None else [] ),
        *( ["Close BASEMAPREF"] if basemaps and basemap_template is not None else [] ),
        *( ["Close MICREF"] if receivers and receiver_template is not None else [] ),
        "Format S3D BinOcaf",
        f"SaveAs S3D {tcl_quote(output.as_posix())}",
        "Close S3D",
    ]
    error_log = output.with_suffix(output.suffix + ".error.txt").as_posix()
    body = "\n".join("  " + line for line in lines)
    return (
        f"if {{[catch {{\n{body}\n}} message options]}} {{\n"
        f"  set errorFile [open {tcl_quote(error_log)} w]\n"
        "  puts $errorFile $message\n"
        "  puts $errorFile $options\n"
        "  close $errorFile\n"
        "}\nexit\n"
    )


def occt_environment(occt_root: Path) -> tuple[Path, dict[str, str]]:
    draw = occt_root / "win64" / "vc14" / "bin" / "DRAWEXE.exe"
    third_party = occt_root / "3rdparty-vc14-64"
    if not draw.exists() or not third_party.exists():
        raise FileNotFoundError("OCCT or 3rdparty-vc14-64 was not found.")
    # Some redistributed DLLs live one level below ``bin`` (for example
    # openvr/bin/win64 and TBB's architecture folder). Add the actual DLL
    # directories instead of relying on OCCT's developer-oriented env.bat.
    dll_dirs = {p.parent for p in third_party.rglob("*.dll") if "debug" not in {x.lower() for x in p.parts}}
    bins = [draw.parent] + sorted(dll_dirs, key=lambda p: str(p).lower())
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(map(str, bins)) + os.pathsep + env.get("PATH", "")
    env["CASROOT"] = str(occt_root)
    env["CSF_OCCTResourcePath"] = str(occt_root / "src")
    env["CSF_StandardDefaults"] = str(occt_root / "src" / "StdResource")
    return draw, env


def run_draw_script(draw: Path, env: dict[str, str], cwd: Path, script: str) -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".tcl", encoding="utf-8", delete=False, dir=cwd) as handle:
        handle.write(script)
        script_path = Path(handle.name)
    batch_path = script_path.with_suffix(".bat")
    batch_path.write_text(f'@echo off\r\n"{draw}" -f "{script_path}"\r\n', encoding="utf-8")
    try:
        return subprocess.run(["cmd.exe", "/d", "/c", str(batch_path)], env=env, cwd=cwd).returncode
    finally:
        script_path.unlink(missing_ok=True)
        batch_path.unlink(missing_ok=True)


def occt_palette(occt_root: Path) -> list[tuple[int, int, int]]:
    header = (occt_root / "inc" / "Quantity_NameOfColor.hxx").read_text(encoding="utf-8", errors="ignore")
    html_path = Path(__file__).with_name("occt_colors.html")
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    rgb_by_name = {name: tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
                   for name, value in re.findall(r"text-align:left'>([A-Z0-9_]+)</td>.*?#([0-9A-Fa-f]{6})", html, re.S)}
    values, current = {}, -1
    enum_body = header.split("enum Quantity_NameOfColor", 1)[1].split("};", 1)[0]
    for raw in enum_body.splitlines():
        match = re.search(r"Quantity_NOC_([A-Z0-9_]+)(?:\s*=\s*Quantity_NOC_([A-Z0-9_]+))?\s*,?", raw)
        if not match:
            continue
        name, alias = match.groups()
        if alias:
            values[name] = values[alias]
        else:
            current += 1
            values[name] = current
    palette = [(192, 192, 192)] * (max(values.values()) + 1)
    for name, index in values.items():
        if name in rgb_by_name:
            palette[index] = rgb_by_name[name]
    return palette


def apply_presentations(output: Path, items: list[Surface], draw: Path, env: dict[str, str], occt_root: Path,
                        basemaps: list[Basemap] | None = None,
                        receivers: list[Receiver] | None = None) -> None:
    basemaps = basemaps or []
    receivers = receivers or []
    if not any(item.color_rgba is not None or not item.visible for item in items) and not basemaps and not receivers:
        return
    xml_path = output.with_suffix(".presentation.xml")
    final_path = output.with_suffix(".presentation.s3d")
    export_script = "\n".join([
        "pload OCAF", f"Open {tcl_quote(output.as_posix())} DOC", "Format DOC XmlOcaf",
        f"SaveAs DOC {tcl_quote(xml_path.as_posix())}", "Close DOC", "exit",
    ])
    if run_draw_script(draw, env, output.parent, export_script) != 0 or not xml_path.exists():
        raise RuntimeError("OCCT could not export the presentation data.")
    xml_text = xml_path.read_text(encoding="utf-8")
    tree = ET.ElementTree(ET.fromstring(xml_text))
    root = tree.getroot()
    namespace = root.tag.partition("}")[0] + "}" if root.tag.startswith("{") else ""
    shapes_label = next(label for label in root.iter(namespace + "label")
                        if (name := label.find(namespace + "TDataStd_Name")) is not None and name.text == "Shapes")
    labels_by_tag = {int(label.get("tag")): label for label in shapes_label.findall(namespace + "label")}
    palette = occt_palette(occt_root)
    replacements = {}
    transparency_values = {}
    for index, item in enumerate(items, 2):
        label = labels_by_tag.get(index)
        if label is None:
            continue
        presentation = label.find(namespace + "TDataXtd_Presentation")
        if presentation is None:
            continue
        if item.color_rgba is not None:
            target = tuple(round(component * 255) for component in item.color_rgba[:3])
            color_index = min(range(len(palette)), key=lambda i: sum((palette[i][j] - target[j]) ** 2 for j in range(3)))
            presentation.set("color", str(color_index))
        presentation.set("transparency", number(item.transparency))
        transparency_property = next((attribute for attribute in label.findall(namespace + "TDataStd_Real")
                                      if attribute.get("realattguid") == "6396c74e-86d9-43ea-a5c9-0c9c2ba103af"), None)
        if transparency_property is not None:
            transparency_values[transparency_property.get("id")] = number(item.transparency)
        if item.visible:
            presentation.set("isdisplayed", "true")
        else:
            presentation.attrib.pop("isdisplayed", None)
        replacements[presentation.get("id")] = dict(presentation.attrib)
    if basemaps:
        basemaps_label = next(label for label in root.iter(namespace + "label")
                              if (name := label.find(namespace + "TDataStd_Name")) is not None and name.text == "Basemaps")
        basemap_labels = {int(label.get("tag")): label for label in basemaps_label.findall(namespace + "label")}
        for index, basemap in enumerate(basemaps, 1):
            label = basemap_labels.get(index)
            presentation = label.find(namespace + "TDataXtd_Presentation") if label is not None else None
            if presentation is None:
                continue
            presentation.set("transparency", number(1.0 - basemap.opacity))
            presentation.set("isdisplayed", "true")
            replacements[presentation.get("id")] = dict(presentation.attrib)
    if receivers:
        mics_label = next(label for label in root.iter(namespace + "label")
                          if (name := label.find(namespace + "TDataStd_Name")) is not None and name.text == "Mics")
        receiver_labels = {int(label.get("tag")): label for label in mics_label.findall(namespace + "label")}
        for index, receiver in enumerate(receivers, 1):
            label = receiver_labels.get(index + 1)
            if label is None:
                continue
            color_index = RECEIVER_COLOR_INDICES[(index - 1) % len(RECEIVER_COLOR_INDICES)]
            presentation = label.find(namespace + "TDataXtd_Presentation")
            if presentation is not None:
                presentation.set("color", str(color_index))
                presentation.set("isdisplayed", "true")
                replacements[presentation.get("id")] = dict(presentation.attrib)
            color_label = next((child for child in label.findall(namespace + "label")
                                if child.get("tag") == "4"), None)
            if color_label is not None:
                color_property = next((attribute for attribute in color_label.findall(namespace + "TDataStd_Integer")
                                       if attribute.get("intattguid") == "07918309-717b-4487-9031-0214c38bb1a3"), None)
                if color_property is not None:
                    color_property.text = str(color_index)
    def update_presentation(match):
        tag = match.group(0)
        id_match = re.search(r'\bid="([^"]+)"', tag)
        if id_match is None or id_match.group(1) not in replacements:
            return tag
        attributes = replacements[id_match.group(1)]
        for attribute in ("color", "transparency", "isdisplayed"):
            tag = re.sub(rf'\s+{attribute}="[^"]*"', "", tag)
        controlled = {key: attributes[key] for key in ("color", "transparency", "isdisplayed")
                      if key in attributes}
        tag = tag[:-2] + " " + " ".join(f'{key}="{value}"' for key, value in controlled.items()) + "/>"
        return tag

    # One linear scan is essential for large venues.  Re-running a regular
    # expression and slicing the complete OCAF XML once per shape made this
    # stage effectively quadratic for projects with several thousand faces.
    xml_text = re.sub(r'<TDataXtd_Presentation\s+[^>]*/>', update_presentation, xml_text)
    # Shape templates carry an active presentation on their first control
    # point. Copying that subtree once per imported object otherwise leaves the
    # yellow edit arrows permanently visible. RDShape recreates these helpers
    # itself when the user enters edit mode, so persist them as hidden.
    def hide_control_point_presentation(match):
        tag = match.group(0)
        return re.sub(r'\s+isdisplayed="[^"]*"', "", tag)

    xml_text = re.sub(
        r'<TDataXtd_Presentation\s+[^>]*guid="a3f8675e-8047-410a-9985-77410a3b8ab3"[^>]*/>',
        hide_control_point_presentation,
        xml_text,
    )
    def update_transparency_property(match):
        opening, attribute_id, value, closing = match.groups()
        replacement = transparency_values.get(attribute_id)
        return opening + (replacement if replacement is not None else value) + closing

    xml_text = re.sub(
        r'(<TDataStd_Real\s+[^>]*\bid="([^"]+)"[^>]*\brealattguid="6396c74e-86d9-43ea-a5c9-0c9c2ba103af"[^>]*>)([^<]*)(</TDataStd_Real>)',
        update_transparency_property,
        xml_text,
    )
    xml_path.write_text(xml_text, encoding="utf-8")
    import_script = "\n".join([
        "pload OCAF", f"Open {tcl_quote(xml_path.as_posix())} DOC", "Format DOC BinOcaf",
        f"SaveAs DOC {tcl_quote(final_path.as_posix())}", "Close DOC", "exit",
    ])
    run_code = run_draw_script(draw, env, output.parent, import_script)
    appended = Path(str(final_path) + ".cbf")
    if not final_path.exists() and appended.exists():
        final_path = appended
    if run_code != 0 or not final_path.exists():
        raise RuntimeError("OCCT could not save the presentation data.")
    os.replace(final_path, output)
    xml_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Soundvision .xmlp/.xmls, ArrayCalc .dbacv/.dbpr, EASE Focus .fc3 or EASE 5 .eprj file")
    parser.add_argument("output", type=Path, help="RDShape .s3d output file")
    parser.add_argument("--template", type=Path, help="optional RDShape template S3D")
    parser.add_argument("--occt", type=Path, default=Path("occt-vc143-64"))
    parser.add_argument("--max-inclination", type=float, help="only include surfaces up to this inclination in degrees")
    parser.add_argument("--skip-non-quads", action="store_true", help="only include four-point surfaces")
    parser.add_argument("--audiences-only", action="store_true", help="only include surfaces marked as audience")
    parser.add_argument("--shapes-only", action="store_true", help="create shapes without linked RDShape audience objects")
    parser.add_argument("--limit", type=int, help="only convert the first N matching surfaces")
    args = parser.parse_args()

    suffix = args.input.suffix.lower()
    importers = {
        ".xmlp": lambda path: surfaces(read_xmlp(path)),
        ".xmls": lambda path: surfaces(read_xmlp(path)),
        ".xml": lambda path: surfaces(read_xmlp(path)),
        ".dbacv": read_arraycalc_xml,
        ".dbpr": read_arraycalc_db,
    }
    if suffix not in importers and suffix not in (".fc3", ".eprj"):
        raise ValueError(f"Unsupported input format: {suffix}")
    print(f"Reading {args.input.name} ...", flush=True)
    basemaps: list[Basemap] = []
    receivers: list[Receiver] = []
    if suffix == ".fc3":
        items, basemaps, receivers = read_easefocus(args.input, include_basemaps=True)
    elif suffix == ".eprj":
        items, basemaps = read_ease5(args.input)
    else:
        items = importers[suffix](args.input)
    print(f"Imported {len(items)} surfaces. Preparing geometry ...", flush=True)
    if basemaps:
        print(f"Imported {len(basemaps)} basemap(s).", flush=True)
    if receivers:
        print(f"Imported {len(receivers)} receiver(s).", flush=True)
    if len(items) >= 1000:
        print(f"Large project: {len(items)} surfaces. Conversion may take several minutes.", flush=True)
    if args.max_inclination is not None:
        items = [item for item in items if inclination(item) <= args.max_inclination + 1e-9]
    if args.skip_non_quads:
        items = [item for item in items if len(item.points) == 4]
    if args.audiences_only:
        items = [item for item in items if item.audience_height is not None]
    if args.limit is not None:
        items = items[: args.limit]
    items = normalize_rdshape_polygons(items, split_complex=suffix in (".xmlp", ".xmls", ".xml"))
    if args.shapes_only:
        items = [Surface(item.name, item.points, None, item.visible, item.group, item.color_rgba,
                         item.transparency, item.native_kind, item.native_params, item.shape_name,
                         item.notes) for item in items]
    # The base template's first Shapes entry is the generic polygon prototype.
    # Keep it first; subsequent native entries are copied from their own template.
    items.sort(key=lambda item: item.native_kind is not None)
    if not items:
        raise ValueError("The scene does not contain any surfaces.")
    for item in items:
        if len(item.points) < 3:
            raise ValueError(f"{item.name}: fewer than three points")
        try:
            base_normal = polygon_normal(item.points)
        except ValueError:
            raise ValueError(f"{item.name}: degenerate surface")
        max_deviation = max(
            (abs(sum(base_normal[i] * (point[i] - item.points[0][i]) for i in range(3))) for point in item.points[1:]),
            default=0.0,
        )
        if item.native_kind is None and max_deviation > 5e-2:
            raise ValueError(f"{item.name}: non-planar (deviation {max_deviation:g} m)")
        if item.native_kind is None and len(item.points) == 4 and not is_rectangular_quad(item.points):
            item.points = orient_trapezium_points(item.points)
            item.native_kind = "trapezium"
    # Keep the generic Rectangle prototype in the first Shapes slot whenever
    # possible; native-function entries are copied into subsequent slots.
    items.sort(key=lambda item: item.native_kind is not None)

    default_template = Path("rdshape/20_20_20__audience.s3d" if any(item.audience_height is not None for item in items) else "rdshape/20_20_20.s3d")
    template = (args.template or default_template).resolve()
    group_template_candidate = Path("rdshape/group_template.s3d")
    if not group_template_candidate.exists():
        group_template_candidate = Path("ArrayCalc/rdshape_groups_colors.s3d")
    group_template = group_template_candidate.resolve() if group_template_candidate.exists() else None
    sector_template_candidate = Path("rdshape/sector_template.s3d")
    sector_template = sector_template_candidate.resolve() if sector_template_candidate.exists() else None
    if any(item.native_kind == "sector" for item in items) and sector_template is None:
        raise FileNotFoundError("The RDShape sector template was not found.")
    trapezium_template_candidate = Path("rdshape/trapezium_template.s3d")
    trapezium_template = trapezium_template_candidate.resolve() if trapezium_template_candidate.exists() else None
    if any(item.native_kind == "trapezium" for item in items) and trapezium_template is None:
        raise FileNotFoundError("The RDShape trapezium template was not found.")
    basemap_template_candidate = Path("rdshape/basemap_template.s3d")
    basemap_template = basemap_template_candidate.resolve() if basemap_template_candidate.exists() else None
    if basemaps and basemap_template is None:
        raise FileNotFoundError("The RDShape basemap template was not found.")
    receiver_template_candidate = Path("rdshape/10_10_10_onereceiver.s3d")
    receiver_template = receiver_template_candidate.resolve() if receiver_template_candidate.exists() else None
    if receivers and receiver_template is None:
        raise FileNotFoundError("The RDShape receiver template was not found.")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    draw, env = occt_environment(args.occt.resolve())
    print("Creating RDShape geometry ...", flush=True)
    script = build_tcl(
        template, output, items, group_template=group_template, sector_template=sector_template,
        trapezium_template=trapezium_template, basemaps=basemaps,
        basemap_template=basemap_template, receivers=receivers, receiver_template=receiver_template,
    )
    env["THIRDPARTY_DIR"] = str(args.occt.resolve() / "3rdparty-vc14-64")
    return_code = run_draw_script(draw, env, output.parent, script)
    appended_extension = Path(str(output) + ".cbf")
    if not output.exists() and appended_extension.exists():
        appended_extension.replace(output)
    if return_code != 0 or not output.exists():
        raise RuntimeError(f"OCCT could not create the S3D file (exit code {return_code}).")
    print("Applying names, groups and colors ...", flush=True)
    apply_presentations(output, items, draw, env, args.occt.resolve(), basemaps, receivers)
    audience_count = sum(item.audience_height is not None for item in items)
    print(f"Created: {output} ({output.stat().st_size} bytes, {len(items)} shapes, "
          f"{audience_count} audiences, {len(receivers)} receivers)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
