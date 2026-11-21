"""Fail-fast OS lock for UN mutations in one local checkout (Linux/macOS)."""
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
from functools import wraps
from pathlib import Path

_DELEGATED_ROOT: ContextVar[Path | None] = ContextVar('_DELEGATED_ROOT', default=None)


@contextmanager
def delegated_lock(root):
    resolved = Path(root).resolve()
    path = Path(root) / '.runtime/un-mutation.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('another UN mutation is running in this checkout') from error
        token = _DELEGATED_ROOT.set(resolved)
        try:
            yield
        finally:
            _DELEGATED_ROOT.reset(token)
            fcntl.flock(stream, fcntl.LOCK_UN)


def locked(function):
    @wraps(function)
    def wrapper(root, *args, **kwargs):
        root = Path(root)
        if _DELEGATED_ROOT.get() == root.resolve():
            return function(root, *args, **kwargs)
        path = root / '.runtime/un-mutation.lock'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError('another UN mutation is running in this checkout') from error
            try:
                return function(root, *args, **kwargs)
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
    return wrapper
