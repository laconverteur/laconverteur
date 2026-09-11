import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geo.geo_phase0 import buildings_to_local, bounds_for_metric_extent, global_pixel, local_from_lon_lat, overpass_query, way_polygon


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


if __name__ == "__main__":
    unittest.main()
