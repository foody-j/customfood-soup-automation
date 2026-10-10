# pi/marks-hardening — 정답 사건 입력 보강 (2026-10-07)

지시서 `notes/fedora/orders/20261003-marks-hardening-pi.md` 수행(근거 `notes/fedora/review-20261003.md`).
시작: `python3 tools/dev_session.py start --prepare-only pi marks-hardening`. 계약·`shared/schema.json` 변경 없음.

## 변경
1. **사후 입력 시각** (`models.py` `MarkRequest.occurred_at`, `capture.py` `add_mark`)
   - 시간대 없는 값·해석 불가 값 → 422. 시간대가 있으면 **UTC `...Z`(밀리초)로 정규화**해 저장(+09:00·마이크로초 입력도 같은 형식).
   - Pi 현재보다 **60초 넘게 미래** → 422. 연결 세션의 `started_at`보다 이르면 → 422(문구에 두 시각 표시).
   - 기존 DB 값은 그대로(입력 시점 검사만).
2. **프리셋 키가 남는 문제** (`app.js`) — 프리셋 키를 **저장 설정에 쓰지 않는다**. 화면 변수 `pendingPreset`에만 두고 촬영 시작 때 그 세션의
   `config.extra.preset`에만 싣는다. 예전 버전이 저장 설정에 남긴 `preset`도 프리셋 적용·시작 때 지운다. 시작 전 점검 칸에 '프리셋'
   줄(현재 프리셋 + **해제** 버튼, 없으면 '없음 (일반 세션)'). 데이터셋 판정(조건 전송·종료 경고)은 `pendingPreset`/세션 스냅샷 기준이라
   과거 세션 표시는 그대로. 연속 데이터셋 세션은 페이지를 새로 고치지 않는 한 프리셋이 유지된다(새로 고치면 다시 눌러야 함).
3. **정정 메모가 정답 사건에 붙는 문제** (`app.js`) — 조리 정답 버튼(`.btn-gt`)은 메모 칸을 **읽지도 지우지도 않는다**. 남은 문구가 있으면
   "메모 칸 내용은 붙이지 않았습니다 — 메모 버튼으로 남기세요" 안내. 발생 시각 칸(사후 입력)은 정답 버튼도 그대로 쓴다.
4. **세션 없는 정답 사건** — `boil_start`~`lid`는 세션 ID가 없고 활성 세션도 없으면 **409**, 없는 세션 ID면 **404**.
   기존 `ingredient/heat/stir/note`는 지금처럼 세션 없이 허용. 끝난 세션에 ID를 명시한 사후 입력은 허용(시작 이후 시각이면).
5. **중지 호출 실패 후 stop 시계 표본** — 확인 결과 **이미 동작한다**: Pi 중지가 시간 초과로 실패해 세션이 `stopping`에 남아도, Jetson이 실제로
   멈추면 다음 감시 프로브가 `stop_confirmed`로 닫으면서 `monitor` 표본을 `stop`으로 채운다(doneness-marks의 `_note_clock_from_probe`).
   코드 변경 없이 테스트로 고정.

## 테스트
- 신규 `tests/test_marks_hardening.py` 7개: UTC 정규화(+09:00·마이크로초), 거절(시간대 없음·해석 불가·잘못된 날짜·5분 미래·세션 시작 30초 전)과
  30초 미래 허용, 기존 DB 값 불변, 세션 없는 정답 사건 409·없는 세션 404·기존 종류 허용, 끝난 세션 사후 입력 허용,
  세션 스냅샷의 프리셋 키 유지/일반 세션 없음, 중지 호출 실패 후 프로브 stop 표본.
- 기존 시험 2개(`test_manual_mark_separates_occurred_and_recorded_time`, `test_late_entry_keeps_occurred_at`)는 세션 시작 **전** 고정 시각으로
  사후 입력하던 것을 세션 시작 직후 시각으로 바꿈(새 규칙상 이전 시각은 거절이 맞음).
- `pytest` **78 passed**. `tools/dev_session.py check pi` 통과.
- 2·3번(화면)은 모의 서버 + 헤드리스 Firefox로 확인: 프리셋 적용 후 저장 설정 `extra` = `{}` / 세션1 `extra.preset=dataset`·params 전송,
  정정 문구가 남은 채 '완료 시작' → 사건 text null·메모 칸 유지·안내 표시, 과조리 누락 경고, '해제' 후 세션2 `extra` = `{}`·params 없음·
  종료 경고 없음. 콘솔 오류 없음.

## 남은 일
- 운영 적용(10/20 첫 데이터셋 세션 전): main 병합 후 Pi 운영 폴더 pull + `sudo systemctl restart soup-pi-server`(진행 중 세션 없을 때).
  **주의:** 10/07 현재 운영 서비스는 doneness-marks 이전 코드로 돌고 있다(재시작 필요) — 이 브랜치와 함께 한 번에 적용하면 된다.
- 실물 미검증(정답 사건 흐름은 모의 Jetson 기준).
