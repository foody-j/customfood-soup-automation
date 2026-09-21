"""최소 V4L2 mmap 캡처 (ctypes, 외부 의존 없음).

OpenCV의 V4L2 백엔드 대신 직접 ioctl을 쓰는 이유(2026-09-12 실측, notes/dev-log.md):
- 링크가 끊긴 카메라에서도 OpenCV는 `ok=True`로 **전부 0인 프레임**을 100fps로 돌려줬고
  타임스탬프(`POS_MSEC`=0)·노출·게인(−1)을 주지 않았다 → 유효성·누락·시각 근거가 없다.
- 직접 DQBUF하면 `v4l2_buffer.sequence`(하드웨어 순번 → 누락 감지 근거),
  `timestamp`(monotonic, 드라이버가 매긴 캡처 시각), `V4L2_BUF_FLAG_ERROR`를 얻는다.

구조체 크기·오프셋은 aarch64 커널 헤더(`linux/videodev2.h`)로 검증했다:
v4l2_format 208 / v4l2_requestbuffers 20 / v4l2_buffer 88(ts@24, seq@56, m@64, length@72).
"""

from __future__ import annotations

import ctypes
import fcntl
import mmap
import os
import select
from dataclasses import dataclass
from typing import Any

# ── ioctl 번호 (aarch64, 헤더에서 계산) ──────────────────────────────────────
VIDIOC_QUERYCAP = 0x80685600
VIDIOC_G_FMT = 0xC0D05604
VIDIOC_S_FMT = 0xC0D05605
VIDIOC_REQBUFS = 0xC0145608
VIDIOC_QUERYBUF = 0xC0585609
VIDIOC_QBUF = 0xC058560F
VIDIOC_DQBUF = 0xC0585611
VIDIOC_STREAMON = 0x40045612
VIDIOC_STREAMOFF = 0x40045613
VIDIOC_G_PARM = 0xC0CC5615

V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
V4L2_MEMORY_MMAP = 1
V4L2_FIELD_NONE = 1
V4L2_BUF_FLAG_ERROR = 0x0040
V4L2_BUF_FLAG_TIMESTAMP_MASK = 0xE000
V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC = 0x2000
V4L2_BUF_FLAG_TIMESTAMP_COPY = 0x4000


def fourcc(code: str) -> int:
    return ord(code[0]) | (ord(code[1]) << 8) | (ord(code[2]) << 16) | (ord(code[3]) << 24)


def fourcc_str(value: int) -> str:
    return "".join(chr((value >> (8 * i)) & 0xFF) for i in range(4))


class v4l2_capability(ctypes.Structure):
    _fields_ = [
        ("driver", ctypes.c_uint8 * 16), ("card", ctypes.c_uint8 * 32), ("bus_info", ctypes.c_uint8 * 32),
        ("version", ctypes.c_uint32), ("capabilities", ctypes.c_uint32), ("device_caps", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    ]


class v4l2_pix_format(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32), ("height", ctypes.c_uint32), ("pixelformat", ctypes.c_uint32),
        ("field", ctypes.c_uint32), ("bytesperline", ctypes.c_uint32), ("sizeimage", ctypes.c_uint32),
        ("colorspace", ctypes.c_uint32), ("priv", ctypes.c_uint32), ("flags", ctypes.c_uint32),
        ("ycbcr_enc", ctypes.c_uint32), ("quantization", ctypes.c_uint32), ("xfer_func", ctypes.c_uint32),
    ]


class v4l2_format(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32), ("_pad", ctypes.c_uint32),
        ("pix", v4l2_pix_format), ("_raw", ctypes.c_uint8 * (200 - 48)),
    ]


class v4l2_requestbuffers(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_uint32), ("type", ctypes.c_uint32), ("memory", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32), ("flags", ctypes.c_uint8), ("reserved", ctypes.c_uint8 * 3),
    ]


class timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class v4l2_timecode(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("frames", ctypes.c_uint8),
        ("seconds", ctypes.c_uint8), ("minutes", ctypes.c_uint8), ("hours", ctypes.c_uint8),
        ("userbits", ctypes.c_uint8 * 4),
    ]


class v4l2_buffer(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32), ("type", ctypes.c_uint32), ("bytesused", ctypes.c_uint32),
        ("flags", ctypes.c_uint32), ("field", ctypes.c_uint32), ("_pad0", ctypes.c_uint32),
        ("timestamp", timeval), ("timecode", v4l2_timecode),
        ("sequence", ctypes.c_uint32), ("memory", ctypes.c_uint32),
        ("m_offset", ctypes.c_uint64),  # union m: offset(u32)은 하위 32비트
        ("length", ctypes.c_uint32), ("reserved2", ctypes.c_uint32),
        ("request_fd", ctypes.c_uint32), ("_pad1", ctypes.c_uint32),
    ]


class v4l2_captureparm(ctypes.Structure):
    _fields_ = [
        ("capability", ctypes.c_uint32), ("capturemode", ctypes.c_uint32),
        ("tpf_num", ctypes.c_uint32), ("tpf_den", ctypes.c_uint32),
        ("extendedmode", ctypes.c_uint32), ("readbuffers", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 4),
    ]


class v4l2_streamparm(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("capture", v4l2_captureparm), ("_raw", ctypes.c_uint8 * (200 - 40))]


assert ctypes.sizeof(v4l2_capability) == 104
assert ctypes.sizeof(v4l2_format) == 208
assert ctypes.sizeof(v4l2_requestbuffers) == 20
assert ctypes.sizeof(v4l2_buffer) == 88
assert ctypes.sizeof(v4l2_streamparm) == 204


@dataclass
class DqFrame:
    data: bytes
    bytesused: int
    sequence: int
    #: 드라이버 타임스탬프(ns). flags로 어떤 시계인지 판별.
    timestamp_ns: int
    timestamp_clock: str  # host_monotonic | copy | unknown
    error_flag: bool
    flags: int


