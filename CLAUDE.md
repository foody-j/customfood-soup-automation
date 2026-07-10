# CLAUDE.md — 작업 규칙 (에이전트·기여자 필독)

단국대학교 커스텀푸드 국/탕 조리 자동화 **과제** 저장소. 아래 규칙을 지켜서 작업할 것.

## ⚠️ 과제 관리 규칙 (가장 중요)

이 프로젝트는 학과 과제이므로 **개발 노트와 데이터를 반드시 정리·기록**해야 한다.

1. **개발 노트** — 의미 있는 작업(기능 추가, 실험, 문제 해결)을 할 때마다
   `notes/dev-log.md`에 **날짜와 함께** 한 항목씩 기록한다.
2. **의사결정 기록** — 기술 선택(스택, 통신 방식 등)이나 설계 결정은
   `notes/decisions.md`에 "결정 / 이유 / 대안" 형식으로 남긴다.
3. **데이터 정리** — 수집한 데이터(온도 로그, 조리 영상/이미지, 실험 결과)는
   `notes/data/` 아래에 규칙(`notes/data/README.md`)에 따라 정리한다.
   대용량 원시 데이터는 git에 커밋하지 않는다(`notes/data/raw/`는 gitignore됨).
4. 작업을 마칠 때 관련 노트를 갱신했는지 확인한다. **코드만 바꾸고 노트를 빼먹지 말 것.**

## 개발 환경 (중요)

Node 20 / npm / git이 **사용자 영역(micromamba env `dev`)** 에 설치되어 있다.
시스템 apt에는 없다. 명령 실행 전 PATH를 등록해야 한다:

```bash
export PATH="$HOME/.local/mamba/envs/dev/bin:$PATH"
```

## 아키텍처 요약

- Jetson Orin Nano = AI 추론 → MQTT **발행자**
- Raspberry Pi 5 = Mosquitto 브로커 + 대시보드(구독자)
- 두 보드의 계약은 `docs/data-schema.md` / `shared/schema.json`. **계약을 바꾸면 두 문서와
  `dashboard/src/data/schema.js`를 함께 갱신**한다.

## 대시보드 규칙

- 데이터 소스는 `dashboard/src/data/useCookingData.js` 한 곳에서만 갈아끼운다(mock ↔ mqtt).
  UI 컴포넌트는 데이터 출처를 몰라야 한다.
- 조리 단계·끓음 상태·경고 코드는 `dashboard/src/data/schema.js`의 상수를 쓴다(문자열 하드코딩 금지).

## 커밋

- 의미 단위로 자주 커밋한다. 커밋 메시지는 한국어로 명확하게.
