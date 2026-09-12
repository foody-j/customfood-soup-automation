"""SQLite 저장소 — 실험 세션 · 이벤트(오류·조작 이력) · 실험 설정.

API는 **동기**다. 호출당 작업량이 작고(로컬 파일, 수십 행) WAL 모드라 async
핸들러에서 직접 불러도 이벤트 루프를 의미 있게 막지 않는다. 무거운 집계 조회가
생기면 그때 `asyncio.to_thread`로 감쌀 것.

브라우저가 꺼져도 감시가 계속돼야 하므로(플랜 §4) 상태 이력은 메모리가 아니라
여기(디스크)에 남긴다. 재부팅 후에도 "마지막에 무슨 일이 있었는지" 읽을 수 있다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

from .models import ACTIVE_SESSION_STATES, CaptureState, EventLevel
from .util import iso, utcnow, utcnow_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    level      TEXT NOT NULL,
    source     TEXT NOT NULL,
    code       TEXT NOT NULL,
    message    TEXT NOT NULL,
    session_id TEXT,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_id_desc ON events (id DESC);
CREATE INDEX IF NOT EXISTS idx_events_session ON events (session_id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    note       TEXT,
    state      TEXT NOT NULL,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    config     TEXT NOT NULL DEFAULT '{}',
    jetson_ack INTEGER NOT NULL DEFAULT 0,
    source     TEXT NOT NULL DEFAULT 'pi',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions (started_at DESC);

CREATE TABLE IF NOT EXISTS app_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


#: 운영 이력을 서비스 로그에도 흘려보내는 로거.
#: DB는 "조회·내보내기", journal은 "무슨 일이 있었나를 시간순으로 읽기" 용도다.
#: 둘 중 하나만 있으면 곤란하다 — DB는 journalctl로 못 보고, journal은 회전돼 사라진다.
event_log = logging.getLogger("app.events")

_LEVEL_TO_LOGGING = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── 이벤트 ─────────────────────────────────────────────────────────────
    def log_event(
        self,
        *,
        level: EventLevel | str,
        source: str,
        code: str,
        message: str,
        session_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        level_value = level.value if isinstance(level, EventLevel) else str(level)
        row = {
            "ts": utcnow_iso(),
            "level": level_value,
            "source": source,
            "code": code,
            "message": message,
            "session_id": session_id,
            "detail": json.dumps(detail, ensure_ascii=False) if detail else None,
        }
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, level, source, code, message, session_id, detail)"
                " VALUES (:ts, :level, :source, :code, :message, :session_id, :detail)",
                row,
            )
            self._conn.commit()
            event_id = int(cur.lastrowid or 0)

        # 같은 사건을 서비스 로그에도 남긴다. 맨 앞의 `#<id>`가 DB 행과 journal 줄을
        # 잇는 열쇠다 — 나중에 "이 로그가 어느 이벤트냐"를 정확히 맞출 수 있다.
        event_log.log(
            _LEVEL_TO_LOGGING.get(level_value, logging.INFO),
            "[#%s %s] %s%s%s",
            event_id,
            code,
            message,
            f" (session={session_id})" if session_id else "",
            f" detail={json.dumps(detail, ensure_ascii=False)[:200]}" if detail else "",
        )
        return {"id": event_id, **row, "detail": detail}

    def list_events(
        self,
        *,
        limit: int = 100,
        after_id: int | None = None,
        level: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if after_id is not None:
            sql += " AND id > ?"
            params.append(after_id)
        if level:
            sql += " AND level = ?"
            params.append(level)
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {**dict(row), "detail": _loads(row["detail"]) or None} for row in rows
        ]

    def prune_events(self, keep: int, older_than_days: int | None = None) -> int:
        """오래된 이벤트 정리 — **건수와 기간을 둘 다** 적용한다.

        건수만 제한하면 조용한 기간엔 몇 년 전 기록이 남고, 기간만 제한하면 장애가
        폭주할 때 디스크가 부푼다. 둘 중 먼저 걸리는 쪽이 이긴다.
        반환값은 지운 행 수.
        """
        removed = 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM events WHERE id <= ("
                "  SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET ?"
                ")",
                (max(1, keep),),
            )
            removed += cur.rowcount or 0
            if older_than_days and older_than_days > 0:
                cutoff = iso(utcnow() - timedelta(days=older_than_days))
                cur = self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
                removed += cur.rowcount or 0
            self._conn.commit()
        return removed

    def export_events(self, *, days: int | None = None, limit: int = 100_000) -> list[dict[str, Any]]:
        """내보내기용 조회 — **시간 오름차순**(보고서에 그대로 붙일 수 있게)."""
        sql = "SELECT * FROM events"
        params: list[Any] = []
        if days and days > 0:
            sql += " WHERE ts >= ?"
            params.append(iso(utcnow() - timedelta(days=days)))
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(max(1, limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [{**dict(row), "detail": _loads(row["detail"]) or None} for row in rows]

    # ── 세션 ───────────────────────────────────────────────────────────────
    def create_session(
        self,
        *,
        session_id: str,
        name: str,
        note: str | None,
        state: CaptureState | str,
        started_at: str,
        config: dict[str, Any] | None = None,
        jetson_ack: bool = False,
        source: str = "pi",
    ) -> dict[str, Any]:
        state_value = state.value if isinstance(state, CaptureState) else str(state)
        now = utcnow_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (session_id, name, note, state, started_at, config,"
                " jetson_ack, source, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    name,
                    note,
                    state_value,
                    started_at,
                    json.dumps(config or {}, ensure_ascii=False),
                    1 if jetson_ack else 0,
                    source,
                    now,
                ),
            )
            self._conn.commit()
        return self.get_session(session_id)  # type: ignore[return-value]

    def update_session(self, session_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"name", "note", "state", "stopped_at", "config", "jetson_ack", "source"}
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise KeyError(f"세션에 없는 필드: {key}")
            if key == "state" and isinstance(value, CaptureState):
                value = value.value
            if key == "config":
                value = json.dumps(value or {}, ensure_ascii=False)
            if key == "jetson_ack":
                value = 1 if value else 0
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return self.get_session(session_id)
        sets.append("updated_at = ?")
        params.extend([utcnow_iso(), session_id])
        with self._lock:
            self._conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = ?", params
            )
            self._conn.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return self._session_row(row)

    def active_session(self) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in ACTIVE_SESSION_STATES)
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM sessions WHERE state IN ({placeholders})"
                " ORDER BY started_at DESC LIMIT 1",
                ACTIVE_SESSION_STATES,
            ).fetchone()
        return self._session_row(row)

    def last_session(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return self._session_row(row)

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._session_row(row) for row in rows if row is not None]  # type: ignore[misc]

    @staticmethod
    def _session_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["config"] = _loads(data.get("config"))
        data["jetson_ack"] = bool(data.get("jetson_ack"))
        data.pop("updated_at", None)
        return data

    # ── 실험 설정 ──────────────────────────────────────────────────────────
    def get_config(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM app_config").fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                out[row["key"]] = row["value"]
        return out

    def set_config(self, values: dict[str, Any]) -> dict[str, Any]:
        now = utcnow_iso()
        items: Iterable[tuple[str, str, str]] = [
            (key, json.dumps(value, ensure_ascii=False), now) for key, value in values.items()
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                items,
            )
            self._conn.commit()
        return self.get_config()
