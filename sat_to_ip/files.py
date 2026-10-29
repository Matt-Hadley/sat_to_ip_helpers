"""Atomic file writes, so a DVR reading the file never sees half of it."""

import os
import tempfile
from pathlib import Path


def write_atomic(path: Path, data: bytes | str) -> None:
    """Write to a temp file in the same directory, then rename over the target.

    A symlink is followed (the file it points to is replaced), and an existing file keeps its permissions.
    """
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        # mkstemp makes it 0600; new files get 0644 as DVR containers often run as another user
        os.chmod(tmp, path.stat().st_mode & 0o7777 if path.exists() else 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
