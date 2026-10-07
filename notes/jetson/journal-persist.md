# jetson/journal-persist — 서비스 로그 영구 보관 (2026-10-07)

지시서: `notes/fedora/orders/20261003-journal-persist-jetson.md`. 장비 설정만, 코드 변경 없음.

## 적용 (사용자가 sudo로 실행, 진행 중 세션 없음)
- `sudo mkdir -p /var/log/journal && sudo systemd-tmpfiles --create --prefix /var/log/journal`
- `/etc/systemd/journald.conf.d/soup.conf`:
  ```
  [Journal]
  Storage=persistent
  SystemMaxUse=2G
  MaxRetentionSec=3month
  ```
- `sudo systemctl restart systemd-journald` — **이것만으로는 디스크로 옮겨지지 않았다**(17:20 확인: `/var/log/journal` 비어 있고
  `journalctl --header` 파일 경로가 `/run/log/journal/…`). `sudo journalctl --flush`를 추가로 실행해 해결(17:23).
  지시서에 `--flush` 단계를 넣는 것을 제안.

## 확인 출력 (2026-10-07 17:23)
- `/var/log/journal/5dbfb12414a3456d9014d88183e338b1/` 생성 — `system.journal`(24 MB), 아카이브 1개(128 MB), `user-1000.journal`.
- `journalctl --header`의 파일 경로가 모두 `/var/log/journal/…`. `journalctl --disk-usage` 160.0M.
- 같은 시각 `drop-gemini` 운영 적용도 함께 함: 운영 env 교체(`COLLECTOR_ORBBEC_ENABLED=0`) + `jetson-collector` 재시작(17:20:41) →
  `/status` 센서 목록 `cam_rgb_0, cam_rgb_1, thermal_0, pt100_0`(`cam_depth_0` 없음). 센서는 장비 정리 중이라 미연결.

## 남은 확인
- 재부팅 1회 후 `journalctl -b -1 -u jetson-collector | head`로 이전 부팅 로그가 보이는지(다음 재부팅 때).
