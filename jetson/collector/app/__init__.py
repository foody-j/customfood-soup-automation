"""Jetson 수집 서비스 (플랜 3단계).

Pi 관리 서버(`pi-server/`)의 상대편. 계약은 `docs/pi-jetson-api.md`.
이 패키지는 Pi 쪽 코드와 **독립 배포**되며, 접점은 계약 문서뿐이다.
"""

SERVICE_NAME = "jetson-collector"
VERSION = "0.1.0"
#: 저장 레이아웃(session.json / index.jsonl / manifest.json)의 스키마 버전.
#: 필드 의미가 바뀌면 올린다. 분석 코드는 이 값으로 호환성을 판단한다.
SCHEMA_VERSION = "1.0"
