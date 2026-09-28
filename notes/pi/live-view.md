# pi/live-view — 라이브 보기(원본 저장 안 함) 계약 + Pi 구현, Jetson 인계 (2026-09-28, D-037)

**담당:** 이 공통 계약 변경의 담당 브랜치는 `pi/live-view`. Jetson 구현은 Jetson 담당이 후속 `jetson/<작업>` 브랜치에서 한다.
**계약:** `docs/pi-jetson-api.md` §3.1 · 결정 `notes/decisions.md` D-037.

## 왜
미리보기는 녹화 세션에서만 나온다(`session.py` `preview_frame`/`preview_array`가 RUNNING 세션 캐시만 반환).
카메라 위치·초점을 맞추려고 화면을 켜면 분당 약 1.4 GB가 저장된다(9/27 실측 `sess-20260927T074757Z-f355`).

## 요청·응답 예시
```jsonc
GET /api/v1/status → { ..., "capabilities": ["live_view"], "capture": { "state": "running", "session_id": "live-...", "record": false, "frames_written": 0 } }
POST /api/v1/capture/start
{ "session_id": "live-20260928T050000Z-a1b2", "name": "라이브 보기 (저장 안 함)",
  "config": { "sensors": [...], "fps": 10, "record": false, "preview": {"enabled": true, "max_fps": 1}, "max_duration_sec": 600 } }
→ { "accepted": true, "session_id": "live-...", "state": "running" }
```

## 호환성
| 조합 | 결과 |
|---|---|
| 새 Pi + 옛 Jetson | `capabilities` 없음 → Pi가 라이브 버튼 숨김, `/api/live/start` 409. 기존 녹화 그대로 |
| 새 Pi + 새 Jetson | 라이브 사용 가능 |
| 옛 Pi + 새 Jetson | 옛 Pi는 `record`를 보내지 않음 → 기존 녹화 그대로 |
| capability는 알렸는데 실제로 `record:false`를 되돌려주지 않음 | Pi가 `live.not_confirmed`(error) 기록 후 **즉시 중지 요청**(`reason=live_not_confirmed`) |

## Jetson이 할 일 (`jetson/collector/` — Jetson 담당)
1. `service.status()` → `JetsonReport.capabilities = ["live_view"]`(모델에 필드 추가).
2. `JetsonCapture`에 `record: bool | None` 추가, `snapshot()`이 세션의 record 값을 되돌려준다.
3. `CaptureSession`: `config.get("record") is False`면
   - `SessionStore`로 `data_root/<session_id>`를 만들지 않는다(session.json·events·stats·manifest 없음). 사건은 로거로.
   - `StreamWriter`를 만들지 않는다. **주의:** 지금 `_capture_loop`는 `self._writers.get(...)`이 None이면 샘플을 건너뛰므로
     미리보기(`_update_preview`)까지 끊긴다 — 라이브 경로에서는 기록기 없이 수신 통계와 `_update_preview`만 돌게 해야 한다.
   - `_preview_enabled`를 강제로 True(`preview.max_fps` 상한 2는 그대로).
   - `max_duration_sec`이 없거나 0이면 600초 적용. 사유 문자열은 기존 `max_duration_sec=<값> 도달`.
   - `_finalize`: 기록기 drain·manifest·checksum 없이 stopped. `_on_session_finished`에서 **`_last_summary`를 갱신하지 않는다**
     (`_last_session`은 `record:false`로 남겨도 됨).
4. `start_capture`의 `data_root/<id>/session.json` 존재 검사·디스크 여유 검사는 라이브에서 생략해도 된다(쓰지 않으므로).
5. `/api/v1/sessions` 목록에 라이브가 나오지 않게(디렉터리가 없으면 자연히 빠짐).

## Jetson 테스트 제안
- record:false 시작 → `data_root`에 새 항목 없음, JPEG·`preview_array` 200, status `record:false`·`frames_written 0`.
- max_duration 미지정 라이브 → 600초(시험에서는 짧은 값 주입) 뒤 stopped, 사유 문자열 일치.
- 라이브 종료 후 `last_session_summary`가 직전 녹화 값 그대로.
- 라이브 중 다른 ID 녹화 시작 → `accepted:false`. record 미지정 시작은 기존 테스트 전부 통과(회귀 없음).

