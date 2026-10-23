import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_operation(*, skip=False):
    if skip:
        yield
        return
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts.mkdir(exist_ok=True)
    with (artifacts / "operation.lock").open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Another lab benchmark, drill operation or drain is running"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)
