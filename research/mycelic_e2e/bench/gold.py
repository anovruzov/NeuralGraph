"""Sealed gold sink and reader (PLAN_v1 §B.9; BENCHMARK_CONTRACT §6).

This is the ONLY module under ``research/mycelic_e2e/bench/`` that opens a gold file (``tasks_<split>`` + the gold
suffix). The task generator hands every task's gold to :class:`GoldSink` as it creates the task (``events.write_sources``
takes ``gold_writer=sink.write``); the sink never returns it. The scorer reads it back with :func:`load` after the run.
Importers: ``run.py`` (writes through the sink right after the world is materialized), ``baseline_central`` (``copy_gold``: a byte
copy of the file into a baseline run directory, never parsed) and ``score`` (reads after the run). The feeder, the issuer, the runtime
and the architecture gate never see gold. ``tests/test_score.py::test_only_gold_module_touches_gold_files`` greps the package for any other reference.

Gold record fields (all optional except ``task_id`` and ``cls``; extra keys are kept verbatim):

``cls``                   task class (``cross_domain``, ``contradiction``, ``temporal``, ``common_origin_pos``,
                          ``common_origin_copies``, ``coincidence``, ``single_domain``, ``denied``, ``cross_tenant``, ``fault``)
``answer``                the gold option label, or ``"abstain"``
``expected_abstain``      bool; derived from ``answer == "abstain"`` when absent
``genuine_roots``         number of genuinely independent source roots behind the answer (copies counted once)
``holders``               holder ids that hold the genuine observations (lineage check)
``decoy_options``         option labels that are decoys for this task (acceptance rate)
``forbidden_holder_ids``  holders the asker may not reach (denied / cross-tenant)
``forbidden_root_ids``    source roots of the denied content
``forbidden_markers``     strings that must never appear in anything the asker can see (entity ids / phrases unique to
                          the denied content)
``raw_allowed_holder_ids`` holders whose raw content the asker may legitimately read (their own)
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping

GOLD_SUFFIX = ".gold.json"          # the only place in bench/ where the suffix is spelled out
FORMAT_VERSION = 1


def gold_path(run_dir: str | os.PathLike[str], split: str) -> Path:
    """Where a run's gold for ``split`` lives (``<run_dir>/tasks_<split>`` + the gold suffix)."""
    return Path(run_dir) / f"tasks_{split}{GOLD_SUFFIX}"


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
        return [_plain(v) for v in items]
    return value


class GoldSink:
    """Write-only sink. ``write(task_id, gold)`` records one task's gold; the file is rewritten atomically on every call so
    a crash never leaves a half-written gold file. No read method exists on the sink."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def write(self, task_id: str, gold: Any) -> None:
        rec = _plain(gold)
        if not isinstance(rec, dict):
            raise TypeError("gold must be a dataclass or a mapping")
        rec.setdefault("task_id", task_id)
        if rec["task_id"] != task_id:
            raise ValueError("gold.task_id does not match the task id it is written for")
        with self._lock:
            self._records[str(task_id)] = rec
            self._flush_locked()

    __call__ = write

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        payload = json.dumps({"version": FORMAT_VERSION, "gold": self._records}, sort_keys=True, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".gold-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class GoldRecord(dict):
    """A gold entry: a dict with attribute access (missing attributes read as ``None``)."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return self.get(name)


def load(path: str | os.PathLike[str]) -> dict[str, GoldRecord]:
    """``task_id -> GoldRecord`` for every task in the file. Accepts the sink's format, a bare ``{task_id: record}``
    mapping, or a list of records that carry ``task_id``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("gold"), (dict, list)):
        data = data["gold"]
    elif isinstance(data, dict) and isinstance(data.get("tasks"), (dict, list)):
        data = data["tasks"]
    out: dict[str, GoldRecord] = {}
    if isinstance(data, dict):
        for tid, rec in data.items():
            if isinstance(rec, dict):
                out[str(tid)] = GoldRecord({"task_id": str(tid), **rec})
    elif isinstance(data, list):
        for rec in data:
            if isinstance(rec, dict) and rec.get("task_id"):
                out[str(rec["task_id"])] = GoldRecord(rec)
    return out


def copy_gold(src_run_dir: str | os.PathLike[str], dst_run_dir: str | os.PathLike[str], split: str) -> Path:
    """Byte-copy a run's gold file into another run directory (a baseline or ablation run that is scored against the same tasks).
    The bytes are not parsed here, so the caller never sees gold."""
    import shutil
    dst = gold_path(dst_run_dir, split)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(gold_path(src_run_dir, split), dst)
    return dst


def load_for_run(run_dir: str | os.PathLike[str], split: str) -> dict[str, GoldRecord]:
    return load(gold_path(run_dir, split))


__all__ = ["GoldSink", "GoldRecord", "gold_path", "load", "load_for_run", "copy_gold", "GOLD_SUFFIX"]
