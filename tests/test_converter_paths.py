import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "converter"))

from converteur import (ARRAYCALC_FREEFORM_PLANARITY_TOLERANCE, Surface, _arraycalc_surfaces,
                        _open_sqlite_read_only, prepare_freeform_surfaces)


class ConverterPathTests(unittest.TestCase):
    def test_arraycalc_unc_path_is_not_converted_to_sqlite_uri(self):
        path = Path(r"\\Mac\ArrayCalc\Frankfurt db1.dbpr")
        connection = MagicMock()
        with patch.object(Path, "is_file", return_value=True), patch(
            "converteur.sqlite3.connect", return_value=connection
        ) as connect:
            result = _open_sqlite_read_only(path)

        self.assertIs(result, connection)
        opened_path = connect.call_args.args[0]
        self.assertFalse(opened_path.startswith("file:"))
        self.assertIn("Mac", opened_path)
        connect.assert_called_once_with(opened_path)
        connection.execute.assert_called_once_with("PRAGMA query_only=ON")

    def test_non_planar_polygon_is_split_into_planar_freeforms(self):
        surface = Surface("ramp", [(0, 0, 0), (2, 0, 0), (2, 2, 1), (0, 2, 0)], 1.2, True)
        result, skipped = prepare_freeform_surfaces([surface])
        self.assertEqual(skipped, [])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(item.native_kind == "freeform" for item in result))
        self.assertTrue(all(len(item.points) == 3 for item in result))
        self.assertTrue(all(item.audience_height == 1.2 for item in result))

    def test_self_intersecting_polygon_is_omitted(self):
        surface = Surface("bow tie", [(0, 0, 0), (2, 2, 0), (0, 2, 0), (2, 0, 0)], None, True)
        result, skipped = prepare_freeform_surfaces([surface])
        self.assertEqual(result, [])
        self.assertEqual(skipped, ["bow tie"])

    def test_concave_planar_polygon_stays_one_freeform(self):
        surface = Surface(
            "concave", [(0, 0, 0), (2, 0, 0), (2, 2, 0), (1, 1, 0), (0, 2, 0)], None, True,
        )
        result, skipped = prepare_freeform_surfaces([surface])
        self.assertEqual(skipped, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].points, surface.points)
        self.assertEqual(result[0].native_kind, "freeform")

    def test_arraycalc_curved_strip_keeps_planar_quads(self):
        record = {
            "id": 1, "name": "Curve", "shape": 2, "plane_type": 1, "enabled": True,
            "transparent": False, "listener_height": 1.2, "color": 0xFFFFFFFF,
            "origin": (0, 0, 0), "rotation": (0, 0, 0), "scale": (1, 1, 1),
            "parent": 0, "points": [],
            "curve": {"start_angle": 0, "span_angle": 12, "inner_z": 0, "outer_z": 3,
                      "inner_a": 10, "outer_a": 15, "inner_b": 10, "outer_b": 15,
                      "inner_n": 2, "outer_n": 2},
        }
        result = _arraycalc_surfaces([record])
        self.assertEqual([surface.name for surface in result], ["Curve 1", "Curve 2"])
        self.assertTrue(all(len(surface.points) == 4 for surface in result))

    def test_arraycalc_angles_are_polar_not_ellipse_parameters(self):
        record = {
            "id": 1, "name": "Ellipse", "shape": 2, "plane_type": 1, "enabled": True,
            "transparent": False, "listener_height": 1.2, "color": 0xFFFFFFFF,
            "origin": (0, 0, 0), "rotation": (0, 0, 0), "scale": (1, 1, 1),
            "parent": 0, "points": [],
            "curve": {"start_angle": 45, "span_angle": 1, "inner_z": 0, "outer_z": 1,
                      "inner_a": 10, "outer_a": 20, "inner_b": 5, "outer_b": 10,
                      "inner_n": 2, "outer_n": 2},
        }
        point = _arraycalc_surfaces([record])[0].points[0]
        self.assertAlmostEqual(point[0], point[1], places=9)

    def test_arraycalc_twisted_strip_is_adaptively_refined(self):
        record = {
            "id": 1, "name": "Twisted", "shape": 2, "plane_type": 1, "enabled": True,
            "transparent": False, "listener_height": 1.7, "color": 0xFFFFFFFF,
            "origin": (0, 0, 0), "rotation": (0, 0, 90), "scale": (1, 1, 1),
            "parent": 0, "points": [],
            "curve": {"start_angle": 54.2869730763, "span_angle": 7.1426053847,
                      "inner_z": 18.11, "outer_z": 31.17,
                      "inner_a": 100, "outer_a": 200,
                      "inner_b": 50, "outer_b": 200,
                      "inner_n": 2, "outer_n": 2},
        }
        result = _arraycalc_surfaces([record])
        self.assertGreater(len(result), 1)
        self.assertTrue(all(len(surface.points) == 4 for surface in result))
        self.assertTrue(all(surface.name.startswith("Twisted 1.") for surface in result))
        for surface in result:
            normal = __import__("converteur").polygon_normal(surface.points)
            deviation = max(abs(sum(normal[i] * (point[i] - surface.points[0][i]) for i in range(3)))
                            for point in surface.points[1:])
            self.assertLessEqual(deviation, ARRAYCALC_FREEFORM_PLANARITY_TOLERANCE + 1e-9)


if __name__ == "__main__":
    unittest.main()
