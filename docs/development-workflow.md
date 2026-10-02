# Pi·Jetson 개발 구간과 동기화

2026-09-28. 일반 대화형 Claude Code 개발 기준. 기존 Discord 무인 실행 규칙과 push/merge 권한은 변경하지 않는다.

## 담당 구간

| 담당 | 수정할 경로 | 담당 기능 |
|---|---|---|
| Pi | `pi-server/`, `docs/pi/`, `notes/pi/` | 관리 화면, API 중계, 실험 제어·메타데이터, Pi 서비스 |
| Jetson | `jetson/`, `cam-adaptor/`, `docs/jetson/`, `notes/jetson/` | 센서·SDK·드라이버, 원본 수집·압축, 장치 설정, Jetson 서비스 |
| Pi·Jetson 공동 | `shared/`, 공통 `docs/`, `dashboard/src/data/schema.js`, `notes/dev-log.md`, `notes/decisions.md` | API·데이터 계약, 설계 문서, 병합 기록 |
| Fedora(개발·통합 서버) | `research/`, `notes/fedora/`, 병합 시 중앙 노트 요약, 사용자와 정한 공통 도구 | 브랜치 검토, 통합 테스트, 병합 준비, 연구 데이터 관리·학습 |
| 별도 작업 범위 | 그 밖의 경로 | 나머지 React `dashboard/`, 공통 도구·에이전트 설정 등. 필요 시 사용자와 작업 범위를 정한다 |

Pi에서 Jetson 코드를 읽는 것은 가능하지만 수정은 해당 담당에게 전달한다. 반대도 같다.
공통 `docs/`는 `docs/pi/`와 `docs/jetson/`을 제외한 문서다. 공통 계약은 **어느 장비에서든 수정할 수 있고,
Fedora 검토·병합을 반드시 거칠 필요는 없다.** 해당 변경을 맡은 쪽이 계약과 인계를 책임진다.

### 공통 계약을 바꾸는 순서

1. 시작 전에 원격 작업 브랜치와 인계 노트를 확인한다. 같은 API 변경이 진행 중이면 새로 중복 구현하지 않고
   기존 담당 브랜치에 맞춰 작업한다. Git만으로 상대 장비의 미푸시 작업은 알 수 없으므로 브랜치명과 작업 범위를
   사용자 또는 공유된 인계 기록으로 전달한다. 자동 잠금이나 장비 간 자동 메시지는 제공하지 않는다.
2. 변경을 맡은 쪽이 별도 `pi/<작업>` 또는 `jetson/<작업>` 브랜치에서 계약과 자기 장비 코드를 수정한다.
   조리 데이터 계약 변경은 `docs/data-schema.md`, `shared/schema.json`, `dashboard/src/data/schema.js`를 함께 갱신한다.
   수집 HTTP API 변경은 `docs/pi-jetson-api.md`와 관련 스키마를 갱신한다.
3. 역할 노트에 요청·응답 예시, 기존/새 형식의 호환 여부, 상대 장비가 수정할 파일·동작,
   테스트 결과와 미검증 사항, 장비별 적용 순서를 적어 전달한다. 상대 장비 구현은 그 담당이 후속 브랜치에서 한다.
4. 가능하면 선택 필드 추가·기존 필드 유지로 호환성을 보존한다. 삭제·이름 변경·필수 필드 추가처럼 기존 장비를
   깨뜨리는 변경은 양쪽 구현과 조합 테스트를 준비한 뒤 함께 통합한다. 먼저 main에 깨진 계약만 반영하지 않는다.
5. 병합 담당은 변경마다 정하며 Pi·Jetson·Fedora 어디서든 맡을 수 있다. 현재 push/merge 권한 제한은 아래와 같다.

기존 계약을 재사용하는 작업은 각자 독립 진행한다.

## Fedora 개발·통합 서버 (2026-10-01)

기존 Windows의 검토·통합 역할을 Fedora PC로 옮겼다. 과거 기록(`notes/windows/`, 중앙 노트의 Windows 언급)은 그대로 둔다.

- **맡는 일:** 원격 `pi/*`·`jetson/*` 브랜치 검토, 조합·통합 테스트, 병합 준비(중앙 노트 요약·D 번호 부여),
  연구 데이터 관리(`notes/data/` 규칙)와 학습 작업.
- **필수 관문이 아니다.** Pi·Jetson은 지금처럼 각자 담당 브랜치를 공유하고 변경별 병합 담당을 정한다.
  Fedora가 맡는 것은 사용자가 그 변경의 검토·병합을 맡긴 경우다.
- **운영 장비는 건드리지 않는다.** Fedora에서 Pi·Jetson 운영 서비스·운영 폴더를 자동으로 pull·변경·재시작하지 않는다.
  운영 적용은 아래 "개발 최신화와 운영 배포는 구분"대로 담당 장비에서 한다.
- **기록:** Fedora 작업은 `notes/fedora/<작업명>.md`에 남기고, 병합을 맡은 변경은 중앙 노트에 요약한다.
  대용량 원본·학습 산출물(체크포인트 등)과 자격증명은 Git 밖에 두고 위치·조건만 기록한다.
