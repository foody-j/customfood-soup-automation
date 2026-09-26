# Jetson Orin Nano 개발자 키트 센서 배선안

작성일: 2026-09-12. 갱신일: 2026-09-23. NVIDIA 정품 개발자 키트의 P3768 캐리어 보드, J12 40핀 헤더 기준.
핀 번호는 물리 핀 번호이며 GPIO 번호나 Linux 버스 번호가 아니다. 아래 그림은 번호 배열이며 실제 보드의 1번 핀 표시와 방향을 맞춰야 한다.

현재 확정: MLX90640 두 개(220565 / 220573), MLX90614 두 개(GY-906-DCI / BCC), VLT-THM024 MAX31865 한 개, 빨강·파랑·파랑 PT100 3선 탐침.
제안하는 추가 부품: 3.3V에서 사용 가능한 TCA9548A 브레이크아웃 두 개와 전원·접지 분배 단자대.
아직 확인할 부분: SMG 실물 보드의 입력 전압·레벨 변환 회로, Voltly의 `3V3` 핀 입출력 방향·레벨 변환 회로, MAX31865 단자 및 점퍼 배치·기준 저항, 기존 GMSL 어댑터의 헤더 점유 여부. 사용자가 확인한 Voltly 표기는 VIN/Vlogic 3~5 VDC다. 나머지는 최종 결선 전 확인한다.

> **2026-09-23 현재 구성(D-030·D-031):** 열화상은 **D55 1대(시리얼 `1708-6762-0190`)를 3/5번에 직결**한다 —
> 같은 주소 2대를 쓰지 않으므로 **TCA9548A는 쓰지 않는다**. 장착 높이는 지름 40 cm 솥 기준 **64 cm 이상**(권장 70 cm).
> MLX90614 2대는 계획에서 제외됐고(솥 내장 온도센서로 대체), 27/28번 버스와 두 mux는 당장 쓰지 않는다.
> 아래 2대 + mux 구성은 되살릴 경우를 위해 남겨 둔 기술이다.

## 연결 구조

```text
Jetson Orin Nano J12
│
├─ 3 SDA / 5 SCL ── TCA9548A A (0x70, 최대 400 kHz)
│                    ├─ CH0 SD0/SC0 ── MLX90640 D55  [220565] 0x33
│                    └─ CH1 SD1/SC1 ── MLX90640 D110 [220573] 0x33
│
├─ 27 SDA / 28 SCL ─ TCA9548A B (0x70, 100 kHz)
│                    ├─ CH0 SD0/SC0 ── MLX90614 DCI 5°  0x5A
│                    └─ CH1 SD1/SC1 ── MLX90614 BCC 35° 0x5A
│
├─ 19 MOSI / 21 MISO / 23 SCK / 별도 GPIO CS ── MAX31865 ── PT100 3선
│
├─ 1 또는 17: 3.3V ── 분배 ── 전압 적합성이 확인된 모듈 전원
└─ GND ────────────── 분배 ── 모든 모듈 GND
```

주소가 같은 센서끼리 서로 다른 채널에 놓고 한 번에 한 채널만 선택한다. 두 TCA9548A는 서로 다른 상위 버스에 있으므로 둘 다 0x70을 사용할 수 있다. A0/A1/A2는 LOW로 고정하고 RESET_N은 모듈 회로에 맞게 풀업한다. 분기 SDn은 센서 SDA, SCn은 센서 SCL로 연결한다.

열화상 버스와 MLX90614 버스를 분리하면 MLX90614의 100 kHz 제한을 지키면서 열화상 쪽 속도를 높일 수 있다. TCA9548A의 상한은 400 kHz다. SEENGREAT 예제의 800 kHz 설정을 그대로 적용하지 않는다. 열화상은 우선 각 2 Hz로 시작해 누락·읽기 시간·온도 변환 부하를 측정하며 올린다. 이 수치는 초기 시험 설정이며 검증된 최대 성능이 아니다.

