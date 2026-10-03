# 작업 지시서 — Jetson: 서비스 로그 영구 보관 (2026-10-03)

**시작:** `python3 tools/dev_session.py start jetson journal-persist` → 이 파일을 읽고 수행(장비 설정 작업, 코드 변경 없음 예상).
**배경:** Fedora 점검(10/03) — Jetson은 `/var/log/journal`이 없고 `journald.conf` `Storage=auto`라 **서비스 로그가 메모리(/run)에만**
있다. 재부팅·전원 차단 시 `jetson-collector` 로그가 사라진다. 세션 안 사건은 `events.jsonl`에 남지만, 세션 밖 오류(시작 실패·센서
재연결·서비스 재시작 원인)는 잃는다. Pi는 이미 영구 보관(`/var/log/journal` 있음).

## 할 일 (sudo — 사용자 확인 후, 진행 중 세션 없을 때)
1. `sudo mkdir -p /var/log/journal && sudo systemd-tmpfiles --create --prefix /var/log/journal`
2. `/etc/systemd/journald.conf.d/soup.conf`: `[Journal]` `Storage=persistent`, `SystemMaxUse=2G`, `MaxRetentionSec=3month`.
3. `sudo systemctl restart systemd-journald` → `journalctl --disk-usage`, `ls /var/log/journal/` 확인.
4. 재부팅 1회 후 `journalctl -b -1 -u jetson-collector | head` 로 이전 부팅 로그가 보이는지 확인(가능할 때).

## 완료 / 보고
- `notes/jetson/journal-persist.md`: 설정 파일 내용, 확인 출력, 재부팅 확인 여부. 브랜치 `jetson/journal-persist` push.