- **검토 절차 예시:** `git fetch origin --prune` → `git log main..origin/<브랜치>`·`git diff main...origin/<브랜치>` →
  해당 역할 범위 확인(`git switch` 후 `python3 tools/dev_session.py check <역할>`) → 테스트 → 병합 준비.
  `.claude/settings.json`이 push/merge를 deny하므로 실제 병합·push는 사용자가 터미널에서 실행한다.
- **작업 지시서:** Fedora가 장비 담당에게 맡길 일은 `notes/fedora/orders/YYYYMMDD-<작업명>-<pi|jetson>.md`에
  장비 Claude에 그대로 줄 수 있게 쓴다(목표·근거·수정 파일·하지 말 것·완료 조건). 형식은 `notes/fedora/orders/README.md`.
- **연구 데이터·학습 코드:** `research/`(반출·검증·카탈로그·라벨·데이터셋·분석). 원본은 Fedora `~/soup-data/`(Git 밖).
  일정·범위는 `docs/roadmap-2026-11.md`.
- `tools/dev_session.py`의 역할은 pi·jetson 두 가지다. Fedora 작업은 사용자와 범위를 정해 `fedora/<작업>` 브랜치에서 한다.

## 노트 충돌 방지

두 장비가 동시에 `notes/dev-log.md`와 `notes/decisions.md` 맨 위/아래에 붙이면 코드가 달라도 충돌한다.
앞으로 장비 담당은 작업마다 **`notes/pi/<작업명>.md` 또는 `notes/jetson/<작업명>.md`** 한 파일에 기록한다.
파일 안에 날짜·배경·변경·검증·실물/모의 여부·남은 일·결정 제안을 적는다. 실험 소용량 자료도 각 역할 노트 하위에
작업별로 둔다. 대용량 원본·자격증명은 여전히 Git 밖이다.

해당 변경의 병합 담당이 병합 시 중앙 개발 노트에 링크 요약을 추가하고 필요할 때 D 번호를 부여한다.
Pi·Jetson 모두 중앙 노트 편집이 가능하지만 일상 작업은 역할별 노트에 쓰고, 중앙 요약은 병합 시 모은다.
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
커밋·작업 상태와 관련 브랜치를 확인해 변경 담당끼리 조정한다. 변경이 있다는 이유로 세션을 종료하거나 코드를 지우지 않는다.
venv/node_modules/장비별 env는 worktree에 자동 복사되지 않는다. 개발용 환경을 준비하고 기존 서비스·DB와 포트를 공유하지 않는다.

## 담당 경로 검사와 완료

```bash
# 각 작업 폴더에서 커밋 전에 실행
python3 tools/dev_session.py check pi
# 또는
python3 tools/dev_session.py check jetson
```

검사는 브랜치 이름, main과 갈라진 이후의 커밋, staged/unstaged 변경, Git 비추적 파일을 확인한다.
이름 변경은 옛 경로·새 경로 모두 검사한다. 자기 장비 전용 구간과 공통 구간 밖의 파일이 있으면 실패한다.
런처는 Claude 종료 후에도 검사한다. 이 검사는 경로만 확인하며 계약 호환성·인계 완료 여부를 자동 검증하지 않는다.
이는 개발 규칙과 변경 검사이며 OS 파일 접근 제한이나 강제 Git 훅은 아니다. 다른 도구로 우회 가능한 보안 경계로 설명하지 않는다.
다른 장비 전용 코드가 브랜치에 들어 있으면 무시 옵션을 만들지 말고 변경을 분리하여 해당 담당에게 넘긴다.

일반 흐름:

```text
Pi:     최신 main → pi/<작업>     → 테스트·범위 검사 → 브랜치 공유 ─┐
                                                               ├→ 변경별 담당 검토·main 병합
Jetson: 최신 main → jetson/<작업> → 테스트·범위 검사 → 브랜치 공유 ─┘
다음 새 작업은 병합된 최신 main에서 시작
```

현재 `.claude/settings.json`은 **일반 대화형 Claude에도 `git push`와 `git merge`를 deny**한다.
이 설정 때문에 브랜치 공유는 현재 사용자가 터미널에서 다음처럼 실행한다. 런처는 이 제한을 우회하지 않는다.

```bash
# 예시: 해당 작업 폴더에서, 실제 브랜치 이름 사용
git push -u origin pi/thermal-card
```

검토·통합은 어느 장비에서든 할 수 있다. 다른 장비의 미푸시 로컬 커밋은 원격에서 가져올 수 없다.
일반 대화형 개발의 push 자동화까지 원하면 그 권한 정책을 별도로 변경한다. Discord 무인 실행은 기존 제한을 유지한다.

## 개발 최신화와 운영 배포는 구분

자동으로 코드를 받았다고 서비스가 업데이트된 것은 아니다. 운영 적용은 세션 종료·저장 확인 후
담당 장비에서 수행하며 실제 서비스 경로, 의존성, 재시작, health 확인을 함께 기록한다.
systemd 서비스는 기존 운영 폴더를 계속 사용하며 새 worktree로 자동 변경하지 않는다.
`main` pull을 cron/timer로 돌리거나 촬영 중 서비스를 자동 재시작하지 않는다.

## 근거

- [Claude Code 프로젝트 지침](https://code.claude.com/docs/en/memory)
- [CLI의 추가 지침 및 worktree 지원](https://code.claude.com/docs/en/cli-reference)
