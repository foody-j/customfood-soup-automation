# 2026-09-12 — ISX031F V4L2 직접 캡처 점검 (GMSL 링크 미확립 상태)

**목적**: 수집 서비스의 V4L2 ioctl 바인딩(`jetson/collector/app/sensors/v4l2dev.py`)이 실기기 노드에서
동작하는지, 그리고 링크가 없을 때 드라이버가 무엇을 내놓는지 확인. (카메라 보드 전원/케이블 미연결 상태.)

**도구**: `jetson/collector/tools/v4l2_check.py --device /dev/video4 --frames 30 --timeout 2`
**원시 결과**: `notes/data/logs/20260912_isx031f_v4l2-check_link-down.json`

| 항목 | 값 |
|---|---|
| fzcam_cfg 링크 | Link satus:0-0-0-0 |
| 컨트롤(v4l2-ctl) | exposure=30, gain=0, frame_rate=30000000 |
| 적용 포맷 | 1920x1536 UYVY, sizeimage=5898240, driver_fps=30.0 |
| 받은 프레임 | 8개 / 7.106s (타임아웃 3회) |
| sequence | 0 → 0 (링크 없음 → 순번이 오르지 않음) |
| 빈(전부 0) 프레임 | 8 / 8 |
| 타임스탬프 시계 | ['host_monotonic'] (값 0 — 링크 없을 때 무의미) |

**관찰**
- S_FMT/REQBUFS/QUERYBUF/mmap/STREAMON/DQBUF 경로가 실기기 드라이버(tegra-video, fzcam)에서 오류 없이 돈다.
- 링크가 없으면 드라이버는 **전부 0인 프레임을 sequence=0, timestamp=0으로** 간헐적으로 내놓는다(2초 타임아웃 사이사이).
  OpenCV 백엔드는 이를 `ok=True`로 100fps 반환했었다(dev-log 참고). → 어댑터는 `blank_frame`으로 `valid=false` 처리.
- **실제 FPS·CPU·JPEG 인코딩·기록 속도 실측은 링크 확립 후** 같은 도구로 `--jpeg` 옵션을 붙여 다시 잰다(플랜 7단계).
