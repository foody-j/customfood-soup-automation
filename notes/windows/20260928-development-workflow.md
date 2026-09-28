# 2026-09-28 — Pi·Jetson 개발 경로와 작업 시작 동기화

- 사용자 요청: Pi와 Jetson의 Claude Code 담당 구간을 나누고 최신 코드를 자동으로 받게 한다.
- Pi는 `pi-server/`, Jetson은 `jetson/`·`cam-adaptor/`, 역할별 docs/notes를 담당한다.
  공통 계약·중앙 노트는 통합 담당으로 모아 같은 파일 동시 편집과 D 번호 충돌을 줄인다.
- 운영 체크아웃에 자동 pull 대신 최신 origin/main에서 역할별 worktree를 생성하는 Python 런처를 추가했다.
  범위 검사는 브랜치 커밋·미커밋·비추적 파일을 포함한다. 접근 제어/강제 Git 훅은 아니다.
- 기존 `.claude/settings.json`의 push/merge deny와 무인 작업 제한은 유지한다. 서비스 재시작·자동 배포는 수행하지 않는다.
- D-036은 아직 원격에 올라오지 않은 Pi 작업에서 사용됐다고 인계받았으므로 새 중앙 D 번호를 선점하지 않았다.
- 장비 현장 설치와 Claude 실제 실행은 별도 적용이 필요하다. 로컬 임시 Git 저장소로 최신 main 반영과 원래 작업 보존 등
  7개 테스트를 실행해 모두 통과했다(`python -m unittest discover -s tools/tests -v`).
