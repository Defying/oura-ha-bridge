import argparse
import contextlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
                store.upsert_documents(
                    "daily_sleep",
                    [
                        {
                            "id": "sleep-score-1",
                            "day": oh.today().isoformat(),
                            "score": 72,
                            "contributors": {"total_sleep": 70},
                        }
                    ],
                    now,
                )
                store.upsert_documents(
                    "daily_readiness",
                    [
                        {
                            "id": "ready-1",
                            "day": oh.today().isoformat(),
                            "score": 68,
                            "contributors": {"hrv_balance": 75},
                            "temperature_deviation": 0.1,
                        }
                    ],
                    now,
                )
                store.upsert_documents(
                    "daily_activity",
                    [
                        {
                            "id": "activity-1",
                            "day": oh.today().isoformat(),
                            "score": 80,
                            "steps": 6000,
                            "active_calories": 400,
                        }
                    ],
                    now,
                )
                store.upsert_documents(
                    "daily_stress",
                    [
                        {
                            "id": "stress-1",
                            "day": oh.today().isoformat(),
                            "day_summary": "normal",
                            "stress_high": 3600,
                            "recovery_high": 1800,
                        }
                    ],
                    now,
                )
                store.upsert_documents(
                    "sleep",
                    [
                        {
                            "id": "sleep-1",
                            "day": oh.today().isoformat(),
                            "type": "long_sleep",
                            "total_sleep_duration": 7 * 3600,
                            "time_in_bed": 8 * 3600,
                            "efficiency": 88,
                            "average_hrv": 50,
                            "lowest_heart_rate": 52,
                        }
                    ],
                    now,
                )
                store.upsert_documents(
                    "ring_battery_level",
                    [{"timestamp": now, "timestamp_unix": 1, "level": 90}],
                    now,
                )
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

    def test_analyze_no_sync_does_not_require_token(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(
                oh,
                "get_token",
                side_effect=AssertionError("token lookup should not run"),
            ),
        ):
            args = argparse.Namespace(
                db=str(Path(td) / "oura.sqlite3"),
                days=14,
                json=False,
                max_pages=1,
                no_timeseries=True,
                quiet_if_missing_token=False,
                sync=False,
                timeout=1,
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(oh.cmd_analyze(args), 0)
            self.assertIn("oura adaptive analysis", stdout.getvalue())

    def test_analyze_quiet_missing_token_stays_quiet(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(oh, "get_token", return_value=None),
        ):
            args = argparse.Namespace(
                db=str(Path(td) / "oura.sqlite3"),
                days=14,
                json=False,
                max_pages=1,
                no_timeseries=True,
                quiet_if_missing_token=True,
                sync=True,
                timeout=1,
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(oh.cmd_analyze(args), 0)
            self.assertEqual(stdout.getvalue(), "")

    def test_setup_token_does_not_pass_token_in_argv(self):
        sample_value = "placeholder-value"
        completed = oh.subprocess.CompletedProcess(
            args=["security"], returncode=0, stdout="", stderr=""
        )
        with (
            patch("getpass.getpass", side_effect=[sample_value, sample_value]),
            patch.object(oh, "run_security", return_value=completed) as run_security,
        ):
            args = argparse.Namespace(service="svc", account="acct")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(oh.setup_token(args), 0)
        security_args = run_security.call_args.args[0]
        self.assertNotIn(sample_value, security_args)
        self.assertEqual(security_args[-1], "-w")
        self.assertEqual(
            run_security.call_args.kwargs["input_text"], sample_value + "\n"
        )

    def test_token_status_reports_unreadable_keychain_item(self):
        denied = oh.subprocess.CompletedProcess(
            args=["security"], returncode=36, stdout="", stderr=""
        )
        metadata = oh.subprocess.CompletedProcess(
            args=["security"], returncode=0, stdout="metadata", stderr=""
        )
        with patch.object(oh, "run_security", side_effect=[denied, metadata]):
            args = argparse.Namespace(service="svc", account="acct")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(oh.token_status(args), 1)
        self.assertIn("item exists but password is not readable", stdout.getvalue())

    def test_required_token_reports_unreadable_keychain_item(self):
        denied = oh.subprocess.CompletedProcess(
            args=["security"], returncode=36, stdout="", stderr=""
        )
        metadata = oh.subprocess.CompletedProcess(
            args=["security"], returncode=0, stdout="metadata", stderr=""
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(oh, "run_security", side_effect=[denied, metadata]),
            self.assertRaisesRegex(oh.MissingToken, "exists in macOS Keychain"),
        ):
            oh.get_token(required=True)

    def test_sqlite_file_is_private_on_posix(self):
        if os.name != "posix":
            self.skipTest("POSIX file modes only")
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "oura.sqlite3")
            store = oh.OuraStore(db)
            store.close()
            mode = stat.S_IMODE(os.stat(db).st_mode)
            self.assertEqual(mode & 0o077, 0)

    def test_client_refuses_invalid_limits_and_partial_pagination(self):
        with self.assertRaises(ValueError):
            oh.OuraClient("placeholder", timeout=0)
        with self.assertRaises(ValueError):
            oh.OuraClient("placeholder", max_pages=0)

        class PagingClient(oh.OuraClient):
            def get(self, path, params=None):
                payload = {"data": [{"id": "doc"}]}
                payload["next_" + "token"] = "more"
                return payload

        with self.assertRaisesRegex(oh.OuraError, "pagination exceeded"):
            PagingClient("placeholder", max_pages=1).list_documents("/v2/test", {})

    def test_digest_tolerates_string_battery_timestamps(self):
        day = oh.today().isoformat()
        bundle = {
            "range": {"start_date": day, "end_date": day},
            "daily_readiness": [{"day": day, "score": 80}],
            "ring_battery_level": [
                {"timestamp_unix": 1, "level": 80},
                {"timestamp_unix": "2", "level": 50},
                "ignored",
            ],
        }
        report = oh.build_digest(bundle, days=1)
        self.assertIn("ring battery 50%", report)


if __name__ == "__main__":
    unittest.main()
