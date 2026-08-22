# CLAUDE.md

## 명령

```bash
uv sync --extra dev
docker compose up -d db          # POSTGRES_PORT 로 포트 변경 가능
uv run alembic upgrade head
uv run salgil seed
uv run uvicorn app.main:app --reload

uv run pytest -q                 # 상류는 전부 목이다 — 네트워크를 타지 않는다
uv run ruff check . && uv run ruff format .
uv run alembic check             # 모델과 마이그레이션이 갈라졌는지
uv run salgil check              # 상류 연결 점검 (실제 호출)
```

## 이 저장소가 지키는 선

바꾸기 전에 이유를 먼저 읽어라. 전부 다른 레포에서 실제로 사고가 났던 것들이다.

1. **`except: return []` 금지.** 상류 실패는 `UpstreamError` 로 올린다. 조회 실패를 빈 결과로
   바꾸면 화면에서 '위험 없음'이 된다.
2. **봉투를 줄이지 않는다.** `records` 만 꺼내면 `complete`·`absence_confirmed`·`freshness`·
   `source` 가 사라진다. `Envelope` 는 `extra="allow"` 라 상류가 필드를 늘려도 보존된다.
3. **3상태.** `DataState` 는 `DATA`/`NONE`/`UNVERIFIED` 다. 2상태로 줄이면 장애가 초록이 된다.
4. **자체 모델은 자체 모델이라고 쓴다.** 예측 응답의 `is_derived`·`derived_notice` 는 선택이 아니다.
5. **대피소 조회는 `hazard` 필수.** 지진 대피소와 호우 대피소는 다른 시설이다.
6. **합성 데이터는 `data_mode="synthetic"`.** 주민 정보는 공개 데이터로 얻을 수 없다.
7. **승인은 approver 만.** 절차 위반은 문서가 아니라 409 로 막는다.
8. **커밋은 응답 전에.** `TransactionalRoute` 가 그 일을 한다 — yield 의존성의 정리 코드는
   응답을 보낸 뒤에 돌아서, 거기서 커밋하면 read-after-write 가 깨진다.

## 구조

```
app/
  core/      설정·로깅·오류·캐시·인증
  db/        모델 (운영 상태만. 공공데이터는 저장하지 않는다)
  schemas/   pydantic. common.py 의 Envelope 가 상류 계약이다
  clients/   gbsafe(읽기 전용 상류) · mlengine(Triton 게이트웨이, stub 모드 있음)
  services/  절차. 라우터는 얇게 두고 규칙은 여기 둔다
  api/v1/    라우터. route_class=TransactionalRoute 를 반드시 붙인다
```

## 연관 레포

- `../jxkr2026-mlengine` — 학습 엔진 + `serving/` 추론 서버. 예측 계약은 양쪽이 공유한다
  (`app/schemas/prediction.py` ↔ `serving/src/jxkr_serving/schemas.py`). 한쪽만 바꾸면 조용히 깨진다.
- GB SafeData — `https://datainfra.salgil.gyeongbuk.kr` (배포됨, 읽기 전용)
