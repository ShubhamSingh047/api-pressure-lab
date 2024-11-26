import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from pressurelab.demo import DemoServer


def get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


class DemoServerTests(unittest.TestCase):
    def setUp(self):
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.close()

    def start(self, vulnerable=False, capacity=4, work_seconds=0.05):
        server = DemoServer(
            vulnerable=vulnerable,
            capacity=capacity,
            work_seconds=work_seconds,
        )
        server.start()
        self.servers.append(server)
        return server

    def test_safe_items_query_treats_injection_as_data(self):
        server = self.start()
        payload = "apple' OR 1=1 --"
        status, body = get_json(
            f"{server.url}/items?q={urllib.parse.quote(payload)}"
        )
        self.assertEqual(200, status)
        self.assertEqual([], body["items"])

    def test_vulnerable_mode_demonstrates_boolean_injection(self):
        server = self.start(vulnerable=True)
        payload = "apple' OR 1=1 --"
        status, body = get_json(
            f"{server.url}/items?q={urllib.parse.quote(payload)}"
        )
        self.assertEqual(200, status)
        self.assertEqual(["apple", "banana", "pear"], body["items"])

    def test_work_rejects_requests_over_capacity(self):
        server = self.start(capacity=1, work_seconds=0.2)
        gate = threading.Barrier(3)
        outcomes = []

        def request_work():
            gate.wait()
            outcomes.append(get_json(f"{server.url}/work")[0])

        threads = [threading.Thread(target=request_work) for _ in range(2)]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join()

        self.assertEqual([200, 503], sorted(outcomes))


if __name__ == "__main__":
    unittest.main()

# Demo server (2024-11-26 20:48:49): SQLite mock database helper
