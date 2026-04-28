import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import oura_health as oh


class AnalysisTests(unittest.TestCase):
    def test_store_roundtrip_and_analysis(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "oura.sqlite3")
            store = oh.OuraStore(db)
            try:
                now = "2026-04-28T12:00:00+00:00"
                store.upsert_documents("daily_sleep", [{"id": "sleep-score-1", "day": oh.today().isoformat(), "score": 72, "contributors": {"total_sleep": 70}}], now)
                store.upsert_documents("daily_readiness", [{"id": "ready-1", "day": oh.today().isoformat(), "score": 68, "contributors": {"hrv_balance": 75}, "temperature_deviation": 0.1}], now)
                store.upsert_documents("daily_activity", [{"id": "activity-1", "day": oh.today().isoformat(), "score": 80, "steps": 6000, "active_calories": 400}], now)
                store.upsert_documents("daily_stress", [{"id": "stress-1", "day": oh.today().isoformat(), "day_summary": "normal", "stress_high": 3600, "recovery_high": 1800}], now)
                store.upsert_documents("sleep", [{"id": "sleep-1", "day": oh.today().isoformat(), "type": "long_sleep", "total_sleep_duration": 7 * 3600, "time_in_bed": 8 * 3600, "efficiency": 88, "average_hrv": 50, "lowest_heart_rate": 52}], now)
                store.upsert_documents("ring_battery_level", [{"timestamp": now, "timestamp_unix": 1, "level": 90}], now)
                bundle = store.load_bundle(14)
                report = oh.build_adaptive_analysis(bundle, days=14)
                self.assertIn("oura adaptive analysis", report)
                self.assertIn("confidence: high", report)
                self.assertIn("battery 90%", report)
                self.assertIn("sleep:", report)
            finally:
                store.close()

    def test_percentile_ordinal(self):
        self.assertEqual(oh.fmt_percentile(93), "top-ish (93rd percentile)")
        self.assertEqual(oh.fmt_percentile(11), "11th percentile")


if __name__ == "__main__":
    unittest.main()
