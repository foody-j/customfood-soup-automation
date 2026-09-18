# Gemini 2를 Jetson 수집 서비스에서 실행하기

Orbbec Gemini 2의 USB 케이블은 **Jetson Orin Nano**에 연결한다. Jetson의
`jetson/collector`가 SDK로 color·depth·IR 프레임을 읽고 원본을 Jetson 디스크에
저장한다. Raspberry Pi는 기존 [제어 API](pi-jetson-api.md)로 촬영을 시작·중지하고
상태를 조회한다. Pi에서 Gemini 2 SDK나 카메라 프로세스를 실행하지 않는다.

이 문서는 **Jetson 실기기 검증 절차**다. 저장소에서의 모의 센서·단위 테스트 결과는
Gemini 2의 USB 연결, 실제 프레임 수집, 장시간 안정성을 증명하지 않는다. 실기기 확인
결과는 날짜·JetPack·SDK·펌웨어·USB 경로·세션 ID와 함께 `notes/dev-log.md` 및
`notes/data/README.md`의 규칙에 맞춰 기록한다. 원본은 Git에 넣지 않는다.

## 1. Jetson에서 SDK와 수집 서비스 설치

아래 예시는 저장소가 `/home/ubuntu/customfood-soup-automation`, 서비스 사용자가
`ubuntu`, JetPack 6 / Ubuntu 22.04 / Python 3.10인 현재 systemd 템플릿 기준이다.
다른 사용자·경로라면 서비스 파일의 `User`, `WorkingDirectory`, `ExecStart`,
`ReadWritePaths`와 환경 파일의 저장 경로를 함께 바꾼다.

```bash
cd /home/ubuntu/customfood-soup-automation
uname -m                      # aarch64 기대
python3 --version             # 사용 중인 JetPack Python 확인
python3 -m venv /home/ubuntu/collector-venv --system-site-packages
/home/ubuntu/collector-venv/bin/python -m pip install -r jetson/collector/requirements.txt
/home/ubuntu/collector-venv/bin/python -m pip install --no-deps pyorbbecsdk2   # --no-deps 필수(아래 설명)
/home/ubuntu/collector-venv/bin/python -c 'import pyorbbecsdk; print(pyorbbecsdk.__file__)'
/home/ubuntu/collector-venv/bin/python -m pip show pyorbbecsdk2
```

**`--no-deps`를 빼지 않는다(2026-09-18 이 Jetson에서 확인, pyorbbecsdk2 2.1.2).** wheel이 예제용
의존성(`opencv-python`, `open3d`, `pygame`, `av`, `pynput`)을 끌어오는데, `opencv-python`은 venv에서
JetPack의 cv2를 가리고, 의존성 빌드가 `packaging>=24.2` 문제로 실패해 설치 자체가 중단된다.
수집 서비스가 쓰는 것은 numpy(<2)와 JetPack cv2뿐이며 둘 다 이미 있다. 설치 후
`python -c 'import cv2; print(cv2.__file__)'`가 `/usr/lib/python3.10/dist-packages/`를 가리키는지 확인한다.
SDK는 실행 디렉터리에 `Log/OrbbecSDK.log.txt`를 만든다(저장소에서는 gitignore됨).

