# jetson/cook-ready — 첫 조리 준비 측정 (2026-10-03 진행 중)

지시서: `notes/fedora/orders/20261002-cook-ready-jetson.md`. 선행 `jetson/drop-gemini`(커밋 8e6a94d, push 대기).
이 시점 장비 정리 중이라 **센서 전부 미연결** → 1~3은 재연결 후. 4·5를 먼저 수행했다.

## 4. 시계 동기 (2026-10-03 13:20 KST)
- Jetson: `systemd-timesyncd` 활성, `System clock synchronized: yes`, 서버 `1.pool.ntp.org`(3.39.176.65), stratum 2,
  **offset −2.4 ms**, delay 10.9 ms, jitter 4.2 ms, 폴링 34분. chrony·ntpd 없음. RTC UTC.
- Pi ↔ Jetson: Pi 서버 `GET http://10.42.0.1:8100/api/health`의 `server_time`(ms 단위)을 요청 전후 Jetson 시각 중간값과 비교,
  20회 — **Pi − Jetson 중앙 +1.0 ms(범위 0.6~1.5 ms)**, 왕복 중앙 2.9 ms(이더넷 10.42.0.0/24). 측정 분해능(±1.5 ms) 안이라 사실상 일치.
- 판정: 기준 0.5 s보다 두 자릿수 이상 작다 → **설정 변경 불필요.** 단, 둘 다 인터넷 NTP에 의존하므로 현장에 인터넷이 없으면
  각자 표류한다(Jetson 측정 주파수 오차 +30 ppm ≈ 시간당 0.1 s). 오프라인 현장이면 Pi를 NTP 서버로 두고 Jetson이 Pi를 따르는 구성을 제안
  (사용자 확인 후).

## 5. Fedora 반출 준비
- 경로: `/home/ubuntu/collector-data/<session_id>/`(소유 ubuntu, 디렉터리 755·파일 644 — 같은 계정 읽기에 문제 없음).
  디스크 456 GB 중 여유 363 GB(10-03), 세션 24개 46 GB.
- 체크섬(`COLLECTOR_CHECKSUM=after_stop`, 세션 종료 후 백그라운드 sha256) 완료까지 — manifest `written_at` → `checksum_done_at`:

  | 세션 | 크기 | 소요 | 속도 |
  |---|---|---|---|
  | sess-20260928T113621Z-2b33 (물 끓이기 10분, 5센서) | 8.55 GB | **25.2 s** | 339 MB/s |
  | sess-20260929T040620Z-2c54 | 6.83 GB | 19.7 s | 348 MB/s |
  | check-20260921T053231Z | 25.67 GB | 67.1 s | 383 MB/s |
  | 1 GB 안팎 세션들 | 0.2~1.4 GB | 0.2~2.1 s | 550~680 MB/s |

  → 대용량은 약 340~380 MB/s. **60분 세션(Gemini 제외 약 36 GB 추정, 1번 실측 전)이면 약 100초.** Fedora는 `checksum_state=done`을 확인한 뒤 가져간다.
- 접근: 2026-10-03 Fedora 키(`fedora-soup-integration`, ED25519)를 Jetson `~/.ssh/authorized_keys`에 추가(사용자 요청, 백업
  `authorized_keys.bak-20261002`). 현재는 **ubuntu 계정 전체 셸 권한**. sshd 활성.
- 제안(사용자 결정): Fedora 도구 `research/tools/soupctl.py`는 rsync만 쓰므로 키를 **읽기 전용 rsync로 제한** 가능 — Jetson에 `/usr/bin/rrsync` 있음.
  ```
  restrict,from="<Fedora Tailscale IP>",command="/usr/bin/rrsync -ro /home/ubuntu/collector-data" ssh-ed25519 AAAA… fedora-soup-integration
  ```
  이 경우 원격 경로가 제한 디렉터리 기준이 되므로 soupctl의 SRC를 `ubuntu@100.70.82.36:/home/ubuntu/collector-data` →
  `ubuntu@100.70.82.36:` 형태로 바꿔야 한다(Fedora 담당). 셸이 필요한 점검(로그 확인 등)은 별도 키로.
  전송 경로는 Tailscale(Jetson `100.70.82.36`) 권장.

## 3(일부). 카메라 각도·솥 ROI (2026-10-07, cam_rgb_0만 연결)
- 재연결 후 부팅 순서 주의: Sensing 보드는 **부팅(드라이버 적재) 때 한 번만** 카메라 링크를 찾는다. 카메라를 나중에 꽂으면
  `dser_link_check link:0x00 … detect error`, 보드 전원이 흔들리면 `max96712 9-006b: i2c-w, write failed`. 보드·카메라 연결 후
  재부팅(또는 `sudo systemctl stop jetson-collector && sudo systemctl restart sensing-gmsl && sudo systemctl start jetson-collector`).
  20:20 재부팅에서 포트 0 `been detected`(link 0xda), 포트 1~3 link 0x00 — **GMSL ②는 아직 미연결**.
- 렌즈 100° 광각, 위에서 거의 수직. 솥 전체가 화면 안, 중심이 가운데보다 약간 오른쪽. 30° 렌즈는 초점이 맞지 않아 사용 안 함(사용자).
- **솥 ROI 실측**(점검 세션 `check-20261007T112328Z` 프레임 003441, 1920×1536): OpenCV 허프 원(1/3 축소, medianBlur 7,
  반지름 90~140 px 제한) → 솥 안쪽 테두리 **중심 (1064, 765), 반지름 322 px**.
  사각형 비율 **(0.386, 0.288, 0.722, 0.708)**. `research/soupdata/camera.py` 기본 `ROI=(0.25,0.25,0.75,0.75)`는 인덕션 판·손잡이까지 포함.
  - 제안(Fedora 담당): 사각형 대신 **원형 마스크**, 그리고 솥이 밀릴 수 있으므로 **세션마다 첫 프레임에서 원 재검출**(반지름 범위 제한 필수 —
    제한 없이 돌리면 r≈609 px의 엉뚱한 원을 잡았다). 카메라를 옮기면 값 무효.

## 남은 일 (센서 재연결 후)
1. 저장량 실측: 4센서(Gemini 제외) 가열 없이 5분 × fps 10 / fps 2, 스트림별 바이트/분, 60분 예상, 보관 가능 세션 수, JPEG 품질(현재 90).
2. PT100 재측정: 탐침 깊이(mm) 기록, 끓는 물 3분 유지 값, 필요 시 얼음물 → 2점 보정 계수 후보.
3. 카메라: GMSL ① 흐림 원인, 고정 위치·높이·각도, 끓는 물 5분 김서림 관찰(미리보기 캡처).
