"""세션 요약 이미지(PNG 한 장) — 사람이 세션을 5분 안에 훑어보고 사용/제외를 판단하기 위한 것.

구성(위→아래):
  머리글  세션 ID·이름·길이·조건·무결성
  카메라  카메라마다 일정 간격 썸네일 한 줄(김 서림·가림·조명 확인)
  온도    PT100 · 열화상 최고 · 열화상 평균 (단위가 모두 °C라 축 하나) + Pi 정답 사건 세로선
  수신    스트림별로 데이터가 들어온 구간 막대 — 빈 곳이 끊김

원본은 읽기만 한다. 색은 dataviz 기본 팔레트(categorical 1~3), 글자는 잉크 색.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .qc import parse_utc, pi_marks
from .session import ARRAY, IMAGE, SCALAR, Session

INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e6e5e0", "#fcfcfb"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]  # blue, orange, aqua — 고정 순서
MARK_LABELS = {"boil_start": "끓음", "taste": "맛", "done_start": "완료 시작", "done_end": "완료 끝",
               "overcooked": "과조리", "lid": "뚜껑", "ingredient": "재료", "heat": "가열", "stir": "교반", "note": "메모"}
VALUE_LABELS = {"undercooked": "미완", "done": "완료", "overcooked": "과조리", "on": "덮음", "off": "엶"}
GT_KINDS = ("boil_start", "taste", "done_start", "done_end", "overcooked")


def _font():
    import logging

    import matplotlib
    from matplotlib import font_manager

    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)  # VF 굵기 경고 숨김

    for name in ("Noto Sans CJK KR", "NanumGothic", "Noto Sans KR"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            matplotlib.rcParams["font.family"] = name
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def _minutes(t: datetime, t0: datetime) -> float:
    return (t - t0).total_seconds() / 60.0


def _thumbs(sess: Session, sensor_id: str, stream_id: str, t0: datetime, n: int) -> list[tuple[float, Any]]:
    from PIL import Image

    lines = sess.stored(sensor_id, stream_id)
    if not lines:
        return []
    idx = np.linspace(0, len(lines) - 1, num=min(n, len(lines))).round().astype(int)
    out = []
    for i in idx:
        ln = lines[i]
        p = sess.frame_path(ln)
        t = parse_utc(ln.get("host_recv_utc"))
        try:
            with Image.open(p) as im:
                im.thumbnail((320, 256))
                out.append((_minutes(t, t0) if t else float("nan"), im.convert("RGB").copy()))
        except Exception:
            out.append((_minutes(t, t0) if t else float("nan"), None))  # .raw 등 열 수 없는 프레임
    return out


def render_summary(sess: Session, out: Path, pi_export: dict[str, Any] | None = None,
                   verify: dict[str, Any] | None = None, n_thumbs: int = 10, calibration: dict[str, Any] | None = None,
                   rules=None) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _font()
    meta, manifest = sess.meta, sess.manifest or {}
    ph = meta.get("phases") or {}
    t0 = parse_utc(ph.get("running"))
    t1 = parse_utc(ph.get("stop_requested") or ph.get("stopping") or ph.get("files_closed"))
    refs = sess.streams()
    if t0 is None:
        firsts = [parse_utc(sess.stored(r.sensor_id, r.stream_id)[0]["host_recv_utc"]) for r in refs if sess.stored(r.sensor_id, r.stream_id)]
        t0 = min(firsts) if firsts else datetime.now().astimezone()
    dur_min = _minutes(t1, t0) if t1 else None

    from .qc import heating_qc

    hq = heating_qc(sess, t0, pi_export, calibration, rules) if t0 else {}
    heat, objective = hq.get("heating") or {}, hq.get("objective") or {}
    warn_flags = list(heat.get("flags") or []) + list(objective.get("flags") or [])
    cams = [r for r in refs if r.kind == IMAGE]
    from .camera import camera_qc

    cam_qc = {}
    for r in cams:
        try:
            cam_qc[r.rel] = camera_qc(sess, r.sensor_id, r.stream_id, t0)
        except Exception:  # 품질 지표 실패가 요약 이미지를 막지 않게
            pass
    fig_h = 1.0 + 1.55 * len(cams) + 3.4 + 0.32 * max(len(refs), 1) + 0.6
    fig = plt.figure(figsize=(16, fig_h), dpi=110, facecolor=SURFACE)
    heights = [0.9] + [1.55] * len(cams) + [3.4, 0.32 * max(len(refs), 1) + 0.6]
    gs = fig.add_gridspec(len(heights), 1, height_ratios=heights, hspace=0.45, left=0.1, right=0.9, top=0.98, bottom=0.04)

    # ── 머리글 ──
    ax = fig.add_subplot(gs[0]); ax.axis("off")
    s = manifest.get("summary") or {}
    sess_pi = (pi_export or {}).get("session") or {}
    params = sess_pi.get("params") or {}
    ver = "무결성 OK" if (verify or {}).get("ok") else ("무결성 미검증" if not verify else "무결성 문제")
    ax.text(0, 0.95, f"{sess.session_id}  ·  {meta.get('name') or ''}", fontsize=15, color=INK, va="top", weight="bold")
    line2 = (f"길이 {dur_min:.1f}분" if dur_min else "길이 —") + \
        f"  ·  저장 {s.get('frames_written', '—')}  ·  버림 {s.get('frames_dropped', '—')}  ·  무효 {s.get('frames_invalid', '—')}" + \
        f"  ·  {(s.get('bytes_written') or 0) / 1e9:.2f} GB  ·  {ver}"
    ax.text(0, 0.5, line2, fontsize=10.5, color=INK2, va="top")
    cond = "  ·  ".join(f"{k}={v}" for k, v in params.items() if v is not None) or (sess_pi.get("conditions") or "조건 기록 없음")
    ax.text(0, 0.12, f"조건: {cond}", fontsize=10.5, color=INK2, va="top")
    boil = (heat.get("boil") or {})
    if heat:
        b_txt = (f"끓기 시작 {boil['onset_s'] / 60:.1f}분 · 끓는 구간 {boil['plateau_c']:.1f} ℃"
                 if boil.get("onset_s") is not None else "끓는 구간 없음")
        ax.text(0.55, 0.5, b_txt + (f"  ·  조리값 C100 {heat['c100_end']:.1f}분" if heat.get("c100_end") is not None else ""),
                fontsize=10.5, color=INK2, va="top")
        crit = [f for f in warn_flags if "보정" in f and "끓는 구간" in f] or [f for f in warn_flags if "못 찾음" in f]
        if crit:
            ax.text(0.55, 0.12, "⚠ " + crit[0][:70], fontsize=10.5, color=INK, va="top", weight="bold")

    # ── 카메라 썸네일 ──
    for ci, r in enumerate(cams):
        ax = fig.add_subplot(gs[1 + ci]); ax.axis("off")
        th = _thumbs(sess, r.sensor_id, r.stream_id, t0, n_thumbs)
        ax.text(-0.005, 0.6, r.sensor_id.replace("cam_", ""), transform=ax.transAxes, ha="right", va="center", fontsize=10, color=INK2)
        cq = cam_qc.get(r.rel)
        if cq is not None:
            if any("초점 흐림" in f for f in cq.flags):
                tag = "⚠ 흐림"
            elif any("김 서림" in f for f in cq.flags):
                tag = f"⚠ 김 {cq.fog_first_min:.1f}분~" if cq.fog_first_min is not None else "⚠ 김 서림"
            else:
                tag = f"선명도 {cq.sharp_median:.0f}" if cq.sharp_median is not None else ""
            ax.text(-0.005, 0.3, tag, transform=ax.transAxes, ha="right", va="center", fontsize=9,
                    color=INK if tag.startswith("⚠") else MUTED, weight="bold" if tag.startswith("⚠") else "normal")
        w = 1.0 / max(len(th), 1)
        for k, (m, im) in enumerate(th):
            sub = ax.inset_axes([k * w + 0.002, 0.14, w - 0.004, 0.86]); sub.axis("off")
            if im is not None:
                sub.imshow(im)
            else:
                sub.text(0.5, 0.5, "열 수 없음", ha="center", va="center", fontsize=8, color=MUTED, transform=sub.transAxes)
            ax.text(k * w + w / 2, 0.02, f"{max(m, 0):.0f}분" if m == m else "—", ha="center", va="bottom", fontsize=8.5, color=MUTED,
                    transform=ax.transAxes)

    # ── 온도 ──
    axT = fig.add_subplot(gs[1 + len(cams)], facecolor=SURFACE)
    series = []
    pt = next((r for r in refs if r.kind == SCALAR and r.sensor_id.startswith("pt100")), None)
    if pt:
        pts = [(parse_utc(t), v) for t, v in sess.scalars(pt.sensor_id, pt.stream_id) if v is not None]
        series.append(("PT100 (중심)", [_minutes(t, t0) for t, _ in pts], [v for _, v in pts]))
    th = next((r for r in refs if r.kind == ARRAY and r.sensor_id.startswith("thermal")), None)
    if th:
        xs, mx, mn = [], [], []
        for ln, a in sess.iter_arrays(th.sensor_id, th.stream_id):
            a = np.asarray(a, dtype=np.float64)
            xs.append(_minutes(parse_utc(ln["host_recv_utc"]), t0)); mx.append(float(np.nanmax(a))); mn.append(float(np.nanmean(a)))
        series.append(("열화상 최고", xs, mx))
        series.append(("열화상 평균", xs, mn))
    for (name, xs, ys), color in zip(series, SERIES):
        axT.plot(xs, ys, color=color, lw=2, label=name, solid_capstyle="round")
        if xs:
            axT.annotate(name, (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points", va="center",
                         fontsize=9.5, color=INK2, annotation_clip=False)
    axT.set_ylabel("온도 (°C)", color=INK2, fontsize=10)
    axT.grid(axis="y", color=GRID, lw=0.8)
    for sp in ("top", "right"):
        axT.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        axT.spines[sp].set_color(MUTED)
    axT.tick_params(colors=MUTED, labelsize=9)
    if series:
        axT.legend(loc="upper left", frameon=False, fontsize=9.5, labelcolor=INK2, ncol=len(series))
    marks = [m for m in pi_marks(pi_export) if m["at"]]
    if heat:
        ref_c = heat.get("ref_c", 75.0)
        axT.axhline(ref_c, color=MUTED, lw=0.9, ls=(0, (2, 3)), zorder=0)
        axT.text(0.002, ref_c, f" {ref_c:g} ℃ 참고", transform=axT.get_yaxis_transform(), va="bottom", fontsize=8.5, color=INK2)
        if boil.get("onset_s") is not None:
            xb = boil["onset_s"] / 60.0
            axT.axvline(xb, color=INK2, lw=1.4, ls=(0, (5, 3)), zorder=0)
    ymax = axT.get_ylim()[1]
    if heat and boil.get("onset_s") is not None:  # 선 왼쪽 아래에 가로로 — 위쪽은 곡선·사건 이름과 겹친다
        axT.annotate("끓음(자동) ", (boil["onset_s"] / 60.0, 0.03), xycoords=("data", "axes fraction"), ha="right",
                     va="bottom", fontsize=8.5, color=INK2)
    for k, m in enumerate(marks):
        t = parse_utc(m["at"])
        if t is None:
            continue
        x = _minutes(t, t0)
        strong = m["kind"] in GT_KINDS
        axT.axvline(x, color=INK if strong else MUTED, lw=1.2 if strong else 0.8, ls="-" if strong else ":", zorder=0)
        label = MARK_LABELS.get(m["kind"], m["kind"]) + (f":{VALUE_LABELS.get(m['value'], m['value'])}" if m["value"] else "")
        axT.text(x, ymax, label, rotation=90, va="top", ha="right", fontsize=8.5, color=INK if strong else INK2)
    if not marks:  # 세션 단위 상태라 그래프 안이 아니라 머리글에 둔다(곡선·선 이름과 겹치지 않게)
        fig.axes[0].text(0.55, 0.95, "Pi 사건 없음 — 관능 라벨·맛보기 검증 불가", fontsize=10.5, color=INK2, va="top")

    # ── 수신 상태 ──
    axR = fig.add_subplot(gs[2 + len(cams)], sharex=axT, facecolor=SURFACE)
    for row, r in enumerate(refs):
        ts = [parse_utc(ln["host_recv_utc"]) for ln in sess.stored(r.sensor_id, r.stream_id)]
        xs = np.array([_minutes(t, t0) for t in ts if t])
        if len(xs) == 0:
            axR.text(0, row, "  수신 없음", va="center", fontsize=8.5, color=INK2)
            continue
        gaps = np.diff(xs) * 60.0
        typical = float(np.median(gaps)) if len(gaps) else 1.0
        breaks = np.where(gaps > max(3 * typical, 2.0))[0]
        starts = np.concatenate([[xs[0]], xs[breaks + 1]])
        ends = np.concatenate([xs[breaks], [xs[-1]]])
        axR.broken_barh([(a, max(b - a, 0.05)) for a, b in zip(starts, ends)], (row - 0.32, 0.64), color=SERIES[0], linewidth=0)
        if len(breaks):
            axR.text(xs[-1], row, f"  끊김 {len(breaks)}", va="center", fontsize=8.5, color=INK2, clip_on=False)
    axR.set_yticks(range(len(refs)), [r.rel for r in refs], fontsize=8.5, color=INK2)
    axR.set_ylim(-0.6, len(refs) - 0.4)
    axR.invert_yaxis()
    axR.set_xlabel("촬영 시작 후 경과 (분)", color=INK2, fontsize=10)
    for sp in ("top", "right", "left"):
        axR.spines[sp].set_visible(False)
    axR.spines["bottom"].set_color(MUTED)
    axR.tick_params(colors=MUTED, labelsize=9, left=False)
    if dur_min:
        axT.set_xlim(0, dur_min)

    # 설치된 한글 글꼴이 가변 글꼴(VF)뿐이면 matplotlib가 가장 가는 굵기로 그린다 → 같은 색 외곽선으로 굵기 보정
    from matplotlib import patheffects
    from matplotlib.text import Text

    for t in fig.findobj(Text):
        if t.get_text():
            t.set_path_effects([patheffects.withStroke(linewidth=0.6, foreground=t.get_color())])
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return out
