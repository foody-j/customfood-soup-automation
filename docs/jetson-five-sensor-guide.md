# Jetson Orin Nano 센서 5대 개발·검증 지침서

작성일: 2026-09-18. 대상은 MLX90640 열화상 2대, MLX90614 비접촉 온도 2대,
MAX31865 + 3선식 PT100 1대다. 이 문서는 **구현 순서와 현장 검증 기준**이며,
실물 Jetson에서 버스 번호·배선·속도가 검증됐다는 뜻은 아니다.

## 1. 개발 방식

센서마다 **단독 진단 프로그램**을 먼저 만들고, 검증된 읽기 기능을 공통 수집 서비스에서
호출한다. 최종 운영은 센서별 프로세스 5개가 아니라 **수집 서비스 1개**가 맡는다.
기존 [Jetson 수집 서비스](../jetson/collector/README.md)는 센서마다 읽기 스레드를
만든다. 같은 I²C 버스를 쓰는 어댑터끼리는 **버스별 공유 잠금**으로 mux 채널 선택부터
읽기 완료까지 직렬화한다. 별도 프로세스가 같은 mux를 제어하지 않도록 한다.

| 단계 | 구현할 것 | 다음 단계로 넘어가는 조건 |
|---|---|---|
| 0. 물리 구성 | J12 핀·I²C/SPI 버스·케이블·전원 확인 | 3.3 V와 GND, 버스 매핑, SPI 장치 확인 |
| 1. 단독 읽기 | 센서 1대씩 짧은 선에서 읽기 | 센서별 식별·값·오류 기록 성공 |
| 2. 같은 주소 2대 | mux의 서로 다른 채널에 연결 | 열화상 2대와 IR 2대를 각각 구별 |
| 3. 실제 길이 | 1.5 m 배선으로 반복 | 목표 주기의 연속 기록과 오류율 측정 |
| 4. 통합 | 5대 공통 수집 서비스 | 센서 하나의 오류가 다른 4대 수집을 멈추지 않음 |
| 5. 연동 | Pi 상태 API·원본 저장·필요 시 MQTT | 계약과 센서 ID가 일치 |

## 2. 구현 전 배선 결정

[센서 배선안](jetson-sensor-wiring.md)은
Jetson J12 **3/5번 I²C → TCA9548A A → MLX90640 2대**, **27/28번 I²C →
TCA9548A B → MLX90614 2대**, **19/21/23번 SPI + 별도 GPIO CS → MAX31865**를 제안한다.
같은 주소 `0x33`인 열화상 2대와 `0x5A`인 IR 2대는 각 mux의 CH0/CH1로 분리한다.
두 mux는 상위 버스가 서로 다르므로 둘 다 주소 `0x70`이어도 된다.

**1.5 m 4심 케이블 한 가닥으로는 위의 I²C 버스 두 개를 보낼 수 없다.**
두 버스를 유지한다면 4심 케이블 **두 가닥**을 쓴다. 각 케이블은 해당 버스의
`3.3 V / GND / SDA / SCL`을 전달하고, 센서 쪽에 해당 mux를 놓아 mux에서 센서까지는
짧게 연결한다. PT100 탐침의 3선은 별도 케이블로 Jetson 근처 MAX31865까지 보내며,
MAX31865의 SPI 배선은 짧게 둔다. 즉, 원격 센서부까지 계획상 **I²C 4심 2가닥 +
PT100 3선 1가닥**이다. 전원선 전압 강하와 센서·mux 소비 전류도 실측한다.

이 지침서의 `adafruit-circuitpython-max31865`는 `digitalio`로 **별도 GPIO CS**를
제어하는 경로를 선택한다. J12 물리 15번(GPIO12)은 후보지만, 기존 카메라 어댑터
점유와 pinmux를 확인한 뒤 확정한다. 하드웨어 SPI CS0인 **24번은 MAX31865 CS에
연결하지 않는다**. Linux/Blinka 전송 중 하드웨어 CS0가 별도로 토글될 수 있기 때문이다.
24번을 쓰려면 이 드라이버 대신 하드웨어 CS를 쓰는 `spidev` 경로로 구현을 바꿔야 한다.

4심 **한 가닥만** 사용하려면 I²C 버스 하나와 TCA9548A 한 개의 CH0~CH3에 4개
I²C 센서를 나누는 대안이 있다. 이때 MLX90614 때문에 버스 전체를 100 kHz 이하로
운용해야 하므로 열화상 2대의 프레임 속도를 보장할 수 없다. 1.5 m I²C 자체도
선로 용량·풀업·전원 상태에 따라 실패할 수 있다. **우선 짧은 선으로 성공시킨 뒤 실제
케이블에서 측정**한다. 긴 선에서 안정화되지 않으면 센서 쪽에 마이크로컨트롤러를
두고 Jetson과 장거리용 링크로 통신하는 방식으로 재설계한다. 단순히 I²C 클록만
올려 해결하려 하지 않는다.

