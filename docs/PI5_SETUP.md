# Raspberry Pi 5 셋업 (Mosquitto 브로커 + 대시보드)

Pi 5는 배포(런타임) 노드로, 두 가지를 담당한다.
- **Mosquitto MQTT 브로커** — Jetson(발행) · 로봇(구독) · 대시보드(구독)가 붙는 중앙 허브
- **React 대시보드** — 조리 상태 실시간 시각화 (브라우저 키오스크로 상시 표출)

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

## 5. (선택) 키오스크 자동 표출

Pi에 모니터를 붙여 부팅 시 대시보드를 전체화면으로 띄우려면 Chromium 키오스크:

```bash
sudo apt-get install -y chromium-browser unclutter
```

자동 로그인 데스크톱 세션에서 자동 실행(`~/.config/autostart/soup-kiosk.desktop`):

```ini
[Desktop Entry]
Type=Application
Name=Soup Kiosk
Exec=chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:3000
X-GNOME-Autostart-enabled=true
```

> 헤드리스(모니터 없음)로 운영하고 다른 PC/태블릿에서 볼 거면 이 절은 건너뛴다.

---

## 6. 시간 동기화 (NTP) — 중요

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

## 7. 방화벽 · 접속 정리

로컬망 안이면 방화벽 없이도 되지만, ufw를 쓴다면 포트 개방:

```bash
sudo apt-get install -y ufw
sudo ufw allow 1883/tcp     # MQTT
sudo ufw allow 9001/tcp     # MQTT WebSocket
sudo ufw allow 3000/tcp     # 대시보드
# (로컬 NTP 쓰면) sudo ufw allow 123/udp
sudo ufw enable
```

---

## 8. (운영 전) 브로커 인증 추가 — 개발 후 권장

개발이 끝나면 `allow_anonymous true`를 끄고 사용자/비밀번호를 건다:

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd soup
# soup.conf 에서:  allow_anonymous false   /   password_file /etc/mosquitto/passwd
sudo systemctl restart mosquitto
```

> 이 경우 Jetson 발행자·로봇 구독자·대시보드 MQTT 소스에도 같은 자격증명을 넣어야 한다.

---

## 9. 최종 점검 체크리스트

- [ ] `systemctl status mosquitto` = running, `netstat`에 1883·9001 LISTEN
- [ ] `mosquitto_sub`/`mosquitto_pub` 왕복 확인
- [ ] Jetson에서 Pi 브로커로 발행 → `mosquitto_sub -h <PiIP> -t 'soup/#' -v` 로 수신 확인
- [ ] 다른 PC 브라우저에서 `http://<PiIP>:3000` 접속, 대시보드가 **mqtt 실데이터**로 갱신되는지
- [ ] `timedatectl` 시간 동기화 상태 확인
- [ ] 재부팅 후 mosquitto·soup-dashboard 자동 기동 확인

---

## 10. 트러블슈팅

| 증상 | 확인 |
|------|------|
| 대시보드가 안 뜸 | `systemctl status soup-dashboard`, `journalctl -u soup-dashboard -e` |
| 대시보드가 mock으로 보임 | `.env.production`의 `VITE_DATA_SOURCE=mqtt` 확인 후 **재빌드**(`npm run build`) 필요 |
| 브라우저가 브로커 못 붙음 | `VITE_MQTT_URL`이 `ws://`(WebSocket 9001)인지, TCP 1883 아닌지 확인 |
| Jetson이 발행 안 됨 | Jetson→Pi IP·포트(1883) 방화벽/네트워크, `mosquitto_sub`로 브로커 수신 먼저 확인 |
| 영상 안 나옴 | `VITE_POT_MJPEG_URL`이 Jetson MJPEG 실제 주소인지, Jetson HTTP 서버 동작 여부 |
| 시간 어긋남 | `timedatectl` — 스위치만이면 NTP 불가(6절 A/B) |
