# Fedora 서버 세팅 (2026-10-02~)

## 상태
| 항목 | 상태 |
|---|---|
| Git 사용자 | 완료 (`youngjin <heyyoungjin@gmail.com>`) |
| GPU 드라이버 | **보류** — 모니터 없음 + Secure Boot(MOK 등록 필요). 데이터셋·보고서는 CPU로 진행 |
| `research/.venv` | 완료 (numpy·lz4·pandas·pyarrow·pytest, Python 3.14) |
| SSH 키 | 완료 — `~/.ssh/id_ed25519_soup` (Pi·Jetson 접속 전용, 암호 없음: 무인 반출용. 이 PC 밖으로 내보내지 않음) |
| SSH 별칭 | 완료 — `~/.ssh/config`: `soup-pi`(Tailscale 100.80.193.99, user youngjin), `soup-jetson`(10.42.0.52, user ubuntu, **Pi 점프**) |
| Pi 접속 | 완료 — Tailscale IP가 `100.92.124.114`(호스트 이름 youngjin)로 바뀜, SSH 키 등록됨 |
| Jetson 접속 | 완료 — Pi 점프, SSH 키 등록됨. 데이터 루트 `/home/ubuntu/collector-data`(여유 364 GB) |
| 보조 디스크 sdb1 (477 GB NTFS) | 대기 — 마운트에 사용자 인증 필요, 내용 미확인 |
| Node/npm | 미설치(대시보드는 이번 범위 밖) |

## 반출·접속 경로 (결정 2026-10-03 — 10/02 SSD 결정을 대체)
- **매일 밤 자동 반출:** Fedora 사용자 타이머 `~/.config/systemd/user/soup-nightly.{service,timer}` — 매일 01:00(±5분), 놓친 실행은
  다음 부팅 때(`Persistent=true`). 실행 계정 linger 켬(로그인 없이 동작). 내용: `soupctl.py nightly` — Pi DB 백업 → `--since 20261003`
  이후 새 `sess-*` 반출(종료·Jetson 체크섬 완료분만) → sha256 전수 검증 → Pi 내보내기 → QC(`~/soup-data/qc/`) → 카탈로그.
  로그 `~/soup-data/logs/nightly-YYYYMMDD.log`, `journalctl --user -u soup-nightly`.
- **안전장치:** Pi `/api/status`에 진행 중 세션이 있거나 Pi 응답이 없으면 반출하지 않는다. 반출 중 촬영이 시작되거나 07:00이 지나면
  rsync를 멈추고(부분 파일 보존) 다음 밤에 이어 받는다. Nice 10·IO idle.
- **경로:** Fedora ↔ Pi는 Tailscale **직접 연결**(같은 공유기, `direct 192.168.0.47`, 외부 중계 없음), Pi ↔ Jetson은 직결 이더넷(10.42.0.0/24).
  실측 약 4.8 MB/s(병목 추정: 무선). 촬영 시간과 겹치지 않으므로 수집에 영향 없음.
- **외장 SSD는 예비:** 네트워크 장애 시 `soupctl.py pull <SSD>/collector-data <id>`.
- 관리: 끄기 `systemctl --user disable --now soup-nightly.timer`, 다음 실행 `systemctl --user list-timers`, 수동 실행 `systemctl --user start soup-nightly`.

## 사용자 작업
1. Pi에서 `sudo tailscale up` (Pi가 Tailscale에 다시 보이게).
2. 공개키 등록 — Pi와 Jetson 각각 `~/.ssh/authorized_keys`에 한 줄 추가:
   `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIByG/4caRr4f3k1ACYKSNIZ//JewDHYFXJrfDdAEXfFf fedora-soup-integration`
3. 확인(Fedora): `ssh soup-pi hostname`, `ssh soup-jetson hostname`.
4. 보조 디스크: 내용 확인 후 백업용으로 쓸지 결정(포맷하면 기존 내용 삭제).
