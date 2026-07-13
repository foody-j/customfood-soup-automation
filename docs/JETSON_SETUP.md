# Jetson Orin Nano 셋업 (JetPack 6.2.2 / L4T 36.5)

발행자(publisher) 겸 AI 추론 노드인 **Jetson Orin Nano Super**의 소프트웨어 스택 셋업.
센서가 없어도 여기까지는 끝낼 수 있다. 실행은 **Jetson에서** 한다.

## 확인된 스택 (JetPack 6.2.2)
| 구성 | 버전 |
|------|------|
| L4T (Jetson Linux) | 36.5.0 (R36 Rev 5.0) |
| Ubuntu | 22.04 |
| Python | 3.10 |
| CUDA | 12.6 |
| cuDNN | 9.3 |
| TensorRT | 10.3 |
| 보드 | Jetson Orin Nano Developer Kit (Super) |

## 0. 저장소 클론 (Jetson에 repo 없을 때 — 최초 1회)
프로젝트는 dev 머신에서 이미 만들어 GitHub(private)에 있음. Jetson에선 **새로 만들지 말고 clone**한다.
```bash
git --version || sudo apt-get install -y git      # JetPack에 보통 있음
```
GitHub private 저장소라 인증 필요. 방법 택1:
```bash
# (A) SSH 키 — 권장. Jetson에서 키 생성 후 공개키를 GitHub에 등록.
ssh-keygen -t ed25519 -C "jetson-orin"            # 엔터로 기본 경로
cat ~/.ssh/id_ed25519.pub                         # 출력 → GitHub Settings > SSH keys 에 추가
git clone git@github.com:foody-j/customfood-soup-automation.git ~/customfood-soup-automation

# (B) HTTPS + Personal Access Token (SSH 안 쓸 때)
# git clone https://github.com/foody-j/customfood-soup-automation.git ~/customfood-soup-automation
```
```bash
cd ~/customfood-soup-automation
git log --oneline -3                              # 최신 커밋 보이면 성공
```
> 이후 업데이트는 `git pull`. Jetson에서 생성한 데이터(벤치 결과, 노트 갱신 등)는 **Jetson에서 직접
> 커밋/푸시해도 된다** (2026-07-13 정책 변경, `notes/decisions.md` 참고). 단 **작업 전 `git pull` 먼저** —
> dev 머신과 히스토리가 갈리지 않게 한다. 무인(headless) 에이전트의 push 금지 규칙은 그대로 유지.

## 1. 버전·상태 확인
```bash
sudo jetson_release        # JetPack/CUDA/TensorRT/cuDNN 한눈에
```

## 2. 모니터링 도구 (jtop) — 벤치마크에 필수
```bash
sudo pip3 install -U jetson-stats
sudo systemctl restart jtop.service   # 최초 설치 후
jtop                                   # 전력·온도·GPU 사용률 실시간
```

## 3. 전력 모드 최대로 (Super = MAXN SUPER)
```bash
grep POWER_MODEL /etc/nvpmodel.conf   # 모드 ID↔이름 목록 (sudo 불필요)
# 이 보드(JetPack 6.2.2) 실측: ID 0=15W, 1=25W, 2=MAXN_SUPER ← "보통 0" 아님!
sudo nvpmodel -m 2               # MAXN_SUPER
sudo jetson_clocks               # 클럭 최대 고정 (벤치 시 권장)
nvpmodel -q                      # 적용 확인 (MAXN_SUPER 표시돼야 함)
```
> 벤치마크 수치는 전력모드에 따라 크게 달라진다. **측정 전 반드시 최대 모드 + jetson_clocks**.

## 4. Python 가상환경
시스템 파이썬 오염 방지. `--system-site-packages`로 **시스템 TensorRT(파이썬 바인딩)에 접근**.
```bash
python3 -m venv ~/cf-venv --system-site-packages
source ~/cf-venv/bin/activate
pip install -U pip
```
> `ensurepip is not available` 에러가 나면(`python3.10-venv` 미설치) sudo 없이 우회 가능:
> ```bash
> python3 -m venv --without-pip --system-site-packages ~/cf-venv
> curl -sSL https://bootstrap.pypa.io/get-pip.py | ~/cf-venv/bin/python3
> ```

