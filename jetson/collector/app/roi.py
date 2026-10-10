"""솥 ROI(관심 영역) — top view 프레임에서 솥 테두리 원을 찾는다.

카메라와 인덕션은 고정이지만 솥은 회차마다(조리 중에도) 조금씩 밀린다(2026-10-07·10-10 실측 ±90 px).
그래서 ROI를 상수로 박지 않고 **세션마다 주기적으로 다시 찾아** 기록한다. 원본 프레임은 자르지 않는다 —
ROI가 틀려도 나중에 다시 계산할 수 있게 전체 화면을 저장하고, ROI는 분석 때 적용한다.

검출: 긴 변 640 px로 축소 → 회색·medianBlur 7 → 허프 원(반지름은 긴 변의 14~22%로 제한 — 제한이 없으면
r≈609 px 같은 엉뚱한 원을 잡는다) → 후보마다 **테두리 일치 점수**(원 둘레 360점 중 Canny 에지 위에 놓인 비율)를
계산해 가장 높은 후보를 고른다. 실측(1920×1536, 100° 렌즈): 솥이 있는 프레임 0.72~0.94, 솥이 없는 프레임 ≤0.52.
기준 0.65 미만이면 "못 찾음"으로 남긴다.

좌표는 원본 해상도 화소(cx, cy, r)와 비율(cx/w, cy/h, r/w), 외접 사각형 비율(x0, y0, x1, y1)로 함께 준다.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .clock import utcnow_iso

log = logging.getLogger(__name__)

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - 수집은 cv2 없이도 돈다
    cv2 = None

WORK_SIDE = 640
DEFAULT_RADIUS_FRAC = (0.14, 0.22)
DEFAULT_MIN_SCORE = 0.65
#: 직전 검출보다 중심이 이 비율(긴 변 기준) 넘게 움직이면 "솥 이동" 사건을 남긴다
MOVE_WARN_FRAC = 0.03
_RING_POINTS = 360


def _ring_score(edges: np.ndarray, x: float, y: float, r: float) -> float:
    """원 둘레 점 중 에지 위에 놓인 비율. 화면 밖으로 나간 둘레는 0으로 센다."""
    t = np.linspace(0.0, 2 * np.pi, _RING_POINTS, endpoint=False)
    px = np.rint(x + r * np.cos(t)).astype(int)
    py = np.rint(y + r * np.sin(t)).astype(int)
    h, w = edges.shape
    inside = (px >= 0) & (px < w) & (py >= 0) & (py < h)
    if not inside.any():
        return 0.0
    return float((edges[py[inside], px[inside]] > 0).sum()) / _RING_POINTS


def detect_pot(image: np.ndarray, *, radius_frac: tuple[float, float] = DEFAULT_RADIUS_FRAC,
               min_score: float = DEFAULT_MIN_SCORE) -> dict[str, Any]:
    """BGR 또는 회색 이미지에서 솥 원을 찾는다. 항상 dict를 돌려주고 `found`로 성공 여부를 알린다."""
    if cv2 is None:
        return {"found": False, "reason": "opencv_unavailable"}
    img = np.asarray(image)
    if img.ndim not in (2, 3) or img.size == 0:
        return {"found": False, "reason": "bad_image"}
    h, w = img.shape[:2]
    scale = min(1.0, WORK_SIDE / max(h, w))
    small = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))),
                       interpolation=cv2.INTER_AREA) if scale < 1.0 else img
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
    gray = cv2.medianBlur(gray.astype(np.uint8), 7)
    side = max(gray.shape)
    r_min, r_max = int(radius_frac[0] * side), int(radius_frac[1] * side)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=10, param1=100, param2=30,
                               minRadius=r_min, maxRadius=r_max)
    base = {"image_w": int(w), "image_h": int(h), "candidates": 0}
    if circles is None:
        return {**base, "found": False, "reason": "no_circle"}
    edges = cv2.dilate(cv2.Canny(gray, 50, 100), np.ones((3, 3), np.uint8))
    cands = circles[0, :8]
    scores = [_ring_score(edges, float(c[0]), float(c[1]), float(c[2])) for c in cands]
    best = int(np.argmax(scores))
    bx, by, br = (float(v) for v in cands[best][:3])
    out = {**base, "candidates": int(len(cands)), "score": round(scores[best], 3)}
    if scores[best] < min_score:
        return {**out, "found": False, "reason": "low_score"}
    # 솥은 안쪽 벽 윗선(r≈322 px)과 바깥 테두리(r≈350 px)가 동심원 두 개로 잡힌다. 허프는 회차마다 둘 중 하나를
    # 고르므로, 같은 중심에서 반지름을 훑어 점수 기준을 넘는 **가장 큰 원(바깥 테두리)**으로 통일한다.
    radii = np.arange(r_min, r_max + 1, dtype=float)
    ring = np.array([_ring_score(edges, bx, by, rr) for rr in radii])
    ok = np.flatnonzero(ring >= min_score)
    if ok.size:
        br, out["score"] = float(radii[ok[-1]]), round(float(ring[ok[-1]]), 3)
    x, y, r = bx / scale, by / scale, br / scale
    return {**out, "found": True, **circle_fields(x, y, r, w, h)}


def circle_fields(x: float, y: float, r: float, w: int, h: int) -> dict[str, Any]:
    """원(화소)과 그 비율 표현. 사각형은 화면 안으로 자른다."""
    return {
        "cx": round(x, 1), "cy": round(y, 1), "r": round(r, 1),
        "norm": {"cx": round(x / w, 4), "cy": round(y / h, 4), "r": round(r / w, 4)},
        "bbox_ratio": [round(max(0.0, (x - r) / w), 4), round(max(0.0, (y - r) / h), 4),
                       round(min(1.0, (x + r) / w), 4), round(min(1.0, (y + r) / h), 4)],
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """한 센서의 검출 기록을 요약한다: 중앙값 원, 성공 횟수, 최대 이동량."""
    found = [r for r in results if r.get("found")]
    summary: dict[str, Any] = {"tried": len(results), "found": len(found)}
    if not found:
        return {**summary, "circle": None}
    w, h = found[-1]["image_w"], found[-1]["image_h"]
    xs, ys, rs = (np.array([f[k] for f in found]) for k in ("cx", "cy", "r"))
    mx, my, mr = float(np.median(xs)), float(np.median(ys)), float(np.median(rs))
    shift = float(np.max(np.hypot(xs - mx, ys - my)))
    return {**summary, "circle": circle_fields(mx, my, mr, w, h), "max_shift_px": round(shift, 1),
            "first_found_utc": found[0].get("host_utc"), "last_found_utc": found[-1].get("host_utc")}


class PotRoiTracker:
    """세션 중 솥 원을 주기적으로 찾는다. 수집 루프는 `offer`만 부르고, 변환·검출은 전용 스레드 하나가 한다.

    - 검출 중이면 새 프레임은 그냥 넘긴다(대기열 1칸) — 수집을 절대 막지 않는다.
    - 결과는 메모리(뷰어용)와, 녹화 세션이면 `<session>/pot_roi.jsonl`(한 줄 = 검출 1회)에 남긴다.
    """

    def __init__(self, sensor_ids: tuple[str, ...], *, decode: Callable[[Any], np.ndarray | None],
                 out_path: Path | None, interval_sec: float, first_delay_sec: float,
                 radius_frac: tuple[float, float] = DEFAULT_RADIUS_FRAC, min_score: float = DEFAULT_MIN_SCORE,
                 on_event: Callable[..., Any] | None = None) -> None:
        self.sensor_ids = tuple(sensor_ids)
        self._decode = decode
        self._out = out_path
        self._interval = max(1.0, float(interval_sec))
        self._radius_frac = radius_frac
        self._min_score = min_score
        self._on_event = on_event
        self._lock = threading.Lock()
        start = time.monotonic() + max(0.0, float(first_delay_sec))
        self._due = {sid: start for sid in self.sensor_ids}
        self._results: dict[str, list[dict[str, Any]]] = {sid: [] for sid in self.sensor_ids}
        self._last_found: dict[str, dict[str, Any]] = {}
        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pot-roi", daemon=True)
        self._thread.start()

    def wants(self, sensor_id: str) -> bool:
        return sensor_id in self._due

    def offer(self, sensor_id: str, sample: Any) -> None:
        due = self._due.get(sensor_id)
        if due is None or self._stop.is_set() or time.monotonic() < due or not getattr(sample, "valid", True):
            return
        try:
            self._queue.put_nowait((sensor_id, sample))
        except queue.Full:
            return
        with self._lock:
            self._due[sensor_id] = time.monotonic() + self._interval

    def request_now(self, sensor_id: str | None = None) -> list[str]:
        """다음 프레임에서 바로 다시 찾게 한다(뷰어 "솥 다시 찾기")."""
        targets = [sensor_id] if sensor_id else list(self.sensor_ids)
        hit = [sid for sid in targets if sid in self._due]
        with self._lock:
            for sid in hit:
                self._due[sid] = 0.0
        return hit

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sensor_id, sample = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                img = self._decode(sample)
                res = (detect_pot(img, radius_frac=self._radius_frac, min_score=self._min_score) if img is not None
                       else {"found": False, "reason": "decode_failed"})
            except Exception as exc:  # 검출 실패가 세션을 죽이지 않는다
                res = {"found": False, "reason": f"error: {exc!r}"}
            res = {"sensor_id": sensor_id, "stream_id": sample.stream_id, "seq": sample.seq,
                   "host_utc": sample.host.utc, "detected_at": utcnow_iso(), **res}
            self._record(sensor_id, res)

    def _record(self, sensor_id: str, res: dict[str, Any]) -> None:
        with self._lock:
            prev = self._last_found.get(sensor_id)
            self._results[sensor_id].append(res)
            if res.get("found"):
                self._last_found[sensor_id] = res
        if self._out is not None:
            try:
                with open(self._out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(res, ensure_ascii=False) + "\n")
            except OSError as exc:
                log.warning("pot_roi 기록 실패: %s", exc)
        if self._on_event is None:
            return
        if res.get("found") and prev is None:
            self._on_event("info", "pot_roi.found", f"{sensor_id} 솥 원 검출 (중심 {res['cx']},{res['cy']} r={res['r']})",
                           sensor_id=sensor_id, cx=res["cx"], cy=res["cy"], r=res["r"], score=res["score"])
        elif res.get("found") and prev is not None:
            moved = float(np.hypot(res["cx"] - prev["cx"], res["cy"] - prev["cy"]))
            if moved > MOVE_WARN_FRAC * max(res["image_w"], res["image_h"]):
                self._on_event("warn", "pot_roi.moved", f"{sensor_id} 솥 위치 이동 {moved:.0f} px",
                               sensor_id=sensor_id, moved_px=round(moved, 1), cx=res["cx"], cy=res["cy"], r=res["r"])

    def latest(self) -> dict[str, Any]:
        with self._lock:
            return {sid: {"last": (self._results[sid][-1] if self._results[sid] else None),
                          "last_found": self._last_found.get(sid),
                          "tried": len(self._results[sid]),
                          "found": sum(1 for r in self._results[sid] if r.get("found"))}
                    for sid in self.sensor_ids}

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {sid: summarize(list(rs)) for sid, rs in self._results.items()}

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)
