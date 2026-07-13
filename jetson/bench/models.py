"""후보 모델 정의 + ONNX export.

이 과제의 방법론 조사(docs/research-methodology*.md)에서 나온 후보 백본을 Jetson에서
벤치마크하기 위한 것. **정확도가 아니라 성능(지연·FPS·전력·메모리)만** 측정하므로
가중치는 랜덤 초기화면 충분하다.

- 도네스 분류: MobileNetV3-Small, EfficientNet-B0(≈lite0)  — 입력 3×224×224, 3클래스(미완/완료/과조리)
- 열화상 브랜치: 작은 2D-CNN(1×24×32), 그리고 2D-CNN+1D TCN 시퀀스 버전(조사 권고 구조)
- (TSM은 향후 항목 — torchvision 기본 미포함이라 여기선 제외)
"""
import torch
import torch.nn as nn


# ── 도네스 분류 백본 ─────────────────────────────────────────────────────────
def build_mobilenet_v3_small(num_classes=3):
    from torchvision import models
    m = models.mobilenet_v3_small(weights=None)
    m.classifier[-1] = nn.Linear(m.classifier[-1].in_features, num_classes)
    return m


def build_efficientnet_b0(num_classes=3):
    from torchvision import models
    m = models.efficientnet_b0(weights=None)  # efficientnet-lite0에 가장 근접한 기본 제공 모델
    m.classifier[-1] = nn.Linear(m.classifier[-1].in_features, num_classes)
    return m


# ── 열화상 브랜치 (MLX90640 32×24) ───────────────────────────────────────────
class ThermalCNN(nn.Module):
    """단일 프레임 2D-CNN. 입력 (B,1,24,32) → num_classes."""
    def __init__(self, num_classes=3, emb=64):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1), nn.BatchNorm2d(16), nn.ReLU(inplace=True),
            nn.Conv2d(16, 32, 3, padding=1, stride=2), nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            nn.Conv2d(32, emb, 3, padding=1, stride=2), nn.BatchNorm2d(emb), nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d(1),
        )
        self.head = nn.Linear(emb, num_classes)

    def forward(self, x):
        z = self.features(x).flatten(1)
        return self.head(z)


class ThermalSeqTCN(nn.Module):
    """2D-CNN(프레임별) + 1D TCN(시간축). 조사 권고 구조(3D CNN 회피).
    입력 (B,T,1,24,32) → num_classes.  (B=1, T 고정 export)
    """
    def __init__(self, num_classes=3, emb=64, t=16):
        super().__init__()
        self.t = t
        self.frame = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1), nn.BatchNorm2d(16), nn.ReLU(inplace=True),
            nn.Conv2d(16, 32, 3, padding=1, stride=2), nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            nn.Conv2d(32, emb, 3, padding=1, stride=2), nn.BatchNorm2d(emb), nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d(1),
        )
        self.tcn = nn.Sequential(
            nn.Conv1d(emb, emb, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv1d(emb, emb, 3, padding=2, dilation=2), nn.ReLU(inplace=True),
        )
        self.head = nn.Linear(emb, num_classes)

    def forward(self, x):
        b, t = x.shape[0], x.shape[1]
        x = x.reshape(b * t, 1, x.shape[3], x.shape[4])  # (B*T,1,24,32)
        z = self.frame(x).flatten(1)                     # (B*T, emb)
        z = z.reshape(b, t, -1).transpose(1, 2)          # (B, emb, T)
        z = self.tcn(z).mean(dim=2)                      # (B, emb)
        return self.head(z)


# ── 레지스트리: 이름 → (빌더, 더미 입력 shape) ───────────────────────────────
def model_specs(num_classes=3, img=224, tcn_frames=16):
    return {
        "mobilenet_v3_small": (lambda: build_mobilenet_v3_small(num_classes), (1, 3, img, img)),
        "efficientnet_b0":    (lambda: build_efficientnet_b0(num_classes),    (1, 3, img, img)),
        "thermal_cnn":        (lambda: ThermalCNN(num_classes),               (1, 1, 24, 32)),
        "thermal_seq_tcn":    (lambda: ThermalSeqTCN(num_classes, t=tcn_frames), (1, tcn_frames, 1, 24, 32)),
    }


def export_onnx(model, dummy_shape, path, opset=17):
    """model을 eval 모드로 ONNX export. 랜덤 가중치(성능 측정용)."""
    model = model.eval()
    dummy = torch.randn(*dummy_shape)
    torch.onnx.export(
        model, dummy, path,
        input_names=["input"], output_names=["output"],
        opset_version=opset, do_constant_folding=True,
    )
    return path
