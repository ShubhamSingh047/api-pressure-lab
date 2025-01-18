"""Bounded SQL-injection regression probes for explicitly authorized targets."""

import argparse
import hashlib
import http.client
import ipaddress
import json
import math
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone


MAX_BODY_BYTES = 64 * 1024
ERROR_PATTERN = re.compile(
    rb"(?:sql(?:ite)?|syntax|database).{0,80}(?:error|exception|near|query)|"
    rb"(?:error|exception).{0,80}(?:sql(?:ite)?|database)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProbeConfig:
    url: str
    parameter: str
    value: str
    repeats: int = 3
    timeout: float = 3.0
    body_limit: int = MAX_BODY_BYTES


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sanitize_target(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host:
        host = "[%s]" % host
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host += ":%d" % port
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))


def _is_loopback_url(url):
    hostname = urllib.parse.urlsplit(url).hostname
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname or "").is_loopback
    except ValueError:
        return False


def _probe_url(config, value):
    parsed = urllib.parse.urlsplit(config.url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    pairs = [(key, old) for key, old in pairs if key != config.parameter]
    pairs.append((config.parameter, value))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(pairs), "")
    )


def _fetch(opener, config, value):
    request = urllib.request.Request(
        _probe_url(config, value),
        headers={"Accept": "application/json,text/plain;q=0.8,*/*;q=0.1"},
        method="GET",
    )
    status = None
    response = None
    try:
        response = opener.open(request, timeout=config.timeout)
        status = response.status
    except urllib.error.HTTPError as error:
        response = error
        status = error.code
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
        return {"error": type(error).__name__}
    try:
        body = response.read(config.body_limit + 1)
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                expected_length = int(content_length)
            except ValueError:
                expected_length = None
            if (
                expected_length is not None
                and expected_length <= config.body_limit
                and len(body) != expected_length
            ):
                return {"error": "IncompleteRead", "status": status}
    except (http.client.IncompleteRead, socket.timeout, TimeoutError, OSError) as error:
        return {"error": type(error).__name__, "status": status}
    finally:
        if response is not None:
            response.close()
    if len(body) > config.body_limit:
        return {"error": "ResponseTooLarge", "status": status}
    return {
        "status": status,
        "length": len(body),
        "digest": hashlib.sha256(body).hexdigest(),
        "error_signal": bool(ERROR_PATTERN.search(body)),
    }


def _stable(samples):
    return bool(samples) and not any("error" in item for item in samples) and all(
        item == samples[0] for item in samples[1:]
    )


def _public_sample(sample):
    return {
        key: sample[key]
        for key in ("status", "length", "digest", "error_signal", "error")
        if key in sample
    }


def run_check(config):
    if not 2 <= config.repeats <= 10:
        raise ValueError("repeats must be between 2 and 10")
    parsed = urllib.parse.urlsplit(config.url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("url must be an absolute http or https URL")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("url has an invalid port") from error
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url must not contain credentials")
    if not config.parameter:
        raise ValueError("parameter cannot be empty")
    if not math.isfinite(config.timeout) or not 0 < config.timeout <= 30:
        raise ValueError("timeout must be finite and no more than 30 seconds")
    if config.body_limit < 1 or config.body_limit > MAX_BODY_BYTES:
        raise ValueError("body_limit must be between 1 and %d" % MAX_BODY_BYTES)

    opener = urllib.request.build_opener(_NoRedirect)
    values = {
        "baseline": config.value,
        "control": config.value + "-pressurelab-control",
        "boolean_true": config.value + "' AND '1'='1",
        "boolean_false": config.value + "' AND '1'='2",
        "error": config.value + "'",
    }
    samples = {}
    for name, value in values.items():
        count = config.repeats if name != "error" else 1
        samples[name] = [_fetch(opener, config, value) for _ in range(count)]

    errors = sorted(
        {item["error"] for group in samples.values() for item in group if "error" in item}
    )
    findings = []
    stable_names = ("baseline", "control", "boolean_true", "boolean_false")
    stable = all(_stable(samples[name]) for name in stable_names)
    comparable = stable and all(
        200 <= samples[name][0].get("status", 0) < 300 for name in stable_names
    )
    if not errors and comparable:
        baseline = samples["baseline"][0]
        control = samples["control"][0]
        true_probe = samples["boolean_true"][0]
        false_probe = samples["boolean_false"][0]
        error_probe = samples["error"][0]
        if (
            baseline != control
            and true_probe == baseline
            and false_probe == control
        ):
            findings.append(
                {
                    "kind": "boolean_difference",
                    "confidence": "potential",
                    "detail": "Repeated true/false controls produced a stable response difference.",
                }
            )
        if error_probe.get("error_signal") and not baseline.get("error_signal"):
            findings.append(
                {
                    "kind": "error_signal",
                    "confidence": "potential",
                    "detail": "A quote probe produced a database-like error response.",
                }
            )
        elif error_probe.get("status", 0) >= 500 and baseline.get("status", 0) < 500:
            findings.append(
                {
                    "kind": "application_error",
                    "confidence": "potential",
                    "detail": "A quote probe produced a new server error without a database-specific signal.",
                }
            )

    if errors or not comparable:
        assessment = "inconclusive"
    elif findings:
        assessment = "potential"
    else:
        assessment = "no_finding"
    return {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "target": sanitize_target(config.url),
        "parameter": config.parameter,
        "assessment": assessment,
        "proof": False,
        "notice": "Signals are regression hints, not proof. No data extraction was attempted.",
        "findings": findings,
        "errors": errors,
        "probes": {
            name: [_public_sample(item) for item in group]
            for name, group in samples.items()
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run bounded, read-only SQLi regression probes"
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--param", required=True)
    parser.add_argument("--value", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--authorized",
        action="store_true",
        help="confirm you own or are authorized to test the target",
    )
    args = parser.parse_args(argv)
    if not args.authorized and not _is_loopback_url(args.url):
        parser.error(
            "--authorized is required for non-loopback targets; only test systems "
            "you own or may assess"
        )
    result = run_check(
        ProbeConfig(
            url=args.url,
            parameter=args.param,
            value=args.value,
            repeats=args.repeats,
            timeout=args.timeout,
        )
    )
    with open(args.output, "w", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    print("%s: %s" % (result["target"], result["assessment"]))


if __name__ == "__main__":
    main()

# Security probe (2025-01-18 19:52:41): SQLi detection heuristics
