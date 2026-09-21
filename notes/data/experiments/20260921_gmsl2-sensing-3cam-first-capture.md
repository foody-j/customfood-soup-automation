# 2026-09-21 GMSL2 ×2(Sensing SG4A) + Gemini 2 — 카메라 3대 첫 동시 촬영

- **장치:** Sensing SG4A-NONX-G2Y-A1(MAX96712 @ i2c-9 0x6b) + ISX031F ×2(MAX96717F, 포트 0·1, GMSL2 3G), Orbbec Gemini 2(USB3)
- **드라이버:** SDK `max96712.ko` → `sgx-yuv-gmsl2.ko GMSLMODE_1=2,2,2,2`(v2.0.6) 수동 insmod. 커널 로그
  `Der-1 port 0/1 camera … been detected!`, 포트 2·3은 `detect error`(카메라 없음). `clock_config.sh`는 **미실행**.
- **노드:** Gemini 2가 `/dev/video0~5`, GMSL이 `/dev/video6~9`(포트 0~3, 카메라 없는 포트도 노드는 생김).
- 원본은 시험용이라 삭제했다(60초에 3.2 GB). 수치는 status·index.jsonl·manifest에서 읽었다.

| 실행 | 결과 |
|---|---|
| `v4l2-ctl` 단독(video6·7, sensor_mode=1, 1920×1536 UYVY) | 30 fps, 프레임 5,898,240 B, 육안 정상. `sensor_mode` 없이 포맷만 바꾸면 1920×1080에 머문다 |
| 어댑터 단독 90프레임(video7) | 29.3 fps, 순번 0→89 갭 0, 프레임 간격 33.33 ms, 빈 프레임 0, **ERROR 플래그 23/90** |
| 서비스 3대 동시 30초(전역 fps 10) | 5스트림 모두 약 10 fps, 드롭 0, 미리보기 4종 200 OK. **갭 576×2는 오집계**(30→10 추림의 seq 간격) → 수정 |
| 서비스 3대 동시 60초(수정 후) | GMSL 각 587장·갭 0·드롭 0(쓰기 44~48 ms/장, JPEG 0.33~0.89 MB), depth·IR 639장·갭 1, Gemini color 6 fps(319장), CPU 46 %, Tj 51.8 ℃ |

## 관찰

- **ERROR 플래그:** Sensing 드라이버가 프레임 일부에 V4L2 ERROR 플래그를 붙인다 — 60초 세션에서 포트 0은 86/590(15 %),
  포트 1은 455/589(77 %). 커널 로그는 `tegra-capture-vi: corr_err: discarding frame 0, err_data 4194402`(드물게 `64`).
  해당 프레임을 저장해 보면 육안으로 정상이다. 버리지 않고 `flags.driver_error_flag`로 표시해 저장한다.
  nvcsi 클록 고정(`clock_config.sh`) 후 다시 재서 비교할 것 — 원인 미확정.
- **타임스탬프:** V4L2 버퍼 시각은 monotonic 표시지만 호스트 `CLOCK_MONOTONIC`보다 약 25초 앞서 있었다(장치 시각으로만 기록).
- **발견한 버그:** `V4L2Capture.dequeue()`가 QBUF 뒤에 구조체를 읽어 순번·시각·플래그가 전부 0이었다(9/12 작성, 링크가 없어 미발견).
- GMSL ①(포트 0)은 아직 리그에 고정되지 않은 화면, GMSL ②(포트 1)와 Gemini 2는 인덕션을 내려다보는 화면이었다.
- Gemini 2 color는 이번에도 요청 10 fps에 6 fps(자동 노출 추정, 미확인).
