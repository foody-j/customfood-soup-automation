"""Fedora 연구 데이터 도구 — Jetson 세션 원본 읽기·검증·카탈로그.

원본 형식의 단일 출처는 Jetson 수집기(`jetson/collector/app/storage.py`)다. 배열 레코드 해제는
그 모듈의 `unpack_record`를 그대로 불러 쓴다(`jetson_storage.py`). 형식을 여기서 다시 구현하지 않는다.
"""

from .session import Session, StreamRef
from .verify import verify_session

__all__ = ["Session", "StreamRef", "verify_session"]
