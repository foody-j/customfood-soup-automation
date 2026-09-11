# Raspberry Pi 5 셋업 (관리 서버 + Mosquitto 브로커 + 대시보드)

Pi 5는 **장비 관리 PC 겸 배포 런타임 노드**로, 세 가지를 담당한다.
- **관리 서버** (`pi-server/`, FastAPI, 포트 8100) — Jetson 수집 서비스 상태 감시,
  촬영 시작/중지, 실험 설정·조작 이력. **Jetson이 꺼져 있어도 동작한다.** ← 5절
- **Mosquitto MQTT 브로커** — Jetson(발행) · 로봇(구독) · 대시보드(구독)가 붙는 중앙 허브
- **React 대시보드** — 조리 상태 실시간 시각화 (브라우저 키오스크로 상시 표출)

> 모니터는 **Pi에 직결**한다(D-011 — D-008 헤드리스 개정). 장비 옆에서 즉시 조작할
> 화면이 필요하고, 그 화면은 Jetson이 꺼진 상태에서도 떠 있어야 하기 때문.

> 통신 구조: Jetson·로봇은 표준 MQTT(**1883/TCP**)로, 브라우저 대시보드는
> **MQTT-over-WebSocket(9001)** 으로 같은 브로커에 붙는다. 그래서 리스너를 **둘 다** 연다.
> 솥 RGB 영상은 MQTT가 아니라 Jetson이 직접 서빙하는 MJPEG(HTTP)로 대시보드에 표출한다.

---

## 0. 저장소 클론 (최초 1회)

프로젝트는 GitHub(private)에 있으므로 **새로 만들지 말고 clone**한다. 인증은 SSH 키 권장.

```bash
sudo apt-get update && sudo apt-get install -y git
ssh-keygen -t ed25519 -C "pi5-broker"          # 엔터로 기본 경로
cat ~/.ssh/id_ed25519.pub                       # 출력 → GitHub Settings > SSH keys 에 등록
git clone git@github.com:foody-j/customfood-soup-automation.git ~/customfood-soup-automation
cd ~/customfood-soup-automation && git log --oneline -3   # 최신 커밋 보이면 성공
```

> 이후 업데이트는 작업 전 `git pull` 먼저(히스토리 분기 방지).

---

## 1. 기본 확인 · 시스템 업데이트

Raspberry Pi OS (64-bit, Bookworm 이상) 기준.

```bash
cat /etc/os-release          # Debian 12(bookworm) / 64-bit 확인
uname -m                     # aarch64 확인
sudo apt-get update && sudo apt-get -y full-upgrade
sudo reboot                  # 커널 업데이트 시
```

고정 IP 권장(브로커 주소가 바뀌면 Jetson·로봇·대시보드 설정을 다 고쳐야 함). 라우터 DHCP 예약
또는 `nmtui`로 Pi에 고정 IP 부여. 이 문서는 예시로 **`192.168.0.50`** 을 브로커 IP로 쓴다.

---

## 2. Mosquitto MQTT 브로커 설치

```bash
sudo apt-get install -y mosquitto mosquitto-clients
```

설정 파일 `/etc/mosquitto/conf.d/soup.conf` 를 만든다(두 리스너 + 접근 허용):

```conf
# /etc/mosquitto/conf.d/soup.conf

# 표준 MQTT (Jetson 발행, 로봇 구독)
listener 1883 0.0.0.0

# MQTT-over-WebSocket (브라우저 대시보드 구독)
listener 9001 0.0.0.0
protocol websockets

# 개발 단계: 인증 없이 로컬망 허용. (운영 전 인증 추가 권장 — 8절 참고)
allow_anonymous true
```

적용 · 자동 시작:

```bash
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager     # active (running) 확인
```

**동작 확인** (터미널 2개):

```bash
# 터미널 A — 구독
mosquitto_sub -h localhost -t 'soup/#' -v
# 터미널 B — 발행
mosquitto_pub -h localhost -t 'soup/test' -m 'hello'
# A 에 soup/test hello 가 뜨면 브로커 정상
```

WebSocket(9001) 확인:

```bash
sudo apt-get install -y net-tools
sudo netstat -tlnp | grep -E '1883|9001'       # 두 포트 LISTEN 확인
```

---

## 3. Node.js 설치 · 대시보드 빌드

대시보드는 React + Vite라 Node로 빌드해 정적 파일을 서빙한다. Node 20 LTS 설치:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node -v && npm -v
```

빌드:

```bash
cd ~/customfood-soup-automation/dashboard
npm install
```

**MQTT 모드 설정** — `dashboard/.env.production` (또는 `.env.local`) 생성:

```dotenv
# 대시보드가 붙을 브로커 (WebSocket 리스너 9001) — Pi 자신의 IP
VITE_DATA_SOURCE=mqtt
VITE_MQTT_URL=ws://192.168.0.50:9001
# 솥 RGB 영상 (Jetson이 서빙하는 MJPEG) — Jetson IP:포트에 맞게
VITE_POT_MJPEG_URL=http://192.168.0.51:8080/stream
```

> 코드상 기본값은 `VITE_DATA_SOURCE=mock`, `VITE_MQTT_URL=ws://raspberrypi.local:9001`
> (`src/data/useCookingData.js`). MJPEG 미설정 시 대시보드는 "스트림 URL 미설정"을 표시한다.

