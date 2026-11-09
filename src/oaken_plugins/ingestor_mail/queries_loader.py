# SPDX-License-Identifier: Apache-2.0
"""Pick the generated query modules that match the active store's engine.

The store says what it is (``db.engine``: ``"sqlite"``, ``"postgresql"``, ...). We load the sqlc
output built for that engine from this plugin's own ``dbgen/<engine>/`` (resolved relative to this
package, never by absolute name). Nothing here names a specific engine or the plugin's own location,
so a new sqlc-supported store drops in by adding its ``dbgen/<engine>/`` folder (an sqlc block) and
its store plugin: no change to this file or the ingestor. The door is open to any engine sqlc supports.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType

from oaken_core.errors import PluginError


@dataclass(frozen=True)
class Queries:
    """The generated query modules for one engine."""

    messages: ModuleType
    attachments: ModuleType


_cache: dict[str, Queries] = {}


def queries_for(engine: str) -> Queries:
    """The generated query modules for ``engine``. Raises ``PluginError`` with a plain message if
    no generated code is shipped for it (the store's engine has no ``dbgen/<engine>/``)."""
    cached = _cache.get(engine)
    if cached is not None:
        return cached
    base = f"{__package__}.dbgen.{engine}"  # relative to this plugin, whatever it is named or wherever it lives
    try:
        q = Queries(
            messages=importlib.import_module(f"{base}.messages"),
            attachments=importlib.import_module(f"{base}.attachments"),
        )
    except ModuleNotFoundError as exc:
        raise PluginError(
            f"mail: no generated queries for the {engine!r} store engine "
            f"(expected {base}); run sqlc for that engine, or use a store the mail ingestor ships queries for"
        ) from exc
    _cache[engine] = q
    return q