J12의 숫자는 물리 핀 번호다. Linux `/dev/i2c-N`이나 `/dev/spidevB.C` 번호로
그대로 바꾸지 않는다. 기존 카메라 어댑터가 J12를 점유하는지도 먼저 확인한다.
모든 Jetson 입력 신호는 3.3 V여야 한다. MAX31865 모듈의 `VIN`은 사용자가 확인한
3~5 VDC 사양에 따라 3.3 V를 넣는다. 모듈의 `3V3` 핀은 입출력 방향을
실물 표기·판매자 자료로 확인하기 전까지 연결하지 않는다.
PT100은 전원을 끈 상태에서 파랑–파랑이 낮은 선 저항인지, 빨강–각 파랑이
서로 비슷한 약 100 Ω대인지 먼저 저항계로 확인한다. 그 결과를 기준으로
3선식 단자와 점퍼를 연결하고, MAX31865 보드의 **실제 기준 저항값**을 확인한다.
선 색깔만 보고 단자를 확정하지 않는다. 상세 결선 메모는 위 센서 배선안에 있다.

## 3. Jetson 준비와 실제 버스 확인

1. 전원을 끄고 배선한다. 초기에는 I²C를 20~30 cm 정도로 짧게 만든다.
2. Jetson에서 `sudo /opt/nvidia/jetson-io/config-by-pin.py`로 물리 핀 기능을
   확인한다. SPI가 비활성이라면 `sudo /opt/nvidia/jetson-io/jetson-io.py`에서
   해당 핀 기능을 저장하고 재부팅한다. 별도 CS GPIO도 출력으로 쓸 수 있는지
   확인한다. 기존 카메라용 설정을 기록하고 변경한다.
3. `i2cdetect -l`, `ls /dev/i2c-*`, `ls /dev/spidev*`로 장치 목록을 기록한다.
   실제 J12 버스와 번호를 매핑한 후 설정 파일에 쓴다. 확인 없이 `i2c-1`이나
   `spidev0.0`으로 고정하지 않는다. 하드웨어 CS0 장치 파일이 있어도
   24번 핀은 센서에 연결하지 않는다. I²C A에 mux 하나만 붙여 후보 버스에서
   `sudo i2cdetect -y <버스번호> 0x70 0x70`으로 주소를 제한해 확인하고,
   분리한 다음 I²C B에서 반복하면 두 버스를 구별할 수 있다.
4. mux 하나와 센서 하나만 연결한 상태에서 해당 채널의 예상 주소를 확인한다.
   전체 시스템의 임의 버스를 반복 스캔하지 않는다. 실제 I²C 버스 클록도 확인한다. MLX90614
   버스는 100 kHz, 열화상 버스는 TCA9548A 한도 400 kHz **이하**로 둔다.
   Linux 버스 클록은 Python의 객체 생성 인자만으로 바뀐다고 가정하지 않는다.
5. 사용 중인 Jetson의 Python 환경에서 필요한 패키지를 설치한다. 단독 검증과
   운영에서 같은 버전으로 재현할 수 있게 버전을 기록한다.

```bash
sudo apt install i2c-tools
cd ~/customfood-soup-automation/jetson/collector
source ~/collector-venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install adafruit-blinka adafruit-extended-bus \
  adafruit-circuitpython-tca9548a adafruit-circuitpython-mlx90640 \
  adafruit-circuitpython-mlx90614 adafruit-circuitpython-max31865
python -m pip freeze > ~/sensor-requirements-tested.txt
```

`~/collector-venv`는 기존 [수집 서비스 README](../jetson/collector/README.md)의
환경이다. 아직 만들지 않았다면 해당 README의 `--system-site-packages` 절차를 따른다.
현장 검증 후 센서 패키지 버전을 `jetson/collector/requirements.txt`에 반영한다.
설치 결과와 재부팅 후 장치 파일 존재 여부를 기록한다. MAX31865 Python SPI의
CS 핀 매핑도 실제 헤더에서 확인한다.

## 4. 프로그램 구조

