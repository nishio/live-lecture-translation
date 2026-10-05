"""Cooperative cancellation before a worker admits new processing.

Each worker binds the event for its own session.  Checks belong immediately
before dispatch and after potentially blocking preparation or lock acquisition.
They do not cancel a request or inference that has already been admitted, nor
prevent its result or cost from being recorded.
"""

from contextlib import contextmanager
import threading


class ProcessingStopped(RuntimeError):
    """The session stopped before this operation was dispatched."""

    # A pre-dispatch cancellation never leaves a local model running.
    local_inference_finished = True


_worker = threading.local()


def current_cancel_event():
    """Return the session cancellation event bound to this worker, if any."""
    return getattr(_worker, "cancel_event", None)


@contextmanager
def processing_scope(cancel_event):
    """Bind a session event to this thread, restoring a surrounding scope."""
    previous = current_cancel_event()
    _worker.cancel_event = cancel_event
    try:
        yield
    finally:
        _worker.cancel_event = previous


def check_processing_allowed():
    """Reject work not yet admitted when its owning session has stopped."""
    cancel_event = current_cancel_event()
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessingStopped("Processing stopped before dispatch")
