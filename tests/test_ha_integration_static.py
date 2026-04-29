import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "oura_openclaw"


class HomeAssistantIntegrationStaticTests(unittest.TestCase):
    def test_hacs_manifest_shape(self):
        manifest = json.loads(
            (INTEGRATION / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["domain"], "oura_openclaw")
        for key in ("documentation", "issue_tracker", "codeowners", "name", "version"):
            self.assertIn(key, manifest)
        self.assertTrue(manifest["config_flow"])
        self.assertTrue(manifest["single_config_entry"])

        hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs["name"], "Oura OpenClaw")

    def test_hacs_repository_layout(self):
        integrations = [
            path for path in (ROOT / "custom_components").iterdir() if path.is_dir()
        ]
        self.assertEqual([path.name for path in integrations], ["oura_openclaw"])
        for filename in (
            "__init__.py",
            "api.py",
            "config_flow.py",
            "coordinator.py",
            "diagnostics.py",
            "manifest.json",
            "sensor.py",
            "services.yaml",
            "strings.json",
        ):
            self.assertTrue((INTEGRATION / filename).exists(), filename)

    def test_brand_assets_exist(self):
        for filename, signature in (
            ("icon.png", b"\x89PNG\r\n\x1a\n"),
            ("logo.png", b"\x89PNG\r\n\x1a\n"),
        ):
            path = INTEGRATION / "brand" / filename
            self.assertTrue(path.exists(), filename)
            self.assertEqual(path.read_bytes()[:8], signature)

    def test_sensor_translations_cover_descriptions(self):
        sensor_py = (INTEGRATION / "sensor.py").read_text(encoding="utf-8")
        strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
        translated = set(strings["entity"]["sensor"])
        keys = set(re.findall(r'translation_key="([^"]+)"', sensor_py))
        self.assertEqual(keys - translated, set())


if __name__ == "__main__":
    unittest.main()
