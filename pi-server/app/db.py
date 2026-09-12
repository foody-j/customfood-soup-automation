"""SQLite 저장소 — 실험(세션) · 사건(이벤트) · 설정 · Pi 운영 지표.

API는 **동기**다. 호출당 작업량이 작고(로컬 파일, 수십 행) WAL 모드라 async
핸들러에서 직접 불러도 이벤트 루프를 의미 있게 막지 않는다.

브라우저가 꺼져도 감시가 계속돼야 하므로(플랜 §4) 상태 이력은 메모리가 아니라
여기(디스크)에 남긴다. 재부팅 후에도 "마지막에 무슨 일이 있었는지" 읽을 수 있다.

보관 원칙 (중요)
----------------
- **실험 기록은 일반 로그 정리로 지우지 않는다.** `sessions`는 절대 자동 삭제하지
  않고, `events` 중 **session_id가 붙은 행(= 실험 기록)도 보존 정책에서 제외**한다.
  정리 대상은 세션과 무관한 운영 로그(링크 상태·서버 기동 등)와 `host_metrics`뿐이다.
- 원본 영상·프레임별 전체 기록은 **Jetson에 남기고 이 DB로 끌어오지 않는다**(D-006).
  Pi는 세션 단위 요약만 보관한다.
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

#: 저장 스키마 버전. 내보내기 파일에도 같이 실어 나중에 해석할 수 있게 한다.
SCHEMA_VERSION = 2

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS host_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    boot_id         TEXT NOT NULL,
    cpu_percent     REAL,
    load1           REAL,
    mem_used_bytes  INTEGER,
    mem_total_bytes INTEGER,
    temp_c          REAL,
    disk_free_bytes INTEGER,
    disk_total_bytes INTEGER,
    uptime_sec      REAL
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON host_metrics (ts DESC);
"""

#: v1 → v2에서 추가된 열. 기존 DB(실기기에 이미 데이터가 있다)를 지우지 않고 확장한다.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "events": {
        # 전달받은 사건의 **실제 발생 시각**. Pi 수신 시각(ts)과 구분한다.
        # 모르면 NULL — ts를 복사해 넣지 않는다(발생 시각을 지어내지 않기 위해).
        "occurred_at": "TEXT",
        # 같은 명령에 속한 사건들을 잇는 키 (요청 → 응답 → 확인)
        "request_id": "TEXT",
        # 이 사건이 어디서 생겼나: pi | jetson | manual
        "origin": "TEXT",
    },
    "sessions": {
        "project_id": "TEXT",
        "device_id": "TEXT",
        "ingredients": "TEXT",   # 재료 (자유 텍스트)
        "conditions": "TEXT",    # 실험 조건 (자유 텍스트)
        "jetson_summary": "TEXT",  # 종료 시점의 Jetson 저장 결과 요약(JSON)
        "schema_version": "INTEGER",
    },
}

