# 작업 지시서 — Pi: 조리 정답 사건·조건 입력·데이터셋 프리셋·시계 오차 기록 (2026-10-02)

**시작:** `python3 tools/dev_session.py start pi doneness-marks` → 이 파일을 읽고 수행.
**근거:** `docs/roadmap-2026-11.md` 1단계, `docs/cooking-protocol.md`, `docs/gt-definition-design.md` §1·§4.
**배경:** 11월 말 제출용 소고기무국 데이터셋의 정답(미완/완료/과조리 구간)은 실험 중 Pi 화면 사건으로 남긴다.
지금 사건은 `ingredient/heat/stir/note` + 자유 문구뿐이라 Fedora가 기계적으로 라벨을 만들 수 없다.

## 목표
실험 중 누른 정답 사건·조건이 **구조화된 값**으로 저장되고 세션 내보내기(json/csv/jsonl)로 Fedora가 그대로 읽을 수 있게 한다.

## 할 일
1. **정답 사건 종류 추가** — `pi-server/app/models.py` `MarkKind`·`MARK_LABELS`:
   `boil_start`(끓음 시작), `taste`(맛보기), `done_start`(완료 시작), `done_end`(완료 끝), `overcooked`(과조리), `lid`(뚜껑).
   - `taste`는 판정값 필수: `undercooked | done | overcooked`(`shared/schema.json` `doneness` 값과 같은 문자열).
     `lid`는 `on | off`. 구현 예: `MarkRequest`에 선택 필드 `value: str | None` 추가, kind별 허용값 검사 → 이벤트 `detail.value`
     (Fedora `research/soupdata/qc.py` `pi_marks`가 `detail.value`를 읽는다).
     (필드 이름·방식은 Pi 판단. 기존 요청은 그대로 동작해야 함)
   - 이벤트 `code`는 기존 규칙대로 `mark.<kind>`. 사후 입력(`occurred_at`) 그대로 지원.
2. **실험 화면** — `app/static/index.html`·`app.js`: "조리 정답" 버튼 줄(끓음 시작 · 맛보기 미완/완료/과조리 · 완료 시작 · 완료 끝 · 과조리 · 뚜껑 덮음/엶).
   기존 `btn-quick` 패턴 재사용, 진행 중 세션에서만 활성, 누른 직후 목록에 표시. 잘못 누른 사건은 메모로 정정 표시(삭제 기능은 만들지 않음).
3. **조건 입력 구조화** — 데이터셋 세션 조건을 자유 문구 외에 **키-값**으로도 저장:
   `heat_level`(숫자), `water_added_ml`, `lid_initial`(on/off), `start_temp_c`, `probe_depth_mm`, `product_weight_g`, `taster`(이니셜).
   세션 메타에 선택 객체(예: `params`)로 저장하고 세션 상세·내보내기에 포함. 빈 값은 null. 기존 세션은 `params` 없음으로 정상 표시.
4. **데이터셋 프리셋** — `app.js` `PRESETS`에 '소고기무국 데이터셋'(최대 60분, 센서 `TRIAL_SENSORS`, fps는 기존 10 유지 —
   Jetson 실측 후 Fedora가 별도 지시). 문구는 `docs/cooking-protocol.md` 4절과 맞춘다.
5. **종료 전 경고** — 데이터셋 세션에서 `done_start`나 `overcooked` 사건이 없으면 중지 확인창에 경고(중지는 막지 않음).
6. **Pi↔Jetson 시계 오차 기록** — 상태 프로브 응답의 Jetson `device_time`과 Pi 요청·응답 시각으로 오차 추정
   (`offset = device_time − (t_req + t_resp)/2`, 왕복 시간도 함께). 세션 시작·중지 시점 값을 세션 메타로 남기고 내보내기에 포함.
   **Fedora 라벨 코드가 읽는 형태:** 세션 객체의 `clock_offsets: [{"at": <Pi UTC>, "offset_s": <Jetson−Pi 초>, "rtt_s": <왕복 초>}, …]`
   (`research/soupdata/labels.py` `clock_offset`). 다른 형태로 하면 노트에 적어 Fedora가 맞춘다.
   장치 시각을 고치거나 사건 시각을 바꾸지 않는다 — 측정만 남긴다(보정은 Fedora 라벨 단계).
7. **내보내기 확인** — `/api/sessions/{id}/export` json에 사건 `kind`·`value`·`occurred_at`·`late_entry`, `params`, 시계 오차가 모두 들어가는지.

## 하지 말 것
- `docs/pi-jetson-api.md`·`shared/schema.json` 계약 변경 없음(Pi 내부 기능). 필요해 보이면 노트에 제안만.
- Jetson 코드 수정 금지. 운영 서비스 자동 재시작 금지. 기존 사건·세션 데이터 변경 금지.

## 완료 조건 / 보고
- `pytest` 통과(새 사건 종류·값 검사·사후 입력·params·내보내기·시계 오차·과거 세션 호환 테스트 포함).
- `python3 tools/dev_session.py check pi` 통과. 모의 Jetson으로 화면 동작 확인.
- `notes/pi/doneness-marks.md`: 변경·테스트·내보내기 예시 JSON 일부(사건 1개·params·시계 오차)·남은 일. 브랜치 `pi/doneness-marks` push.
- 운영 적용(진행 중 세션 없을 때)은 Pi 담당. 첫 데이터셋 세션 전에 적용 필요.
