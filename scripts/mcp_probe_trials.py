"""Record observed Claude handoffs without storing documents, URLs, or credentials.

Examples:
  python scripts/mcp_probe_trials.py record --client web --case pdf --method embedded \
    --seconds 4.2 --text-ok --visuals-ok --equations-ok
  python scripts/mcp_probe_trials.py summary

Record elapsed time from prompt submission until document content is available.
Server-only timings and simulated tests must not be entered as Claude observations.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["client"], row["case"], row["phase"], row["session_state"], row["method"])].append(row)
    output = []
    for key, samples in sorted(groups.items()):
        times = sorted(row["seconds"] for row in samples)
        passed = all(row["text_ok"] and row["visuals_ok"] and row["equations_ok"]
                     and not row["manual_followup"] for row in samples)
        output.append(dict(zip(("client", "case", "phase", "session_state", "method"), key),
                           trials=len(samples), median_seconds=round(statistics.median(times), 3),
                           p95_seconds=round(times[math.ceil(.95 * len(times)) - 1], 3),
                           all_content_checks_pass=passed, sufficient_trials=len(samples) >= 20))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", type=Path, default=Path("/tmp/unitrack-mcp-observations.jsonl"))
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record")
    record.add_argument("--client", choices=["web", "desktop"], required=True)
    record.add_argument("--case", choices=["pdf", "docx", "pptx", "zip"], required=True)
    record.add_argument("--method", choices=["embedded", "resource_link", "signed_url", "manual"], required=True)
    record.add_argument("--phase", choices=["synthetic", "assignment"], default="synthetic")
    record.add_argument("--session-state", choices=["not_applicable", "warm", "expired", "container_cold"],
                        default="not_applicable")
    record.add_argument("--seconds", type=float, required=True)
    for name in ("text-ok", "visuals-ok", "equations-ok", "manual-followup"):
        record.add_argument("--" + name, action="store_true")
    commands.add_parser("summary")
    args = parser.parse_args()
    if args.command == "record":
        if not math.isfinite(args.seconds) or args.seconds <= 0:
            parser.error("--seconds must be finite and positive")
        if args.phase == "synthetic" and args.session_state in ("warm", "expired"):
            parser.error("Synthetic files do not exercise university sessions")
        if args.phase == "assignment" and args.session_state == "not_applicable":
            parser.error("Assignment trials must identify the university session state")
        row = {name: getattr(args, name) for name in ("client", "case", "method", "phase", "session_state",
                                                      "seconds", "text_ok", "visuals_ok", "equations_ok",
                                                      "manual_followup")}
        row.update(source="human_observation", recorded_at=datetime.now(timezone.utc).isoformat())
        with args.file.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        print("Observation recorded. This does not mark the release gate complete.")
    else:
        rows = [json.loads(line) for line in args.file.read_text().splitlines() if line.strip()] if args.file.exists() else []
        print(json.dumps({"observations": len(rows), "groups": summarize(rows),
                          "release_gate": "requires format checks, real-session trials, and manual comparison"}, indent=2))


if __name__ == "__main__":
    main()
