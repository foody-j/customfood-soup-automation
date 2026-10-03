# pi/doneness-marks — 조리 정답 사건·조건 입력·데이터셋 프리셋·시계 오차 기록 (2026-10-03)

지시서 `notes/fedora/orders/20261002-doneness-marks-pi.md` 수행. 시작: `python3 tools/dev_session.py start --prepare-only pi doneness-marks`.
계약(`docs/pi-jetson-api.md`·`shared/schema.json`) 변경 없음 — Pi 내부 기능.

## 변경 (`pi-server/`)
1. **사건 종류** `MarkKind`: `boil_start` `taste` `done_start` `done_end` `overcooked` `lid`. `MarkRequest.value`(선택) 추가 —
   `taste`는 `undercooked|done|overcooked`(schema `doneness` 문자열), `lid`는 `on|off` **필수**, 그 밖의 종류에 value를 주면 422.
   기존 요청(value 없음)은 그대로. 이벤트 `code=mark.<kind>`, `detail={kind, value, text, late_entry}`, 사후 입력 `occurred_at` 유지.
   메시지에 값 표시(예: `[수동] 맛보기(완료)`).
2. **화면**: '조리 정답' 버튼 줄(끓음 시작 · 맛보기 미완/완료/과조리 · 완료 시작 · 완료 끝 · 과조리 · 뚜껑 덮음/엶) — **녹화 중에만** 켜짐
   (세션 없음·라이브 보기면 꺼짐). 목록에 값 표시, 진행 중이면 그 세션 사건만 표시. 목록 항목을 누르면 메모 칸에 `정정: <시각> <사건> — `
   를 채운다(삭제 기능 없음 — 원래 사건은 남는다).
3. **구조화 조건** `SessionParams`: `heat_level` `water_added_ml` `lid_initial(on|off)` `start_temp_c` `probe_depth_mm` `product_weight_g` `taster`
   (범위 검사, 빈 값 null). 시작 요청 `params`로 받아 세션 `params`(DB 열 추가)에 저장, 상세·내보내기에 포함. `PATCH /api/sessions/{id}`로
   사후 보완 가능(같은 검사, 전후 값 사건 기록). 화면: 시작 입력의 '데이터셋 조건' 접이식 칸. 데이터셋 프리셋이거나 한 칸이라도 채우면 보낸다.
   기존 세션은 `params: null`.
4. **프리셋 '소고기무국 데이터셋'**: 최대 60분, 센서 `TRIAL_SENSORS`(GMSL2 2·열화상·PT100), fps 10, 미리보기 1 Hz, 문구는 프로토콜 §4 절차.
   프리셋 적용 시 실험 설정 `extra.preset`에 프리셋 키를 저장(세션 스냅샷에 남아 데이터셋 세션을 구분).
5. **종료 전 경고**: `config.extra.preset == "dataset"` 세션에서 `mark.done_start`·`mark.overcooked`가 없으면 중지 확인창에 빠진 사건 표시.
   취소하면 녹화 계속, 확인하면 중지(막지 않음).
6. **시계 오차**: `offset_s = device_time − (t_req + t_resp)/2`(Jetson − Pi), 경과는 monotonic. 세션 `clock_offsets`(DB 열 추가)에
   `{at, offset_s, rtt_s, source, event}` 추가 — 시작 직후(`start_probe`)·Pi 중지 직후(`stop_probe`) 별도 조회로 측정,
   Jetson이 스스로 끝낸 경우(최대 시간)·시작 측정이 실패한 경우는 감시 프로브 값(`monitor`)으로 채운다. 어떤 시각도 고치지 않는다.
   화면 세션 칸에 왕복 최소 측정값 표시.

## 내보내기 예시 (`/api/sessions/{id}/export?format=json`, 모의 Jetson)
```jsonc
"session": {
  "params": {"heat_level": 7.0, "water_added_ml": null, "lid_initial": "off", "start_temp_c": null,
             "probe_depth_mm": null, "product_weight_g": null, "taster": "YJ"},
  "clock_offsets": [
    {"at": "2026-10-03T04:26:22.147Z", "offset_s": -0.0003, "rtt_s": 0.0002, "source": "start_probe", "event": "start"},
    {"at": "2026-10-03T04:26:36.636Z", "offset_s": -0.001,  "rtt_s": 0.0001, "source": "stop_probe",  "event": "stop"}]
},
"events": [ {"ts": "2026-10-03T04:26:27.549Z", "code": "mark.taste", "occurred_at": null, "origin": "manual",
             "detail": {"kind": "taste", "value": "undercooked", "text": null, "late_entry": false}}, … ]
```
`occurred_at`은 사후 입력일 때만 값이 있다(평소 null → Fedora `pi_marks`는 `ts`를 쓴다).

## 테스트
- 신규 `tests/test_doneness_marks.py` 11개: 6종 사건·값 저장, 값 검사(필수·허용값·값 없는 종류 거절·기존 요청 유지), 사후 입력,
  params 저장·null·검사, params 없는 세션, PATCH 보완, 시작·중지 시계 오차(모의 Jetson 1.5 초 빠름 → ≈1.5), 자동 종료 시 프로브 값,
  사건 시각 미보정, 내보내기 내용, **Fedora `research/soupdata`의 `labels.clock_offset`·`qc.pi_marks`·`labels.build_timeline`으로 내보내기를
  직접 읽어** done_start/done_end/overcooked·taste 2개·offset ≈2.0 확인(research/ 없으면 skip).
- `pytest` **71 passed**. `tools/dev_session.py check pi` 통과.
- 모의 서버 + 헤드리스 Firefox: 위 흐름(프리셋 → 조건 입력 → 시작 → 정답 버튼 → 정정 채우기 → 누락 경고/취소 → 사건 추가 후 경고 없음 → 내보내기) 확인,
  콘솔 오류 없음.

## 남은 일 / 제안
- **실물 미검증**(Jetson 센서가 10/02 기준 전부 미연결 상태였음). 실물 시계 오차 크기·왕복 시간은 첫 세션에서 확인.
- 운영 적용: main 병합 후 Pi 운영 폴더 pull + `sudo systemctl restart soup-pi-server`(진행 중 세션 없을 때). **첫 데이터셋 세션 전에 필요**
  — DB에 `params`·`clock_offsets` 열이 추가된다(열 추가만, 기존 기록 불변).
- 제안(Fedora): 세션이 길면 시작·중지 두 점 사이 시계 표류를 볼 수 없다. 필요하면 녹화 중 N분마다 프로브 값을 남기도록 늘릴 수 있다.
- 운영 Pi의 저장된 실험 설정 `extra`에는 마지막으로 누른 프리셋 키가 남는다 — 프리셋 없이 시작하면 직전 프리셋 키가 세션에 따라간다.
  데이터셋이 아닌 세션을 시작할 때는 다른 프리셋을 한 번 누르는 것을 권장(필요하면 '프리셋 해제'를 추가).
