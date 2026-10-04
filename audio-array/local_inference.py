"""One cooperative local inference slot across POSIX processes.

All local ASR/LLM callers must use the same directory and acquire once around
the actual work. This lock is intentionally not reentrant and makes no FIFO
promise. Timeout/cancellation apply to waiting; running work must cooperate by
calling ``lease.check_cancelled()``. The lock never interrupts a model itself.

State files contain operational labels only, never audio/transcript content.
Reading state does not create files, acquire the lock, or remove stale records.
A heartbeat is evidence of a lock owner's report, not proof a model is running.
Dead/stale metadata cannot establish whether the kernel lock is available.
For HTTP inference servers such as Ollama, this coordinates cooperative clients;
it cannot stop server work that survives a client timeout or client termination.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Iterator
import uuid


DEFAULT_LOCK_DIR = Path(__file__).resolve().parent.parent / "data/event-audio/local-inference"
DEFAULT_STALE_AFTER = 10.0


class InferenceTimeout(TimeoutError):
    """No inference slot was acquired within the requested waiting time."""


class InferenceCancelled(RuntimeError):
    """Waiting, or explicitly checked cooperative work, was cancelled."""


def _cancelled(cancel) -> bool:
    if cancel is None:
        return False
    return bool(cancel.is_set() if hasattr(cancel, "is_set") else cancel())


def _stamp() -> dict:
    now = time.time()
    return {"updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "updated_unix": now}


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _positive(value: float, name: str, *, allow_zero: bool = False) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
        raise ValueError(f"{name} must be {'nonnegative' if allow_zero else 'positive'} and finite")
    return value


@dataclass
class InferenceLease:
    """An acquired slot. Keep a child process inside this lease's lifetime."""

    job_label: str
    pid: int
    acquired_at: str
    waited_seconds: float
    _fd: int
    _cancel: object = None

    def check_cancelled(self) -> None:
        if _cancelled(self._cancel):
            raise InferenceCancelled(f"Local inference cancelled: {self.job_label}")

    def fileno(self) -> int:
        """FD for ``subprocess.run(..., pass_fds=(lease.fileno(),))``.

        Explicit inheritance keeps the kernel lock held if the parent is killed
        while its child still runs. Wait for that child before leaving the with
        block: normal lease release unlocks the shared file description.
        """
        if self._fd < 0:
            raise ValueError("Inference lease has already been released")
        return self._fd


