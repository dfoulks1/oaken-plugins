# SPDX-License-Identifier: Apache-2.0
"""Where a stripped attachment is kept: one file per distinct content, named by its sha256, with no permissions at all.

Content-addressed, so storing the same bytes twice (a retry, a re-sync, the same file in two messages) writes one file. Nothing opens these
files here; a scanner or a release step would, later, by turning a mode back on for the length of the read.
"""

import hashlib
import os
from pathlib import Path


def store(base: Path, data: bytes) -> str:
    """Write ``data`` under ``base`` as <sha[:2]>/<sha>.quarantined, mode 0000, and return its sha256. Already there: nothing is written."""
    sha = hashlib.sha256(data).hexdigest()
    path = base / sha[:2] / f"{sha}.quarantined"
    if path.exists():
        return sha
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.parent / f".{sha}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o000)
        os.replace(tmp, path)  # whole or not at all
    finally:
        tmp.unlink(missing_ok=True)
    return sha