`pyorbbecsdk2`가 **설치 패키지 이름**, `pyorbbecsdk`가 **Python import 이름**이다.
Orbbec의 [Python SDK 안내](https://github.com/orbbec/pyorbbecsdk/blob/v2-main/README.md)는
Jetson Orin Nano용 Linux ARM64 wheel과 Gemini 2 지원을 명시한다. 배포 전에 실제
설치 버전을 `pip show`와 세션 메타에 남긴다. Python/아키텍처에 맞는 wheel이 없다면
무리하게 x86 wheel을 설치하지 말고 [공식 릴리스](https://github.com/orbbec/pyorbbecsdk/releases)의
ARM64 wheel 또는 공식 소스 빌드 절차를 확인한다.

Linux에서는 SDK의 udev 규칙을 한 번 설치해야 일반 서비스 사용자도 USB 장치를 열 수
있다. Orbbec가 패키지에 포함한 [환경 설정 스크립트](https://github.com/orbbec/pyorbbecsdk/blob/v2-main/scripts/env_setup/setup_env.py)를
사용한다.

```bash
SDK_SETUP=$(/home/ubuntu/collector-venv/bin/python -c \
  'import os, pyorbbecsdk; print(os.path.join(os.path.dirname(pyorbbecsdk.__file__), "shared", "setup_env.py"))')
test -f "$SDK_SETUP"
/home/ubuntu/collector-venv/bin/python "$SDK_SETUP" --check
sudo /home/ubuntu/collector-venv/bin/python "$SDK_SETUP"
/home/ubuntu/collector-venv/bin/python "$SDK_SETUP" --check
```

설치 스크립트가 `/etc/udev/rules.d/99-obsensor-libusb.rules`를 배치하고 규칙을
재적용한다. 설치 후 카메라를 다시 연결한다. 패키지에 스크립트가 없다면
[공식 `v2-main` 저장소](https://github.com/orbbec/pyorbbecsdk)의
`scripts/env_setup/setup_env.py`와 인접한 규칙 파일을 사용한다. SDK import와
udev 규칙 확인은 장치 프레임 수신과 별개의 확인 단계다.

> **2026-09-18 실기기 메모:** 규칙 설치 전에는 SDK가 `usbEnumerator openUsbDevice failed!`로 장치를 열지
> 못했다. `sudo`는 비밀번호 입력이 되는 **실제 터미널**에서 실행한다(에이전트·`!` 실행에는 TTY가 없다).
> 스크립트 대신 `sudo cp <pyorbbecsdk>/shared/99-obsensor-libusb.rules /etc/udev/rules.d/` →
> `sudo udevadm control --reload-rules && sudo udevadm trigger` → USB 재연결로도 된다. 성공하면
> `/dev/Gemini_2` 링크가 생긴다. 30 fps 기본 프로필은 depth+IR만 약 115 MB/s를 쓰므로 긴 세션은
> `"fps": 10` 등으로 낮춘다.

## 2. USB와 서비스 계정 확인

Gemini 2를 Jetson의 USB 3 포트에 데이터용 케이블로 직접 연결한 뒤 Jetson에서 실행한다.

```bash
lsusb | grep -i -E 'orbbec|2bc5'
lsusb -t                       # 해당 장치 경로가 5000M 이상인지 확인
ls -l /etc/udev/rules.d/99-obsensor-libusb.rules
sudo -u ubuntu /home/ubuntu/collector-venv/bin/python -c 'import pyorbbecsdk; print("SDK import OK")'
sudo -u ubuntu /home/ubuntu/collector-venv/bin/python - <<'PY'
from pyorbbecsdk import Context
context = Context()
devices = context.query_devices()
for i in range(devices.get_count()):
    info = devices.get_device_by_index(i).get_device_info()
    print(info.get_name(), info.get_serial_number(), info.get_firmware_version())
PY
```

`lsusb`에서 Orbbec 장치가 보이지 않으면 포트·케이블·전원부터 확인한다. `lsusb`에는
보이는데 수집 서비스의 `reason`이 권한 오류라면 udev `--check`, 재연결 및 서비스
사용자 계정을 확인한다. `lsusb -t`가 `480M`이면 USB 2 경로이므로 USB 3 포트와
케이블을 확인한다. `lsusb`의 `2bc5`는 Orbbec 공급업체 ID 확인용이며, **그 자체로
Gemini 2 모델을 확정하지 않는다**. 최종 모델·시리얼은 SDK 탐색 결과와 실물 라벨로
대조한다.

[Orbbec SDK v2 지원표](https://orbbec.github.io/pyorbbecsdk/source/1_overview/Support_platform.html)는
Gemini 2 권장 최소 펌웨어를 **1.4.92**로 제시한다. SDK가 보고한 버전과 실물
펌웨어를 대조하고, 낮은 버전이면 공식 절차로 업데이트한 뒤 다시 시험한다.
서비스가 펌웨어를 자동으로 변경하지는 않는다.

## 3. 수집 서비스 설정·기동

`jetson/collector/systemd/jetson-collector.env`를 `/etc/default/jetson-collector`로
복사한 뒤 다음 값을 확인한다. Gemini 2만 시험할 때는 `real`로 두고 시작 요청에
`cam_depth_0`만 명시한다. 저장 루트는 서비스 사용자에게 쓰기 가능하고 충분한 여유가
있어야 한다. `COLLECTOR_V4L2_DEVICES`는 GMSL2 카메라용이며 Gemini 2의 USB 장치
선택 변수로 사용하지 않는다.

```ini
COLLECTOR_SENSOR_MODE=real
# Gemini 2가 여러 대면 실물의 시리얼을 지정한다. 한 대면 비워 둔다.
COLLECTOR_ORBBEC_SERIAL=
COLLECTOR_DATA_ROOT=/home/ubuntu/collector-data
COLLECTOR_MIN_FREE_BYTES=2000000000
COLLECTOR_HOST=0.0.0.0
COLLECTOR_PORT=8000
```

```bash
cd /home/ubuntu/customfood-soup-automation
sudo install -d -o ubuntu -g ubuntu /home/ubuntu/collector-data
sudo cp jetson/collector/systemd/jetson-collector.service /etc/systemd/system/
sudo cp jetson/collector/systemd/jetson-collector.env /etc/default/jetson-collector
sudoedit /etc/default/jetson-collector   # 위 값을 반영
sudo systemctl daemon-reload
sudo systemctl enable --now jetson-collector
systemctl status jetson-collector --no-pager
journalctl -u jetson-collector -n 80 --no-pager
curl -fsS http://127.0.0.1:8000/api/v1/status | python3 -m json.tool
```

상태의 `sensors`에서 `sensor_id: "cam_depth_0"`, `kind: "depth_usb"`,
`connected: true`, `simulated: false`, SDK가 읽은 모델·시리얼을 확인한다.
`connected: false`면 `reason`을 읽고 해결한 뒤 진행한다. `mock_cam_depth_0` 또는
`simulated: true`는 실기기 합격 근거가 아니다. 코드에 있는 `verified`는 실제
수집 검증 전에는 `false`일 수 있으므로 연결 감지와 수집 성공을 구분해 기록한다.

Pi에서는 `/etc/default/soup-pi-server`의 `SOUP_JETSON_MODE=http`,
`SOUP_JETSON_URL=http://<Jetson-IP>:8000`을 설정한다. Pi가 보낸 `session_id`가
Jetson 저장 디렉터리 이름이 된다. 상세 계약은 [Pi–Jetson API](pi-jetson-api.md)를 따른다.

## 4. 세션 시작·중지 및 미리보기

아래 명령은 **Jetson**에서 로컬 API를 직접 시험하는 예다. 운영 시에는 Pi 관리 서버의
촬영 버튼이 같은 API를 호출한다. `SESSION_ID`는 매 시험마다 새 값으로 만든다.

```bash
API=http://127.0.0.1:8000/api/v1
SESSION_ID="sess-gemini2-$(date -u +%Y%m%dT%H%M%SZ)"
curl -fsS "$API/capture/start" -H 'Content-Type: application/json' -d "{
  \"session_id\": \"$SESSION_ID\",
  \"name\": \"Gemini 2 실기기 확인\",
  \"config\": {
    \"sensors\": [\"cam_depth_0\"],
    \"preview\": {\"enabled\": true, \"max_fps\": 1}
  }
}" | python3 -m json.tool
sleep 10
curl -fsS "$API/status" | python3 -m json.tool
curl -fsS "$API/capture/preview/cam_depth_0/color" -o /tmp/gemini2-color-preview.jpg
curl -fsS "$API/capture/preview/cam_depth_0/depth" -o /tmp/gemini2-depth-preview.jpg
curl -fsS "$API/capture/preview/cam_depth_0/ir" -o /tmp/gemini2-ir-preview.jpg
curl -fsS "$API/capture/stop" -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SESSION_ID\",\"reason\":\"실기기 확인 종료\"}" | python3 -m json.tool
curl -fsS "$API/status" | python3 -m json.tool
curl -fsS "$API/sessions/$SESSION_ID" | python3 -m json.tool
```

`start`는 HTTP 200이어도 `accepted: false`일 수 있다. 반드시 `accepted: true`를
확인하고 `capture.state`가 `running`이며 `capture.frames_written`과
`sensors[].stats.frames_written`이 증가하는지 본다. 미리보기는 활성 세션에서만
제공하는 JPEG이며 `config.preview.enabled=true`일 때 사용한다. 낮은 갱신율(예:
1 FPS)로 점검하고, 미리보기 JPEG를 원본 프레임으로 취급하지 않는다. `stop` 응답이
`stopping`이거나 Pi가 타임아웃되면 상태를 다시 조회해 `stopped` 및
`last_session_summary.ok: true`를 확인한다. 같은 `session_id`로 새 시험을 시작하지
않는다.

예시는 SDK의 기본 color·depth·IR 프로필과 color JPEG 저장을 선택한다. 별도
프로필이 필요하면 SDK가 지원하는 목록을 확인한 뒤 `config.fps`(세 스트림 공통),
`config.resolution`(`가로x세로` 형식, color만 적용), 또는
`config.orbbec_profiles.color|depth|ir`의 `width`·`height`·`fps`를 요청한다.
스트림별 설정은 공통 요청을 덮어쓴다. **정확히 일치하는 프로필이 없으면 센서
열기가 실패**하므로 새 세션 ID로 다시 시험한다. 적용 결과는 `session.json`의
`sensors[].applied_config.profiles`와 대조한다. 색상 저장은 기본 `jpeg`이며
`config.encoding: "raw"`를 주면 BGR8 raw 파일로 저장된다. 아래 저장 검사는 기본
JPEG 설정을 대상으로 한다.

## 5. Jetson에 저장된 원본 검사

완료된 세션은 `$COLLECTOR_DATA_ROOT/$SESSION_ID/`에 남는다. `color`는 프레임별
JPEG, `depth`는 **mm 단위 `uint16`**, `ir`은 Y8 값을 손실 없이 확장하거나 Y16
값을 유지한 `uint16` 배열이며 depth·IR 배열은 각각 `records.bin`에 이어 쓴다.
depth는 SDK Z16 코드에 `depth_scale_mm_per_code`를 곱해 반올림한 값이며, 변환 전
Z16 코드는 별도로 저장하지 않는다. 적용한 scale·원래 픽셀 형식은 인덱스 `flags`에
남는다.
분석 시 각 스트림의
`index.jsonl`에서 `path`, `offset`, `bytes`, `dtype`, `shape`, `unit`을 읽어
해당 레코드를 복원한다. 깊이의 `0` 값 등 무효 픽셀은 후처리에서 별도 취급한다.

```bash
DATA_ROOT=/home/ubuntu/collector-data
SESSION_DIR="$DATA_ROOT/$SESSION_ID"
test -f "$SESSION_DIR/session.json" && test -f "$SESSION_DIR/manifest.json"
python3 -m json.tool "$SESSION_DIR/session.json" | head -80
python3 -m json.tool "$SESSION_DIR/manifest.json" | head -80
for stream in color depth ir; do
  echo "$stream $(wc -l < "$SESSION_DIR/cam_depth_0/$stream/index.jsonl") index rows"
  head -1 "$SESSION_DIR/cam_depth_0/$stream/index.jsonl"
done
find "$SESSION_DIR/cam_depth_0" -type f -printf '%p %s bytes\n' | head -30
python3 - "$SESSION_DIR" <<'PY'
import json
import math
import sys
from pathlib import Path

root = Path(sys.argv[1])
session = json.loads((root / "session.json").read_text())
manifest = json.loads((root / "manifest.json").read_text())
assert session["state"] == manifest["state"] == "stopped"
assert all(entry["status"] == "complete" for entry in manifest["files"])
for stream in ("color", "depth", "ir"):
    index = root / "cam_depth_0" / stream / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text().splitlines() if line]
    saved = [row for row in rows if row["valid"] and row["path"]]
    assert saved, f"{stream}: 저장된 프레임 없음"
    for row in saved:
        data = root / row["path"]
        assert data.is_file(), data
        if stream == "color":
            assert data.suffix == ".jpg" and row["bytes"] > 0
        else:
            assert row["dtype"] == "uint16" and len(row["shape"]) == 2
            assert row["bytes"] == math.prod(row["shape"]) * 2
            assert row["offset"] + row["bytes"] <= data.stat().st_size
            if stream == "depth":
                assert row["unit"] == "mm"
    print(stream, "saved", len(saved), "rows", len(rows))
print("세션 파일·인덱스 기본 검사 통과")
PY
```

각 스트림에 유효한 샘플의 `path`가 있고 참조 파일이 실제로 존재해야 한다.
`depth`·`ir` 레코드는 인덱스의 `offset + bytes`가 대응 `records.bin` 크기를
넘지 않아야 한다. `manifest.json`의 `state`가 `stopped`이고 스트림 파일 상태가
`complete`인지 확인한다. SHA-256은 `COLLECTOR_CHECKSUM=after_stop`에서 종료 후
백그라운드로 계산하므로 `checksum_state: done`은 늦게 나타날 수 있다. 원본의
실제 장면과 depth 거리값·IR 영상도 눈으로 확인한다.

## 6. 문제 해결

| 증상 | 확인·조치 |
|---|---|
| `cam_depth_0`이 `connected: false` | `reason`, `lsusb`, 모델·시리얼, SDK import 확인. USB 장치가 없으면 케이블·포트·전원 점검. |
| `lsusb`에는 보이나 SDK가 열지 못함 | udev `--check`, 장치 재연결, `User=ubuntu`, `journalctl -u jetson-collector` 확인. SDK 공식 장치 탐색 예제도 실행. |
| 프로필 선택 실패 또는 일부 스트림만 기록 | `session.json`의 요청값·실제 적용값과 `events.jsonl` 확인. 지원 FPS·해상도를 SDK 탐색 결과에 맞춰 낮추고 새 세션 ID로 재시험. |
| `480M`, 낮은 FPS, 드롭 증가 | `lsusb -t`에서 USB 3 링크 확인. 케이블·허브·포트 교체, FPS/해상도 축소, `stats.jsonl`의 큐·드롭 사유 확인. |
| `accepted: false` | 응답 `message`, 기존 활성 세션, 남은 디스크 공간, 센서 연결 상태 확인. 종료된 ID는 재사용 불가. |
| 저장 실패·`failed` | `events.jsonl`, 서비스 로그, 저장 경로 소유권, `df -h` 확인. 실패 세션을 성공으로 기록하지 않는다. |
| 미리보기 404/503 | 세션이 활성인지, `preview.enabled`가 true인지, 해당 스트림 프레임이 들어왔는지 확인. 원본 인덱스와 상태를 먼저 본다. |

## 실기기 합격 조건

1. Jetson의 일반 서비스 사용자로 SDK가 Gemini 2를 탐색하고, `/status`의
   `cam_depth_0`이 `connected: true`, `simulated: false`이며 모델·시리얼이 실물과 맞다.
2. `cam_depth_0`만 지정한 새 세션의 시작 응답이 `accepted: true`이고, 실행 중
   color·depth·IR 각 스트림의 저장 수가 증가한다.
3. 활성 세션에서 color·depth·IR 미리보기가 실제 장면을 보여주며, 저장된 color
   JPEG와 depth·IR 원본 배열의 인덱스·크기·값을 별도로 확인한다.
4. stop 뒤 `capture.state: stopped`, `last_session_summary.ok: true`,
   `manifest.state: stopped`와 파일 `complete`를 확인한다. 실패·드롭·재연결이
   있었다면 수치와 원인을 함께 기록한다.
5. Pi 관리 서버에서 시작·중지·상태가 같은 세션 ID로 왕복되고, Pi의 저장 완료
   표시와 Jetson `last_session_summary`가 일치한다.

장시간 운전과 USB 분리·재연결 시험은 별도로 수행해 결과를 남긴다. 위 조건을
통과하지 않았다면 문서나 UI에 실기기 연동 완료로 표시하지 않는다.

## 공식 자료

- [Orbbec Python SDK v2 설치 및 지원 플랫폼](https://github.com/orbbec/pyorbbecsdk/blob/v2-main/README.md)
- [Orbbec Linux udev 환경 설정 스크립트](https://github.com/orbbec/pyorbbecsdk/blob/v2-main/scripts/env_setup/setup_env.py)
- [Orbbec SDK 예제와 장치 탐색 절차](https://github.com/orbbec/pyorbbecsdk/blob/v2-main/examples/README.md)
- [Gemini 2 제품 데이터시트](https://www.orbbec.com/wp-content/uploads/2023/04/ORBBEC_Datasheet_Gemini-2.pdf)
