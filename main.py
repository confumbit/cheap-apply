"""Resumable scraping, experience/title filtering, and resume orchestration."""
import argparse
from contextlib import redirect_stdout
import csv
import io
import logging
import os
from pathlib import Path
import re
import sys
import uuid

from dotenv import load_dotenv

from job_functions import SEARCH_TERM, check_job_titles, classify_jobs, mark_eligible, scraper
from resume_pipeline import ROOT, save_csv, tailor_jobs
from workflow_support import Recovery, WorkflowStopped, atomic_bytes, load_json, save_json

load_dotenv()
logger = logging.getLogger(__name__)
CHECKPOINT = ROOT / ".workflow" / "state.json"


class ConsoleCapture(io.StringIO):
    """Keep classifier output visible and detect errors it reports without raising."""
    def __init__(self):
        super().__init__()
        self.console = sys.stdout

    def write(self, text):
        self.console.write(text)
        self.console.flush()
        return super().write(text)


def classify_remaining(state, persist, recovery):
    """Call the unchanged classifier one row at a time, saving each result."""
    path = Path(state["working_csv"])
    with path.open(newline="", encoding="utf-8-sig") as stream:
        records = list(csv.reader(stream))
    if not records:
        raise ValueError("Cannot classify an empty CSV")
    header, rows = records[0], records[1:]
    cols = {name.lower(): i for i, name in enumerate(header)}
    row_file = path.parent / "classification-current.csv"
    for index in range(state.get("next_row", 0), len(rows)):
        row = rows[index]
        label = " / ".join(row[cols[k]] for k in ("company", "title") if k in cols and cols[k] < len(row))
        task = f"Classify job {index + 1}/{len(rows)} ({label})"
        logger.info("%s; CSV row %d", task, index + 2)

        def classify():
            save_csv(row_file, header, [row])
            output = ConsoleCapture()
            with redirect_stdout(output):
                classify_jobs(str(row_file))
            errors = re.findall(r"^row \d+: (.+)$", output.getvalue(), re.MULTILINE)
            if errors:
                raise RuntimeError("Classifier reported: " + "; ".join(errors))
            with row_file.open(newline="", encoding="utf-8-sig") as stream:
                result = list(csv.reader(stream))
            if len(result) != 2:
                raise ValueError("Classifier did not return the expected CSV row")
            return result[1]

        rows[index] = recovery.run(task, classify)
        recovery.run("Save classified job", lambda: save_csv(path, header, rows))
        state["next_row"] = index + 1
        persist()


