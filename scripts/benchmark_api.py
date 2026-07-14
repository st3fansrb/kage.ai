#!/usr/bin/env python3
"""Măsoară p50/p95 pentru endpoint-uri Kage fără dependențe suplimentare.

Exemplu: python scripts/benchmark_api.py --base-url http://localhost:4001
"""
import argparse
import statistics
import time
import urllib.request


def _request(url, token):
    headers = {"Authorization": "Bearer " + token} if token else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("%s returned %s" % (url, response.status))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:4001")
    parser.add_argument("--token", default="")
    parser.add_argument("--requests", type=int, default=100)
    args = parser.parse_args()
    if args.requests < 20:
        parser.error("--requests must be at least 20")

    paths = ("/health", "/api/missions?limit=20", "/api/usage?limit=20")
    for path in paths:
        samples = []
        for _ in range(args.requests):
            started = time.perf_counter()
            _request(args.base_url.rstrip("/") + path, args.token)
            samples.append((time.perf_counter() - started) * 1000)
        samples.sort()
        p50 = statistics.median(samples)
        p95 = samples[int((len(samples) - 1) * 0.95)]
        print("%s  p50=%.2fms  p95=%.2fms  n=%d" % (path, p50, p95, len(samples)))


if __name__ == "__main__":
    main()