근거: [TI TCA9548A](https://www.ti.com/lit/ds/symlink/tca9548a.pdf), [MLX90614 사양](https://www.melexis.com/-/media/files/documents/datasheets/mlx90614-datasheet-melexis.pdf), [SEENGREAT 예제](https://seengreat.com/wiki/89/thermal-camera-mlx90640-d110).

**실물 확인(2026-09-23):** J12 **3/5번 = `/dev/i2c-7`(c250000, 400 kHz)**, **27/28번 = `/dev/i2c-1`(c240000, 100 kHz)** 이다. MLX90640 1대를 mux 없이
1(3.3 V)/3/5/6번(= i2c-7)으로 직결해 주소 `0x33` 응답과 2 Hz 3분 연속 판독(360/360)을 확인했다. 같은 센서를 100 kHz 버스로 옮겨 재 보니 refresh 8 Hz는 전부 실패하고, 4 Hz로 낮춰야 동작하며 취득이 158 → 508 ms(3.2배)가 됐다.
**열화상 2대 × 2 Hz는 100 kHz 한 버스에서 불가능**하므로 버스 분리를 확정했다(D-028). `i2c-1`에는 보드 내장 장치 `0x25`(fusb301)·`0x40`(ina3221)이 커널 드라이버에 잡혀 있으므로,
그 버스에 mux·센서를 붙일 때 주소가 겹치지 않는지 확인한다.

## 실제 사용할 핀

| J12 물리 핀 | 기능 | 연결 대상 |
|---|---|---|
| 1 또는 17 | 3.3V | 확인된 3.3V 전원 입력에 분배 |
| 6 / 9 / 14 / 20 / 25 / 30 / 34 / 39 | GND | 공통 접지, 배치에 맞춰 선택 |
| 3 | I2C1 SDA | TCA9548A A의 상위 SDA |
| 5 | I2C1 SCL | TCA9548A A의 상위 SCL |
| 27 | I2C0 SDA | TCA9548A B의 상위 SDA |
| 28 | I2C0 SCL | TCA9548A B의 상위 SCL |
| 19 | SPI0 MOSI | MAX31865 SDI / DIN |
| 21 | SPI0 MISO | MAX31865 SDO / DOUT |
| 23 | SPI0 SCK | MAX31865 SCK / CLK |
| 24 | SPI0 CS0_N | **MAX31865 CS (D-034, 실물 확인)** — 수집기 `COLLECTOR_PT100_CS_PIN=CE0` |
| 15 / 16 | GPIO | ~~MAX31865 CS~~ — GPIO CS로는 실물 무응답(2026-09-26). 쓰지 않음 |

MAX31865의 DRDY는 초기 구성에서 연결하지 않고 상태를 읽어 처리한다. 모듈의 헤더 순서·케이블 색깔 대신 인쇄된 신호 이름을 기준으로 연결한다.
Adafruit CircuitPython MAX31865 드라이버는 `digitalio`로 별도 GPIO CS를 제어한다.
Linux/Blinka의 SPI 전송은 하드웨어 CS0를 별도로 토글할 수 있으므로 이 드라이버 경로에서
24번을 센서 CS에 연결하지 않는다. 24번을 쓰려면 하드웨어 CS를 사용하는 `spidev`
기반 드라이버를 별도로 선택해야 한다. 근거: [Adafruit Linux/Jetson SPI 안내](https://learn.adafruit.com/circuitpython-libraries-on-linux-and-the-nvidia-jetson-nano/spi-sensors-devices).

**SPI 실물 확인(2026-09-25):** `jetson-io`로 헤더 SPI를 켜면 `config-by-pin`에 **19 `spi1_dout` / 21 `spi1_din` /
23 `spi1_sck` / 24 `spi1_cs0` / 26 `spi1_cs1`** 로 나온다(캐리어 사양의 `SPI0_*` 이름과 체계가 다르다).
**19↔21번을 직접 이은 루프백으로 Linux 장치가 `/dev/spidev0.0`·`0.1`임을 확정했다** — `spidev1.x`는 헤더와 무관하다.
`i2c8 → /dev/i2c-7`과 같은 이름 불일치이니 헤더 이름을 장치 번호로 바꿔 읽지 않는다.
CS로 쓸 GPIO는 **15번 = Blinka `D22`, 16번 = `D23`**(둘 다 `unused` 상태라 사용 가능). 24·26번은 컨트롤러가 자동
토글하므로 Adafruit 드라이버 경로에서는 연결하지 않는다.

> **해결(2026-09-26, D-034):** 확정 배선 = **VIN 17(3.3 V, VIN에 인가) / GND 25 / CLK 23 / SDO 21 / SDI 19 / CS 24(하드웨어 CS0)**, 3V3·RDY 비움.
> 같은 배선에서 CS만 16번(GPIO)으로 옮기면 무응답(전부 `0x00`), 24번으로 돌리면 즉시 응답한다. 수집기는 `CE0`일 때
> Adafruit 드라이버 대신 `/dev/spidev0.0` 전이중 전송으로 읽는다(주소+데이터를 CS 한 번에). SPI 모드 1/3만 응답.
> 이 페이지 위쪽의 "24번은 연결하지 않음" 서술은 Adafruit 드라이버 경로에 한정된 것이다.
>
> **PT100 결선:** 보드의 2/3선 납땜 점퍼가 출하 상태(4선)라 3선 결선(F− 비움)은 회로 열림(원시값 `32767`, 상한 fault)으로
> 읽힌다. 당장은 **F+ 파랑1 / RTD+ 파랑2 / RTD− 빨강 + RTD−↔F− 점퍼**로 4선 모드(`wires=4`)로 읽는다 — 빨강 선 저항만큼
> 오차(보통 1 ℃ 미만)가 남는다. 정식 3선은 점퍼 납땜 후 `wires=3`. 기준 저항은 430 Ω(실온 원시값 ≈ 8486으로 확인).

## J12 전체 핀맵

```text
        홀수 핀              짝수 핀
        3.3V   [ 1] [ 2]    5V
    I2C1_SDA   [ 3] [ 4]    5V
    I2C1_SCL   [ 5] [ 6]    GND
      GPIO09   [ 7] [ 8]    UART1_TXD
         GND   [ 9] [10]    UART1_RXD
 UART1_RTS_N   [11] [12]    I2S0_SCLK
    SPI1_SCK   [13] [14]    GND
      GPIO12   [15] [16]    SPI1_CS1_N
        3.3V   [17] [18]    SPI1_CS0_N
   SPI0_MOSI   [19] [20]    GND
   SPI0_MISO   [21] [22]    SPI1_MISO
    SPI0_SCK   [23] [24]    SPI0_CS0_N
         GND   [25] [26]    SPI0_CS1_N
    I2C0_SDA   [27] [28]    I2C0_SCL
      GPIO01   [29] [30]    GND
      GPIO11   [31] [32]    GPIO07
      GPIO13   [33] [34]    GND
     I2S0_FS   [35] [36]    UART1_CTS_N
   SPI1_MOSI   [37] [38]    I2S0_DIN
         GND   [39] [40]    I2S0_DOUT
```

표는 사용 가능한 기능 이름이다. 현재 부팅 설정에서 해당 기능이 활성화됐다는 뜻은 아니다. 신호 전압은 3.3V이며 2/4번의 5V 전원과 구분한다. 헤더의 SPI0/I2C0 같은 이름을 `/dev/spidev0.0`, `/dev/i2c-0`으로 바로 치환하지 않는다.

원본: [NVIDIA Orin Nano 캐리어 보드 사양, J12 항목](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/orin_nano/docs/jetson_orin_nano_devkit_carrier_board_specification_sp.pdf).

## 전원과 배선

Jetson은 기존 어댑터로 구동한다. 센서들 때문에 대형 SMPS를 추가할 필요는 없다. SEENGREAT 두 모듈은 제조사에서 3.3V/5V 대응을 명시하므로 이 구성에서는 3.3V로 사용한다. Voltly MAX31865는 사용자가 확인한 VIN/Vlogic 3~5 VDC 표기에 따라 VIN에 3.3V를 공급한다. `3V3` 핀은 입출력 방향을 확인하기 전까지 연결하지 않는다. SMG 두 모듈은 실물의 레귤레이터 및 풀업 구성을 확인한 뒤 전원 단자를 확정한다. IC의 3.3V 사양만 보고 보드 VIN에 3.3V를 넣어도 된다고 단정하지 않는다.

모든 신호는 Jetson 쪽에서 3.3V가 되어야 한다. 외부 전원을 추가한다면 그 출력과 Jetson 3.3V 출력을 병렬로 연결하지 않는다. GND는 공유한다. 전원을 끄고 어댑터를 분리한 상태에서 배선한다.

Jetson I2C에는 기존 풀업 저항이 있다. 브레이크아웃마다 추가된 풀업을 모두 합산해 확인한다. 다른 헤더 신호는 TXB0108 특성상 강한 외부 풀업·풀다운과 충돌할 수 있어 MAX31865 보드의 레벨 변환 회로까지 확인한다. 초기 시험에서는 I2C 선을 짧게, 가능하면 20~30cm 안쪽으로 배치한다. 이는 설계 목표이며 보장되는 길이 제한은 아니다.

근거: [NVIDIA 캐리어 사양](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/orin_nano/docs/jetson_orin_nano_devkit_carrier_board_specification_sp.pdf), [SEENGREAT D55](https://seengreat.com/product/209/thermal-camera), [SEENGREAT D110](https://seengreat.com/product/208/thermal-camera-110-view).

## PT100 빨강·파랑·파랑 3선식

탐침을 모듈에서 분리하고 멀티미터 저항 모드로 확인한다. 파랑–파랑이 선 저항 수준으로 낮고, 빨강–각 파랑이 비슷한 약 100Ω대라면 두 파랑이 RTD의 같은 쪽 선이다. PT100은 0°C에서 100Ω이며 실온에서는 그보다 높다. 색깔만으로 단자 배치를 결정하지 않는다.

```text
빨강 ─────── [ PT100 저항체 ] ──┬──── 파랑 1
                                └──── 파랑 2
          ※ 위 저항 측정으로 확인했을 때의 탐침 내부 개념
```

MAX31865 보드의 3선식 점퍼 설정과 소프트웨어의 `wires=3` 설정이 모두 필요하다. 소프트웨어의 RTD 공칭 저항은 100Ω, 기준 저항 값은 실물 보드 값으로 지정한다. PT1000용 기준 저항 보드인지도 확인한다. 위 그림은 탐침 구조이며 MAX31865 단자 결선도가 아니다. Voltly 보드 앞·뒷면의 단자명과 점퍼 표시를 확인해야 빨강/파랑 각각의 최종 연결 위치를 확정할 수 있다.

근거: [MAX31865 데이터시트](https://www.analog.com/media/en/technical-documentation/data-sheets/MAX31865.pdf).

## Jetson에서 설정·검증할 순서

1. FG12-4CH 등 기존 카메라 어댑터가 J12 핀을 사용 중인지와 현재 카메라용 Device Tree 설정을 확인한다. 기존 부팅 설정을 기록한 뒤 변경한다.
2. `sudo /opt/nvidia/jetson-io/config-by-pin.py`로 현재 핀 기능을 확인한다. 필요한 경우 `sudo /opt/nvidia/jetson-io/jetson-io.py`에서 19/21/23번에 대응하는 SPI 기능과 별도 CS GPIO를 확인하고 저장·재부팅한다. 하드웨어 SPI CS0 기능이 함께 활성화돼도 Adafruit 드라이버 경로에서는 24번 핀을 MAX31865에 연결하지 않는다. 메뉴 이름보다 표시되는 물리 핀을 확인한다.
3. `i2cdetect -l`로 버스 목록을 확인하고 실제 J12 버스와 매핑한다. 이는 목록 조회이며 연결 장치 전체를 무작정 주소 스캔하는 명령이 아니다. 실제 I2C 클록도 확인해 각각 최대 400 kHz / 100 kHz로 설정한다.
4. 열화상 한 개, 비접촉 센서 한 개를 각각 먼저 시험한 뒤 나머지를 추가한다. mux는 채널 선택과 해당 센서 읽기를 한 작업으로 묶어 다른 스레드가 중간에 바꾸지 못하게 한다. 커널 mux 드라이버를 사용한다면 그 드라이버가 생성한 하위 버스로 접근한다. 수동 채널 제어와 혼용하지 않는다.
5. MAX31865 보드 사양을 확인하고 3선식으로 결선한다. 실제 SPI 장치 파일, 3선 보상 설정, 기준 저항 값, 센서 단선 오류와 실온 측정을 검증한다.
6. 각 센서의 ID·설정·취득 시작/종료 시간·값·오류를 저장한다. mux 전환은 동시 측정 트리거가 아니므로 서로 다른 센서를 완전히 같은 순간 측정했다고 기록하지 않는다.

Jetson-IO 근거: [NVIDIA Jetson Linux R36 설정 문서](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/HR/ConfiguringTheJetsonExpansionHeaders.html).

Pi는 네트워크로 Jetson 상태와 측정 결과를 받는다. 같은 센서 버스에 Pi GPIO를 함께 연결하지 않는다. Gemini 2와 GMSL2 카메라는 각자의 USB/전용 어댑터 경로를 유지한다. 플래시 구동부는 전압·전류·트리거 규격이 확정된 뒤 별도로 설계한다.