빌드 · 미리보기:

```bash
npm run build                    # dist/ 생성
npm run preview -- --host        # 임시 확인용 (기본 4173 포트)
```

---

## 4. 대시보드 상시 서빙 (정적 서버 + 자동 시작)

`dist/`를 가벼운 정적 서버로 상시 서빙한다. 예: `serve`.

```bash
sudo npm install -g serve
# 수동 테스트: serve -s dist -l 3000
```

systemd 서비스로 등록 — `/etc/systemd/system/soup-dashboard.service`:

```ini
[Unit]
Description=Soup Dashboard (static serve)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/customfood-soup-automation/dashboard
ExecStart=/usr/bin/serve -s dist -l 3000
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now soup-dashboard
sudo systemctl status soup-dashboard --no-pager
# 다른 PC 브라우저에서 http://192.168.0.50:3000 접속되면 성공
```

> 코드를 갱신했을 때: `git pull && npm install && npm run build && sudo systemctl restart soup-dashboard`

---

## 5. Pi 관리 서버 (FastAPI) 설치 · 상시 실행

Jetson 상태 감시와 촬영 제어를 담당한다. **Jetson·카메라가 없어도 모의 모드로 바로 뜬다.**
자세한 설계·API는 `pi-server/README.md`, Jetson 쪽 계약은 `docs/pi-jetson-api.md`.

```bash
cd ~/customfood-soup-automation/pi-server
python3 -m venv .venv                       # Debian 12/13은 시스템 pip 설치가 막혀 있어 venv 필수
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q               # (선택) 테스트 14개
```

수동 실행으로 먼저 확인:

```bash
SOUP_JETSON_MODE=mock SOUP_POWER_MODE=mock .venv/bin/python -m app.main
# 브라우저에서 http://localhost:8100 — "모의 모드" 배지가 보이면 정상
```

systemd 등록(부팅 시 자동 실행, 실패 시 재시작):

```bash
sudo cp systemd/soup-pi-server.service /etc/systemd/system/
sudo cp systemd/soup-pi-server.env /etc/default/soup-pi-server
sudo nano /etc/default/soup-pi-server      # SOUP_JETSON_URL, SOUP_JETSON_MODE 등 수정
sudo systemctl daemon-reload
sudo systemctl enable --now soup-pi-server
systemctl status soup-pi-server --no-pager
```

> **서비스 파일의 `User=` · `WorkingDirectory=` · `ExecStart=` 경로**가 실제 계정/경로와
> 맞는지 확인할 것(기본값은 `yj-rpi`).

**로그 확인** — 서비스 로그는 journald로 간다:

```bash
journalctl -u soup-pi-server -f              # 실시간
journalctl -u soup-pi-server --since today   # 오늘치
```

장비 운영 이력(촬영·오류·조작)은 서비스 로그가 아니라 **SQLite에 따로** 쌓이고
관리 화면 하단에 보인다. **보존 기간(기본 5000건/90일)이 지나면 사라지므로,
실험이 끝나면 내보내 둘 것**:

```bash
curl -OJ "http://localhost:8100/api/events/export?format=csv"
```

자세한 로그 정책은 `pi-server/README.md`의 "로그" 절 참고.

**실제 Jetson 연결로 전환** — `/etc/default/soup-pi-server`에서:

```dotenv
SOUP_JETSON_MODE=http
SOUP_JETSON_URL=http://192.168.0.51:8000
SOUP_JETSON_PROBE_PORT=22        # 수집 서비스가 죽어도 열려 있는 포트(호스트 생존 확인용)
```

Jetson 수집 서비스는 아직 구현 전이므로, 전환하면 화면이 `무응답`/`수집 서비스 중단`으로
표시된다 — **정상 동작이다**(관리 화면 자체는 계속 뜬다).

---

## 6. (권장) 키오스크 자동 표출 — 관리 화면

Pi 직결 모니터에 부팅 시 **관리 화면**을 전체화면으로 띄운다(D-011).

```bash
sudo apt-get install -y chromium unclutter
```

자동 로그인 데스크톱 세션에서 자동 실행(`~/.config/autostart/soup-kiosk.desktop`):

```ini
[Desktop Entry]
Type=Application
Name=Soup Kiosk
Exec=chromium --kiosk --noerrdialogs --disable-infobars http://localhost:8100
X-GNOME-Autostart-enabled=true
```

> 조리 대시보드를 띄우려면 주소를 `http://localhost:3000`으로 바꾼다. 둘 다 필요하면
> 탭 2개(`--kiosk` 대신 창 모드)나 별도 모니터를 쓴다.
> 패키지 이름은 Bookworm 이후 `chromium-browser` → `chromium`으로 바뀌었다.

---

## 7. 시간 동기화 (NTP) — 중요

