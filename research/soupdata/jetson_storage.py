"""Jetson 수집기 저장 모듈을 **독립 이름**으로 불러온다.

`jetson/collector/app`은 패키지 이름이 `app`이라 그대로 import하면 다른 `app`(pi-server)과 섞일 수 있다.
여기서는 경로로 패키지를 `_jetson_collector_app`이라는 이름에 올려 상대 import가 그대로 동작하게 한다.
수집기 런타임 의존성 중 필요한 것은 numpy(필수)·lz4(lz4 레코드)뿐이고 cv2는 선택이다.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "jetson" / "collector" / "app"
_PKG = "_jetson_collector_app"


def _load_pkg() -> ModuleType:
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    spec = importlib.util.spec_from_file_location(
        _PKG, APP_DIR / "__init__.py", submodule_search_locations=[str(APP_DIR)])
    if spec is None or spec.loader is None:
        raise ImportError(f"Jetson 수집기 패키지를 찾을 수 없음: {APP_DIR}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_PKG] = mod
    spec.loader.exec_module(mod)
    return mod


def storage() -> ModuleType:
    """`jetson/collector/app/storage.py` 모듈."""
    _load_pkg()
    return importlib.import_module(f"{_PKG}.storage")


def schema_version() -> int:
    return _load_pkg().SCHEMA_VERSION


def unpack_record(buf: bytes, index_line: dict):
    return storage().unpack_record(buf, index_line)
