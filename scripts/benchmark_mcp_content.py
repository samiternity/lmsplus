"""Benchmark synthetic document preparation. These are not Claude handoff timings.

Run from the repository root with the project virtual environment:
  python scripts/benchmark_mcp_content.py --runs 20
"""

import argparse
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lms_mcp.document_content import prepare_document_content
from lms_mcp.fixtures import document_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.runs <= 100:
        parser.error("--runs must be between 1 and 100")
    cases = []
    for case in ("pdf", "docx"):
        original = document_bytes(case)
        times = []
        for _ in range(args.runs):
            started = time.perf_counter()
            blocks = prepare_document_content(original, case)
            times.append((time.perf_counter() - started) * 1000)
        cases.append({
            "format": case, "runs": len(times), "source_bytes": len(original),
            "content_blocks": len(blocks),
            "encoded_content_bytes": len(json.dumps(blocks).encode()),
            "median_preparation_ms": round(statistics.median(times), 3),
            "p95_preparation_ms": round(sorted(times)[math.ceil(.95 * len(times)) - 1], 3),
        })
    print(json.dumps({"environment": "local machine, not Cloud Run",
                      "python": platform.python_version(), "cases": cases,
                      "includes_university_login": False, "includes_claude_ingestion": False,
                      "token_consumption_measured": False}, indent=2))


if __name__ == "__main__":
    main()
