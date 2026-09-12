"""센서 어댑터.

- `base.py`  인터페이스와 샘플 자료형
- `mock.py`  모의 센서 — 항상 `simulated=True`
- `v4l2.py`  ISX031F GMSL2(FG12-4CH) — V4L2 ioctl 직접 사용
- `unsupported.py` 실물·SDK가 없어 붙이지 못한 센서를 **정직하게** 미지원으로 보고
- `registry.py` 설정에 따라 어댑터 목록을 조립
"""
