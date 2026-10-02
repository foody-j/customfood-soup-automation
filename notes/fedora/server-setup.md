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

## 반출·접속 경로 (결정 2026-10-02)
- **원본 반출은 외장 SSD로 물리적으로 한다**(사용자 결정). Jetson에서 SSD로 복사 → Fedora에 연결 →
  `soupctl.py pull <SSD 마운트>/collector-data <id>` → sha256 전수 검증 후 `~/soup-data/raw/`로 확정.
- 네트워크(`soup-pi` Tailscale `100.92.124.114` → `soup-jetson` Pi 점프)는 접속 확인·세션 목록·manifest 상태·Pi 내보내기 같은 소량 작업용.
  실측: 9/28 세션 8.0 GB를 네트워크로 받는 데 약 30분(≈4.8 MB/s, Fedora 무선 + Pi 중계).

## 사용자 작업
1. Pi에서 `sudo tailscale up` (Pi가 Tailscale에 다시 보이게).
2. 공개키 등록 — Pi와 Jetson 각각 `~/.ssh/authorized_keys`에 한 줄 추가:
   `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIByG/4caRr4f3k1ACYKSNIZ//JewDHYFXJrfDdAEXfFf fedora-soup-integration`
3. 확인(Fedora): `ssh soup-pi hostname`, `ssh soup-jetson hostname`.
4. 보조 디스크: 내용 확인 후 백업용으로 쓸지 결정(포맷하면 기존 내용 삭제).
