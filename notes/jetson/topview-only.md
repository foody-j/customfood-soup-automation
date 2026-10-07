# jetson/topview-only — GMSL 카메라를 top view 1대로 (2026-10-07)

- 사용자 결정: 비스듬한(측면 경사) 카메라(GMSL 포트 1, `cam_rgb_1`)는 **보류**, top view(포트 0, `cam_rgb_0`)만 사용.
  근거: 10/07 재연결에서 top view는 100° 광각으로 솥 전체가 수직에 가깝게 들어온다(ROI 실측은 `notes/jetson/cook-ready.md`).
- 변경: 운영 env `COLLECTOR_V4L2_DEVICES=gmsl:0,gmsl:1` → `gmsl:0`. 코드 변경 없음.
  → `/status` 센서 목록에서 `cam_rgb_1`이 빠져 Pi 시작 전 점검이 '센서 누락'으로 막지 않는다. 다시 쓰려면 `gmsl:0,gmsl:1`.
- 영향: GMSL 저장량이 한 대분 줄어든다(9/27 실측 GMSL 장당 130~880 KB, 장면 의존). 정확한 값은 cook-ready 저장량 실측에서.
- **Pi 인계(필수):** Pi 프리셋 `pi-server/app/static/app.js` `TRIAL_SENSORS = ['cam_rgb_0', 'cam_rgb_1', 'thermal_0', 'pt100_0']`로
  시작하면 Jetson이 `accepted:false, "알 수 없는 센서: cam_rgb_1"`로 **녹화 시작을 거절**한다(env 변경 전에도 미연결이면 '센서 미연결'로 거절).
  Pi 담당이 `TRIAL_SENSORS`, `config.py` 기본 미리보기 목록(`gmsl2_2`), `soup-pi-server.env` 주석에서 `cam_rgb_1` 제거.
  과거 세션 이력 표시는 유지. 그 전까지는 Pi 실험 설정의 활성 센서를 `cam_rgb_0, thermal_0, pt100_0`으로 저장해 우회.
- 적용: main 병합 → `sudo cp jetson/collector/systemd/jetson-collector.env /etc/default/jetson-collector` →
  진행 중 세션 없을 때 `sudo systemctl restart jetson-collector`(사람).
