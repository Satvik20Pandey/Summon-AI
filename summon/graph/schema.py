"""FalkorDB connection and schema. One graph per developer: ``summon_<developer_id>``.

Indexes are created idempotently; FalkorDB raises when an index already exists, so
each statement is wrapped individually.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from falkordb import FalkorDB, Graph

from summon.config import get_settings

log = logging.getLogger(__name__)

# Range (exact/ordered) indexes: label -> properties
RANGE_INDEXES: dict[str, list[str]] = {
    "Task": ["id", "done_ts"],
    "Call": ["id", "provider_call_id", "ts", "status"],
    "Instruction": ["id", "status"],
    "Decision": ["id", "topic"],
    "Preference": ["key"],
    "Session": ["id"],
    "Question": ["id"],
    "Event": ["id"],
    "Project": ["id"],
    "Developer": ["id"],
    "Agent": ["name"],
}

# Full-text indexes: label -> property (RediSearch under the hood)
FULLTEXT_INDEXES: dict[str, str] = {
    "Decision": "text",
    "Instruction": "text",
    "Task": "summary",
}

_SAFE_ID = re.compile(r"[^a-z0-9_]+")


def graph_name(developer_id: str) -> str:
    return "summon_" + _SAFE_ID.sub("_", developer_id.lower())


@lru_cache
def get_db() -> FalkorDB:
    s = get_settings()
    return FalkorDB(
        host=s.falkordb_host,
        port=s.falkordb_port,
        username=s.falkordb_username or None,
        password=s.falkordb_password or None,
        ssl=s.falkordb_tls,
    )


def get_graph(developer_id: str, ensure: bool = True) -> Graph:
    g = get_db().select_graph(graph_name(developer_id))
    if ensure:
        ensure_schema(g)
    return g


_ensured: set[str] = set()


def ensure_schema(g: Graph) -> None:
    """Create every index once per process. Safe to call repeatedly."""
    if g.name in _ensured:
        return
    for label, props in RANGE_INDEXES.items():
        for prop in props:
            _try(g, f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
    for label, prop in FULLTEXT_INDEXES.items():
        _try(g, f"CALL db.idx.fulltext.createNodeIndex('{label}', '{prop}')")
    _ensured.add(g.name)


def _try(g: Graph, cypher: str) -> None:
    try:
        g.query(cypher)
    except Exception as exc:  # noqa: BLE001 - "already indexed" is the normal case
        msg = str(exc).lower()
        if "already" not in msg and "exists" not in msg:
            log.warning("schema statement failed: %s -> %s", cypher, exc)


def ping() -> bool:
    try:
        get_db().select_graph("summon_ping").query("RETURN 1")
        return True
    except Exception:  # noqa: BLE001
        return False
