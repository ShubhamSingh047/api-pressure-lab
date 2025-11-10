"""Reproduce a local capacity experiment and safe/vulnerable SQLi controls."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pressurelab.demo import DemoServer
from pressurelab.report import render_report
from pressurelab.security import ProbeConfig, run_check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default="bin/pressure")
    parser.add_argument("--output-dir", default="examples")
    args = parser.parse_args()
    destination = Path(args.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    demo = DemoServer(capacity=2, work_seconds=0.1).start()
    try:
        run = subprocess.run(
            [str(Path(args.binary).resolve()), "--url", demo.url + "/work",
             "--rates", "5,10,30", "--duration", "2s", "--workers", "16",
             "--p95", "500ms", "--max-error-rate", "0.01"],
            capture_output=True, text=True, timeout=30,
        )
        if run.returncode not in (0, 2):
            raise RuntimeError(run.stderr)
        result = json.loads(run.stdout)
        if result["highest_passing_rps"] != 10 or result["stop_reason"] != "threshold_exceeded":
            raise RuntimeError("Demo did not produce the expected capacity boundary; inspect generator scheduling: " + run.stdout)
        safe = run_check(ProbeConfig(demo.url + "/items", "q", "apple"))
        if safe["assessment"] != "no_finding":
            raise RuntimeError("Safe query control failed: " + json.dumps(safe))
    finally:
        demo.close()
    vulnerable = DemoServer(vulnerable=True).start()
    try:
        unsafe = run_check(ProbeConfig(vulnerable.url + "/items", "q", "apple"))
        if unsafe["assessment"] != "potential":
            raise RuntimeError("Vulnerable query control failed: " + json.dumps(unsafe))
    finally:
        vulnerable.close()
    for filename, data in (("capacity.json", result), ("security-safe.json", safe), ("security-vulnerable.json", unsafe)):
        (destination / filename).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (destination / "capacity.html").write_text(render_report(result), encoding="utf-8")
    print("Local demo verified: 10 offered req/s passed; 30 failed; safe SQL control negative; vulnerable control flagged.")
    print("These are synthetic local demo measurements, not production capacity claims.")


if __name__ == "__main__":
    main()

# Reporting engine (2025-11-10 20:27:32): HTML dashboard metrics
