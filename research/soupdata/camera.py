"""카메라 프레임 품질과 특징 — 선명도(초점·김 서림), 밝기, 화면 정지, 솥 영역 색(CIELAB)·색 변화, 움직임.

JPEG을 축소 디코드(PIL draft, 약 320×256)해 프레임당 수 ms(9/28 실측 약 6 ms). 원본은 읽기만 한다.
선명도 = 회색조 라플라시안 분산 × 10⁴. 9/28 실측: 선명한 GMSL2 ②는 ≈350, 초점이 나간 GMSL2 ①은 ≈0.8,
물이 끓자 GMSL2 ②도 20~160으로, Gemini 컬러는 ≈1로 떨어졌다(렌즈 김 서림).

- **초점 흐림·가림:** 세션 선명도 중앙값이 `SHARP_BLUR_ABS` 미만.
- **김 서림:** 앞부분(기준 구간) 선명도 대비 `FOG_RATIO` 미만인 표본이 일정 비율 이상 — 처음 시각을 함께 남긴다.
- **어두움 / 화면 정지:** 평균 밝기 `DARK` 미만, 연속 표본 차이 `FROZEN_DIFF` 미만(같은 화면 반복).
- 특징(모델 입력): 선명도, 밝기, 솥 영역 L*·a*·b*, 첫 프레임 대비 색 차이 ΔE, 직전 표본 대비 움직임(끓는 거품 등).
  솥 영역은 기본 가운데 50%(`ROI`) — 카메라 위치를 고정한 뒤 실측해 바꾼다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .qc import parse_utc
from .session import Session

SHARP_BLUR_ABS = 5.0
FOG_RATIO = 0.3
FOG_MIN_FRAC = 0.05
DARK = 0.06
FROZEN_DIFF = 1e-4
ROI = (0.25, 0.25, 0.75, 0.75)  # (x0, y0, x1, y1) 비율
BASELINE_S = 120.0
DECODE_SIZE = (320, 256)
_LUMA = np.array([0.299, 0.587, 0.114])


def _srgb_to_lab(rgb: np.ndarray) -> tuple[float, float, float]:
    """평균 sRGB(0~1) 한 점 → CIELAB(D65)."""
    c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
    x, y, z = (m @ c) / np.array([0.95047, 1.0, 1.08883])
    f = lambda t: np.cbrt(t) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116  # noqa: E731
    fx, fy, fz = f(x), f(y), f(z)
    return float(116 * fy - 16), float(500 * (fx - fy)), float(200 * (fy - fz))


def frame_metrics(path: Path | None, roi: tuple[float, float, float, float] = ROI) -> dict[str, Any] | None:
    """프레임 하나의 품질·색 지표. 열 수 없으면(.raw 등) None."""
    if path is None:
        return None
    from PIL import Image

    try:
        with Image.open(path) as im:
            im.draft("RGB", DECODE_SIZE)
            im = im.convert("RGB")
            im.thumbnail(DECODE_SIZE)
            a = np.asarray(im, dtype=np.float64) / 255.0
    except Exception:
        return None
    if a.ndim != 3 or min(a.shape[:2]) < 3:
        return None
    g = a @ _LUMA
    lap = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1]
    h, w = g.shape
    x0, y0, x1, y1 = int(roi[0] * w), int(roi[1] * h), max(int(roi[2] * w), int(roi[0] * w) + 1), max(int(roi[3] * h), int(roi[1] * h) + 1)
    roi_rgb = a[y0:y1, x0:x1]
    L, A, B = _srgb_to_lab(roi_rgb.reshape(-1, 3).mean(axis=0))
    small = g[y0:y1, x0:x1]
    step = max(1, small.shape[0] // 48)
    return {"sharp": float(lap.var() * 1e4), "bright": float(g.mean()), "contrast": float(g.std()),
            "L": L, "a": A, "b": B, "_roi_gray": small[::step, ::step]}


def camera_series(sess: Session, sensor_id: str, stream_id: str, t0: datetime, step_s: float = 1.0,
                  roi: tuple[float, float, float, float] = ROI) -> list[dict[str, Any]]:
    """`step_s`초마다 한 프레임(그 시각 이후 첫 저장 프레임)의 지표 + ΔE(첫 유효 프레임 대비)·움직임(직전 표본 대비)."""
    lines = sess.stored(sensor_id, stream_id)
    out: list[dict[str, Any]] = []
    next_t, first_lab, prev = -1e9, None, None
    for ln in lines:
        t = parse_utc(ln.get("host_recv_utc"))
        if t is None:
            continue
        sec = (t - t0).total_seconds()
        if sec < next_t:
            continue
        next_t = sec + step_s
        m = frame_metrics(sess.frame_path(ln), roi)
        if m is None:
            continue
        lab = np.array([m["L"], m["a"], m["b"]])
        first_lab = lab if first_lab is None else first_lab
        roi_g = m.pop("_roi_gray")
        motion = float(np.mean(np.abs(roi_g - prev))) if prev is not None and prev.shape == roi_g.shape else None
        prev = roi_g
        out.append({"sec": sec, **m, "dE": float(np.linalg.norm(lab - first_lab)), "motion": motion})
    return out


@dataclass
class CameraQC:
    stream: str
    samples: int
    sharp_median: float | None
    baseline: float | None
    fog_frac: float | None
    fog_first_min: float | None
    dark_frac: float | None
    frozen_frac: float | None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def camera_qc(sess: Session, sensor_id: str, stream_id: str, t0: datetime, step_s: float = 10.0) -> CameraQC:
    ser = camera_series(sess, sensor_id, stream_id, t0, step_s)
    rel = f"{sensor_id}/{stream_id}"
    if not ser:
        return CameraQC(rel, 0, None, None, None, None, None, None, [f"{rel}: 열 수 있는 프레임 없음"])
    sec = np.array([s["sec"] for s in ser])
    sharp = np.array([s["sharp"] for s in ser])
    bright = np.array([s["bright"] for s in ser])
    motion = np.array([s["motion"] for s in ser if s["motion"] is not None])
    med = float(np.median(sharp))
    base_mask = sec <= sec[0] + BASELINE_S
    baseline = float(np.median(sharp[base_mask])) if base_mask.sum() >= 3 else float(np.percentile(sharp, 75))
    flags = []
    fog_frac = fog_first = None
    if med < SHARP_BLUR_ABS:
        flags.append(f"{rel}: 초점 흐림·렌즈 가림 의심(선명도 중앙값 {med:.1f} < {SHARP_BLUR_ABS:g}, 정상 수백)")
    elif baseline > 0:
        after = ~base_mask
        foggy = after & (sharp < FOG_RATIO * baseline)
        fog_frac = float(foggy.sum() / max(after.sum(), 1))
        if foggy.any():
            fog_first = float(sec[foggy][0] / 60.0)
        if fog_frac >= FOG_MIN_FRAC:
            flags.append(f"{rel}: 김 서림·가림 의심 — 기준 선명도의 {FOG_RATIO:.0%} 미만이 {fog_frac:.0%}"
                         f"(처음 {fog_first:.1f}분)")
    dark_frac = float((bright < DARK).mean())
    if dark_frac >= 0.2:
        flags.append(f"{rel}: 너무 어두움(밝기 {DARK:g} 미만 {dark_frac:.0%}) — 조명 확인")
    frozen_frac = float((motion < FROZEN_DIFF).mean()) if len(motion) else None
    if frozen_frac is not None and frozen_frac >= 0.5:
        flags.append(f"{rel}: 화면 정지 의심(연속 표본이 같은 화면 {frozen_frac:.0%})")
    return CameraQC(rel, len(ser), med, baseline, fog_frac, fog_first, dark_frac, frozen_frac, flags)
