"""Small terminal interface for the VS Code application agent. No model calls."""
import argparse
import json
from pathlib import Path

from application_pipeline import DEFAULT_STATE, load_state, next_application, record_application
from workflow_io import workflow_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("next", help="Claim exactly one validated application packet")
    sub.add_parser("status", help="Compact status list; does not include full JDs")
    record = sub.add_parser("record", help="Checkpoint browser progress")
    record.add_argument("id")
    record.add_argument("status", choices=["filling", "ready", "submitted", "needs_input", "submission_uncertain", "skipped"])
    record.add_argument("--note", required=True)
    record.add_argument("--evidence", default="")
    args = parser.parse_args()
    with workflow_lock(args.state_dir):
        if args.command == "next":
            result = next_application(args.state_dir)
        elif args.command == "status":
            result = [{k: row.get(k) for k in ("id", "company", "title", "status", "note", "evidence")}
                      for row in load_state(args.state_dir)["jobs"].values()]
        else:
            record_application(args.state_dir, args.id, args.status, args.note, args.evidence)
            result = {"id": args.id, "status": args.status}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
