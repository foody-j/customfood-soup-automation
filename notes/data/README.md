# 수집 데이터 정리 규칙

과제용으로 수집하는 데이터를 여기에 정리한다.

## 디렉토리 구조

```
notes/data/
├── README.md          # 이 문서
├── raw/               # 원시 데이터 (영상/이미지/대용량) — git에 커밋 안 함(.gitignore)
├── logs/              # 온도·상태 로그 (CSV/JSON) — 소용량이면 커밋 가능
└── experiments/       # 실험 기록 (조건별 결과, 표/요약)
```

## 규칙

- **파일명:** `YYYYMMDD_<레시피>_<설명>.<ext>` 예: `20260710_kimchi_jjigae_temp-log.csv`
- **원시 데이터(raw/)** 는 용량이 커서 git에 올리지 않는다. 위치·수집일·조건을
  `experiments/`에 메모로 남긴다.
- **로그(logs/)** 는 가급적 CSV로 저장하고, 컬럼은 `shared/schema.json` 필드명과 맞춘다
  (`timestamp, stage, temperature_c, ...`).
- **실험(experiments/)** 은 각 실험마다 `YYYYMMDD_<제목>.md`로 조건/결과/관찰을 기록.
