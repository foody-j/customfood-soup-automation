# Pi·Jetson 개발 구간과 동기화

2026-09-28. 일반 대화형 Claude Code 개발 기준. 기존 Discord 무인 실행 규칙과 push/merge 권한은 변경하지 않는다.

## 담당 구간

| 담당 | 수정할 경로 | 담당 기능 |
|---|---|---|
| Pi | `pi-server/`, `docs/pi/`, `notes/pi/` | 관리 화면, API 중계, 실험 제어·메타데이터, Pi 서비스 |
| Jetson | `jetson/`, `cam-adaptor/`, `docs/jetson/`, `notes/jetson/` | 센서·SDK·드라이버, 원본 수집·압축, 장치 설정, Jetson 서비스 |
| 통합 담당(현재 Windows) | 공통 파일과 나머지 경로 | 공통 API 계약, 모델 설계, `shared/`, 기존 React `dashboard/`, 공통 지침, 병합·통합 검증 |

Pi에서 Jetson 코드를 읽는 것은 가능하지만 수정은 해당 담당에게 전달한다. 반대도 같다.
`docs/pi-jetson-api.md`, `docs/data-schema.md`, `shared/schema.json`, `dashboard/src/data/schema.js`는
공통 계약이므로 한 담당만 갱신한다. 새 API가 필요하면 장비 노트에 요청·응답 예시와 호환성·테스트 결과를 적고
통합 담당이 계약 변경을 반영한 뒤 양쪽이 그 기준으로 구현한다. 기존 계약을 재사용하는 작업은 독립 진행한다.

## 노트 충돌 방지

두 장비가 동시에 `notes/dev-log.md`와 `notes/decisions.md` 맨 위/아래에 붙이면 코드가 달라도 충돌한다.
앞으로 장비 담당은 작업마다 **`notes/pi/<작업명>.md` 또는 `notes/jetson/<작업명>.md`** 한 파일에 기록한다.
파일 안에 날짜·배경·변경·검증·실물/모의 여부·남은 일·결정 제안을 적는다. 실험 소용량 자료도 각 역할 노트 하위에
작업별로 둔다. 대용량 원본·자격증명은 여전히 Git 밖이다.

통합 담당이 병합 시 중앙 개발 노트에 링크 요약을 추가하고 필요할 때 D 번호를 부여한다.
장비가 D 번호를 각자 선점하지 않는다. 기존 D-036 등 이미 작성됐으나 미푸시인 기록은 병합할 때 번호를 조정한다.
이 규칙은 루트 노트 작성 요구를 **장비별 기록 + 병합 시 중앙 요약**으로 구체화한 것이다.

## 작업 시작: 최신 main 자동 반영

최초 한 번은 스크립트가 포함된 main을 받는다. 진행 중 작업이 있는 `pi-first-cook-trial` 등은
그대로 보존·푸시·검토한 뒤 통합한다. 새 작업 시작기가 기존 브랜치를 옮기거나 지우지 않는다.
운영 폴더나 기존 작업 브랜치에서 파일을 갱신하지 않고 최초 실행하려면 다음처럼 스크립트만 임시 경로에 받는다.

```bash
# Pi/Jetson의 기존 저장소 안에서 실행. FETCH_HEAD/원격 참조만 갱신하고 작업 파일은 유지한다.
git fetch origin main
launcher=$(mktemp /tmp/customfood-dev-session.XXXXXX.py)
git show FETCH_HEAD:tools/dev_session.py > "$launcher"
python3 "$launcher" start pi thermal-card  # Jetson은 start jetson capture-health
```

저장소 폴더에서 일반 `claude` 대신 다음 명령을 실행한다(Python 3.9+, Git 2.31+ 필요).

```bash
# Pi: 매 작업마다 새 이름
python3 tools/dev_session.py start pi thermal-card

# Jetson
python3 tools/dev_session.py start jetson capture-health
```

