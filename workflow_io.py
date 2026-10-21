"""Atomic checkpoints and a fail-fast lock shared by the pipeline and application CLI."""
import csv
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def _atomic(path, write):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            write(stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path, data):
    _atomic(path, lambda stream: json.dump(data, stream, ensure_ascii=False, indent=2))


def atomic_csv(path, fields, rows):
    def write(stream):
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    _atomic(path, write)


@contextmanager
def workflow_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "workflow.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"Workflow locked: {path}. If a process crashed, verify it has stopped before removing this lock.") from None
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink()