class V4L2Capture:
    """단일 캡처 노드. `open → start → dequeue()… → stop → close`."""

    def __init__(self, device: str, buffers: int = 4) -> None:
        self.device = device
        self._nbuf = buffers
        self._fd: int | None = None
        self._maps: list[mmap.mmap] = []
        self.width = 0
        self.height = 0
        self.pixelformat = ""
        self.bytesperline = 0
        self.sizeimage = 0
        self.driver = ""
        self.card = ""
        self.fps: float | None = None

    # ── 수명주기 ──
    def open(self) -> None:
        self._fd = os.open(self.device, os.O_RDWR | os.O_NONBLOCK)
        cap = v4l2_capability()
        fcntl.ioctl(self._fd, VIDIOC_QUERYCAP, cap)
        self.driver = bytes(cap.driver).split(b"\0", 1)[0].decode(errors="replace")
        self.card = bytes(cap.card).split(b"\0", 1)[0].decode(errors="replace")

    def set_format(self, width: int, height: int, pixfmt: str = "UYVY") -> dict[str, Any]:
        assert self._fd is not None
        fmt = v4l2_format()
        fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        fmt.pix.width = width
        fmt.pix.height = height
        fmt.pix.pixelformat = fourcc(pixfmt)
        fmt.pix.field = V4L2_FIELD_NONE
        fcntl.ioctl(self._fd, VIDIOC_S_FMT, fmt)
        # 드라이버가 조정했을 수 있으니 **실제 적용값**을 다시 읽는다
        got = v4l2_format()
        got.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        fcntl.ioctl(self._fd, VIDIOC_G_FMT, got)
        self.width, self.height = got.pix.width, got.pix.height
        self.pixelformat = fourcc_str(got.pix.pixelformat)
        self.bytesperline, self.sizeimage = got.pix.bytesperline, got.pix.sizeimage
        parm = v4l2_streamparm()
        parm.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        try:
            fcntl.ioctl(self._fd, VIDIOC_G_PARM, parm)
            if parm.capture.tpf_num:
                self.fps = parm.capture.tpf_den / parm.capture.tpf_num
        except OSError:
            self.fps = None
        return self.format_info()

    def format_info(self) -> dict[str, Any]:
        return {
            "width": self.width, "height": self.height, "pixel_format": self.pixelformat,
            "bytesperline": self.bytesperline, "sizeimage": self.sizeimage, "driver_fps": self.fps,
            "driver": self.driver, "card": self.card,
        }

    def start(self) -> None:
        assert self._fd is not None
        req = v4l2_requestbuffers()
        req.count, req.type, req.memory = self._nbuf, V4L2_BUF_TYPE_VIDEO_CAPTURE, V4L2_MEMORY_MMAP
        fcntl.ioctl(self._fd, VIDIOC_REQBUFS, req)
        if req.count < 1:
            raise OSError("V4L2 버퍼 할당 실패")
        self._maps = []
        for i in range(req.count):
            buf = v4l2_buffer()
            buf.index, buf.type, buf.memory = i, V4L2_BUF_TYPE_VIDEO_CAPTURE, V4L2_MEMORY_MMAP
            fcntl.ioctl(self._fd, VIDIOC_QUERYBUF, buf)
            offset = buf.m_offset & 0xFFFFFFFF
            self._maps.append(mmap.mmap(self._fd, buf.length, mmap.MAP_SHARED, mmap.PROT_READ, offset=offset))
            fcntl.ioctl(self._fd, VIDIOC_QBUF, buf)
        fcntl.ioctl(self._fd, VIDIOC_STREAMON, ctypes.c_int(V4L2_BUF_TYPE_VIDEO_CAPTURE))

    def dequeue(self, timeout_sec: float) -> DqFrame | None:
        """프레임 1개. 타임아웃이면 None. 장치 오류는 OSError."""
        assert self._fd is not None
        r, _, _ = select.select([self._fd], [], [], timeout_sec)
        if not r:
            return None
        buf = v4l2_buffer()
        buf.type, buf.memory = V4L2_BUF_TYPE_VIDEO_CAPTURE, V4L2_MEMORY_MMAP
        try:
            fcntl.ioctl(self._fd, VIDIOC_DQBUF, buf)
        except BlockingIOError:
            return None
        # ★ 메타데이터는 QBUF **전에** 꺼낸다. QBUF ioctl은 같은 구조체를 덮어써서(flags=QUEUED, sequence·timestamp=0)
        #   뒤에 읽으면 순번·장치 시각·오류 플래그가 전부 사라진다(2026-09-21 실기기에서 발견).
        flags, sequence = int(buf.flags), int(buf.sequence)
        ts_ns = buf.timestamp.tv_sec * 1_000_000_000 + buf.timestamp.tv_usec * 1000
        try:
            used = buf.bytesused or self.sizeimage
            data = bytes(self._maps[buf.index][:used])
        finally:
            fcntl.ioctl(self._fd, VIDIOC_QBUF, buf)
        ts_kind = flags & V4L2_BUF_FLAG_TIMESTAMP_MASK
        clock = "host_monotonic" if ts_kind == V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC else (
            "copy" if ts_kind == V4L2_BUF_FLAG_TIMESTAMP_COPY else "unknown"
        )
        return DqFrame(
            data=data, bytesused=used, sequence=sequence, timestamp_ns=ts_ns,
            timestamp_clock=clock, error_flag=bool(flags & V4L2_BUF_FLAG_ERROR), flags=flags,
        )

    def stop(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.ioctl(self._fd, VIDIOC_STREAMOFF, ctypes.c_int(V4L2_BUF_TYPE_VIDEO_CAPTURE))
        except OSError:
            pass
        for m in self._maps:
            try:
                m.close()
            except (OSError, ValueError):
                pass
        self._maps = []

    def close(self) -> None:
        self.stop()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
