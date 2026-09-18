"""Orbbec Gemini 2 RGB-D adapter for the Jetson collector.

The SDK is optional in mock mode. Device discovery and profile negotiation happen
on the Jetson; the Pi never opens the USB camera or receives its raw frames.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import re
import time
from typing import Any

import numpy as np

from ..clock import DeviceStamp, HostStamp
from .base import DATA_ARRAY, DATA_IMAGE, KIND_DEPTH_USB, Sample, SensorAdapter, SensorError, SensorProbe, StreamSpec


#: 켠 스트림 하나가 이 시간 동안 프레임을 안 주면(다른 스트림은 오더라도) 장치를 다시 연다.
STREAM_SILENCE_SEC = 10.0
#: 열고 나서 이 시간 안에 첫 프레임이 안 온 스트림이 있으면 시작 실패로 보고 다시 연다.
#: (실기기: 세션 4회 중 2회, color만 오고 depth·IR 백엔드 콜백이 끝까지 0이었다.)
STREAM_START_SEC = 5.0
#: 한 스트림의 변환 실패가 연속으로 이만큼 쌓이면 일시적 오류가 아니라고 보고 다시 연다.
BAD_FRAME_LIMIT = 30


def _sdk_module():
    try:
        return importlib.import_module("pyorbbecsdk")
    except (ImportError, OSError) as exc:
        raise SensorError(f"pyorbbecsdk2 로드 실패: {exc}") from exc


def _format_name(value: Any) -> str:
    return str(value).rsplit(".", 1)[-1].upper()


def _image_data(frame: Any) -> np.ndarray:
    """Convert SDK-owned color bytes to an independent OpenCV BGR image."""
    import cv2  # JetPack cv2 is used by the collector writer as well

    w, h = int(frame.get_width()), int(frame.get_height())
    raw = np.asarray(frame.get_data(), dtype=np.uint8)
    fmt = _format_name(frame.get_format())
    if fmt == "MJPG":
        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if image is None:
            raise SensorError("Gemini 2 MJPG 색상 프레임 디코딩 실패")
        return image.copy()
    if fmt in ("RGB", "BGR"):
        image = raw.reshape(h, w, 3)
        return cv2.cvtColor(image, cv2.COLOR_RGB2BGR) if fmt == "RGB" else image.copy()
    if fmt in ("YUYV", "UYVY"):
        image = raw.reshape(h, w, 2)
        code = cv2.COLOR_YUV2BGR_YUY2 if fmt == "YUYV" else cv2.COLOR_YUV2BGR_UYVY
        return cv2.cvtColor(image, code)
    if fmt in ("NV12", "NV21", "I420"):
        image = raw.reshape(h * 3 // 2, w)
        code = {"NV12": cv2.COLOR_YUV2BGR_NV12, "NV21": cv2.COLOR_YUV2BGR_NV21,
                "I420": cv2.COLOR_YUV2BGR_I420}[fmt]
        return cv2.cvtColor(image, code)
    raise SensorError(f"Gemini 2 미지원 색상 포맷: {fmt}")


def _depth_mm(frame: Any) -> tuple[np.ndarray, float]:
    if _format_name(frame.get_format()) != "Y16":
        raise SensorError(f"Gemini 2 깊이 포맷은 Y16이어야 함: {frame.get_format()}")
    w, h = int(frame.get_width()), int(frame.get_height())
    raw = np.asarray(frame.get_data(), dtype=np.uint8).view("<u2").reshape(h, w)
    scale = float(frame.get_depth_scale())
    if not np.isfinite(scale) or scale <= 0:
        raise SensorError(f"Gemini 2 깊이 scale 오류: {scale}")
    # SDK's scale converts Z16 codes to millimetres. Zero remains invalid/zero.
    mm = np.clip(np.rint(raw.astype(np.float32) * scale), 0, 65535).astype("<u2")
    return mm, scale


def _ir_values(frame: Any) -> np.ndarray:
    w, h = int(frame.get_width()), int(frame.get_height())
    fmt = _format_name(frame.get_format())
    raw = np.asarray(frame.get_data(), dtype=np.uint8)
    if fmt == "Y8":
        return raw.reshape(h, w).astype("<u2")
    if fmt == "Y16":
        return raw.view("<u2").reshape(h, w).copy()
    raise SensorError(f"Gemini 2 미지원 IR 포맷: {fmt}")


def _frame_stamp(frame: Any) -> DeviceStamp | None:
    try:
        return DeviceStamp(int(frame.get_timestamp_us()), "us", "device", "sdk_frame_timestamp_us")
    except (AttributeError, TypeError, ValueError):
        try:
            return DeviceStamp(int(frame.get_timestamp()), "ms", "device", "sdk_frame_timestamp")
        except (AttributeError, TypeError, ValueError):
            return None


def _frame_seq(frame: Any, fallback: int) -> tuple[int, bool]:
    try:
        return int(frame.get_index()), True
    except (AttributeError, TypeError, ValueError):
        return fallback, False


def _device_info(info: Any) -> dict[str, Any]:
    methods = {"name": "get_name", "serial": "get_serial_number", "firmware": "get_firmware_version",
               "hardware": "get_hardware_version", "connection": "get_connection_type",
               "vid": "get_vid", "pid": "get_pid"}
    result: dict[str, Any] = {}
    for key, method in methods.items():
        try:
            result[key] = getattr(info, method)()
        except (AttributeError, RuntimeError):
            result[key] = None
    return result


def _is_gemini2(info: dict[str, Any]) -> bool:
    # Gemini 2 L/XL and other Orbbec products have different profile matrices.
    return bool(re.search(r"\bgemini\s*2\b(?!\s*(?:l|xl)\b)", str(info.get("name") or ""), re.I))


class OrbbecGemini2(SensorAdapter):
    sensor_id = "cam_depth_0"
    kind = KIND_DEPTH_USB
    simulated = False
    streams = (
        StreamSpec("color", DATA_IMAGE, description="Gemini 2 color, decoded BGR and stored as JPEG"),
        StreamSpec("depth", DATA_ARRAY, unit="mm", dtype="uint16", description="Gemini 2 Z16 converted to mm"),
        StreamSpec("ir", DATA_ARRAY, unit="raw_ir", dtype="uint16", description="Gemini 2 Y8/Y16 intensity values"),
    )

    def __init__(self, serial: str | None = None, *, default_fps: int | None = None, sdk: Any = None) -> None:
        self.serial = serial or None
        #: 세션 설정에 fps가 없을 때 쓸 값. None/0이면 SDK 기본 프로필.
        self._default_fps = int(default_fps) if default_fps else None
        self._sdk = sdk
        self._pipeline: Any = None
        self._context: Any = None
        self._device: Any = None
        self._applied: dict[str, Any] = {}
        self._seq = 0
        self._last_frame_at = 0.0
        self._stream_last: dict[str, float] = {}
        self._stream_seen: set[str] = set()
        self._opened_at = 0.0
        self._bad_frames: dict[str, int] = {}
        self._encoding = "jpeg"

    def _module(self) -> Any:
        return self._sdk if self._sdk is not None else _sdk_module()

    def _find_device(self, sdk: Any) -> tuple[Any, Any, dict[str, Any]]:
        context = sdk.Context()
        devices = context.query_devices()
        for i in range(int(devices.get_count())):
            device = devices.get_device_by_index(i)
            info = _device_info(device.get_device_info())
            if _is_gemini2(info) and (self.serial is None or info.get("serial") == self.serial):
                return context, device, info
        raise SensorError("Gemini 2 USB 장치 없음" + (f" (serial={self.serial})" if self.serial else ""))

    def probe(self) -> SensorProbe:
        try:
            sdk = self._module()
            _context, _device, info = self._find_device(sdk)
        except Exception as exc:
            return SensorProbe(False, False, model="Orbbec Gemini 2", driver="pyorbbecsdk2",
                               reason=str(exc), facts={"requested_serial": self.serial})
        return SensorProbe(True, False, detail="Gemini 2 SDK 장치 탐색 성공 (프레임 미검증)",
                           model=info.get("name") or "Orbbec Gemini 2", serial=info.get("serial"),
                           driver="pyorbbecsdk2", verified=False,
                           facts={**info, "requested_serial": self.serial})

    @staticmethod
    def _profile(pipeline: Any, sensor_type: Any, requested: dict[str, Any]) -> Any:
        profiles = pipeline.get_stream_profile_list(sensor_type)
        if not requested:
            return profiles.get_default_video_stream_profile()
        default = profiles.get_default_video_stream_profile()
        width = requested.get("width")
        height = requested.get("height")
        fps = requested.get("fps")
        matches = []
        for i in range(len(profiles)):
            p = profiles[i]
            # 목록 인덱싱은 기반형 StreamProfile을 줄 수 있다(get_width 없음) — 영상 프로필로 내려받는다
            if not hasattr(p, "get_width") and hasattr(p, "as_video_stream_profile"):
                p = p.as_video_stream_profile()
            if width is not None and int(p.get_width()) != int(width):
                continue
            if height is not None and int(p.get_height()) != int(height):
                continue
            if fps is not None and int(p.get_fps()) != int(fps):
                continue
            matches.append(p)
        if not matches:
            raise SensorError(f"Gemini 2 요청 프로파일 미지원: {requested}")
        # Preserve the SDK default when it satisfies the request.
        if any(int(default.get_width()) == int(p.get_width()) and
               int(default.get_height()) == int(p.get_height()) and
               int(default.get_fps()) == int(p.get_fps()) and
               default.get_format() == p.get_format() for p in matches):
            return default
        # Otherwise stay as close to the SDK default as the request allows: same pixel format first,
        # then same size. (실기기: fps만 10으로 요청했더니 목록 첫 항목인 1920x1080이 골라져 해상도까지 바뀌었다.)
        def closeness(p: Any) -> tuple[bool, bool]:
            same_size = (int(p.get_width()) == int(default.get_width()) and
                         int(p.get_height()) == int(default.get_height()))
            return p.get_format() == default.get_format(), same_size
        return max(matches, key=closeness)

    def open(self, config: dict[str, Any]) -> None:
        self.close()
        sdk = self._module()
        try:
            context, device, info = self._find_device(sdk)
            pipeline = sdk.Pipeline(device)
            cfg = sdk.Config()
            requested = config.get("orbbec_profiles") or {}
            if not isinstance(requested, dict):
                raise SensorError("orbbec_profiles는 스트림별 객체여야 함")
            resolution = config.get("resolution")
            color_size: dict[str, int] = {}
            if resolution is not None:
                match = re.fullmatch(r"(\d+)x(\d+)", str(resolution))
                if not match:
                    raise SensorError(f"Gemini 2 해상도 형식 오류: {resolution!r}")
                color_size = {"width": int(match.group(1)), "height": int(match.group(2))}
            encoding = str(config.get("encoding") or "jpeg").lower()
            if encoding not in ("jpeg", "raw"):
                raise SensorError(f"Gemini 2 색상 인코딩 미지원: {encoding}")
            fps = config["fps"] if config.get("fps") is not None else self._default_fps
            selected = {}
            for stream, sensor_type in (("color", sdk.OBSensorType.COLOR_SENSOR),
                                        ("depth", sdk.OBSensorType.DEPTH_SENSOR),
                                        ("ir", sdk.OBSensorType.IR_SENSOR)):
                req = requested.get(stream) or {}
                if not isinstance(req, dict):
                    raise SensorError(f"orbbec_profiles.{stream}은 객체여야 함")
                effective = {"fps": fps} if fps is not None else {}
                if stream == "color":
                    effective.update(color_size)
                effective.update(req)
                profile = self._profile(pipeline, sensor_type, effective)
                cfg.enable_stream(profile)
                selected[stream] = {"width": int(profile.get_width()), "height": int(profile.get_height()),
                                    "fps": int(profile.get_fps()), "format": _format_name(profile.get_format())}
            pipeline.start(cfg)
        except Exception as exc:
            try:
                if "pipeline" in locals():
                    pipeline.stop()
            except Exception:
                pass
            raise SensorError(f"{self.sensor_id}: Gemini 2 시작 실패: {exc}") from exc
        self._context, self._device, self._pipeline = context, device, pipeline
        self._applied = {"device": info, "profiles": selected, "requested_profiles": requested,
                         "requested_fps": config.get("fps"), "default_fps": self._default_fps, "requested_color_resolution": resolution,
                         "encoding": encoding, "frame_sync": False, "read_timeout_ms": 1000}
        self._encoding = encoding
        self._seq = 0
        self._last_frame_at = time.monotonic()
        self._stream_last = {stream: self._last_frame_at for stream in selected}
        self._stream_seen = set()
        self._opened_at = self._last_frame_at
        self._bad_frames = {stream: 0 for stream in selected}

    def read(self) -> list[Sample]:
        if self._pipeline is None:
            raise SensorError(f"{self.sensor_id}: 열리지 않음")
        try:
            frames = self._pipeline.wait_for_frames(1000)
        except Exception as exc:
            raise SensorError(f"{self.sensor_id}: Gemini 2 프레임 수신 실패: {exc}") from exc
        host = HostStamp.now()
        if not frames:
            if time.monotonic() - self._last_frame_at >= 10:
                raise SensorError(f"{self.sensor_id}: Gemini 2 프레임 10초 이상 없음")
            return []
        self._last_frame_at = time.monotonic()
        self._seq += 1
        result: list[Sample] = []
        for stream, getter in (("color", "get_color_frame"), ("depth", "get_depth_frame"),
                               ("ir", "get_ir_frame")):
            frame = getattr(frames, getter)()
            if not frame:
                continue
            fmt = _format_name(frame.get_format())
            seq, from_device = _frame_seq(frame, self._seq)
            w, h = int(frame.get_width()), int(frame.get_height())
            flags: dict[str, Any] = {"sdk_format": fmt}
            try:
                if stream == "color":
                    data = _image_data(frame)
                    pixel_format = "BGR8"
                    flags["encoding"] = self._encoding
                elif stream == "depth":
                    data, scale = _depth_mm(frame)
                    flags.update({"raw_format": fmt, "depth_scale_mm_per_code": scale,
                                  "conversion": "round(raw_z16 * depth_scale_mm_per_code)"})
                    pixel_format = "Z16_MM"
                else:
                    data = _ir_values(frame)
                    flags["raw_format"] = fmt
                    pixel_format = "IR_U16"
            except (ValueError, SensorError) as exc:
                # 실기기에서 SDK가 드물게 해제 전 RLE 깊이 프레임을 그대로 준다(2026-09-18 관측).
                # 프레임 하나 때문에 세 스트림을 모두 끊지 않고 무효 샘플로 기록한다.
                self._bad_frames[stream] = self._bad_frames.get(stream, 0) + 1
                if self._bad_frames[stream] >= BAD_FRAME_LIMIT:
                    raise SensorError(f"{self.sensor_id}/{stream}: 연속 {self._bad_frames[stream]}회 변환 실패 — {exc}") from exc
                self._stream_last[stream] = self._last_frame_at
                result.append(Sample(stream, seq, host, _frame_stamp(frame), None, valid=False,
                                     invalid_reason=f"convert_failed: {exc}", width=w, height=h,
                                     seq_is_device=from_device, flags=flags))
                continue
            self._bad_frames[stream] = 0
            self._stream_last[stream] = self._last_frame_at
            result.append(Sample(stream, seq, host, _frame_stamp(frame), data,
                                 width=w, height=h, pixel_format=pixel_format,
                                 seq_is_device=from_device, flags=flags))
        self._stream_seen.update(smp.stream_id for smp in result)
        never = [st for st in self._stream_last if st not in self._stream_seen]
        if never and self._last_frame_at - self._opened_at >= STREAM_START_SEC:
            raise SensorError(f"{self.sensor_id}: 시작 후 {STREAM_START_SEC:.0f}초 동안 프레임 없는 스트림: {', '.join(never)}")
        silent = [st for st, at in self._stream_last.items() if self._last_frame_at - at >= STREAM_SILENCE_SEC]
        if silent:
            # 시작 직후 일부 스트림만 안 나오는 경우가 있었다(color만 수신, depth·IR 0) — 다시 열어 복구한다
            raise SensorError(f"{self.sensor_id}: 스트림 무응답 {STREAM_SILENCE_SEC:.0f}초 이상: {', '.join(silent)}")
        return result

    def close(self) -> None:
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is not None:
            try:
                pipeline.stop()
            finally:
                self._device = None
                self._context = None

    def applied_config(self) -> dict[str, Any]:
        return dict(self._applied)

    def version_info(self) -> dict[str, Any]:
        try:
            version = importlib.metadata.version("pyorbbecsdk2")
        except importlib.metadata.PackageNotFoundError:
            version = None
        return {"backend": "pyorbbecsdk", "sdk_package": "pyorbbecsdk2", "sdk_version": version}
