import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUALITY_CHECK_PATH = ROOT / "tools" / "quality_check.py"


spec = importlib.util.spec_from_file_location("quality_check", QUALITY_CHECK_PATH)
quality_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality_check)


class QualityCheckTests(unittest.TestCase):
    def test_quality_check_suite_passes_for_repository(self):
        self.assertEqual(quality_check.main(), 0)

    def test_runtime_config_schema_check_detects_drift(self):
        errors = []
        original = quality_check._config_field_keys
        try:
            quality_check._config_field_keys = lambda: {"history_limit"}
            quality_check.check_runtime_config_schema(errors)
        finally:
            quality_check._config_field_keys = original

        self.assertTrue(any("missing defaults" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
