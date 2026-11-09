# SPDX-License-Identifier: Apache-2.0
import hashlib
import os
from pathlib import Path

import pytest

from oaken_plugins.formatter_rfc822 import Rfc822Config, Rfc822Formatter
from oaken_plugins.formatter_rfc822.quarantine import store

PDF = (b"From: a@b.example\r\nSubject: hi\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n"
       b"--B\r\nContent-Type: text/plain\r\n\r\nsee attached\r\n"
       b"--B\r\nContent-Type: application/pdf; name=jd.pdf\r\nContent-Disposition: attachment; filename=jd.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\nSGVsbG8=\r\n--B--\r\n")  # fmt: skip
SHA = hashlib.sha256(b"Hello").hexdigest()


def formatter(**settings: object) -> Rfc822Formatter:
    f = Rfc822Formatter()
    f.config = Rfc822Config(**settings)
    return f


async def test_stripping_is_off_by_default_so_nothing_is_written(tmp_path: Path) -> None:
    parts = await formatter(quarantine_dir=tmp_path / "q").split(PDF)
    assert parts["attachment-1"].content == {"filename": "jd.pdf", "content_type": "application/pdf", "size": 5}
    assert not (tmp_path / "q").exists()


async def test_a_stripped_attachment_is_kept_by_its_sha256_with_no_permissions_and_the_part_points_to_it(tmp_path: Path) -> None:
    parts = await formatter(strip_attachments=True, quarantine_dir=tmp_path / "q").split(PDF)
    assert parts["attachment-1"].content == {"filename": "jd.pdf", "content_type": "application/pdf", "size": 5, "sha256": SHA}
    file = tmp_path / "q" / SHA[:2] / f"{SHA}.quarantined"
    assert file.stat().st_mode & 0o777 == 0
    file.chmod(0o400)
    assert file.read_bytes() == b"Hello"


async def test_the_same_bytes_again_write_one_file_and_leave_no_temporary_file(tmp_path: Path) -> None:
    f = formatter(strip_attachments=True, quarantine_dir=tmp_path / "q")
    await f.split(PDF)
    await f.split(PDF)
    assert [p.name for p in (tmp_path / "q").rglob("*") if p.is_file()] == [f"{SHA}.quarantined"]


def test_store_writes_the_whole_content_or_nothing(tmp_path: Path) -> None:
    assert store(tmp_path, b"") == hashlib.sha256(b"").hexdigest()  # an empty attachment is a file too
    blocked = tmp_path / "x"
    blocked.write_text("a file where the folder should go")
    with pytest.raises(OSError):
        store(blocked, b"data")
    assert not [p for p in tmp_path.rglob(".*.tmp")]
