"""ISX031F GMSL2 카메라 어댑터 — V4L2 ioctl 직접 사용. 어댑터 보드 두 종류를 지원한다.

- **Sensing SG4A-NONX-G2Y-A1**(현재 장착, 2026-09-21 실기기 확인): 드라이버 `sgx-yuv-gmsl2`, 노드 이름
  `vi-output, sgx-yuv-gmsl2 9-001a`(포트 0)…`9-001d`(포트 3). 해상도는 **`sensor_mode` 컨트롤**로 고른다
  (포맷만 바꾸면 1920x1080에 머문다). 링크 상태를 알려 주는 도구가 없어 **짧은 시험 캡처**로 연결을 판정한다.
  포트 4개의 노드는 카메라가 없어도 생긴다.
- **Fangzhu FG12-4CH**(2026-07 검증): 드라이버 `fzcam`, `/dev/video4~7`, 링크 상태는 `fzcam_cfg`.

장치 지정은 경로(`/dev/video4`) 또는 **`gmsl:<포트>`**. `/dev/video` 번호는 Gemini 2(UVC 노드 6개)를 언제 꽂았느냐에
따라 밀리므로(실측: GMSL이 video6·7로 밀림) 운영에서는 `gmsl:0,gmsl:1`처럼 포트로 지정한다.

실기기로 아는 것:
- 포맷 UYVY/NV16, **모든 해상도 30fps 고정**. 요청 fps < 30이면 어댑터가 간격 추림(decimation)한다.
- 링크가 없으면 드라이버가 전부 0인 프레임을 낼 수 있다 → `valid=false, invalid_reason=blank_frame`.
- Sensing 드라이버는 프레임 일부(읽는 속도에 따라 25~100%)에 V4L2 ERROR 플래그를 붙인다(커널 로그
  `corr_err: discarding frame`). 영상은 육안으로 정상이라 **버리지 않고 저장하되** `flags.driver_error_flag=true`로
  남긴다. SDK의 `clock_config.sh`(nvcsi 클록 고정, sudo)를 실행하지 않은 상태의 관측이다.
- V4L2 타임스탬프는 monotonic 표시지만 호스트 `CLOCK_MONOTONIC`과 수십 초 어긋나 있었다 — 장치 시각은 호스트 시각과
  분리해 기록하므로 그대로 둔다(프레임 간격 33.33 ms, 순번 갭 0 확인).

`verified`는 실기기 스트리밍으로 확인되기 전까지 False다.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

import numpy as np

from ..clock import DeviceStamp, HostStamp
from .base import DATA_IMAGE, KIND_RGB_GMSL2, Sample, SensorAdapter, SensorError, SensorProbe, StreamSpec, is_blank_image
from .v4l2dev import V4L2Capture

DEFAULT_SIZE = (1920, 1536)
DEFAULT_PIXFMT = "UYVY"
_RES = re.compile(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$")


#: Sensing sgx-yuv-gmsl2 드라이버의 sensor_mode 번호(드라이버의 포맷 목록 순서, SDK quick_bring_up.sh와 동일)
SGX_SENSOR_MODES = {(1920, 1080): 0, (1920, 1536): 1, (2880, 1860): 2, (3840, 2160): 3, (1280, 720): 4}
_GMSL_SPEC = re.compile(r"^gmsl:(\d)$")
_SGX_CARD = re.compile(r"sgx-yuv-gmsl2 \d+-001([a-d])")
#: 시험 캡처로 연결이 확인된 뒤 다시 시험하기까지의 간격(초). 미연결이면 probe 때마다 다시 본다.
PROBE_RECHECK_SEC = 60.0


def video_node_cards() -> dict[str, str]:
    """`/dev/videoN` → 드라이버가 붙인 이름(card)."""
    out: dict[str, str] = {}
    base = "/sys/class/video4linux"
    try:
        # v4l-subdevN(센서 서브디바이스)도 같은 이름을 달고 있다 — 캡처 노드 videoN만 본다
        names = sorted((n for n in os.listdir(base) if re.fullmatch(r"video\d+", n)), key=lambda n: int(n[5:]))
    except OSError:
        return out
    for name in names:
        try:
            with open(f"{base}/{name}/name", encoding="utf-8") as f:
                out[f"/dev/{name}"] = f.read().strip()
        except OSError:
            continue
    return out


def resolve_device(spec: str) -> tuple[str | None, str | None]:
    """장치 지정 → (노드 경로, card 이름). `gmsl:<포트>`는 Sensing 노드 이름의 포트 글자(a~d)로 찾는다."""
    cards = video_node_cards()
    m = _GMSL_SPEC.match(spec.strip())
    if not m:
        return (spec if os.path.exists(spec) else None), cards.get(spec)
    letter = "abcd"[int(m.group(1))] if int(m.group(1)) < 4 else None
    for node, card in cards.items():
        found = _SGX_CARD.search(card)
        if found and found.group(1) == letter:
            return node, card
    return None, None


def _v4l2_ctl(args: list[str], timeout: float = 3.0) -> str | None:
    try:
        res = subprocess.run(["v4l2-ctl", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def read_controls(device: str) -> dict[str, Any]:
    """exposure·gain 등 실제 컨트롤 값. 못 읽으면 None."""
    out: dict[str, Any] = {"exposure": None, "gain": None, "frame_rate": None, "sensor_mode": None}
    text = _v4l2_ctl(["-d", device, "--get-ctrl", "exposure,gain,frame_rate,sensor_mode"])
    if not text:
        return out
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            if k in out:
                try:
                    out[k] = int(v.strip())
                except ValueError:
                    out[k] = v.strip()
    return out


def gmsl_link_status() -> dict[str, Any]:
    """FG12-4CH 링크 상태(`fzcam_cfg`). 없으면 미확인(None)."""
    try:
        res = subprocess.run(["fzcam_cfg"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "links": None, "raw": None}
    m = re.search(r"Link satus:([0-9-]+)", res.stdout + res.stderr)
    links = [int(x) for x in m.group(1).split("-")] if m else None
    return {"available": True, "links": links, "raw": (m.group(0) if m else None)}


class Isx031fGmsl2Camera(SensorAdapter):
    kind = KIND_RGB_GMSL2
    simulated = False
    streams = (StreamSpec("rgb", DATA_IMAGE, description="ISX031F UYVY → JPEG(프레임 단위) 또는 raw"),)

    def __init__(self, sensor_id: str, device: str, link_index: int | None = 0) -> None:
        self.sensor_id = sensor_id
        #: 설정에 적힌 지정(경로 또는 gmsl:<포트>). 실제 노드는 probe/open 때마다 다시 찾는다.
        self.spec = device
        self.device = device
        self._card: str | None = None
        self._link_index = link_index
        self._probe_ok_at: float | None = None
        self._cap: V4L2Capture | None = None
        self._applied: dict[str, Any] = {}
        self._decimation = 1
        self._counter = 0
        self._last_seq: int | None = None
        self._pending_gap = 0
        self._encoding = "jpeg"
        self._controls: dict[str, Any] = {}
        self._controls_read_at = 0.0
        self._timeouts = 0

    # ── 탐색 ──
    def _resolve(self) -> bool:
        node, card = resolve_device(self.spec)
        self._card = card
        if node is None:
            return False
        self.device = node
        return True

    @property
    def _is_sgx(self) -> bool:
        return bool(self._card and "sgx-yuv-gmsl2" in self._card)

    def _test_capture(self) -> tuple[bool, str | None]:
        """Sensing 보드용 연결 판정: 스트림을 잠깐 열어 비어 있지 않은 프레임이 오는지 본다."""
        cap = V4L2Capture(self.device, buffers=2)
        try:
            cap.open()
            cap.start()
            for _ in range(3):
                fr = cap.dequeue(timeout_sec=1.0)
                if fr is not None and not is_blank_image(fr.data):
                    return True, None
            return False, "시험 캡처에서 프레임 없음 — 이 포트에 카메라가 없거나 링크 미확립"
        except OSError as exc:
            return False, f"시험 캡처 실패: {exc}"
        finally:
            cap.close()

    def probe(self) -> SensorProbe:
        if not self._resolve():
            why = ("Sensing 드라이버 노드 없음 — max96712.ko·sgx-yuv-gmsl2.ko 미적재(재부팅마다 insmod 필요)"
                   if _GMSL_SPEC.match(self.spec) else "V4L2 노드 없음(드라이버 미적재)")
            return SensorProbe(connected=False, simulated=False, detail=f"{self.spec} 없음", model="Sensing ISX031F",
                               reason=why, facts={"spec": self.spec})
        facts: dict[str, Any] = {"spec": self.spec, "device": self.device, "card": self._card}
        if self._card and "vi-output" not in self._card:
            # 예: Gemini 2를 꽂으면 그 UVC 노드가 /dev/video0~5를 차지해 예전 기본값 /dev/video4가 Gemini를 가리킨다
            return SensorProbe(connected=False, simulated=False, model="Sensing ISX031F", verified=False,
                               detail=f"{self.device}는 GMSL 카메라가 아님",
                               reason=f"{self.device}는 CSI(vi-output) 노드가 아님: {self._card!r} — gmsl:<포트>로 지정할 것",
                               facts=facts)
        text = _v4l2_ctl(["-d", self.device, "--info"])
        if text:
            for key in ("Driver name", "Card type", "Driver version"):
                m = re.search(rf"{key}\s*:\s*(.+)", text)
                if m:
                    facts[key.lower().replace(" ", "_")] = m.group(1).strip()
        if self._is_sgx:
            if self._cap is not None:  # 수집 중에는 장치를 다시 열지 않는다
                connected, reason = True, None
            elif self._probe_ok_at is not None and time.monotonic() - self._probe_ok_at < PROBE_RECHECK_SEC:
                connected, reason = True, None
            else:
                connected, reason = self._test_capture()
                self._probe_ok_at = time.monotonic() if connected else None
            facts["link_check"] = "test_capture"
            board = "Sensing SG4A-NONX-G2Y-A1"
        else:
            link = gmsl_link_status()
            locked: bool | None = None
            if link["links"] is not None and self._link_index is not None and self._link_index < len(link["links"]):
                locked = link["links"][self._link_index] == 1
            facts["gmsl_link"] = link
            connected = bool(locked) if locked is not None else False
            reason = None if connected else (
                "GMSL 링크 미확립(카메라 보드 전원/케이블 확인 — fzcam_cfg Link 0)" if locked is False
                else "링크 상태 확인 불가(fzcam_cfg 없음)"
            )
            board = "FG12-4CH"
        return SensorProbe(
            connected=connected, simulated=False, model=f"Sensing ISX031F (MAX96717F → {board})",
            driver=facts.get("driver_name"), detail=f"ISX031F {self.spec} → {self.device}" + ("" if connected else " — 링크 없음"),
            verified=False, reason=reason, facts=facts,
        )

    # ── 수집 ──
    def open(self, config: dict[str, Any]) -> None:
        w, h = DEFAULT_SIZE
        res = config.get("resolution")
        if isinstance(res, str) and (m := _RES.match(res)):
            w, h = int(m.group(1)), int(m.group(2))
        pixfmt = str(config.get("pixel_format") or DEFAULT_PIXFMT).upper()
        self._encoding = str(config.get("encoding") or "jpeg").lower()
        if not self._resolve():
            raise SensorError(f"{self.sensor_id}: V4L2 노드 없음 ({self.spec})")
        sensor_mode = None
        if self._is_sgx:
            # 이 드라이버는 포맷이 아니라 sensor_mode 컨트롤이 해상도를 정한다
            sensor_mode = SGX_SENSOR_MODES.get((w, h))
            if sensor_mode is None:
                raise SensorError(f"{self.sensor_id}: Sensing 드라이버 미지원 해상도 {w}x{h} — {sorted(SGX_SENSOR_MODES)}")
            if _v4l2_ctl(["-d", self.device, "--set-ctrl", f"bypass_mode=0,sensor_mode={sensor_mode}"]) is None:
                raise SensorError(f"{self.sensor_id}: sensor_mode={sensor_mode} 설정 실패({self.device})")
        cap = V4L2Capture(self.device, buffers=int(config.get("v4l2_buffers") or 4))
        try:
            cap.open()
            fmt = cap.set_format(w, h, pixfmt)
            cap.start()
        except OSError as exc:
            cap.close()
            raise SensorError(f"{self.sensor_id}: V4L2 열기 실패 {self.device}: {exc}") from exc
        if (fmt.get("width"), fmt.get("height")) != (w, h):
            cap.close()
            raise SensorError(f"{self.sensor_id}: 요청 {w}x{h}인데 드라이버가 {fmt.get('width')}x{fmt.get('height')}로 열림")
        self._cap = cap
        driver_fps = fmt.get("driver_fps") or 30.0
        requested = config.get("fps")
        want = float(requested) if requested else driver_fps
        self._decimation = max(1, int(round(driver_fps / want))) if want > 0 else 1
        self._counter = 0
        self._last_seq = None
        self._pending_gap = 0
        self._controls = read_controls(self.device)
        self._controls_read_at = time.monotonic()
        self._applied = {
            "requested": {"resolution": res, "fps": requested, "pixel_format": config.get("pixel_format"), "encoding": config.get("encoding")},
            "applied": {
                **fmt, "spec": self.spec, "device": self.device, "sensor_mode": sensor_mode, "encoding": self._encoding, "decimation": self._decimation,
                "save_fps": driver_fps / self._decimation, "controls": dict(self._controls),
            },
        }

    def read(self) -> list[Sample]:
        if self._cap is None:
            raise SensorError(f"{self.sensor_id}: 열리지 않음")
        try:
            fr = self._cap.dequeue(timeout_sec=1.0)
        except OSError as exc:
            raise SensorError(f"{self.sensor_id}: DQBUF 실패(장치 분리?): {exc}") from exc
        host = HostStamp.now()
        if fr is None:
            self._timeouts += 1
            return []
        # 누락은 드라이버의 전체 속도(30 fps) 순번에서 센다. 추림으로 건너뛴 프레임은 누락이 아니므로
        # 건너뛰는 동안 생긴 실제 누락만 모아 두었다가 다음 저장 샘플에 실어 보낸다.
        if self._last_seq is not None and fr.sequence > self._last_seq + 1:
            self._pending_gap += fr.sequence - self._last_seq - 1
        self._last_seq = fr.sequence
        self._counter += 1
        if (self._counter - 1) % self._decimation:
            return []  # 추림: 드라이버 프레임은 받았지만 저장 대상 아님
        gap, self._pending_gap = self._pending_gap, 0
        if time.monotonic() - self._controls_read_at > 5.0:
            self._controls = read_controls(self.device)
            self._controls_read_at = time.monotonic()
        blank = is_blank_image(fr.data)
        # 드라이버 ERROR 플래그만으로는 버리지 않는다(머리말 참고) — 프레임은 저장하고 표시만 남긴다
        valid = not blank
        reason = "blank_frame" if blank else None
        dev = DeviceStamp(value=fr.timestamp_ns, unit="ns", clock=fr.timestamp_clock, source="v4l2_buffer")
        return [
            Sample(
                stream_id="rgb", seq=fr.sequence, host=host, device_ts=dev, data=fr.data,
                valid=valid, invalid_reason=reason,
                width=self._cap.width, height=self._cap.height, pixel_format=self._cap.pixelformat,
                exposure=self._controls.get("exposure"), gain=self._controls.get("gain"),
                seq_is_device=True, device_gap=gap,
                flags={"v4l2_flags": fr.flags, "driver_error_flag": fr.error_flag, "bytesused": fr.bytesused,
                       "driver_seq_gap": gap, "encoding": self._encoding},
            )
        ]

    def close(self) -> None:
        if self._cap is not None:
            self._cap.close()
            self._cap = None

    def applied_config(self) -> dict[str, Any]:
        return dict(self._applied)

    def apply_change(self, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {k: v for k, v in changes.items() if k in ("exposure", "gain")}
        if not allowed:
            raise SensorError(f"{self.sensor_id}: 변경 가능 항목은 exposure, gain")
        arg = ",".join(f"{k}={int(v)}" for k, v in allowed.items())
        if _v4l2_ctl(["-d", self.device, "--set-ctrl", arg]) is None:
            raise SensorError(f"{self.sensor_id}: v4l2-ctl set-ctrl 실패")
        self._controls = read_controls(self.device)
        self._controls_read_at = time.monotonic()
        self._applied.setdefault("applied", {})["controls"] = dict(self._controls)
        return {"controls": dict(self._controls)}

    def version_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {"backend": "v4l2-ioctl(ctypes)", "sensor_driver": "sgx-yuv-gmsl2" if self._is_sgx else "fzcam",
                                "card": self._card}
        text = _v4l2_ctl(["-d", self.device, "--info"])
        if text:
            m = re.search(r"Driver version\s*:\s*(.+)", text)
            info["kernel_driver_version"] = m.group(1).strip() if m else None
        try:
            import cv2  # type: ignore
            info["opencv"] = cv2.__version__
        except Exception:
            info["opencv"] = None
        return info


def uyvy_to_bgr(data: bytes, width: int, height: int) -> np.ndarray:
    import cv2  # type: ignore

    arr = np.frombuffer(data, dtype=np.uint8)[: width * height * 2].reshape(height, width, 2)
    return cv2.cvtColor(arr, cv2.COLOR_YUV2BGR_UYVY)
