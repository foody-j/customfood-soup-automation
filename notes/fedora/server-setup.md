# Fedora 서버 세팅 (2026-10-02~)

## 상태
| 항목 | 상태 |
|---|---|
| Git 사용자 | 완료 (`youngjin <heyyoungjin@gmail.com>`) |
| GPU 드라이버 | **보류** — 모니터 없음 + Secure Boot(MOK 등록 필요). 데이터셋·보고서는 CPU로 진행 |
| `research/.venv` | 완료 (numpy·lz4·pandas·pyarrow·pytest, Python 3.14) |
| SSH 키 | 완료 — `~/.ssh/id_ed25519_soup` (Pi·Jetson 접속 전용, 암호 없음: 무인 반출용. 이 PC 밖으로 내보내지 않음) |
| SSH 별칭 | 완료 — `~/.ssh/config`: `soup-pi`(Tailscale 100.80.193.99, user youngjin), `soup-jetson`(10.42.0.52, user ubuntu, **Pi 점프**) |
| Pi 접속 | 대기 — Pi Tailscale offline(9/16~). 공개키 등록 필요 |
| Jetson 접속 | 대기 — Pi 점프 경로. 공개키 등록 필요 |
| 보조 디스크 sdb1 (477 GB NTFS) | 대기 — 마운트에 사용자 인증 필요, 내용 미확인 |
| Node/npm | 미설치(대시보드는 이번 범위 밖) |

## 접속 경로 결정 제안
Jetson에 Tailscale을 새로 깔지 않고 **Pi를 점프 호스트**로 쓴다(Pi만 Tailscale 연결되면 됨, Jetson 네트워크 구성 변경 없음).
반출: `soupctl.py pull soup-jetson:/home/ubuntu/collector-data <id>`. 전송은 Pi를 거치므로 Pi 유선·무선 대역폭이 상한.
10분 세션(≈수 GB) 반출 시간을 첫 실물에서 재고, 느리면 Jetson Tailscale 직접 연결로 바꾼다.

## 사용자 작업
1. Pi에서 `sudo tailscale up` (Pi가 Tailscale에 다시 보이게).
2. 공개키 등록 — Pi와 Jetson 각각 `~/.ssh/authorized_keys`에 한 줄 추가:
   `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIByG/4caRr4f3k1ACYKSNIZ//JewDHYFXJrfDdAEXfFf fedora-soup-integration`
3. 확인(Fedora): `ssh soup-pi hostname`, `ssh soup-jetson hostname`.
4. 보조 디스크: 내용 확인 후 백업용으로 쓸지 결정(포맷하면 기존 내용 삭제).
