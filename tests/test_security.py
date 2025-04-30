import http.server
import math
import json
import os
import tempfile
import threading
import unittest

from pressurelab.security import ProbeConfig, main, run_check, sanitize_target
from pressurelab.demo import DemoServer


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    mode = "safe"
    count = 0

    def do_GET(self):
        type(self).count += 1
        if self.mode == "redirect":
            if self.path.startswith("/landing"):
                body = b"redirect was followed"
                self.send_response(200)
            else:
                body = b""
                self.send_response(302)
                self.send_header("Location", "/landing")
        elif self.mode == "volatile":
            body = f"nonce-{self.count}".encode()
            self.send_response(200)
        elif self.mode == "large":
            body = b"x" * 100
            self.send_response(200)
        elif self.mode == "error" and "%27" in self.path and "AND" not in self.path:
            body = b"sqlite syntax error near quote"
            self.send_response(500)
        elif self.mode == "generic500" and "%27" in self.path and "AND" not in self.path:
            body = b"service unavailable"
            self.send_response(500)
        elif self.mode == "existing_sql":
            body = b"sql syntax error shown on every response"
            self.send_response(200)
        elif self.mode == "incomplete":
            body = b"short"
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return
        elif self.mode == "empty":
            body = b'{"items":[]}'
            self.send_response(200)
        else:
            body = b'{"items":["apple"]}'
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class LocalServer:
    def __init__(self, mode):
        handler = type("Handler", (FixtureHandler,), {"mode": mode, "count": 0})
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_port}/items?token=secret"

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()


class SecurityTests(unittest.TestCase):
    def config(self, url):
        return ProbeConfig(url=url, parameter="q", value="apple", repeats=2, timeout=1)

    def test_sanitized_target_excludes_query_and_credentials(self):
        self.assertEqual(
            "https://example.test/items",
            sanitize_target("https://user:pass@example.test/items?token=secret"),
        )

    def test_safe_stable_endpoint_has_no_finding(self):
        with LocalServer("safe") as url:
            result = run_check(self.config(url))
        self.assertEqual("no_finding", result["assessment"])
        self.assertEqual([], result["findings"])
        self.assertNotIn("secret", json.dumps(result))

    def test_stable_empty_responses_do_not_create_a_false_positive(self):
        with LocalServer("empty") as url:
            result = run_check(self.config(url))
        self.assertEqual("no_finding", result["assessment"])
        self.assertEqual([], result["findings"])

    def test_error_response_is_only_a_potential_finding(self):
        with LocalServer("error") as url:
            result = run_check(self.config(url))
        self.assertEqual("potential", result["assessment"])
        self.assertTrue(any(item["kind"] == "error_signal" for item in result["findings"]))
        self.assertTrue(all(item["confidence"] != "proof" for item in result["findings"]))

    def test_generic_server_error_is_not_described_as_database_error(self):
        with LocalServer("generic500") as url:
            result = run_check(self.config(url))
        self.assertEqual("potential", result["assessment"])
        self.assertEqual("application_error", result["findings"][0]["kind"])

    def test_preexisting_sql_text_is_not_a_new_error_signal(self):
        with LocalServer("existing_sql") as url:
            result = run_check(self.config(url))
        self.assertEqual("no_finding", result["assessment"])
        self.assertEqual([], result["findings"])

    def test_volatile_baseline_is_inconclusive(self):
        with LocalServer("volatile") as url:
            result = run_check(self.config(url))
        self.assertEqual("inconclusive", result["assessment"])

    def test_network_error_is_reported_without_crashing(self):
        result = run_check(self.config("http://127.0.0.1:1/items"))
        self.assertEqual("inconclusive", result["assessment"])
        self.assertGreater(len(result["errors"]), 0)

    def test_body_limit_makes_result_inconclusive(self):
        with LocalServer("large") as url:
            config = ProbeConfig(
                url=url, parameter="q", value="apple", repeats=2, timeout=1, body_limit=16
            )
            result = run_check(config)
        self.assertEqual("inconclusive", result["assessment"])
        self.assertIn("ResponseTooLarge", result["errors"])

    def test_redirects_are_recorded_and_not_followed(self):
        with LocalServer("redirect") as url:
            result = run_check(self.config(url))
        statuses = [
            sample["status"]
            for group in result["probes"].values()
            for sample in group
        ]
        self.assertTrue(statuses)
        self.assertEqual({302}, set(statuses))
        self.assertEqual("inconclusive", result["assessment"])

    def test_incomplete_response_is_reported_without_crashing(self):
        with LocalServer("incomplete") as url:
            result = run_check(self.config(url))
        self.assertEqual("inconclusive", result["assessment"])
        self.assertIn("IncompleteRead", result["errors"])

    def test_configuration_is_bounded_and_rejects_credentials(self):
        invalid = [
            ProbeConfig("http://127.0.0.1/items", "q", "x", repeats=1),
            ProbeConfig("http://127.0.0.1/items", "q", "x", repeats=11),
            ProbeConfig("http://127.0.0.1/items", "q", "x", timeout=math.inf),
            ProbeConfig("http://user:pass@127.0.0.1/items", "q", "x"),
            ProbeConfig("http://127.0.0.1:99999/items", "q", "x"),
        ]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                run_check(config)

    def test_ipv6_target_is_sanitized_with_brackets(self):
        self.assertEqual(
            "http://[::1]:8080/items",
            sanitize_target("http://user:pass@[::1]:8080/items?token=secret"),
        )

    def test_demo_safe_mode_is_negative_and_vulnerable_mode_is_potential(self):
        safe = DemoServer().start()
        vulnerable = DemoServer(vulnerable=True).start()
        try:
            safe_result = run_check(self.config(safe.url + "/items"))
            vulnerable_result = run_check(self.config(vulnerable.url + "/items"))
        finally:
            safe.close()
            vulnerable.close()
        self.assertEqual("no_finding", safe_result["assessment"])
        self.assertEqual("potential", vulnerable_result["assessment"])
        self.assertTrue(
            any(
                finding["kind"] == "boolean_difference"
                for finding in vulnerable_result["findings"]
            )
        )

    def test_cli_allows_loopback_without_authorized_flag(self):
        server = DemoServer().start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = os.path.join(directory, "security.json")
                main(
                    [
                        "--url", server.url + "/items", "--param", "q",
                        "--value", "apple", "--output", output, "--repeats", "2",
                    ]
                )
                with open(output, encoding="utf-8") as source:
                    result = json.load(source)
        finally:
            server.close()
        self.assertEqual("no_finding", result["assessment"])

    def test_cli_requires_authorization_for_non_loopback(self):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "security.json")
            with self.assertRaises(SystemExit):
                main(
                    [
                        "--url", "https://example.test/items", "--param", "q",
                        "--value", "apple", "--output", output,
                    ]
                )
            self.assertFalse(os.path.exists(output))


if __name__ == "__main__":
    unittest.main()

# Security probe (2025-04-30 22:29:14): SQLi detection heuristics
