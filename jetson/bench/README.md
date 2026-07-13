# Jetson 엣지 벤치마크

후보 모델을 **Jetson Orin Nano에서** TensorRT로 돌려 **지연·FPS·전력·온도**를 실측한다.
3차 조사(`docs/research-methodology-3-open-questions.md`)가 남긴 "검증된 Jetson 성능 수치 없음"을 메우는 용도.

> ⚠️ **성능 전용.** 랜덤 가중치·랜덤 입력이라 **정확도가 아니라 속도/전력만** 잰다.
> 실제 정확도는 데이터 수집·학습 후 별도로.

## 사전 준비
`docs/JETSON_SETUP.md`를 먼저 끝낼 것 (PyTorch(jp6/cu126) + venv + 전력모드). 그다음:
```bash
source ~/cf-venv/bin/activate
cd ~/customfood-soup-automation/jetson/bench
pip install -r requirements.txt          # onnx, numpy (torch/torchvision은 이미 설치돼 있어야 함)
```
측정 정확도를 위해 **최대 전력 + 클럭 고정**:
```bash
sudo nvpmodel -m 0        # MAXN SUPER (nvpmodel -q로 인덱스 확인)
sudo jetson_clocks
```

## 실행
```bash
cd ~/customfood-soup-automation
python3 jetson/bench/benchmark.py                       # 전체 모델 × fp16,int8
# 옵션
python3 jetson/bench/benchmark.py --models mobilenet_v3_small,thermal_cnn --precisions fp16
python3 jetson/bench/benchmark.py --img 256 --iterations 300
```
결과:
- `notes/data/bench/summary.md` — 사람이 볼 표 (모델별 지연·FPS·전력·온도)
- `notes/data/bench/<model>_<precision>.json` — 개별 원시 수치
- `notes/data/bench/onnx/` — export된 ONNX (gitignore, 커밋 안 됨)

## 후보 모델
| 이름 | 역할 | 입력 |
|------|------|------|
| `mobilenet_v3_small` | 도네스 분류 백본 | 3×224×224 |
| `efficientnet_b0` | 도네스 분류 백본(≈lite0) | 3×224×224 |
| `thermal_cnn` | 열화상 단일프레임 2D-CNN | 1×24×32 |
| `thermal_seq_tcn` | 열화상 2D-CNN+1D TCN(시퀀스) | 16×1×24×32 |

## 해석 기준
- **실시간 목표 ≥ 5~10 FPS** (조리는 느려 충분). 조사의 X3D-UGT가 Orin Nano에서 ~10 FPS.
- 도네스 분류 백본은 여유가 커야 정상, 열화상 브랜치는 매우 가벼움 → 합쳐도 실시간 가능한지 확인.
- 이 수치로 **모델 아키텍처(백본·정밀도) 확정**.

## 트러블슈팅
- `trtexec not found` → `--trtexec /usr/src/tensorrt/bin/trtexec`
- 전력이 `-`로 나옴 → tegrastats 권한/미설치. `which tegrastats` 확인(보통 기본 제공).
- export 실패한 모델은 표에 사유 표기되고 다음 모델로 진행됨.
