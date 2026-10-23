"""Batch adapter for the existing classifier CSV and resume-tailor CLI."""
from __future__ import annotations

import argparse
import csv
import html
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from workflow_support import Recovery, WorkflowStopped, load_json, save_json

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
TAILOR = ROOT / "resume-tailor" / "resume_tailor.py"
CONFIG = TAILOR.with_name("resume_tailor.json")
RESUME_COLUMN = "resume_pdf"


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(value)).casefold()
    return " ".join(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", value)))


def canonical_url(value: str) -> str:
    """Remove tracking parameters, retaining requisition IDs and other query data."""
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"trk", "trackingid", "ref", "refid", "source", "src"}
    )
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def eligible(value: str, experience: float) -> bool:
    """Blank/explicitly missing values qualify; malformed values must not qualify."""
    value = value.strip().casefold()
    if value in {"", "null", "none", "nan", "n/a", "not mentioned", "not stated", "unknown"}:
        return True
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*\+?\s*(?:[-\u2010-\u2015\u2212]\s*\d+(?:\.\d+)?)?\s*(?:years?|yrs?)?",
        value,
    )
    if not match:
        raise ValueError(f"Unrecognized experience value: {value!r}")
    return float(match.group(1)) <= experience


def duplicate_groups(rows: list[list[str]], cols: dict[str, int]) -> list[list[int]]:
    """Group across boards by posting URL, company/title/location, or matching JD.

    A matching JD also catches board-specific location labels. Company and title
    must match for that fallback, and at least 20 words must be available.
    """
    parents = list(range(len(rows)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def join(a, b):
        parents[find(b)] = find(a)

    seen = {}
    descriptions = {}
    for i, row in enumerate(rows):
        company, title, location = (normalized(row[cols[k]]) for k in ("company", "title", "location"))
        keys = []
        for field in ("job_url", "job_url_direct"):
            if field in cols and row[cols[field]].strip():
                try:
                    url = canonical_url(row[cols[field]])
                except ValueError:
                    url = ""
                if url:
                    keys.append(("url", url))
        if company and title:
            if location:
                keys.append(("identity", company, title, location))
            words = normalized(row[cols["description"]]).split()
            if len(words) >= 20:
                shingles = {tuple(words[n:n + 5]) for n in range(len(words) - 4)}
                peers = descriptions.setdefault((company, title), [])
                for other, prior in peers:
                    if len(shingles & prior) / len(shingles | prior) >= 0.9:
                        join(i, other)
                peers.append((i, shingles))
        for key in keys:
            if key in seen:
                join(i, seen[key])
            else:
                seen[key] = i
    groups = {}
    for i in range(len(rows)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def save_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def tailor_one(company: str, title: str, description: str, config: Path) -> Path:
    """Use the public CLI unchanged, including its configured output directory."""
    with tempfile.TemporaryDirectory(prefix="resume-jd-") as tmp:
        jd = Path(tmp) / "jd.txt"
        jd.write_text(description, encoding="utf-8", newline="\n")
        process = subprocess.Popen(
            [sys.executable, "-u", str(TAILOR), "--config", str(config), "run",
             "--company", company, "--role", title, "--jd", str(jd)],
            cwd=TAILOR.parent, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        lines = []
        try:
            for line in process.stdout:
                lines.append(line)
                logger.info("Resume tailor: %s", line.rstrip())
            returncode = process.wait()
        except BaseException:
            # Do not leave the old tailor running while a resumed workflow starts.
            try:
                process.terminate()
            except OSError:
                pass  # The child may have exited just before the interruption.
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        finally:
            process.stdout.close()
    output = "".join(lines)
    if returncode:
        raise RuntimeError(f"Resume tailor exited with code {returncode}:\n" + "".join(lines[-30:]))
    outputs = re.findall(r"^Output: (.+)$", output, flags=re.MULTILINE)
    if not outputs:
        raise RuntimeError("Resume tailor did not report an output directory")
    final = Path(outputs[-1].strip())
    if not final.is_absolute():
        final = TAILOR.parent / final
    pdf = final / "resume.pdf"
    if not pdf.is_file() or pdf.stat().st_size == 0:
        raise RuntimeError(f"Resume tailor produced no PDF: {pdf}")
    return pdf.resolve()


class ResumeJournal:
    """Record results before CSV writes, plus run folders for interrupted CLI calls."""
    def __init__(self, csv_path, config, recovery):
        self.path = csv_path.with_name(csv_path.name + ".resume-progress.json")
        self.config = config
        self.recovery = recovery
        self.data = recovery.run("Read resume checkpoint", lambda: load_json(self.path)) if self.path.exists() else {"version": 1, "jobs": {}}
        if self.data.get("version") != 1 or not isinstance(self.data.get("jobs"), dict):
            raise ValueError(f"Invalid resume checkpoint: {self.path}")

    def save(self):
        self.recovery.run("Save resume checkpoint", lambda: save_json(self.path, self.data))

    def tailor(self, company, title, jd):
        config_text = self.config.read_text(encoding="utf-8-sig")
        key = hashlib.sha256(json.dumps([company, title, jd, str(self.config), config_text]).encode("utf-8")).hexdigest()
        output = Path(json.loads(config_text).get("output_dir", "tailor-runs"))
        if not output.is_absolute():
            output = self.config.parent / output
        record = self.data["jobs"].get(key, {})
        pdf = Path(record["pdf"]) if record.get("pdf") else None
        if pdf and pdf.is_file() and pdf.stat().st_size:
            logger.info("Recovered completed PDF from checkpoint: %s", pdf)
            return pdf
        expected_jd = f"{company}\n{title}\n\n{jd}".replace("\r\n", "\n").replace("\r", "\n")
        if "existing_runs" in record and output.exists():
            previous = set(record["existing_runs"])
            for run in sorted(output.iterdir(), reverse=True):
                candidate = run / "final" / "resume.pdf"
                source = run / "jd.txt"
                # finalize writes issues.txt after copying the PDF; its presence
                # prevents reuse of a half-copied PDF after a forced shutdown.
                finished = run / "final" / "issues.txt"
                if run.name not in previous and candidate.is_file() and candidate.stat().st_size and source.is_file() and finished.is_file():
                    if source.read_text(encoding="utf-8-sig") == expected_jd:
                        logger.info("Recovered PDF from interrupted tailor run: %s", candidate)
                        record["pdf"] = str(candidate.resolve())
                        self.save()
                        return candidate.resolve()
        record = {"existing_runs": [p.name for p in output.iterdir()] if output.exists() else []}
        self.data["jobs"][key] = record
        self.save()
        pdf = tailor_one(company, title, jd, self.config)
        record["pdf"] = str(pdf)
        self.save()
        return pdf


def tailor_jobs(path, experience=None, *, config=CONFIG, experience_column=None, dry_run=False, recovery=None):
    """Link one tailored PDF per eligible duplicate group; checkpoint each group.

    Use the named minimum column when present, otherwise column AD, which is
    where classify_jobs writes its result. Pass experience_column to override.
    """
    recovery = recovery or Recovery(False)
    logger.info("Reading classified CSV: %s", path)
    if experience is None:
        from job_functions import EXPERIENCE
        experience = EXPERIENCE
    if not math.isfinite(experience) or experience < 0:
        raise ValueError("EXPERIENCE must be a finite, nonnegative number")
    path, config = Path(path).resolve(), Path(config).resolve()
    with path.open(newline="", encoding="utf-8-sig") as stream:
        records = list(csv.reader(stream))
    if not records:
        raise ValueError("CSV is empty")
    header, rows = records[0], records[1:]
    cols = {name.strip().lower(): n for n, name in enumerate(header)}
    missing = {"company", "title", "location", "description"} - cols.keys()
    if missing:
        raise ValueError(f"Missing CSV columns: {sorted(missing)}")
    if experience_column:
        exp_index = cols[experience_column.lower()]
    elif "min_years_experience" in cols:
        exp_index = cols["min_years_experience"]
    elif len(header) > 29 and header[29].strip().lower() == "experience_range":
        exp_index = 29
    else:
        raise ValueError("CSV has no classified experience column; run classify_jobs first")
    if RESUME_COLUMN not in cols:
        cols[RESUME_COLUMN] = len(header)
        header.append(RESUME_COLUMN)
    resume_index = cols[RESUME_COLUMN]
    for row in rows:
        row.extend([""] * (len(header) - len(row)))

    stats = dict(rows=len(rows), eligible=0, groups=0, generated=0, reused=0, failed=0, invalid=0)
    qualified = set()
    for i, row in enumerate(rows):
        try:
            if eligible(row[exp_index], experience):
                qualified.add(i)
            elif not dry_run:
                row[resume_index] = ""
        except ValueError as exc:
            stats["invalid"] += 1
            if not dry_run:
                row[resume_index] = ""
            logger.warning("Row %d skipped: %s", i + 2, exc)
    stats["eligible"] = len(qualified)
    logger.info("Checking duplicate postings among %d rows (%d eligible)", len(rows), len(qualified))
    groups = [group for group in duplicate_groups(rows, cols) if any(i in qualified for i in group)]
    logger.info("Found %d eligible unique job groups", len(groups))
    journal = None if dry_run else ResumeJournal(path, config, recovery)
    if not dry_run:
        recovery.run("Save CSV resume column", lambda: save_csv(path, header, rows))
    for number, group in enumerate(groups, start=1):
        targets = [i for i in group if i in qualified]
        if not targets:
            continue
        stats["groups"] += 1
        if dry_run:
            continue
        logger.info("Resume group %d/%d; CSV rows %s", number, len(groups), [i + 2 for i in targets])
        try:
            pdf = None
            for i in targets:
                existing = rows[i][resume_index].strip()
                if existing:
                    candidate = Path(existing)
                    if not candidate.is_absolute():
                        candidate = path.parent / candidate
                    if candidate.is_file() and candidate.stat().st_size:
                        pdf = candidate.resolve()
                        break
            if pdf is not None:
                logger.info("Reusing PDF: %s", pdf)
                stats["reused"] += 1
            else:
                # The fullest posting generally supplies the most useful JD.
                row = max((rows[i] for i in group), key=lambda r: len(r[cols["description"]].strip()))
                company, title, jd = (row[cols[k]].strip() for k in ("company", "title", "description"))
                if not all((company, title, jd)):
                    raise ValueError("Missing company, title, or job description")
                logger.info("Tailoring %s / %s for %d eligible row(s)", company, title, len(targets))
                pdf = recovery.run(f"Tailor {company} / {title}", lambda: journal.tailor(company, title, jd))
                stats["generated"] += 1
            for i in targets:
                rows[i][resume_index] = str(pdf)
        except WorkflowStopped:
            raise
        except Exception:
            stats["failed"] += 1
            for i in targets:
                rows[i][resume_index] = ""
            logger.exception("Could not tailor CSV rows %s; continuing batch", [i + 2 for i in targets])
        recovery.run("Save resume links to CSV", lambda: save_csv(path, header, rows))
        logger.info("Completed resume group %d/%d", number, len(groups))
    logger.info("Resume batch: %s", stats)
    if stats["failed"] or stats["invalid"]:
        raise RuntimeError(f"Resume batch needs attention: {stats}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="CSV already processed by classify_jobs")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--experience-column", help="Override automatic experience-column selection")
    parser.add_argument("--dry-run", action="store_true", help="Count eligible rows and groups without writing or tailoring")
    parser.add_argument("--non-interactive", action="store_true", help="Exit on errors instead of prompting")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    recovery = Recovery(False if args.non_interactive else None)
    try:
        recovery.run("Tailor CSV jobs", lambda: tailor_jobs(
            args.csv, config=args.config, experience_column=args.experience_column,
            dry_run=args.dry_run, recovery=recovery))
    except (KeyboardInterrupt, WorkflowStopped):
        logger.info("Stopped. Rerun this command to resume completed resume groups.")
        sys.exit(130)
    except Exception:
        logger.exception("Batch failed. Fix the issue and rerun this command to resume.")
        sys.exit(1)
