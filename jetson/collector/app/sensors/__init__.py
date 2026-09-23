"""센서 어댑터.

- `base.py`  인터페이스와 샘플 자료형
- `mock.py`  모의 센서 — 항상 `simulated=True`
- `v4l2.py`  ISX031F GMSL2(FG12-4CH) — V4L2 ioctl 직접 사용
- `i2cmux.py` I²C 버스·TCA9548A 공유와 버스별 잠금
- `polled.py` 저속 폴링 센서 공통(주기·실패 기록·재연결 기준)
- `thermal_mlx90640.py` · `rtd_max31865.py` I²C/SPI 센서의 실물 어댑터
- `orbbec.py` Orbbec Gemini 2 — pyorbbecsdk v2 (color/depth/ir)
- `registry.py` 설정에 따라 어댑터 목록을 조립
"""
