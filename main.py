"""Scrape -> classify -> tailor eligible jobs -> checkpoint the Copilot queue."""
import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from application_pipeline import DEFAULT_STATE, ROOT, prepare_applications
from job_functions import EXPERIENCE, classify_jobs, scraper
from workflow_io import workflow_lock

load_dotenv(ROOT / ".env")
logger = logging.getLogger(__name__)


def run_workflow(*, csv_path=None, experience_limit=EXPERIENCE, max_jobs=0,
                 max_rounds=2, retry_failed=False, dry_run=False,
                 config_path=ROOT / "resume-tailor" / "resume_tailor.json",
                 directory=DEFAULT_STATE):
    if experience_limit < 0 or max_jobs < 0 or not 1 <= max_rounds <= 4:
        raise ValueError("Experience/jobs must be nonnegative; max_rounds must be 1..4")
    if dry_run and csv_path is None:
        raise ValueError("--dry-run requires --csv; it performs no network calls")
    with workflow_lock(directory):
        path = Path(csv_path) if csv_path else Path(scraper())
        if not dry_run:
            classify_jobs(path)
        result = asyncio.run(prepare_applications(
            path, experience_limit=experience_limit, config_path=config_path,
            directory=directory, max_jobs=max_jobs, max_rounds=max_rounds,
            retry_failed=retry_failed, dry_run=dry_run))
    logger.info("Workflow result: %s", json.dumps(result, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, help="Reuse this CSV instead of scraping")
    parser.add_argument("--experience", type=float, default=EXPERIENCE)
    parser.add_argument("--max-jobs", type=int, default=0, help="New resume attempts; 0 = all eligible jobs")
    parser.add_argument("--max-rounds", type=int, default=2, help="NotebookLM asks per job (1..4)")
    parser.add_argument("--retry-failed", action="store_true", help="Explicitly retry failed/rejected/interrupted tailoring")
    parser.add_argument("--dry-run", action="store_true", help="Plan from already classified CSV; no network or model calls")
    parser.add_argument("--config", type=Path, default=ROOT / "resume-tailor" / "resume_tailor.json")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        result = run_workflow(csv_path=args.csv, experience_limit=args.experience,
                              max_jobs=args.max_jobs, max_rounds=args.max_rounds,
                              retry_failed=args.retry_failed, dry_run=args.dry_run,
                              config_path=args.config, directory=args.state_dir)
        return 1 if result.get("failed") else 0
    except Exception:
        logger.exception("Workflow failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