#: 운영 이력을 서비스 로그에도 흘려보내는 로거.
#: DB는 "조회·내보내기", journal은 "무슨 일이 있었나를 시간순으로 읽기" 용도다.
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
            self._conn.executescript(BASE_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """열 추가 방식으로만 확장한다 — 기존 실험 기록을 절대 지우지 않는다."""
        for table, columns in ADDED_COLUMNS.items():
            existing = {
                row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for column, decl in columns.items():
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        return int(row["value"]) if row else SCHEMA_VERSION

    # ── 이벤트(사건) ────────────────────────────────────────────────────────
    def log_event(
        self,
        *,
        level: EventLevel | str,
        source: str,
        code: str,
        message: str,
        session_id: str | None = None,
        detail: dict[str, Any] | None = None,
        occurred_at: str | None = None,
        request_id: str | None = None,
        origin: str = "pi",
    ) -> dict[str, Any]:
        """사건 1건 기록.

        `ts`는 **Pi가 기록한 시각**, `occurred_at`은 **실제 발생 시각**이다.
        Jetson에서 전달받은 사건이나 사람이 뒤늦게 입력한 사건은 둘이 다르다.
        모르면 `occurred_at`은 NULL로 둔다 — ts를 복사해 발생 시각인 척하지 않는다.
        """
        level_value = level.value if isinstance(level, EventLevel) else str(level)
        row = {
            "ts": utcnow_iso(),
            "level": level_value,
            "source": source,
            "code": code,
            "message": message,
            "session_id": session_id,
            "detail": json.dumps(detail, ensure_ascii=False) if detail else None,
            "occurred_at": occurred_at,
            "request_id": request_id,
            "origin": origin,
        }
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, level, source, code, message, session_id, detail,"
                " occurred_at, request_id, origin)"
                " VALUES (:ts, :level, :source, :code, :message, :session_id, :detail,"
                " :occurred_at, :request_id, :origin)",
                row,
            )
            self._conn.commit()
            event_id = int(cur.lastrowid or 0)

        # 같은 사건을 서비스 로그에도 남긴다. 맨 앞의 `#<id>`가 DB 행과 journal 줄을
        # 잇는 열쇠다 — 나중에 "이 로그가 어느 이벤트냐"를 정확히 맞출 수 있다.
        event_log.log(
            _LEVEL_TO_LOGGING.get(level_value, logging.INFO),
            "[#%s %s] %s%s%s%s",
            event_id,
            code,
            message,
            f" (session={session_id})" if session_id else "",
            f" (req={request_id})" if request_id else "",
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
        request_id: str | None = None,
        origin: str | None = None,
        code: str | None = None,
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
        if request_id:
            sql += " AND request_id = ?"
            params.append(request_id)
        if origin:
            sql += " AND origin = ?"
            params.append(origin)
        if code:
            sql += " AND code = ?"
            params.append(code)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [{**dict(row), "detail": _loads(row["detail"]) or None} for row in rows]

    def prune_events(self, keep: int, older_than_days: int | None = None) -> int:
        """**운영 로그만** 정리한다 — 건수와 기간을 둘 다 적용.

        `session_id`가 붙은 행은 **실험 기록**이므로 건드리지 않는다. 건수만 제한하면
        조용한 기간엔 몇 년 전 기록이 남고, 기간만 제한하면 장애가 폭주할 때 디스크가
        부푼다. 둘 중 먼저 걸리는 쪽이 이긴다. 반환값은 지운 행 수.
        """
        removed = 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM events WHERE session_id IS NULL AND id <= ("
                "  SELECT id FROM events WHERE session_id IS NULL"
                "  ORDER BY id DESC LIMIT 1 OFFSET ?"
                ")",
                (max(1, keep),),
            )
            removed += cur.rowcount or 0
            if older_than_days and older_than_days > 0:
                cutoff = iso(utcnow() - timedelta(days=older_than_days))
                cur = self._conn.execute(
                    "DELETE FROM events WHERE session_id IS NULL AND ts < ?", (cutoff,)
                )
                removed += cur.rowcount or 0
            self._conn.commit()
        return removed

    def export_events(
        self, *, days: int | None = None, session_id: str | None = None, limit: int = 100_000
    ) -> list[dict[str, Any]]:
        """내보내기용 조회 — **시간 오름차순**(보고서에 그대로 붙일 수 있게)."""
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if days and days > 0:
            sql += " AND ts >= ?"
            params.append(iso(utcnow() - timedelta(days=days)))
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(max(1, limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [{**dict(row), "detail": _loads(row["detail"]) or None} for row in rows]

    # ── 세션(실험) ──────────────────────────────────────────────────────────
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
        project_id: str | None = None,
        device_id: str | None = None,
        ingredients: str | None = None,
        conditions: str | None = None,
    ) -> dict[str, Any]:
        state_value = state.value if isinstance(state, CaptureState) else str(state)
        now = utcnow_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (session_id, name, note, state, started_at, config,"
                " jetson_ack, source, updated_at, project_id, device_id, ingredients,"
                " conditions, schema_version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    project_id,
                    device_id,
                    ingredients,
                    conditions,
                    SCHEMA_VERSION,
                ),
            )
            self._conn.commit()
        return self.get_session(session_id)  # type: ignore[return-value]

    def update_session(self, session_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "name", "note", "state", "stopped_at", "config", "jetson_ack", "source",
            "ingredients", "conditions", "jetson_summary", "project_id", "device_id",
        }
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise KeyError(f"세션에 없는 필드: {key}")
            if key == "state" and isinstance(value, CaptureState):
                value = value.value
            if key in ("config", "jetson_summary") and not isinstance(value, (str, type(None))):
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

    def list_sessions(self, limit: int = 50, project_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sessions"
        params: list[Any] = []
        if project_id:
            sql += " WHERE project_id = ?"
            params.append(project_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._session_row(row) for row in rows if row is not None]  # type: ignore[misc]

    @staticmethod
    def _session_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["config"] = _loads(data.get("config"))
        data["jetson_summary"] = _loads(data.get("jetson_summary")) or None
        data["jetson_ack"] = bool(data.get("jetson_ack"))
        data.pop("updated_at", None)
        return data

    # ── Pi 운영 지표 ────────────────────────────────────────────────────────
    def record_metric(self, *, boot_id: str, sample: dict[str, Any]) -> None:
        """측정 1회 기록. **못 읽은 값은 NULL**로 남긴다(0으로 채우지 않는다)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO host_metrics (ts, boot_id, cpu_percent, load1, mem_used_bytes,"
                " mem_total_bytes, temp_c, disk_free_bytes, disk_total_bytes, uptime_sec)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    utcnow_iso(),
                    boot_id,
                    sample.get("cpu_percent"),
                    sample.get("load1"),
                    sample.get("mem_used_bytes"),
                    sample.get("mem_total_bytes"),
                    sample.get("temp_c"),
                    sample.get("disk_free_bytes"),
                    sample.get("disk_total_bytes"),
                    sample.get("uptime_sec"),
                ),
            )
            self._conn.commit()

    def list_metrics(self, *, limit: int = 200, since_days: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM host_metrics"
        params: list[Any] = []
        if since_days and since_days > 0:
            sql += " WHERE ts >= ?"
            params.append(iso(utcnow() - timedelta(days=since_days)))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 5000)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def latest_metric(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM host_metrics ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def prune_metrics(self, keep: int, older_than_days: int | None = None) -> int:
        """운영 지표는 용량이 커지므로 건수·기간 둘 다 제한한다(실험 기록과 무관)."""
        removed = 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM host_metrics WHERE id <= ("
                "  SELECT id FROM host_metrics ORDER BY id DESC LIMIT 1 OFFSET ?"
                ")",
                (max(1, keep),),
            )
            removed += cur.rowcount or 0
            if older_than_days and older_than_days > 0:
                cutoff = iso(utcnow() - timedelta(days=older_than_days))
                cur = self._conn.execute("DELETE FROM host_metrics WHERE ts < ?", (cutoff,))
                removed += cur.rowcount or 0
            self._conn.commit()
        return removed

    # ── 설정 ───────────────────────────────────────────────────────────────
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

    # ── 백업 ───────────────────────────────────────────────────────────────
    def backup_to(self, dest: Path | str) -> Path:
        """**온라인 백업 API**로 일관된 스냅샷을 만든다.

        실행 중인 `.db` 파일을 그냥 복사하면 WAL과 어긋나 깨진 사본이 나온다.
        `sqlite3.Connection.backup()`은 쓰기와 충돌하지 않는 일관된 사본을 보장한다.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self._conn.backup(target)
            finally:
                target.close()
        return dest
