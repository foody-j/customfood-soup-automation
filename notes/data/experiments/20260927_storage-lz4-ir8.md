# 2026-09-27 — 저장량 절감: 배열 lz4 압축 + IR 8비트 (5센서 60초)

## 압축 후보 비교 (재부팅 직후 세션 `postboot-20260926T070032Z`의 실제 프레임 18장씩)
| 방식 | depth 배율 · ms/장 | IR 배율 · ms/장 |
|---|---|---|
| PNG lvl1 | 5.70× · 59.5 | 4.33× · 66.8 |
| PNG lvl3 | 6.41× · 95.3 | 4.74× · 105.5 |
| lz4 | 3.13× · 6.8 | 2.74× · 7.0 |
| zlib1 | 4.27× · 32.0 | 3.98× · 33.4 |
| **shuffle+lz4** | **3.76× · 7.0** | 3.75× · 5.9 |
| shuffle+zlib1 | 5.00× · 32.0 | 4.99× · 31.5 |

- depth 1280×800 `uint16`: 0 ~ 1,855 mm(11비트), 0 비율 18 %. IR: **최댓값 255**(Y8을 uint16으로 늘려 저장 중이었음), 0 비율 26 %.

## 적용 후 실측
- 세션 `storage-lz4-20260927T064440Z`: GMSL ×2 + Gemini(color·depth·IR) + 열화상 + PT100, 전역 10 fps, `max_duration_sec=60` → **스스로 정지**
  (`stop_reason: max_duration_sec=60 도달`). 임시 인스턴스(포트 8020, 같은 env)로 실행 — systemd 서비스는 건드리지 않음.
- 드롭 0, 기록 대기열 최대 1. 기록 시간(마지막 값): depth 16 ms, IR 9 ms, GMSL JPEG 42 ms. CPU 사용 약 42 %(us+sy).

| 스트림 | 이전 KB/장 | 이후 KB/장 | 배율 |
|---|---|---|---|
| cam_depth_0/depth | 2,048 | 547 | 3.74× |
| cam_depth_0/ir | 2,048 (uint16) | 555 (uint8+lz4) | 3.69× |
| cam_depth_0/color (JPEG) | 207 | 206 | — |
| cam_rgb_0/rgb (JPEG) | 131 | 134 | — |
| cam_rgb_1/rgb (JPEG) | 862 | 876 | — |
| thermal_0/temp_array | 3.1 | 2.4 | 1.30× |

- **합계 3.20 → 1.39 GB/분(2.30×), 시간당 192 → 84 GB.** 남은 큰 몫은 depth·IR 각 약 330 MB/분, GMSL ② JPEG 527 MB/분
  (장면에 따라 GMSL ① 131 KB vs ② 876 KB로 차이가 큼).
- 더 줄이려면 손실 선택(JPEG 품질, 해상도·fps)이 필요 — 분석 목적상 사용자 판단 사항.
