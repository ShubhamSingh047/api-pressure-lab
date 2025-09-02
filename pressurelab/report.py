"""Render pressure runner JSON as a standalone, escaped HTML report."""

import argparse
import html
import json


def _escape(value):
    return html.escape(str(value), quote=True)


def _number(value, digits=2):
    if value is None:
        return "—"
    return ("%%.%df" % digits) % value


def render_report(result):
    if result.get("schema_version") != 1:
        raise ValueError("unsupported schema_version; expected 1")
    required = (
        "started_at",
        "target",
        "environment",
        "thresholds",
        "stages",
        "highest_passing_rps",
        "stop_reason",
    )
    missing = [name for name in required if name not in result]
    if missing:
        raise ValueError("missing required fields: %s" % ", ".join(missing))

    rows = []
    for stage in result["stages"]:
        outcome = "Pass" if stage["passed"] else "Fail"
        row_class = "pass" if stage["passed"] else "fail"
        codes = ", ".join(
            "%s: %s" % (_escape(code), _escape(count))
            for code, count in sorted(stage["status_codes"].items())
        ) or "—"
        cells = [
            "%s" % stage["offered_rps"],
            _number(stage["achieved_rps"]),
            "%s / %s" % (stage["completed"], stage["scheduled"]),
            str(stage["failed"]),
            str(stage["dropped"]),
            "%.2f%%" % (stage["error_rate"] * 100),
            _number(stage["p50_ms"]),
            _number(stage["p95_ms"]),
            _number(stage["p99_ms"]),
            codes,
            outcome,
        ]
        rows.append(
            '<tr class="%s">%s</tr>'
            % (row_class, "".join("<td>%s</td>" % _escape(cell) for cell in cells))
        )
    environment = " · ".join(
        "%s: %s" % (_escape(key), _escape(value))
        for key, value in sorted(result["environment"].items())
    )
    thresholds = result["thresholds"]
    highest = result["highest_passing_rps"]
    highest_text = "No stage passed" if not highest else "%s req/s" % highest
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>API Pressure Lab report</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#151d33;--ink:#edf3ff;--muted:#a9b5ce;--cyan:#55d6be;--red:#ff7d8b;--line:#2b3858}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top right,#172a46,var(--bg) 44%%);color:var(--ink);font:15px/1.55 system-ui,sans-serif}
main{max-width:1180px;margin:auto;padding:54px 24px 72px}h1{font-size:clamp(2rem,5vw,4.1rem);line-height:1;margin:.2em 0}.eyebrow{color:var(--cyan);font-weight:750;letter-spacing:.12em;text-transform:uppercase}.lede,.meta{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:30px 0}.card,section{background:#151d33e8;border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 18px 55px #0004}.value{font-size:2rem;font-weight:800}.label{color:var(--muted)}section{margin-top:18px;overflow:auto}table{width:100%%;border-collapse:collapse;white-space:nowrap}th,td{text-align:right;padding:12px;border-bottom:1px solid var(--line)}th:first-child,td:first-child{text-align:left}.pass td:last-child{color:var(--cyan);font-weight:800}.fail td:last-child{color:var(--red);font-weight:800}code{color:var(--cyan)}
</style></head><body><main>
<div class="eyebrow">API Pressure Lab</div><h1>Capacity experiment report</h1>
<p class="lede">This is a workload-specific estimate from tested offered rates, not a universal maximum for the API.</p>
<div class="cards"><div class="card"><div class="label">Highest passing tested offered rate</div><div class="value">%s</div></div>
<div class="card"><div class="label">p95 threshold</div><div class="value">%s ms</div></div>
<div class="card"><div class="label">Maximum error rate</div><div class="value">%.2f%%</div></div></div>
<section><h2>Stages</h2><table><thead><tr><th>Offered req/s</th><th>Achieved req/s</th><th>Completed / scheduled</th><th>Failed</th><th>Dropped</th><th>Error rate</th><th>p50 ms</th><th>p95 ms</th><th>p99 ms</th><th>Status codes</th><th>Result</th></tr></thead><tbody>%s</tbody></table></section>
<section><h2>Methodology</h2><p>Each row is one paced stage. Passing requires a nonempty result set, no dropped arrivals, and latency and error rate within the configured thresholds. The runner stops at the first failing stage.</p><p><strong>Stop reason:</strong> %s</p></section>
<section><h2>Run context</h2><p><strong>Target:</strong> <code>%s</code></p><p><strong>Started:</strong> %s</p><p class="meta">%s</p></section>
</main></body></html>""" % (
        _escape(highest_text),
        _escape(thresholds["p95_ms"]),
        thresholds["max_error_rate"] * 100,
        "".join(rows),
        _escape(result["stop_reason"]),
        _escape(result["target"]),
        _escape(result["started_at"]),
        environment,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render pressure JSON as standalone HTML")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    with open(args.input, encoding="utf-8") as source:
        result = json.load(source)
    rendered = render_report(result)
    with open(args.output, "w", encoding="utf-8") as destination:
        destination.write(rendered)
    print("Wrote %s" % args.output)


if __name__ == "__main__":
    main()

# Reporting engine (2025-09-02 22:11:19): HTML dashboard metrics
