# SPDX-License-Identifier: Apache-2.0
"""Stores one record per CVE id, joining NVD rows and CISA KEV rows

The ingestor decides what an item *is*: a CVE, identified by its id. An NVD row and a KEV row for the same id are the same record,
whichever arrives first, and a repeat of either replaces that source's row. When KEV touches a record it is labelled ``exploited=kev``;
nothing here clears it (a later NVD row cannot un-exploit a CVE).

It reads parts made by the ``cve`` formatter (``format = "cve"`` for NVD rows, ``"kev"`` for CISA rows). A row with no ``cve`` part is
refused, so it waits in Intake's failed jobs. It replaces the built-in ``items`` ingestor, which only runs while no other is installed.
"""

import json
from pathlib import Path
from typing import Any, ClassVar

from oaken_core.clock import fmt_ts
from oaken_core.errors import NotFound, PluginError
from oaken_core.ids import SubjectId, new_subject_id
from oaken_core.labels import BaseLabel
from oaken_core.plugins.base import BaseIngestor
from oaken_core.plugins.contracts import PluginContext

EXPLOITED = BaseLabel.new("exploited", "CISA KEV lists this CVE as exploited in the wild", ("kev",))


class CveIngestor(BaseIngestor):
    """Stores one record per CVE id, NVD and KEV joined

    One CVE record, with the NVD row and the CISA KEV row merged on the CVE id and an `exploited` label once KEV lists it."""
    tags: ClassVar[tuple[str, ...]] = ("signal",)  # what it is for
    icon: ClassVar[str] = "bi-bug"  # CVE

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    migrations_dir: ClassVar[Path | None] = Path(__file__).parent / "migrations"
    setup_help = ('Replaces the built-in items ingestor. Submit NVD rows with `format = "cve"` and CISA KEV rows with `format = "kev"`. '
                  "Find the exploited ones with `oaken label find exploited kev`.")
    config_example = "oaken plugin add cve --kind ingestor"

    def labels(self) -> list[BaseLabel]:
        return [EXPLOITED]

    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)
        if ctx.db is None:
            raise PluginError("cve: no database to store into")

    async def ingest(self, item: Any) -> SubjectId:
        db = self.ctx.db
        assert db is not None
        cve = str(item.parts["cve"].content).strip().upper() if "cve" in item.parts else ""
        if not cve.startswith("CVE-"):
            raise PluginError('not a CVE item: it has no `cve` part. Set format = "cve" (NVD) or "kev" (CISA) where it is submitted, then requeue it')
        row = {n: p.content for n, p in item.parts.items()}
        is_kev = "date_added" in row  # only KEV rows carry the date CISA added it
        now = fmt_ts(self.ctx.clock.now())
        seen = await db.fetchone("SELECT subject_id FROM cve_records WHERE cve = ?", (cve,))
        subject = SubjectId(seen["subject_id"]) if seen else new_subject_id()
        col = "kev_json" if is_kev else "nvd_json"
        if seen:
            await db.execute(f"UPDATE cve_records SET {col} = ?, updated_at = ? WHERE subject_id = ?", (json.dumps(row), now, subject))  # noqa: S608  # col is one of two constants
        else:
            await db.execute(f"INSERT INTO cve_records (subject_id, cve, {col}, created_at, updated_at) VALUES (?,?,?,?,?)", (subject, cve, json.dumps(row), now, now))  # noqa: S608
        if is_kev and self.ctx.labels is not None:
            await self.ctx.labels.set(subject, "exploited", "kev")
        return subject

    async def read(self, subject_id: str, part: str | None = None) -> dict[str, Any]:
        db = self.ctx.db
        assert db is not None
        r = await db.fetchone("SELECT cve, nvd_json, kev_json FROM cve_records WHERE subject_id = ?", (subject_id,))
        if r is None:
            raise NotFound(f"no CVE record {subject_id!r} in the cve ingestor")
        parts: dict[str, Any] = {"cve": r["cve"]}
        if r["nvd_json"]:
            parts["nvd"] = json.loads(r["nvd_json"])
        if r["kev_json"]:
            parts["kev"] = json.loads(r["kev_json"])
        if part is not None:
            if part not in parts:
                raise NotFound(f"CVE record {subject_id!r} has no part {part!r} (it has: {', '.join(parts)})")
            return {part: parts[part]}
        return parts
