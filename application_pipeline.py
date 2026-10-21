"""Sequential, checkpointed resume generation and a small application-agent queue."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import importlib.util
import json
import logging
import math
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from workflow_io import atomic_json

ROOT = Path(__file__).resolve().parent
DEFAULT_STATE = ROOT / "applications"
LOG = logging.getLogger(__name__)
MANUAL_STATES = {"filling", "needs_input", "submission_uncertain", "submitted", "skipped"}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical_url(value):
    try:
        url = urlsplit((value or "").strip())
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            return ""
        query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"trk", "trackingid", "refid"}]
        return urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path.rstrip("/"), urlencode(query), ""))
    except ValueError:
        return ""


def job_id(row):
    # Board URL generally contains the stable requisition ID. Prefer it for identity;
    # direct ATS URLs can change between scrapes. Tracking query strings are removed.
    url = canonical_url(row.get("job_url")) or canonical_url(row.get("job_url_direct"))
    identity = url or json.dumps([row.get(k, "").strip().lower() for k in ("company", "title", "location")])
    return digest(identity.encode())[:24]


def experience(row):
    value = row.get("min_years_experience", "")
    if row.get("experience_status") != "known":
        return None
    try:
        years = float(value)
        return years if math.isfinite(years) and 0 <= years <= 40 else None
    except (TypeError, ValueError):
        return None


def read_jobs(path, experience_limit):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"company", "title", "description", "job_url"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV missing columns: {sorted(required - set(reader.fieldnames or []))}")
        # Prefer the most complete description if a scrape returns duplicate rows.
        unique = {}
        for row in reader:
            years = experience(row)
            if years is None or years > experience_limit:
                continue
            key = job_id(row)
            if key not in unique or len(row.get("description") or "") > len(unique[key].get("description") or ""):
                unique[key] = row
        return unique


def load_state(directory):
    path = Path(directory) / "state.json"
    if not path.exists():
        return {"version": 1, "jobs": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != 1 or not isinstance(state.get("jobs"), dict):
        raise ValueError("Unsupported application state; preserve it and migrate before running")
    return state


def load_tailor(config_path):
    name = "job_resume_tailor"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "resume-tailor" / "resume_tailor.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses needs its defining module registered
        spec.loader.exec_module(module)
    module = sys.modules[name]
    cfg = module.load_config(Path(config_path))
    return module, cfg


def input_fingerprint(cfg):
    # Include candidate data, template dependencies, validation code and prompts.
    files = {Path(__file__), ROOT / "resume-tailor" / "resume_tailor.py",
             ROOT / "resume-tailor" / "resume-modifier.md", Path(cfg["custom_prompt_file"])}
    files.update(p for p in Path(cfg["resume_dir"]).rglob("*")
                 if p.is_file() and p.suffix.lower() in {".tex", ".txt", ".cls", ".sty", ".png", ".jpg", ".pdf"})
    payload = json.dumps(cfg, sort_keys=True, default=str).encode()
    for path in sorted(files):
        payload += str(path.resolve()).encode() + path.read_bytes()
    return digest(payload)


def validated_pdf(final):
    final = Path(final)
    try:
        result = json.loads((final / "validation.json").read_text(encoding="utf-8"))
        pdf = final / "resume.pdf"
        return (result.get("passed") is True and pdf.read_bytes().startswith(b"%PDF-")
                and digest(pdf.read_bytes()) == result.get("pdf_sha256"))
    except (OSError, ValueError):
        return False


async def prepare_applications(csv_path, *, experience_limit, config_path, directory=DEFAULT_STATE,
                               max_jobs=0, max_rounds=2, retry_failed=False, dry_run=False):
    """max_jobs counts new attempts, not cache hits; zero means all eligible jobs.

    Caller holds workflow_lock, including while NotebookLM is mutating its sources.
    Failed/rejected attempts are never automatically repeated on restart.
    """
    directory = Path(directory).resolve()
    state = load_state(directory)
    jobs = read_jobs(csv_path, experience_limit)
    LOG.info("%s unique jobs meet EXPERIENCE <= %s", len(jobs), experience_limit)
    if dry_run:
        return {"eligible": len(jobs), "jobs": [
            {"id": key, "company": row["company"], "title": row["title"],
             "experience": experience(row), "status": state["jobs"].get(key, {}).get("status", "new")}
            for key, row in jobs.items()]}
    tailor, cfg = load_tailor(config_path)
    cfg.update(max_rounds=max_rounds, underflow_min_lines=0, grounded_only=True)
    if not cfg.get("notebook_id") or not cfg.get("source_titles"):
        raise ValueError("Notebook ID and explicit pinned candidate sources are required")
    inputs = input_fingerprint(cfg)
    baseline = None
    attempts = 0
    failed = False
    for key, row in jobs.items():
        old = state["jobs"].get(key, {})
        if old.get("status") in MANUAL_STATES:
            continue
        jd = (row.get("description") or "").strip()
        fingerprint = digest(json.dumps([inputs, row["company"], row["title"], jd]).encode())
        if old.get("fingerprint") == fingerprint:
            if old.get("status") == "ready" and validated_pdf(old["final_dir"]):
                continue
            if old.get("status") in {"tailoring", "failed", "resume_rejected"} and not retry_failed:
                continue
        url = canonical_url(row.get("job_url_direct")) or canonical_url(row.get("job_url"))
        if not all([jd, url, row["company"].strip(), row["title"].strip()]):
            LOG.warning("Skipping incomplete job %s", key)
            continue
        if max_jobs and attempts >= max_jobs:
            break
        attempts += 1
        entry = {"id": key, "company": row["company"], "title": row["title"],
                 "url": url, "board_url": row["job_url"], "location": row.get("location", ""),
                 "experience": experience(row), "fingerprint": fingerprint, "status": "tailoring"}
        state["jobs"][key] = entry
        atomic_json(directory / "state.json", state)
        job_dir = directory / "jobs" / key
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "jd.txt").write_text(jd, encoding="utf-8")
        try:
            if baseline is None:
                baseline = tailor.measure_baseline(cfg, job_dir)
            # Keep attempt directories independent, including explicit retries.
            run_dir = job_dir / "attempts" / uuid.uuid4().hex
            final = await tailor.cmd_run(cfg, row["company"], row["title"], jd,
                                          run_dir=run_dir, base=baseline)
            entry["final_dir"] = str(final)
            entry["status"] = "ready" if validated_pdf(final) else "resume_rejected"
            entry["packet"] = str(job_dir / "application.json")
            packet = {**entry, "resume_pdf": str(final / "resume.pdf"),
                      "resume_tex": str(final / "resume.tex"), "report": str(final / "report.md"),
                      "jd": str(job_dir / "jd.txt"), "submit_when_supported": True,
                      "candidate_resume": str(Path(cfg["main_tex"])),
                      "candidate_section": str(Path(cfg["section_file"])) if cfg.get("section_file") else None,
                      "candidate_sources": [str(p.resolve()) for p in sorted(Path(cfg["resume_dir"]).glob("*-list.txt"))],
                      "candidate_profile": str(ROOT / "candidate-profile.json")}
            atomic_json(entry["packet"], packet)
        except (Exception, SystemExit) as exc:
            entry.update(status="failed", error=str(exc))
            failed = True
            LOG.exception("Tailoring failed for %s; stopping batch to avoid repeated backend failures", key)
            break
        finally:
            atomic_json(directory / "state.json", state)
    counts = {}
    for entry in state["jobs"].values():
        status = entry["status"]
        counts[status] = counts.get(status, 0) + 1
    return {"eligible": len(jobs), "new_attempts": attempts, "statuses": counts, "failed": failed}


def next_application(directory=DEFAULT_STATE):
    """Claim one packet. A crash leaves 'filling', never an automatic duplicate."""
    state = load_state(directory)
    for entry in state["jobs"].values():
        if entry["status"] != "ready":
            continue
        if not validated_pdf(entry["final_dir"]):
            entry["status"] = "resume_rejected"
            atomic_json(Path(directory) / "state.json", state)
            continue
        entry["status"] = "filling"
        atomic_json(Path(directory) / "state.json", state)
        return json.loads(Path(entry["packet"]).read_text(encoding="utf-8"))
    return None


def record_application(directory, key, status, note, evidence=""):
    allowed = {"filling", "submitted", "needs_input", "submission_uncertain", "skipped", "ready"}
    if status not in allowed or not note.strip():
        raise ValueError("A supported status and a nonempty factual note are required")
    if status == "submitted" and not evidence.strip():
        raise ValueError("submitted requires observed confirmation text or URL as --evidence")
    state = load_state(directory)
    entry = state["jobs"][key]
    if entry["status"] == "submitted" and status != "submitted":
        raise ValueError("Submitted applications cannot be requeued")
    if status == "ready" and not validated_pdf(entry["final_dir"]):
        raise ValueError("Cannot requeue an unvalidated PDF")
    entry.update(status=status, note=note, evidence=evidence)
    entry.setdefault("history", []).append({"status": status, "note": note, "evidence": evidence,
                                           "at": dt.datetime.now().astimezone().isoformat()})
    atomic_json(Path(directory) / "state.json", state)