def run_workflow(*, checkpoint=CHECKPOINT, fresh=False, csv_path=None,
                 start_at=None, experience_column=None, recovery=None):
    recovery = recovery or Recovery()
    checkpoint = Path(checkpoint).resolve()
    if checkpoint.exists() and not fresh and csv_path is None:
        state = recovery.run("Read workflow checkpoint", lambda: load_json(checkpoint))
        if state.get("version") != 1:
            raise ValueError(f"Unsupported workflow checkpoint: {checkpoint}")
        logger.info("Resuming workflow at %s; CSV: %s", state["stage"], state.get("csv_path") or "pending scrape")
    else:
        work = checkpoint.parent / "runs" / uuid.uuid4().hex
        recovery.run("Create workflow work folder", lambda: work.mkdir(parents=True, exist_ok=True))
        state = dict(version=1, stage="scrape", work_dir=str(work), next_row=0,
                     csv_path=None, working_csv=None, experience_column="experience_range",
                     search_term=SEARCH_TERM)
        if csv_path is not None:
            source = Path(csv_path).resolve()
            stage = start_at or "classify"
            if stage not in ("classify", "tailor"):
                raise ValueError("start_at must be classify or tailor")
            working = work / source.name
            recovery.run("Copy input CSV to workflow workspace",
                         lambda: atomic_bytes(working, source.read_bytes()))
            state.update(stage=stage, csv_path=str(source), working_csv=str(working))
            if stage == "tailor":
                state["experience_column"] = experience_column

    def persist():
        recovery.run("Save workflow checkpoint", lambda: save_json(checkpoint, state))

    persist()
    while True:
        stage = state["stage"]
        logger.info("Workflow stage: %s", stage)
        if stage == "complete":
            logger.info("Workflow completed; CSV: %s. Use --new for a new scrape.", state["csv_path"])
            return state
        if stage == "scrape":
            logger.info("[1/3] Scraping job boards")

            def scrape():
                previous = Path.cwd()
                try:
                    os.chdir(state["work_dir"])
                    return str(Path(scraper()).resolve())
                finally:
                    os.chdir(previous)

            working = Path(recovery.run("Scrape job boards", scrape))
            state.update(working_csv=str(working), csv_path=str(ROOT / working.name), stage="publish_scrape")
        elif stage in ("publish_scrape", "publish_classification"):
            target, source = Path(state["csv_path"]), Path(state["working_csv"])
            logger.info("Publishing CSV to %s", target)
            recovery.run(f"Publish CSV {target.name}", lambda: atomic_bytes(target, source.read_bytes()))
            state["stage"] = "classify" if stage == "publish_scrape" else "tailor"
        elif stage == "classify":
            logger.info("[2/3] Classifying experience; resume at job %d", state["next_row"] + 1)
            recovery.run("Classify remaining jobs", lambda: classify_remaining(state, persist, recovery))
            recovery.run("Check job titles", lambda: check_job_titles(
                state["working_csv"], search_title=state.get("search_term", SEARCH_TERM)))
            recovery.run("Mark eligible jobs", lambda: mark_eligible(
                state["working_csv"], experience_column=state["experience_column"]))
            state["title_filter_complete"] = True
            state["stage"] = "publish_classification"
        elif stage == "filter_titles":
            # Resume checkpoints saved by the previous separate-title stage.
            logger.info("[2/3] Finishing title/eligibility classification from an older checkpoint")
            recovery.run("Check job titles", lambda: check_job_titles(
                state["csv_path"], search_title=state.get("search_term", SEARCH_TERM)))
            recovery.run("Mark eligible jobs", lambda: mark_eligible(
                state["csv_path"], experience_column=state["experience_column"]))
            state["title_filter_complete"] = True
            state["stage"] = "tailor"
        elif stage == "tailor":
            logger.info("[3/3] Grouping eligible duplicates and tailoring resumes")
            recovery.run("Tailor eligible jobs", lambda: tailor_jobs(
                state["csv_path"], experience_column=state["experience_column"], recovery=recovery))
            state["stage"] = "complete"
        else:
            raise ValueError(f"Unknown workflow stage: {stage}")
        persist()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--new", action="store_true", help="Start a fresh scrape instead of resuming")
    inputs.add_argument("--csv", type=Path, help="Start from an existing CSV")
    parser.add_argument("--start-at", choices=("classify", "tailor"), help="Stage for --csv (default: classify)")
    parser.add_argument("--experience-column", help="Experience column for --csv --start-at tailor")
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    parser.add_argument("--non-interactive", action="store_true", help="Exit on errors instead of prompting")
    args = parser.parse_args(argv)
    if args.start_at and args.csv is None:
        parser.error("--start-at requires --csv")
    if args.experience_column and (args.csv is None or args.start_at != "tailor"):
        parser.error("--experience-column requires --csv --start-at tailor")
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    try:
        run_workflow(checkpoint=args.checkpoint, fresh=args.new, csv_path=args.csv,
                     start_at=args.start_at, experience_column=args.experience_column,
                     recovery=Recovery(False if args.non_interactive else None))
    except (KeyboardInterrupt, WorkflowStopped):
        logger.info("Workflow stopped. Rerun main.py with the same --checkpoint to resume. Checkpoint: %s", args.checkpoint)
        return 130
    except Exception:
        logger.exception("Workflow failed. Fix the issue and rerun main.py to resume. Checkpoint: %s", args.checkpoint)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
