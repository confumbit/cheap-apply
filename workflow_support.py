"""Persistence and interactive retry helpers used only by the workflow adapters."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import tempfile

logger = logging.getLogger(__name__)


class WorkflowStopped(Exception):
    """The user chose to stop at a retry prompt."""


class Recovery:
    def __init__(self, interactive=None):
        self.interactive = sys.stdin.isatty() if interactive is None else interactive

    def run(self, task, action):
        while True:
            try:
                return action()
            except (KeyboardInterrupt, WorkflowStopped):
                raise
            except Exception as exc:
                logger.error("%s failed: %s", task, exc)
                if isinstance(exc, PermissionError):
                    logger.error("Close the CSV/file in Excel or any other app, and check folder permissions.")
                text = str(exc).lower()
                if any(word in text for word in ("login", "auth", "cookie", "credential", "storage", "401")):
                    logger.error("Run notebooklm login in another terminal using this Python environment, then retry.")
                if not self.interactive:
                    raise
                logger.info("Progress is retained. Fix the issue in another window before retrying.")
                while True:
                    try:
                        answer = input(f"{task}: Enter to retry, or q to stop and resume later: ").strip().lower()
                    except EOFError:
                        raise WorkflowStopped("Input closed; rerun the same command to resume") from exc
                    if answer in ("", "r", "retry"):
                        logger.info("Retrying: %s", task)
                        break
                    if answer in ("q", "quit", "stop"):
                        raise WorkflowStopped("Stopped; rerun the same command to resume") from exc


def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_json(path: Path, data):
    atomic_bytes(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