동작은 `origin/main fetch → 최신 main 기준 역할별 브랜치/worktree 생성 → 담당 지침과 함께 Claude 실행`이다.
예를 들어 Pi 브랜치는 `pi/thermal-card`, 폴더는 저장소 옆 `customfood-soup-automation-worktrees/pi-thermal-card`다.
현재 실행 중인 서비스가 참조하는 원래 폴더에는 pull·checkout하지 않는다. 브랜치와 작업 폴더가 이미 있으면 덮어쓰지 않고 종료한다.
네트워크·인증 실패 시 오래된 main으로 새 작업을 만들지 않는다. `--prepare-only`는 Claude를 실행하지 않고 폴더만 만든다.

사용자가 원한 자동 최신화는 이 **새 작업 시작 시점**에 수행한다. 편집 중 주기적 pull, 자동 stash/reset/rebase는 하지 않는다.
기존 작업을 계속할 때는 해당 worktree에서 `claude --continue`를 사용한다. 그 작업 중간에 main을 반영해야 하면
커밋·작업 상태를 확인해 통합 담당과 조정한다. 변경이 있다는 이유로 세션을 종료하거나 코드를 지우지 않는다.
venv/node_modules/장비별 env는 worktree에 자동 복사되지 않는다. 개발용 환경을 준비하고 기존 서비스·DB와 포트를 공유하지 않는다.

## 담당 경로 검사와 완료

```bash
# 각 작업 폴더에서 커밋 전에 실행
python3 tools/dev_session.py check pi
# 또는
python3 tools/dev_session.py check jetson
```

검사는 브랜치 이름, main과 갈라진 이후의 커밋, staged/unstaged 변경, Git 비추적 파일을 확인한다.
이름 변경은 옛 경로·새 경로 모두 검사한다. 역할 밖 파일이 있으면 실패한다. 런처는 Claude 종료 후에도 검사한다.
이는 개발 규칙과 변경 검사이며 OS 파일 접근 제한이나 강제 Git 훅은 아니다. 다른 도구로 우회 가능한 보안 경계로 설명하지 않는다.
다른 역할 변경이 브랜치에 들어 있으면 무시 옵션을 만들지 말고 변경을 분리하거나 통합 담당에게 넘긴다.

일반 흐름:

```text
Pi:     최신 main → pi/<작업>     → 테스트·범위 검사 → 브랜치 공유 ─┐
                                                               ├→ 통합 담당 검토·main 병합
Jetson: 최신 main → jetson/<작업> → 테스트·범위 검사 → 브랜치 공유 ─┘
다음 새 작업은 병합된 최신 main에서 시작
```

현재 `.claude/settings.json`은 **일반 대화형 Claude에도 `git push`와 `git merge`를 deny**한다.
이 설정 때문에 브랜치 공유는 현재 사용자가 터미널에서 다음처럼 실행한다. 런처는 이 제한을 우회하지 않는다.

```bash
# 예시: 해당 작업 폴더에서, 실제 브랜치 이름 사용
git push -u origin pi/thermal-card
```

Windows는 원격에 올라온 브랜치를 검토·통합한다. 원격에 없는 Pi 로컬 커밋을 Windows가 가져올 수는 없다.
일반 대화형 개발의 push 자동화까지 원하면 그 권한 정책을 별도로 변경한다. Discord 무인 실행은 기존 제한을 유지한다.

## 개발 최신화와 운영 배포는 구분

자동으로 코드를 받았다고 서비스가 업데이트된 것은 아니다. 운영 적용은 세션 종료·저장 확인 후
담당 장비에서 수행하며 실제 서비스 경로, 의존성, 재시작, health 확인을 함께 기록한다.
systemd 서비스는 기존 운영 폴더를 계속 사용하며 새 worktree로 자동 변경하지 않는다.
`main` pull을 cron/timer로 돌리거나 촬영 중 서비스를 자동 재시작하지 않는다.

## 근거

- [Claude Code 프로젝트 지침](https://code.claude.com/docs/en/memory)
- [CLI의 추가 지침 및 worktree 지원](https://code.claude.com/docs/en/cli-reference)