@contextmanager
def inference_slot(job_label: str, *, lock_dir: str | Path | None = None,
                   timeout: float | None = None, cancel=None,
                   poll_interval: float = 0.1,
                   heartbeat_interval: float = 2.0) -> Iterator[InferenceLease]:
    """Acquire the shared slot, releasing it on exit or process termination.

    ``job_label`` is a short machine label (e.g. ``asr`` or ``ollama:qwen3:4b``),
    not free-form user content. ``cancel`` accepts a threading.Event or callable.
    ``timeout=0`` makes a single nonblocking attempt. A cancellation already set
    prevents acquisition even when the slot is free. No stdout/stderr is used.
    Filesystem failures propagate; inference never proceeds without a lock.
    """
    if not isinstance(job_label, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,79}", job_label):
        raise ValueError("job_label must be a short machine label, not free-form text")
    poll_interval = _positive(poll_interval, "poll_interval")
    heartbeat_interval = _positive(heartbeat_interval, "heartbeat_interval")
    if timeout is not None:
        timeout = _positive(timeout, "timeout", allow_zero=True)
    if cancel is not None and not (callable(cancel) or callable(getattr(cancel, "is_set", None))):
        raise TypeError("cancel must be an Event or callable")

    directory = Path(lock_dir) if lock_dir is not None else DEFAULT_LOCK_DIR
    directory.mkdir(parents=True, exist_ok=True)
    waiters_dir = directory / "waiters"
    waiters_dir.mkdir(exist_ok=True)
    token = uuid.uuid4().hex
    waiter_path = waiters_dir / f"{token}.json"
    owner_path = directory / "owner.json"
    fd = os.open(directory / "inference.lock", os.O_RDWR | os.O_CREAT, 0o600)
    os.set_inheritable(fd, False)
    started = time.monotonic()
    initial = _stamp()
    metadata = {"schema_version": 1, "token": token, "pid": os.getpid(),
                "job_label": job_label, "requested_at": initial["updated_at"],
                "requested_unix": initial["updated_unix"], **initial}
    acquired = False
    lease = None
    stop = threading.Event()
    heartbeat = None
    try:
        _atomic_json(waiter_path, {**metadata, "phase": "waiting"})
        last_report = started
        attempted = False
        while True:
            if _cancelled(cancel):
                raise InferenceCancelled(f"Local inference wait cancelled: {job_label}")
            if attempted and timeout is not None and time.monotonic() - started >= timeout:
                raise InferenceTimeout(f"Local inference slot unavailable after {timeout:g}s: {job_label}")
            attempted = True
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
            elapsed = time.monotonic() - started
            if timeout is not None and elapsed >= timeout:
                raise InferenceTimeout(f"Local inference slot unavailable after {timeout:g}s: {job_label}")
            if time.monotonic() - last_report >= heartbeat_interval:
                _atomic_json(waiter_path, {**metadata, **_stamp(), "phase": "waiting"})
                last_report = time.monotonic()
            delay = min(poll_interval, max(0, timeout - elapsed)) if timeout is not None else poll_interval
            if callable(getattr(cancel, "wait", None)):
                cancel.wait(delay)
            else:
                time.sleep(delay)

        # Cancellation can arrive between the last check and the flock call.
        if _cancelled(cancel):
            raise InferenceCancelled(f"Local inference wait cancelled: {job_label}")
        acquired_stamp = _stamp()
        owner = {**metadata, **acquired_stamp, "phase": "owner",
                 "acquired_at": acquired_stamp["updated_at"],
                 "acquired_unix": acquired_stamp["updated_unix"]}
        _atomic_json(owner_path, owner)
        waiter_path.unlink(missing_ok=True)
        lease = InferenceLease(job_label, os.getpid(), owner["acquired_at"],
                               time.monotonic() - started, fd, cancel)

        def report_heartbeat() -> None:
            while not stop.wait(heartbeat_interval):
                try:
                    _atomic_json(owner_path, {**owner, **_stamp()})
                except OSError:
                    # Keep exclusion. The unreadable/stale report must not imply
                    # an available slot or successful model execution.
                    return

        reporter = threading.Thread(target=report_heartbeat, name="local-inference-heartbeat", daemon=True)
        reporter.start()
        heartbeat = reporter
        yield lease
    finally:
        stop.set()
        if heartbeat is not None:
            heartbeat.join()
        try:
            waiter_path.unlink(missing_ok=True)
            if acquired:
                current_owner = _load_json(owner_path)
                if current_owner and current_owner.get("token") == token:
                    owner_path.unlink(missing_ok=True)
        finally:
            if lease is not None:
                lease._fd = -1
            try:
                if acquired:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _pid_alive(pid: object) -> bool | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except (OSError, OverflowError):
        return None


def _observed_record(path: Path, now: float, stale_after: float) -> dict:
    record = _load_json(path)
    if not record:
        return {"record_state": "unreadable", "record_file": path.name,
                "pid_alive": None, "age_seconds": None}
    # Whitelist operational metadata; never echo unexpected stored content.
    result = {key: record.get(key) for key in
              ("schema_version", "token", "pid", "job_label", "phase",
               "requested_at", "acquired_at", "updated_at")}
    updated = record.get("updated_unix")
    valid_time = isinstance(updated, (int, float)) and not isinstance(updated, bool) and math.isfinite(updated)
    age = now - updated if valid_time else None
    alive = _pid_alive(record.get("pid"))
    if age is None or age < 0:
        state = "unknown_timestamp"
    elif age > stale_after:
        state = "stale"
    elif alive is False:
        state = "process_not_alive"
    elif alive is None:
        state = "process_unknown"
    else:
        state = "fresh_report"
    result.update(record_state=state, age_seconds=age, pid_alive=alive)
    return result


def read_inference_state(lock_dir: str | Path | None = None, *,
                         stale_after: float = DEFAULT_STALE_AFTER) -> dict:
    """Read operational JSON reports without creating or changing state.

    ``fresh_report`` only means recent metadata plus a PID existing at the time
    of observation. PID reuse and inherited child FDs mean this is deliberately
    not an ``is_running`` API. Stale waiters remain visible as stale evidence.
    """
    stale_after = _positive(stale_after, "stale_after")
    directory = Path(lock_dir) if lock_dir is not None else DEFAULT_LOCK_DIR
    now = time.time()
    owner_path = directory / "owner.json"
    owner = _observed_record(owner_path, now, stale_after) if owner_path.exists() else None
    waiters = [_observed_record(path, now, stale_after)
               for path in sorted((directory / "waiters").glob("*.json"))]
    return {"schema_version": 1, "observed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "stale_after_seconds": stale_after, "lock_path": str(directory / "inference.lock"),
            "kernel_lock_state": "not_probed", "owner": owner, "waiters": waiters,
            "fresh_waiter_reports": sum(item["record_state"] == "fresh_report" for item in waiters)}
