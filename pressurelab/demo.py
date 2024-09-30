"""A deliberately small local API for capacity and SQLi experiments."""

import argparse
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class _DemoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, vulnerable, capacity, work_seconds):
        super().__init__(address, _DemoHandler)
        self.vulnerable = vulnerable
        self.capacity = threading.BoundedSemaphore(capacity)
        self.work_seconds = work_seconds
        self.database_lock = threading.Lock()
        self.database = sqlite3.connect(":memory:", check_same_thread=False)
        self.database.execute("CREATE TABLE items (name TEXT NOT NULL)")
        self.database.executemany(
            "INSERT INTO items(name) VALUES (?)",
            [("apple",), ("banana",), ("pear",)],
        )
        self.database.commit()


class _DemoHandler(BaseHTTPRequestHandler):
    server_version = "PressureLabDemo/1"

    def _send(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        request = urlsplit(self.path)
        if request.path == "/work":
            self._work()
        elif request.path == "/items":
            self._items(parse_qs(request.query).get("q", [""])[0])
        else:
            self._send(404, {"error": "not found"})

    def _work(self):
        if not self.server.capacity.acquire(blocking=False):
            self._send(503, {"error": "demo worker capacity exhausted"})
            return
        try:
            time.sleep(self.server.work_seconds)
            self._send(200, {"status": "ok"})
        finally:
            self.server.capacity.release()

    def _items(self, query):
        try:
            with self.server.database_lock:
                if self.server.vulnerable:
                    # Intentionally unsafe and available only behind --vulnerable.
                    sql = "SELECT name FROM items WHERE name = '%s'" % query
                    rows = self.server.database.execute(sql).fetchall()
                else:
                    rows = self.server.database.execute(
                        "SELECT name FROM items WHERE name = ?", (query,)
                    ).fetchall()
            self._send(200, {"items": [row[0] for row in rows]})
        except sqlite3.Error as error:
            self._send(500, {"error": "database query rejected", "detail": str(error)})

    def log_message(self, format, *args):
        pass


class DemoServer:
    """Lifecycle wrapper used by the CLI and integration tests."""

    def __init__(
        self, vulnerable=False, capacity=4, work_seconds=0.05, host="127.0.0.1", port=0
    ):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        if work_seconds < 0:
            raise ValueError("work_seconds cannot be negative")
        self._httpd = _DemoHTTPServer(
            (host, port), vulnerable, capacity, work_seconds
        )
        self._thread = None

    @property
    def url(self):
        host, port = self._httpd.server_address[:2]
        return "http://%s:%d" % (host, port)

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
        return self

    def close(self):
        if self._thread is not None:
            self._httpd.shutdown()
            self._thread.join()
            self._thread = None
        self._httpd.server_close()
        self._httpd.database.close()

    def serve_forever(self):
        try:
            self._httpd.serve_forever()
        finally:
            self._httpd.server_close()
            self._httpd.database.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the local API Pressure Lab demo")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--capacity", type=int, default=4)
    parser.add_argument("--work-ms", type=float, default=50)
    parser.add_argument(
        "--vulnerable",
        action="store_true",
        help="enable intentionally vulnerable SQL for local demonstrations",
    )
    args = parser.parse_args(argv)
    server = DemoServer(
        vulnerable=args.vulnerable,
        capacity=args.capacity,
        work_seconds=args.work_ms / 1000,
        port=args.port,
    )
    mode = "VULNERABLE demo" if args.vulnerable else "safe"
    print("API Pressure Lab demo (%s) listening at %s" % (mode, server.url), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

# Demo server (2024-09-30 19:26:23): SQLite mock database helper
