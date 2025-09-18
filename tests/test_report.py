import html
import unittest

from pressurelab.report import render_report


class ReportTests(unittest.TestCase):
    def test_renders_contract_and_escapes_untrusted_values(self):
        result = {
            "schema_version": 1,
            "started_at": "2026-09-21T00:00:00Z",
            "target": "https://example.test/<script>alert(1)</script>",
            "environment": {"os": "darwin", "arch": "arm64", "cpus": 8},
            "thresholds": {"p95_ms": 200, "max_error_rate": 0.01},
            "stages": [{
                "offered_rps": 10, "duration_seconds": 5, "elapsed_seconds": 5.1,
                "scheduled": 50, "completed": 50, "failed": 0, "dropped": 0,
                "achieved_rps": 9.8, "error_rate": 0, "p50_ms": 12,
                "p95_ms": 20, "p99_ms": 25, "passed": True,
                "status_codes": {"200": 50},
            }],
            "highest_passing_rps": 10,
            "stop_reason": "all stages completed <ok>",
        }
        output = render_report(result)
        self.assertIn("Highest passing tested offered rate", output)
        self.assertIn("workload-specific estimate", output)
        self.assertIn("10 req/s", output)
        self.assertIn("9.80", output)
        self.assertIn("1.00%", output)
        self.assertNotIn("1.00%%", output)
        self.assertIn(html.escape(result["target"]), output)
        self.assertNotIn("<script>alert(1)</script>", output)
        self.assertNotIn("<ok>", output)

    def test_rejects_unknown_schema(self):
        with self.assertRaisesRegex(ValueError, "schema_version"):
            render_report({"schema_version": 2})

    def test_zero_highest_rate_reports_no_passing_stage(self):
        result = {
            "schema_version": 1, "started_at": "now", "target": "http://local/work",
            "environment": {}, "thresholds": {"p95_ms": 10, "max_error_rate": 0},
            "stages": [], "highest_passing_rps": 0, "stop_reason": "first stage failed",
        }
        output = render_report(result)
        self.assertIn("No stage passed", output)
        self.assertNotIn(">0 req/s<", output)


if __name__ == "__main__":
    unittest.main()
