# fedora/integration-role — Fedora PC를 개발·통합 서버로 지정 (2026-10-01)

## 배경
- 사용자 요청: 기존 Windows의 검토·통합 역할을 이 Fedora PC로 옮긴다.
- Pi는 `pi-server`·Pi 전용 코드, Jetson은 센서 수집·저장과 Jetson 전용 코드를 개발한다. 공통 API·스키마·설계 문서는 어느 쪽이든 수정한다.
- Fedora는 브랜치 검토, 통합 테스트, 병합 준비, 연구 데이터 관리·학습을 맡는다. 모든 변경의 필수 경유지는 아니다.

## 시작 시 상태 확인 (`git fetch origin --prune`, 2026-10-01)
- 로컬: `main` = `origin/main` = `dc46d35`, 미커밋 변경·stash·추가 worktree 없음.
- 원격 브랜치 5개(`ai/thermal-first-read`, `pi-first-cook-trial`, `pi/live-view`, `pi/live-view-verify`, `pi/ui-tabs`)는
  **모두 main에 병합 완료**(main..브랜치 커밋 0). 원격에 미통합 작업 없음. Pi·Jetson의 미푸시 로컬 작업은 Git으로 알 수 없다.
- 열린 후속 확인 거리: Gemini color가 depth·IR 절반 속도로 수신(2026-09-28 dev-log, Jetson 확인 거리).

## 변경
- `CLAUDE.md`, `AGENTS.md`, `docs/development-workflow.md`: Windows 언급을 Fedora 개발·통합 서버 역할로 정리,
  Fedora 절(맡는 일·필수 관문 아님·운영 장비 미변경·기록 위치·검토 절차) 추가.
- `tools/dev_session.py`: Claude 시작 프롬프트의 "Windows is not a required integration gate" 문구를 Fedora로 교체.
  역할(pi/jetson)·경로 검사는 바꾸지 않았다.
- 과거 기록(`notes/windows/`, 중앙 노트의 Windows 언급)은 수정하지 않았다.

## 검증
- `python3 -m unittest discover -s tools/tests -v` — 8개 통과.
- Pi·Jetson 운영 서비스·운영 폴더는 건드리지 않았다.

## 남은 일
- 이미 병합된 원격 브랜치 정리 여부는 사용자가 결정(push 권한은 사용자 터미널).
- 학습 코드·데이터 보관 경로는 첫 학습 작업 때 사용자와 정한다.