```text
jetson/collector/                       # 이미 구현된 수집 서비스 확장
  app/config.py                         # 실측 버스 번호, 채널, GPIO CS 설정 추가
  app/sensors/base.py                   # SensorAdapter·StreamSpec·Sample, RTD kind 추가
  app/sensors/thermal_mlx90640.py       # 새 실물 어댑터: temp_array
  app/sensors/point_mlx90614.py         # 새 실물 어댑터: temp
  app/sensors/rtd_max31865.py           # 새 실물 어댑터: temp + fault
  app/sensors/registry.py               # 실물 5대 등록, MLX 미지원 스텁 교체
  app/session.py                        # 기존 센서별 스레드·재연결·통계 재사용
  app/storage.py                        # 기존 세션 원본·인덱스 저장 재사용
  tools/                                # 버스·mux·센서 단독 진단 도구 추가
```

각 실물 어댑터는 기존 `SensorAdapter`의 `probe/open/read/close`를 구현하고
`Sample`을 반환한다. `registry.build_sensors()`에 등록하고 기존 MLX 미지원
스텁을 교체한다. 센서마다 별도 서비스를 새로 만들지 않는다.
기존 `열화상 카메라/fire.py`는 단일 카메라·Pi 핀·OpenCV 화면
출력을 한 파일에서 처리하고, `비접촉 온도 센서/fire1.py`는 `/dev/i2c-1`과
주소 `0x5A`가 고정돼 있다. 두 파일을 그대로 이어 붙이지 않는다.

권장 Python I²C 경로는 `ExtendedI2C(실측 버스 번호)` → 해당 버스의
`adafruit_tca9548a.TCA9548A` → `tca[채널]` → `adafruit_mlx90640.MLX90640`
또는 `adafruit_mlx90614.MLX90614`다. 두 센서 드라이버가 같은 I²C 객체 형식을
받으므로 기존 `smbus2` 단일 센서 코드를 mux에 억지로 결합할 필요가 없다.
MAX31865는 독립 SPI 드라이버와 확인된 GPIO CS 핀을 사용하고 `wires=3`, `rtd_nominal=100`,
`ref_resistor=실물 보드 저항값`으로 설정한다. 기본값 430 Ω이 실물과 같다고
가정하지 않는다.

| 고정 sensor_id | 장치 | 버스/채널 | 처음 목표 |
|---|---|---|---|
| `thermal_0` | MLX90640 55° | I²C A / CH0 / `0x33` | 2 Hz |
| `thermal_1` | MLX90640 110° | I²C A / CH1 / `0x33` | 2 Hz |
| `point_temp_0` | MLX90614 5° | I²C B / CH0 / `0x5A` | 1 Hz |
| `point_temp_1` | MLX90614 35° | I²C B / CH1 / `0x5A` | 1 Hz |
| `pt100_0` | MAX31865 + PT100 | SPI + 별도 GPIO CS | 1 Hz |

위 주기는 **초기 시험 목표**다. `MLX90640` 2 Hz×2의 실제 취득 시간과
오류율을 먼저 측정한다. 센서 고유 주소는 ID가 아니므로 두 카메라와 두 IR 센서를
각각 다른 `sensor_id`로 식별한다. FOV가 다른 두 열화상 중 어느 것을 AI 입력으로
쓸지는 장착 거리와 실측 후 별도로 결정한다. 기존 `thermal_0`·`point_temp_0`
ID는 유지하고 FOV·모델·채널을 `SensorProbe`의 메타데이터에도 남긴다.

## 5. 단계별 개발·시험 순서

1. **PT100 단독:** 탐침 3선의 저항 관계와 MAX31865 점퍼·기준 저항을 확인한다.
   SPI 한 대만 연결해 실온 근처 값, 센서 단선·단락 시 fault 상태를 기록한다.
2. **열화상 첫 대:** `thermal_0`만 짧은 선에 연결한다. mux CH0에서 주소
   `0x33`, 24×32 = 768개 유한한 섭씨값, 프레임 시작·종료 시각을 확인한다.
   `getFrame()`의 일시적 `ValueError`는 제한 횟수만 재시도하고 오류를 남긴다.
3. **열화상 두 대:** `thermal_1`을 CH1에 추가한다. 채널을 번갈아 읽고
   두 카메라의 ID·장면이 뒤바뀌지 않는지 확인한다. 두 개를 모두 열어 놓은 채
   같은 버스의 두 스레드가 mux를 동시에 제어하지 않도록 잠근다.
4. **IR 첫 대, 두 대:** `point_temp_0`만 100 kHz 버스에서 읽은 뒤 `point_temp_1`을 CH1에
   추가한다. 각각 `object_temperature`, `ambient_temperature`를 기록한다.
   FOV가 다르므로 같은 물체를 바라봐도 값이 동일해야 한다는 기준을 쓰지 않는다.
