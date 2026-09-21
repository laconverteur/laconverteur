import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "converter"))

from converteur import _open_sqlite_read_only


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


if __name__ == "__main__":
    unittest.main()
