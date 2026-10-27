"""Fail-fast OS lock for UN mutations in one local checkout (Linux/macOS)."""
import fcntl
from functools import wraps
from pathlib import Path


def locked(function):
    @wraps(function)
    def wrapper(root, *args, **kwargs):
        root = Path(root)
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