## 5. PyTorch / torchvision (Jetson 전용 wheel)
⚠️ **pytorch.org 일반 wheel은 x86이라 안 됨.** JetPack 6(cu126) 전용 인덱스 사용:
```bash
# ⚠️ 구 도메인 pypi.jetson-ai-lab.dev 는 DNS가 죽어 있음(2026-07 확인) → .io 사용
pip install --index-url https://pypi.jetson-ai-lab.io/jp6/cu126 torch torchvision
```
torch 2.11(jp6/cu126) 기준 추가 의존성 (2026-07-13 실측):
```bash
pip install nvidia-cudss-cu12    # torch import 시 libcudss.so.0 요구
pip install onnx numpy onnxscript  # onnxscript: torch 2.11 ONNX export(dynamo)가 요구
```
`libcudss.so.0` 은 pip 설치만으로는 로더가 못 찾으므로 activate 스크립트 끝에 추가:
```bash
echo 'export LD_LIBRARY_PATH="$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cu12/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"' >> ~/cf-venv/bin/activate
```

## 6. 검증
```bash
python3 -c "import torch, torchvision; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
# 기대: torch 2.x  cuda True
```
`cuda True`가 안 나오면 → wheel 인덱스/JetPack 버전 불일치. §트러블슈팅 참고.

## 7. trtexec 확인 (벤치가 사용)
TensorRT에 포함된 CLI. 벤치 하네스가 이걸로 엔진 빌드+타이밍을 한다.
```bash
/usr/src/tensorrt/bin/trtexec --help | head    # 존재 확인
# PATH에 없으면 벤치에 --trtexec /usr/src/tensorrt/bin/trtexec 로 넘김
```

## 8. 벤치마크 실행
```bash
source ~/cf-venv/bin/activate
cd ~/customfood-soup-automation
python3 jetson/bench/benchmark.py          # 결과 → notes/data/bench/summary.md
```
자세한 사용법: `jetson/bench/README.md`.

---

## 트러블슈팅
| 증상 | 원인 / 조치 |
|------|------|
| `torch.cuda.is_available()` == False | wheel이 JetPack 버전과 불일치. jp6/cu126 인덱스 재확인, 기존 torch 제거 후 재설치 |
| `pypi.jetson-ai-lab.dev` 접속 불가 | 구 도메인 DNS 사망 → `pypi.jetson-ai-lab.io` 사용 |
| `ImportError: libcudss.so.0` | `pip install nvidia-cudss-cu12` + LD_LIBRARY_PATH에 `site-packages/nvidia/cu12/lib` 추가 (§5) |
| export 시 `No module named 'onnxscript'` | torch 2.11부터 ONNX export가 dynamo 경로 → `pip install onnxscript` |
| ONNX export 중 프로세스가 조용히 죽음 | OOM 추정(8GB) → `--models`로 모델을 나눠 실행 |
| `import tensorrt` 실패 | venv를 `--system-site-packages`로 안 만듦 → 재생성 |
| 벤치 중 클럭/전력 요동 | `sudo jetson_clocks` 먼저, jtop로 throttling(온도) 확인 |
| trtexec not found | `/usr/src/tensorrt/bin/trtexec` 절대경로 사용 |
| 발열로 스로틀링 | 방열판/팬 확인, jtop 온도 모니터링 |

## 참고
- PyTorch for Jetson (jetson-ai-lab 인덱스) — https://pypi.jetson-ai-lab.io/jp6/cu126
- NVIDIA JetPack 6.2 릴리즈노트 — https://docs.nvidia.com/jetson/archives/jetpack-archived/jetpack-62/release-notes/
