"""공통 식별자 — 모든 기록에 함께 실린다.

`schema_version` · `project_id` · `session_id` · `device_id`가 기록·내보내기의
공통 키다. 다른 연구과제의 기록과 섞이지 않게 하려는 것이므로, **기록 시점의 값을
세션에 박제**한다(나중에 project_id를 바꿔도 과거 실험의 소속은 변하지 않는다).

값의 우선순위: DB 설정(`app_config`) > 환경변수 기본값. 화면에서 바꿀 수 있게
DB를 우선한다.
"""

from __future__ import annotations

import socket

from .config import Settings
from .db import Database
from .models import EventLevel, IdentityInfo


class Identity:
    def __init__(self, settings: Settings, db: Database, boot_id: str) -> None:
        self._settings = settings
        self._db = db
        self.boot_id = boot_id

    @property
    def project_id(self) -> str:
        return str(self._db.get_config().get("project_id") or self._settings.project_id)

    @property
    def device_id(self) -> str:
        # 설정이 비어 있으면 호스트명으로 채운다. 빈 문자열이 기록에 박히면
        # 나중에 어느 장치의 기록인지 알 수 없게 된다.
        return str(
            self._db.get_config().get("device_id")
            or self._settings.device_id
            or socket.gethostname()
        )

    def info(self) -> IdentityInfo:
        return IdentityInfo(
            schema_version=self._db.schema_version,
            project_id=self.project_id,
            device_id=self.device_id,
            boot_id=self.boot_id,
        )

    def update(self, *, project_id: str | None = None, device_id: str | None = None) -> IdentityInfo:
        before = {"project_id": self.project_id, "device_id": self.device_id}
        values = {k: v for k, v in (("project_id", project_id), ("device_id", device_id)) if v}
        if values:
            self._db.set_config(values)
            self._db.log_event(
                level=EventLevel.INFO,
                source="user",
                code="identity.updated",
                message="과제·장치 식별자 변경",
                detail={"before": before, "after": {**before, **values}},
            )
        return self.info()
