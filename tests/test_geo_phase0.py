import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geo.geo_phase0 import OVERPASS_REQUEST_TIMEOUT, buildings_to_local, bounds_for_metric_extent, download_buildings, global_pixel, local_from_lon_lat, overpass_query, way_polygon
from geo.geo_to_s3d import building_heights, prepare_surfaces, rotate_xy, select_buildings, terrain_height, terrain_surfaces, triangulate_ring
from geo.elevation import grid_to_terrain, parse_ascii_grid


class GeoPhase0Tests(unittest.TestCase):
    def test_metric_extent_round_trip(self):
        lat, lon = 50.941066, 6.957008
        bounds = bounds_for_metric_extent(lat, lon, 200.0, 200.0)
        west_x, _ = local_from_lon_lat(bounds["west"], lat, lon, lat)
        east_x, _ = local_from_lon_lat(bounds["east"], lat, lon, lat)
        _, south_y = local_from_lon_lat(lon, bounds["south"], lon, lat)
        _, north_y = local_from_lon_lat(lon, bounds["north"], lon, lat)
        self.assertAlmostEqual(east_x - west_x, 200.0, places=5)
        self.assertAlmostEqual(north_y - south_y, 200.0, places=5)

    def test_center_is_local_origin(self):
        self.assertEqual(local_from_lon_lat(6.957008, 50.941066, 6.957008, 50.941066), (0.0, 0.0))

    def test_global_pixel_is_monotonic(self):
        left, bottom = global_pixel(6.95, 50.93, 18)
        right, top = global_pixel(6.96, 50.95, 18)
        self.assertGreater(right, left)
        self.assertLess(top, bottom)

    def test_overpass_query_contains_bbox_and_buildings(self):
        query = overpass_query({"south": 1, "west": 2, "north": 3, "east": 4})
        self.assertIn('way["building"](1.00000000,2.00000000,3.00000000,4.00000000)', query)
        self.assertIn('relation["building"]', query)
        self.assertIn('way["building:part"]', query)

    def test_overpass_http_400_uses_fallback(self):
        failure = urllib.error.HTTPError("primary", 400, "Bad Request", {}, None)
        failure.read = lambda: b"bad primary response"
        response = b'{"elements": []}'
        with patch("geo.geo_phase0.cached_request", side_effect=[failure, response]) as request:
            buildings, endpoint = download_buildings({"south": 1, "west": 2, "north": 3, "east": 4}, Path("unused"), "https://overpass-api.de/api/interpreter")
        self.assertEqual(buildings["features"], [])
        self.assertEqual(endpoint, "https://overpass.private.coffee/api/interpreter")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.kwargs["attempts"] == 1 for call in request.call_args_list))
        self.assertTrue(all(call.kwargs["timeout"] == OVERPASS_REQUEST_TIMEOUT for call in request.call_args_list))

    def test_overpass_uses_secondary_fallback(self):
        first = urllib.error.HTTPError("primary", 504, "Gateway Timeout", {}, None)
        second = urllib.error.HTTPError("fallback", 502, "Bad Gateway", {}, None)
        first.read = second.read = lambda: b"temporarily unavailable"
        with patch("geo.geo_phase0.cached_request", side_effect=[first, second, b'{"elements": []}']) as request:
            buildings, endpoint = download_buildings(
                {"south": 1, "west": 2, "north": 3, "east": 4}, Path("unused"),
                "https://overpass-api.de/api/interpreter",
            )
        self.assertEqual(buildings["features"], [])
        self.assertEqual(endpoint, "https://overpass.kumi.systems/api/interpreter")
        self.assertEqual(request.call_count, 3)

    def test_invalid_way_is_skipped(self):
        self.assertIsNone(way_polygon({"nodes": [1, 2]}, {1: (1, 1), 2: (2, 2)}))

    def test_building_coordinates_are_normalized_to_local_metres(self):
        source = {"features": [{"id": "way/1", "properties": {"building": "yes"}, "geometry": {"type": "Polygon", "coordinates": [[[6.957008, 50.941066], [6.957108, 50.941066], [6.957008, 50.941066]]]}}]}
        result = buildings_to_local(source, 6.957008, 50.941066)
        origin = result["features"][0]["geometry"]["coordinates"][0][0]
        east = result["features"][0]["geometry"]["coordinates"][0][1]
        self.assertAlmostEqual(origin[0], 0.0)
        self.assertAlmostEqual(origin[1], 0.0)
        self.assertGreater(east[0], 7.0)
        self.assertAlmostEqual(east[1], 0.0, places=5)

    def test_building_height_precedence(self):
        self.assertEqual(building_heights({"height": "12.5 m", "building:levels": "9"}), (0.0, 12.5))
        self.assertEqual(building_heights({"building:levels": "3"}), (0.0, 9.0))
        self.assertEqual(building_heights({}), (0.0, 8.0))

    def test_stage1_quad_creates_roof_and_walls(self):
        document = {"features": [{"id": "way/1", "properties": {"building": "yes", "height": "10"}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 5], [0, 5], [0, 0]]]}}]}
        surfaces = prepare_surfaces(document, 1)
        self.assertEqual(len(surfaces), 5)
        roof = next(surface for surface in surfaces if surface.name.endswith(" - Roof"))
        self.assertEqual({point[2] for point in roof.points}, {10.0})

    def test_concave_building_is_split_into_stable_roof_triangles(self):
        ring = [(0, 0), (4, 0), (4, 4), (2, 2), (0, 4)]
        triangles = triangulate_ring(ring)
        self.assertEqual(len(triangles), 3)
        triangle_area = sum(abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(item, item[1:] + item[:1]))) / 2 for item in triangles)
        self.assertAlmostEqual(triangle_area, 12.0)
        document = {"features": [{"id": "way/2", "properties": {"building": "yes"}, "geometry": {"type": "Polygon", "coordinates": [[*ring, ring[0]]]}}]}
        surfaces = prepare_surfaces(document, 0)
        self.assertEqual(len(surfaces), 8)
        self.assertEqual(sum(surface.name.startswith("way/2 - Roof") for surface in surfaces), 3)

    def test_building_part_wins_over_coincident_outline(self):
        ring = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
        outline = {"id": "way/outline", "properties": {"building": "cathedral", "height": "157.38"}, "geometry": {"type": "Polygon", "coordinates": [ring]}}
        part = {"id": "way/part", "properties": {"building:part": "yes", "height": "15"}, "geometry": {"type": "Polygon", "coordinates": [[ring[2], ring[3], ring[4], ring[1], ring[2]]]}}
        selected = select_buildings({"features": [outline, part]}, 0)
        self.assertEqual([feature["id"] for feature in selected], ["way/part"])

    def test_ascii_elevation_grid_is_made_relative_to_center(self):
        grid = parse_ascii_grid("ncols 2\nnrows 2\nxllcorner 6.0\nyllcorner 50.0\ncellsize 0.001\nNODATA_value -9999\n12 13\n10 11\n")
        terrain = grid_to_terrain(grid, 6.0005, 50.0015, "test", "test")
        self.assertEqual(terrain["columns"], 2)
        self.assertEqual(terrain["rows"], 2)
        self.assertEqual(terrain["referenceElevation"], 12.0)
        self.assertEqual(terrain["samples"][0][0][2], 0.0)

    def test_invalid_elevation_response_has_readable_error(self):
        with self.assertRaisesRegex(ValueError, "no valid AAIGrid"):
            parse_ascii_grid('{"error":"invalid request"}')

    def test_terrain_is_bilinearly_sampled_and_split_into_triangles(self):
        terrain = {"provider": "test", "dataset": "test", "surfaceModel": "DTM", "columns": 2, "rows": 2, "referenceElevation": 100.0, "samples": [[[-1, 1, 1], [1, 1, 3]], [[-1, -1, -1], [1, -1, 1]]]}
        self.assertAlmostEqual(terrain_height(terrain, 0, 0), 1.0)
        surfaces = terrain_surfaces(terrain, 2, 2, 1)
        self.assertEqual(len(surfaces), 8)
        self.assertTrue(all(len(surface.points) == 3 for surface in surfaces))

    def test_heading_rotates_east_to_rdshape_forward(self):
        x, y, z = rotate_xy((10.0, 0.0, 2.0), 90.0)
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 10.0, places=6)
        self.assertEqual(z, 2.0)


if __name__ == "__main__":
    unittest.main()