타임스탬프 정합이 중요한데(센서·영상 로그 대조), **기가비트 스위치만 쓰면 인터넷이 없어 NTP 불가**.
택1:
- **(A) 공유기 사용** — 인터넷 연결 시 `systemd-timesyncd`가 자동 동기화. 확인: `timedatectl`
- **(B) 로컬 시간서버** — Pi를 로컬 NTP 서버로 만들어 Jetson·로봇이 Pi에 동기화
  ```bash
  sudo apt-get install -y chrony
  # /etc/chrony/chrony.conf 에 로컬망 허용 한 줄 추가:
  #   allow 192.168.0.0/24
  #   local stratum 10
  sudo systemctl restart chrony
  ```
  → Jetson·로봇의 NTP 서버를 Pi IP(192.168.0.50)로 지정.

---

## 8. 방화벽 · 접속 정리

로컬망 안이면 방화벽 없이도 되지만, ufw를 쓴다면 포트 개방:

```bash
sudo apt-get install -y ufw
sudo ufw allow 1883/tcp     # MQTT
sudo ufw allow 9001/tcp     # MQTT WebSocket
sudo ufw allow 3000/tcp     # 조리 대시보드
sudo ufw allow 8100/tcp     # Pi 관리 서버
# (로컬 NTP 쓰면) sudo ufw allow 123/udp
sudo ufw enable
```

---

## 9. (운영 전) 브로커 인증 추가 — 개발 후 권장

개발이 끝나면 `allow_anonymous true`를 끄고 사용자/비밀번호를 건다:

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd soup
# soup.conf 에서:  allow_anonymous false   /   password_file /etc/mosquitto/passwd
sudo systemctl restart mosquitto
```

> 이 경우 Jetson 발행자·로봇 구독자·대시보드 MQTT 소스에도 같은 자격증명을 넣어야 한다.

---

## 10. 최종 점검 체크리스트

- [ ] `systemctl status soup-pi-server` = running, `curl localhost:8100/api/health` = `{"ok":true}`
- [ ] **Jetson 전원을 끈 상태에서** `http://<PiIP>:8100` 접속 → 화면이 뜨고 상태가
      `무응답`/`전원 꺼짐`으로 표시되는지 (관리 화면 독립 동작 확인)
- [ ] `systemctl status mosquitto` = running, `netstat`에 1883·9001 LISTEN
- [ ] `mosquitto_sub`/`mosquitto_pub` 왕복 확인
- [ ] Jetson에서 Pi 브로커로 발행 → `mosquitto_sub -h <PiIP> -t 'soup/#' -v` 로 수신 확인
- [ ] 다른 PC 브라우저에서 `http://<PiIP>:3000` 접속, 대시보드가 **mqtt 실데이터**로 갱신되는지
- [ ] `timedatectl` 시간 동기화 상태 확인
- [ ] 재부팅 후 soup-pi-server·mosquitto·soup-dashboard 자동 기동 확인

---

## 11. 트러블슈팅

| 증상 | 확인 |
|------|------|
| 관리 화면이 백지·무한 로딩 | **주소창이 `0.0.0.0:8100`이 아닌지 확인.** uvicorn이 찍는 `0.0.0.0`은 바인딩 주소라 접속 불가 → `localhost:8100`(Pi 자신) 또는 `<PiIP>:8100`(다른 기기) |
| 관리 화면이 안 뜸 | `systemctl status soup-pi-server`, `journalctl -u soup-pi-server -e` |
| 관리 화면에 "모의 모드" 배지 | `/etc/default/soup-pi-server`의 `SOUP_JETSON_MODE=http` 확인 후 재시작 |
| Jetson이 계속 `수집 서비스 중단` | 호스트는 살아 있고 수집 서비스만 죽은 상태 — Jetson에서 서비스 재시작. (수집 서비스 구현 전에는 정상) |
| Jetson이 계속 `무응답` | IP·랜선·스위치 확인. `SOUP_JETSON_PROBE_PORT`(기본 22)가 Jetson에서 열려 있는지 |
| 전원 버튼이 비활성 | 정상 — 전원 회로 미구성(`SOUP_POWER_MODE=unsupported`). 플랜 6단계에서 활성화 |
| 대시보드가 안 뜸 | `systemctl status soup-dashboard`, `journalctl -u soup-dashboard -e` |
| 대시보드가 mock으로 보임 | `.env.production`의 `VITE_DATA_SOURCE=mqtt` 확인 후 **재빌드**(`npm run build`) 필요 |
| 브라우저가 브로커 못 붙음 | `VITE_MQTT_URL`이 `ws://`(WebSocket 9001)인지, TCP 1883 아닌지 확인 |
| Jetson이 발행 안 됨 | Jetson→Pi IP·포트(1883) 방화벽/네트워크, `mosquitto_sub`로 브로커 수신 먼저 확인 |
| 영상 안 나옴 | `VITE_POT_MJPEG_URL`이 Jetson MJPEG 실제 주소인지, Jetson HTTP 서버 동작 여부 |
| 시간 어긋남 | `timedatectl` — 스위치만이면 NTP 불가(7절 A/B) |