## Pi 구현(이 브랜치, 완료)
- `POST /api/live/start {max_duration_sec?}`(1~3600, 기본 600): 저장된 센서·fps + `record:false`·미리보기 켬, 세션 ID `live-…`.
  capability 없으면 409. 모델 `JetsonReport.capabilities`, `JetsonCapture.record`.
- 재동기화에서 라이브 세션이 `record:false`로 확인되지 않으면 `live.not_confirmed` + 중지 요청(한 번만 기록).
- 화면: capability가 있을 때만 '라이브 보기 (저장 안 함)' 버튼. 라이브 중에는 시작 입력을 그대로 두고
  '라이브 끝내고 촬영 시작'(라이브 중지 → 세션 종료 대기 최대 20초 → 녹화 시작), 중지 버튼은 '라이브 끝내기'.
  세션·이력에 '저장 안 함 (라이브)', 저장 결과를 기다리지 않음.
- mock: `capabilities`, record 되돌림, 무저장·요약 미갱신·기본 600초, `live_view_supported=False`로 옛 Jetson 흉내.
- 검증: pytest 58(신규 6: capability 거절·무저장·기본 600초·입력 검증·옛 Jetson 즉시 중지·일반 녹화 회귀),
  모의 서버 + 헤드리스 Firefox로 라이브 → '라이브 끝내고 촬영 시작' → 녹화 → 이력 구분 확인, 콘솔 오류 없음.
- **미검증:** 실물 Jetson(구현 전이라 버튼이 숨겨진 상태 — 실물에서 Pi 동작 변화 없음).

## 적용 순서
1. 이 브랜치를 main에 병합 → Pi 운영 폴더 pull, 서비스 재시작(진행 중 세션 없을 때). 실물 Jetson이 아직 알리지 않으므로 화면 변화 없음.
2. Jetson 담당이 위 1~5 구현·테스트 → main 병합 → Jetson 서비스 재시작(진행 중 세션 없을 때).
3. Pi 화면에 라이브 버튼이 나타나면 실물 확인: 라이브 1분 → Jetson `data_root` 새 디렉터리 없음·디스크 변화 없음 → '라이브 끝내고 촬영 시작'.
   순서가 바뀌어도 안전하다(버튼은 capability가 있어야만 보인다).

## 실물 검증 (2026-09-28, Jetson `fb591bc` 반영 후)
- Jetson `status.capabilities = ["live_view"]`, Pi 화면에 '라이브 보기 (저장 안 함)' 버튼 표시.
- 라이브 `live-20260928T080625Z-2382` 약 40초: status `record:false`·`frames_written 0`·running, **Jetson 세션 목록 새 항목 없음**,
  디스크 여유 변화 −0.33 MB(서비스 로그 등 — 같은 시간 녹화 ≈ 0.9 GB와 비교). 수신 `cam_rgb_0/1` 408·`cam_depth_0` color 221/depth 442/ir 442·
  열화상 81·PT100 41. 화면: 카메라 3면·열화상(25.1~33.1 ℃)·PT100(24.35 ℃) 모두 '수신 중'. 캡처 `docs/img/pi-real-live-view-20260928.png`.
- '점검 60초' → '라이브 끝내고 촬영 시작' → 라이브 stopped(저장 요약 없음) → 녹화 `sess-20260928T080710Z-80dd` 60초 자동 중지·저장 확인
  (프레임 3,198). 콘솔 오류 없음.
- 보완(이 브랜치): 라이브→녹화 전환 시 중지 사유 `live_to_recording`을 남긴다(검증 때는 사유가 비어 있었다).
- 관찰: 라이브 중 Gemini color 수신이 depth·IR의 절반(40초 221 vs 442) — Jetson 담당 확인 거리(녹화에서도 같은지).
