# FG12-4CH GMSL 카메라 설치 방법 (Jetson Orin Nano / JetPack 6.2 R36.4.3)

작성: 2026-07-22 / 리플래시 복구 후 마지막 단계
전제: JetPack 풀스택 설치 완료 (torch cuda True, tensorrt 10.3, trtexec 확인됨)

---

## ✅ 검증된 성공 기록 (2026-07-27, ISX031F 1대) — 급하면 여기만 봐도 됨

실제로 이 순서로 영상까지 나옴. 아래 상세 챕터는 배경 설명이고, **핵심은 이 5단계**:

**증상이었던 것:** 드라이버·리본·전원 다 정상인데 `dmesg`에 `fzcam 10-001a: channel not found`만 반복,
`fzcam_cfg`는 `Video[0-1-2-3] Link satus:0-0-0-0`. → **설정 파일이 실제 배선과 반대**여서였음.

1. **보드가 어느 버스에 붙었는지 확인** (디시리얼라이저 MAX96712 = 0x29):
   ```bash
   i2cdetect -y -r 9    # 0x29 있으면 → video0-3 그룹([GMSL1])
   i2cdetect -y -r 10   # 0x29 있으면 → video4-7 그룹([GMSL2])   ← 우리 보드는 여기
   ```
   버스9=GMSL1=/dev/video0-3, 버스10=GMSL2=/dev/video4-7. **0x29 뜨는 쪽이 실제 보드.**

2. **`/etc/fzcam_cfg.ini`에서 그 그룹의 `position`을 켜기.** 기본값이 `Video_0000`(꺼짐)이라 링크가 안 올라왔던 것.
   우리는 버스10(GMSL2)에 ISX031F 1대를 J2(LinkA)에 꽂았으므로:
   ```ini
   [GMSL1]
   position=Video_0000          # 버스9는 비었으니 끔
   ...
   [GMSL2]
   position=Video_1000          # ★ LinkA 1대 켜기 (핵심). 자리수 = A B C D
   resolution=1920x1536
   sensor=ISX031F
   serializer=MAX96717F
   vendor=Sensing
   ```
   > `position` 4자리 = LinkA/B/C/D(=J2/J3/J4/J5). 1대=Video_1000, 2대=Video_1100, 4대=Video_1111.

3. **적용 + 재부팅:**
   ```bash
   sudo cp /etc/fzcam_cfg.ini /etc/fzcam_cfg.ini.bak   # 백업(이미 있음)
   sudo cp <새 ini> /etc/fzcam_cfg.ini
   sudo reboot
   ```

4. **링크 확인** — LinkA에 1이 떠야 성공:
   ```bash
   fzcam_cfg    # → Video[4-5-6-7] Link satus:1-0-0-0
   ```
   (부팅 로그의 `channel not found`는 fzcam_cfg 서비스가 링크 올리기 *전* 커널 프로브라 정상. 무시.)

5. **실제 캡처 테스트** — 노드는 `/dev/video0`이 아니라 **`/dev/video4`**:
   ```bash
   v4l2-ctl -d /dev/video4 --set-fmt-video=width=1920,height=1536,pixelformat=UYVY \
     --stream-mmap --stream-count=10 --stream-to=cam4.raw
   # 파일이 5898240*10 바이트면 성공. (1프레임 = 1920*1536*2)
   ```
   프로젝트 코드에서 카메라 경로는 **`/dev/video4`** 사용.

교훈(★방법론이 핵심): 케이블/전원 의심 전에 **`i2cdetect`로 0x29(디시리얼라이저)가 뜨는지부터** 봐라.
0x29가 보이면 보드는 살아있는 것 → 남은 건 거의 항상 `fzcam_cfg.ini`의 `position`/센서 설정 문제.
이 순서는 커넥터를 어디에 꽂았든(CAM0/CAM1, J12/J13) **버스 번호로 그룹을 찾아 맞추는** 방식이라 셋업이 달라도 그대로 통함.

> 참고(이번에 실제로 한 배선): 젯슨쪽 리본 = **CAM1**. 근데 위 방법은 이걸 몰라도 되게 짜여 있음 —
> i2cdetect 결과(→버스10=video4-7)만 보고 그 그룹을 설정하면 끝. 물리 라벨은 중요치 않음.

---

## 0. 준비물

| 항목 | 규격 | 수량 |
|---|---|---|
| CSI 리본 케이블 | 22핀 **0.5mm 피치** FFC (A/B 타입은 실물 확인) | 1 |
| DC 전원 어댑터 | **12~24V, 4A 이상** (12V 딱보다 여유 있게 권장) | 1 |
| FAKRA 동축 케이블 | 50Ω, Amphenol 수평 단면형 | 카메라 수만큼 (최대 4) |

> 보드에 리본이 동봉돼 오는 경우가 많음. **먼저 동봉품 확인.**

### A타입/B타입 판별
- 젯슨 CAM1과 보드 J12의 **금색 접점이 같은 면** → A타입(동일면)
- **반대 면** → B타입(반대면)
- 보드측 커넥터 부품번호: `AFC01-S22FCC-00` (데이터시트로 접점 방향 확인 가능)

---

## 1. 하드웨어 연결 (⚠️ 젯슨 전원 끄고 플러그 뽑은 상태에서)

ESD 주의 — 보드 만지기 전 정전기 제거 (매뉴얼 권고사항).

