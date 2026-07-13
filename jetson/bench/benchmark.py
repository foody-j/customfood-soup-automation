#!/usr/bin/env python3
"""Jetson Orin Nano 엣지 벤치마크 하네스.

후보 모델을 ONNX로 export → `trtexec`로 TensorRT 엔진 빌드 + 타이밍(FP16/INT8) →
지연·처리량을 파싱하고, 실행 중 `tegrastats`로 전력·온도를 샘플링한다.

⚠️ **랜덤 가중치·랜덤 입력이라 정확도가 아니라 '성능(지연/FPS/전력)'만** 측정한다.
   목적: 3차 조사가 남긴 "검증된 Jetson 지연·전력 수치 없음"을 실측으로 메우는 것.

Jetson에서만 실행 가능(CUDA/TensorRT 필요). 사용법: jetson/bench/README.md
"""
import argparse
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_OUT = os.path.join(REPO, "notes", "data", "bench")
DEFAULT_TRTEXEC = "/usr/src/tensorrt/bin/trtexec"


# ── 전력·온도 샘플러 (tegrastats) ────────────────────────────────────────────
class PowerSampler:
    """백그라운드로 tegrastats를 읽어 전력(mW)·온도(℃)를 수집."""
    RAIL_RE = re.compile(r"([A-Z0-9_]+) (\d+)mW/(\d+)mW")
    TEMP_RE = re.compile(r"(\w+)@([\d.]+)C")

    def __init__(self, interval_ms=200):
        self.interval_ms = interval_ms
        self.proc = None
        self.thread = None
        self.powers = []   # 순간 입력전력(mW)
        self.temps = []     # 순간 최대온도(℃)
        self._stop = False

    def _read(self):
        for line in self.proc.stdout:
            if self._stop:
                break
            rails = {m.group(1): int(m.group(2)) for m in self.RAIL_RE.finditer(line)}
            # 입력 전력 레일 우선순위: VDD_IN → POM_5V_IN → 아무 *_IN
            p = rails.get("VDD_IN") or rails.get("POM_5V_IN")
            if p is None:
                for k, v in rails.items():
                    if k.endswith("_IN"):
                        p = v
                        break
            if p is not None:
                self.powers.append(p)
            temps = [float(m.group(2)) for m in self.TEMP_RE.finditer(line)]
            if temps:
                self.temps.append(max(temps))

    def start(self):
        try:
            self.proc = subprocess.Popen(
                ["tegrastats", "--interval", str(self.interval_ms)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
        except FileNotFoundError:
            self.proc = None
            return False
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self._stop = True
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return {
            "power_mean_mw": round(sum(self.powers) / len(self.powers)) if self.powers else None,
            "power_max_mw": max(self.powers) if self.powers else None,
            "temp_max_c": max(self.temps) if self.temps else None,
            "samples": len(self.powers),
        }


# ── trtexec 실행 + 파싱 ──────────────────────────────────────────────────────
COMPUTE_RE = re.compile(
    r"GPU Compute Time:.*?mean = ([\d.]+) ms.*?median = ([\d.]+) ms.*?"
    r"percentile\((\d+)%\) = ([\d.]+) ms"
)
THROUGHPUT_RE = re.compile(r"Throughput:\s*([\d.]+)\s*qps")


def run_trtexec(trtexec, onnx_path, precision, iterations, warmup_ms, percentile):
    cmd = [
        trtexec, f"--onnx={onnx_path}",
        f"--iterations={iterations}", f"--warmUp={warmup_ms}",
        f"--percentile={percentile}", "--avgRuns=100",
    ]
    if precision == "fp16":
        cmd.append("--fp16")
    elif precision == "int8":
        cmd += ["--int8", "--fp16"]  # 캘리브레이션 없음 → 지연/전력 측정 전용
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        return {"error": "trtexec timeout", "trtexec_rc": None}
    txt = out.stdout + out.stderr
    res = {"trtexec_rc": out.returncode}
    m = COMPUTE_RE.search(txt)
    if m:
        res["latency_mean_ms"] = float(m.group(1))
        res["latency_median_ms"] = float(m.group(2))
        res[f"latency_p{m.group(3)}_ms"] = float(m.group(4))
    t = THROUGHPUT_RE.search(txt)
    if t:
        res["throughput_fps"] = float(t.group(1))
    if out.returncode != 0 and "latency_mean_ms" not in res:
        # 마지막 에러 라인 몇 개만 보존
        errlines = [l for l in txt.splitlines() if "error" in l.lower() or "Error" in l][-3:]
        res["error"] = " | ".join(errlines) or f"trtexec rc={out.returncode}"
    return res


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Jetson 엣지 벤치마크 (성능 전용, 랜덤 가중치)")
    ap.add_argument("--models", default="all", help="쉼표구분 또는 all")
    ap.add_argument("--precisions", default="fp16,int8")
    ap.add_argument("--iterations", type=int, default=200)
    ap.add_argument("--warmup-ms", type=int, default=500)
    ap.add_argument("--percentile", type=int, default=95)
    ap.add_argument("--img", type=int, default=224, help="분류 백본 입력 해상도")
    ap.add_argument("--tcn-frames", type=int, default=16)
    ap.add_argument("--num-classes", type=int, default=3)
    ap.add_argument("--trtexec", default=DEFAULT_TRTEXEC)
    ap.add_argument("--outdir", default=DEFAULT_OUT)
    args = ap.parse_args()

    # torch/onnx는 Jetson에서만 존재 → 여기서 import(문법검사 시 미실행)
    from models import model_specs, export_onnx

    trtexec = args.trtexec if os.path.exists(args.trtexec) else "trtexec"
    specs = model_specs(args.num_classes, args.img, args.tcn_frames)
    names = list(specs) if args.models == "all" else [m.strip() for m in args.models.split(",")]
    precisions = [p.strip() for p in args.precisions.split(",")]

    os.makedirs(args.outdir, exist_ok=True)
    onnx_dir = os.path.join(args.outdir, "onnx")
    os.makedirs(onnx_dir, exist_ok=True)
    results = []

    for name in names:
        if name not in specs:
            print(f"[skip] unknown model: {name}")
            continue
        build, shape = specs[name]
        onnx_path = os.path.join(onnx_dir, f"{name}.onnx")
        try:
            print(f"[export] {name}  shape={shape}")
            export_onnx(build(), shape, onnx_path)
        except Exception as e:  # noqa: BLE001
            print(f"[export FAIL] {name}: {e}")
            results.append({"model": name, "precision": "-", "error": f"export: {e}"})
            continue

        for prec in precisions:
            print(f"[bench] {name} · {prec}")
            sampler = PowerSampler()
            sampler.start()
            r = run_trtexec(trtexec, onnx_path, prec, args.iterations, args.warmup_ms, args.percentile)
            power = sampler.stop()
            row = {"model": name, "precision": prec, "input_shape": list(shape), **r, **power}
            results.append(row)
            with open(os.path.join(args.outdir, f"{name}_{prec}.json"), "w") as f:
                json.dump(row, f, ensure_ascii=False, indent=2)
            lat = r.get("latency_mean_ms")
            fps = r.get("throughput_fps")
            pw = power.get("power_mean_mw")
            print(f"   → mean {lat} ms | {fps} fps | {pw} mW"
                  if lat else f"   → FAIL: {r.get('error')}")

    write_summary(args.outdir, results, args)
    print(f"\n요약: {os.path.join(args.outdir, 'summary.md')}")


def write_summary(outdir, results, args):
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        "# Jetson 엣지 벤치마크 결과",
        "",
        f"> 생성: {ts} · **성능 전용(랜덤 가중치·입력, 정확도 아님)**",
        f"> 설정: iterations={args.iterations}, warmup={args.warmup_ms}ms, p{args.percentile}, img={args.img}",
        "> 측정 전 `sudo nvpmodel`(MAXN SUPER) + `sudo jetson_clocks` 적용 권장.",
        "",
        "| 모델 | 정밀도 | 입력 | 평균지연(ms) | p95(ms) | 처리량(FPS) | 평균전력(mW) | 최대온도(℃) | 비고 |",
        "|------|--------|------|-------------|---------|-------------|--------------|-------------|------|",
    ]
    for r in results:
        p95 = next((v for k, v in r.items() if k.startswith("latency_p")), None)
        note = r.get("error", "")
        if note and len(note) > 40:
            note = note[:40] + "…"
        lines.append(
            f"| {r.get('model')} | {r.get('precision')} | "
            f"{'×'.join(map(str, r.get('input_shape', []))) or '-'} | "
            f"{r.get('latency_mean_ms', '-')} | {p95 if p95 is not None else '-'} | "
            f"{r.get('throughput_fps', '-')} | {r.get('power_mean_mw', '-')} | "
            f"{r.get('temp_max_c', '-')} | {note} |"
        )
    lines += [
        "",
        "## 해석",
        "- **실시간 판정 목표 ≥ 5~10 FPS** (조리는 느려 이 정도면 충분). 조사의 X3D-UGT는 Orin Nano에서 ~10 FPS였음.",
        "- 도네스 분류(MobileNetV3-Small/EfficientNet-B0)는 여유가 커야 정상. 열화상 브랜치는 매우 가벼움.",
        "- 이 표를 **모델 아키텍처 확정**(백본·정밀도 선택)의 근거로 사용.",
        "- INT8은 캘리브레이션 없이 측정한 값 → **정확도 아닌 지연/전력 상한 참고용**.",
    ]
    with open(os.path.join(outdir, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