5. **실제 배선 길이:** 위 네 I²C 센서를 1.5 m 케이블로 옮기고 짧은 선 결과와
   비교한다. 전원 강하, 읽기 지연, 오류/재시도, 누락 프레임을 기록한다.
6. **5대 통합:** 기존 `jetson/collector`의 센서별 수집 스레드·재연결·저장 기능에
   실물 어댑터를 붙인다. 같은 버스에서는 mux 채널 선택부터 측정 완료까지 다른
   스레드가 끼어들지 않게 공유 잠금으로 직렬화한다. 한 센서의 오류는 해당 센서의
   상태·사건 기록에 남기고 나머지 센서는 계속 읽는다.
7. **연속 운전:** 30분 이상 5대 동시 기록, 케이블 접촉 변화, 서비스 재시작,
   센서 한 대 분리·재연결을 시험한다. 실측 FPS·최대 공백·오류율을 표로 남긴 뒤
   목표 주기를 확정한다.

초기 통과 기준안: 짧은 선에서 각 장치 식별과 정상 샘플 확인, 실제 길이에서
30분 동안 서비스 중단 없음, 모든 기록에 ID·시각·상태 존재, 센서 한 대의 장애가
나머지 수집을 멈추지 않음. 프레임 성공률 목표는 99% 이상으로 두되 실측 결과가
낮으면 먼저 속도·배선·풀업을 조사하고 실제 달성 수치를 기록한다.

## 6. 저장·연동 규칙

새 저장 형식을 만들지 않고 기존 `Sample`·`StreamSpec`·`SessionStore` 계약에 맞춘다.
열화상 `temp_array`는 `(24, 32)` `float32` 섭씨 원시 배열로 `records.bin`에
저장하고 `index.jsonl`에 오프셋을 남긴다. IR `temp`는 물체·주변 온도를 담은
scalar로 기록한다. PT100은 새 `kind`와 scalar 스트림에 온도·MAX31865 fault를
담는다. 각 샘플에는 기존 `host_recv_utc`·`host_recv_mono_ns`가 남으며, 장치 시각을
제공하지 않으면 `device_ts: null`로 둔다. 실패는 `valid`·`invalid_reason` 또는
`events.jsonl`에 근거와 함께 기록한다. mux로 순차 측정하므로 서로 다른 센서의
시각을 임의로 같게 쓰거나 낡은 마지막 값을 새 측정값처럼 표시하지 않는다.

수집 서비스 상태는 [Pi–Jetson API 계약](pi-jetson-api.md)의 `sensors[]`에
5개 ID와 `sensors[].stats`를 보고하고, 종료 후 `last_session_summary`에 저장 결과를
반영한다. 현재 `kind` 목록에는 SPI RTD가 없으므로 연동 단계에서
`pi-jetson-api.md`와 `pi-server/app/models.py`를 함께 개정한다. 현재
[MQTT 데이터 계약](data-schema.md)은 열화상 1대·중심온도 1개만 담는다.
5대의 전체 원본을 Pi나 대시보드에 보낼지 먼저 정하고, 계약을 바꿀 경우
`docs/data-schema.md`, `shared/schema.json`, `dashboard/src/data/schema.js`를
함께 갱신한다. 원본 수집 자체는 MQTT 계약 결정과 독립적으로 시작할 수 있다.

## 참고 자료

- [NVIDIA Orin Nano 개발자 키트 J12 사양](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/orin_nano/docs/jetson_orin_nano_devkit_carrier_board_specification_sp.pdf)
- [NVIDIA Jetson-IO 설정](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/HR/ConfiguringTheJetsonExpansionHeaders.html)
- [TI TCA9548A 데이터시트](https://www.ti.com/lit/ds/symlink/tca9548a.pdf)
- [Adafruit TCA9548A 채널 사용법](https://learn.adafruit.com/adafruit-tca9548a-1-to-8-i2c-multiplexer-breakout/circuitpython-python)
- [Adafruit Linux ExtendedI2C API](https://docs.circuitpython.org/projects/extended_bus/en/latest/api.html)
- [Adafruit MLX90640 API](https://docs.circuitpython.org/projects/mlx90640/en/latest/api.html)
- [Adafruit MLX90614 API: 100 kHz](https://docs.circuitpython.org/projects/mlx90614/en/stable/api.html)
- [Adafruit MAX31865 API](https://docs.circuitpython.org/projects/max31865/en/latest/api.html)
- [Adafruit Linux/Jetson SPI의 별도 GPIO CS 설명](https://learn.adafruit.com/circuitpython-libraries-on-linux-and-the-nvidia-jetson-nano/spi-sensors-devices)