1. **리본 연결**: 보드 **J12 (CSI0)** ↔ 젯슨 **CAM1 (22핀)**
   - 양쪽 ZIF 레버를 열고 → 접점 방향 맞춰 끝까지 삽입 → 레버 잠금
   - ※ 4-lane 모드는 리본 **1개**면 충분. J13은 2-lane 모드용.
2. **카메라 연결**: FAKRA 커넥터 J2~J5에 순서대로
   - J2=LinkA, J3=LinkB, J4=LinkC, J5=LinkD
   - 동축 하나로 데이터+제어+전원(PoC) 전부 전달됨. 카메라 별도 전원 불필요.
3. **전원**: 보드 **J1**에 12~24V DC 연결
   - 부팅 시 드라이버가 카메라를 프로브하므로 **젯슨 부팅 전에 보드 전원이 들어와 있어야 함**

---

## 2. 드라이버 설치

```bash
cd ~/cam-adaptor/FG12-4CH/_extracted/fg12-4ch-onxa-devkit-yuv-r36.4.0-r36.4.3
sudo ./fg12.4ch.onx.4lane.csi.upgrade.sh
```

- **Enter 2번** 눌러야 함:
  1. 버전 확인 통과 후 "Press Enter to continue"
  2. 완료 후 "press Enter to reboot" → **자동 재부팅**
- 스크립트는 반드시 **위 폴더 안에서** 실행 (상대경로 `rootfs/...` 사용)
- ⚠️ 2-lane 배선이면 대신 `fg12.4ch.onx.2lane.csi.upgrade.sh`

### 스크립트가 하는 일
1. `fzcam.ko` 커널 모듈 복사 + insmod + depmod
2. `fzcam_cfg.ini`, `fzcam_cfg`, `fzcam_ui` 설치
3. `fzcam_cfg.service` systemd 서비스 등록
4. 4-lane 디바이스 트리 오버레이(`.dtbo`) 복사
5. `jetson-io`로 "Camera FG12-4CH-4Lanes-YUV" 활성화
6. 재부팅

---

## 3. 재부팅 후 확인

```bash
ls /dev/video*
dmesg | grep fzcam
```

- 카메라 4대면 `/dev/video0` ~ `/dev/video3` 생성되면 성공
- dmesg에 fzcam 관련 로그가 보여야 함

---

## 4. 카메라 설정 (fzcam_ui)

```bash
sudo fzcam_ui
```

QT GUI에서 순서대로:
1. **GMSL 위치** 선택 (Video-0-1-2-3 또는 Video-4-5-6-7)
2. **廠商** (제조사) 선택
3. **型號** (모델) 선택
4. **串行器** (시리얼라이저) 선택 — 예: MAX9295A
5. **分辨率** (해상도) 선택
6. **保存配置** (설정 저장) → **运行配置** (설정 실행)

### 디바이스 매핑
| 물리 인터페이스 | 디바이스 노드 | I2C 버스 |
|---|---|---|
| GMSL 0~3 | /dev/video0 ~ video3 | IIC-9 → **CAM0~3 설정 선택** |
| GMSL 4~7 | /dev/video4 ~ video7 | IIC-10 → **CAM4~7 설정 선택** |

---

## 5. 영상 테스트 (GStreamer)

해상도에 맞는 것 사용 (매뉴얼 기준):

```bash
# 2M (1920x1080)
gst-launch-1.0 -ev v4l2src device=/dev/video0 ! \
  "video/x-raw, format=(string)UYVY, width=(int)1920, height=(int)1080" ! \
  fpsdisplaysink text-overlay=0 video-sink=fakesink sync=0

# 8M (3840x2160) — 화면 출력
gst-launch-1.0 v4l2src device=/dev/video0 ! \
  "video/x-raw, format=(string)UYVY, width=(int)3840, height=(int)2160" ! \
  videoconvert ! fpsdisplaysink video-sink=xvimagesink sync=false

# 5M (2880x1860)
gst-launch-1.0 v4l2src device=/dev/video0 ! \
  "video/x-raw, format=(string)UYVY, width=(int)2880, height=(int)1860" ! \
  videoconvert ! fpsdisplaysink video-sink=xvimagesink sync=false

# 1M (1280x720)
gst-launch-1.0 -ev v4l2src device=/dev/video0 ! \
  "video/x-raw, format=(string)UYVY, width=(int)1280, height=(int)720" ! \
  fpsdisplaysink text-overlay=0 video-sink=fakesink sync=0
```

---

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| `/dev/video*` 안 생김 | ① 보드 DC 전원 인가됐는지 ② 리본 A/B 타입·삽입 상태 ③ 4lane/2lane 스크립트 맞게 골랐는지 |
| 스크립트가 버전 불일치로 종료 | `cat /etc/nv_tegra_release`에 `REVISION: 4.3` 있어야 함 |
| 영상은 뜨는데 깨짐 | fzcam_ui에서 센서 모델/해상도 잘못 선택 |
| I2C 버스 번호가 문서와 다름 | 매뉴얼 명시: 버스 번호는 하드웨어 위치 기준(J12/J13)이라 소프트웨어 표기와 다를 수 있음 |

---

## 참고 문서 (같은 폴더)
- `FG12-4CH/FG12_4CH_User_Manual.pdf` — 핀아웃, fzcam_ui 사용법, gstreamer 예제
- `FG12-4CH/FG12-4CH__Specification.pdf` — 커넥터/전원 규격
- `FG12-4CH/FG12-4CH Block Diagram.png` — 연결 구조
- 프로젝트: `~/customfood-soup-automation/docs/JETSON_SETUP.md` — 벤치마크 실행법
