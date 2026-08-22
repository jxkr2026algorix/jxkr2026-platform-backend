# jxkr2026-platform-backend

경북 재난대피 플랫폼 **SALGIL** 의 백엔드. 프론트엔드가 붙고, 공공데이터를 읽고, 자체 예측
모델을 부른다.

```
  프론트엔드 (console · mobile PWA · WebGPU map)
        │  REST /api/v1
        ▼
  ┌──────────────────────────────────┐
  │  이 서비스 — 운영 상태를 소유       │   AWS EC2 · docker compose
  │  상황 · 계획 · 승인 · 연락 · 임무 · 보고 │
  └──┬──────────────┬────────────────┘
     │              │
  Postgres     ┌────┴───────────────────┐
               │                        │
       GB SafeData (읽기 전용)      ML 추론 서버
       공공데이터 + 출처            Triton · H100 (Lambda)
```

**공공데이터를 저장하지 않는다.** 관측·특보·대피소 원본은 GB SafeData 가 소유하고, 여기서는
조회해 출처를 붙인 채로 전달한다. 이 서비스가 소유하는 것은 **사람이 내린 결정과 그 이력**이다 —
GB SafeData 에는 없는, 있어서도 안 되는 기능이다.

---

## 30초 실행

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
curl http://127.0.0.1:8000/readyz
open http://127.0.0.1:8000/docs
```

마이그레이션과 데모 시드(마을 4곳·대피소 3곳)가 기동 시 적용된다. `.env` 는 없어도 된다.
5432 를 다른 프로젝트가 쓰고 있으면 `POSTGRES_PORT=55432` 를 준다.

## 배포

기본 compose 는 개발용 오버레이 없이 뜬다. **다만 그대로 나가면 안 되는 값들이 있어,
`SALGIL_ENV=production` 이면 그 상태로는 기동을 거절한다.**

```
RuntimeError: SALGIL_ENV=production 인데 안전하지 않은 기본값이 있습니다:
  - SALGIL_API_KEYS 에 저장소에 적힌 개발용 키가 들어 있습니다 …
  - SALGIL_DATABASE_URL 의 비밀번호가 기본값입니다 …
  - SALGIL_CORS_ORIGINS 에 localhost 가 남아 있습니다 …
```

경고만 남기면 로그에 묻히고, 묻힌 채로 배포된다. 최소 설정은 이렇다.

```bash
cat > .env <<EOF
SALGIL_ENV=production
POSTGRES_PASSWORD=$(openssl rand -hex 16)
SALGIL_DATABASE_URL=postgresql+asyncpg://salgil:<위와 같은 값>@db:5432/salgil
SALGIL_API_KEYS=$(openssl rand -hex 16):operator,$(openssl rand -hex 16):approver,$(openssl rand -hex 16):field
SALGIL_CORS_ORIGINS=https://console.salgil.gyeongbuk.kr
SALGIL_ROAD_NETWORK_BBOX=129.10,36.36,129.22,36.44
EOF

docker compose up -d --build
```

| 기본값 | 개발 | 운영 |
| --- | --- | --- |
| Postgres 포트 | dev 오버레이에서만 노출 | 노출 안 함 |
| 데모 시드 | 켜짐 | **꺼짐** — 운영 DB 에 가짜 마을이 들어가면 안 된다 |
| API 키 | 저장소에 적힌 dev 키 | **직접 생성** (안 하면 기동 거절) |
| ML 서버 | stub | `SALGIL_MLENGINE_*` 설정 |

TLS 는 앞단 리버스 프록시가 맡는다. 이 서비스는 8000 에서 평문 HTTP 로 뜬다.

로컬에서 직접 돌리려면:

```bash
uv sync --extra dev
docker compose up -d db
uv run alembic upgrade head
uv run salgil seed
uv run uvicorn app.main:app --reload
```

---

## ML 추론 서버 붙이기

ML 백엔드는 별도 레포(`jxkr2026-mlengine/serving`)이고 Lambda H100 위에서 Triton 앞에 선다.
이 백엔드는 URL 과 토큰만 알면 된다. `.env` 또는 compose 환경변수 셋이 전부다.

```bash
SALGIL_MLENGINE_MODE=http
SALGIL_MLENGINE_BASE_URL=http://ml.internal:8900     # Lambda 인스턴스 주소
SALGIL_MLENGINE_API_KEY=<ML 서버와 같은 토큰>
```

`SALGIL_MLENGINE_MODE=stub` (기본값)이면 ML 서버 없이도 전체 API 가 동작한다. 스텁 응답은
결정론적이고 `is_stub=true` 를 달고 나가므로 화면에서 진짜 예측과 구분된다.

연결 확인은 `/readyz` 하나면 된다.

```bash
curl -s localhost:8000/readyz | jq '.components[] | select(.name=="mlengine")'
```

`ok: false` 에 "SALGIL_MLENGINE_API_KEY 를 확인하세요"가 나오면 토큰이 다른 것이다.
이 점검은 ML 서버의 **인증이 걸린** `/v1/ping` 을 부른다 — 인증 없는 `/readyz` 를 찌르면
토큰을 빠뜨려도 '준비됨'으로 보고되고, 첫 추론 요청에서야 403 을 만난다.

---

## 화면이 반드시 지켜야 하는 것 — 3상태

관측 응답에는 `state` 가 실린다. **2상태(있음/없음)로 그리면 장애가 초록 타일로 보인다.**

| `state` | 뜻 | 화면 |
| --- | --- | --- |
| `DATA` | 값이 있다 | 표시 |
| `NONE` | 조회 성공 + 부재 확인 | "발효 중 없음" |
| `UNVERIFIED` | 확인 불가 | **안심시키는 색 금지** |

```ts
// 프론트엔드는 이 값만 보면 된다. 계산은 백엔드가 이미 했다.
const tone = res.state === "DATA" ? "active"
           : res.state === "NONE" ? "safe"
           : "unverified";   // 초록 금지
```

빈 `records` 를 '위험 없음'으로 읽는 것이 이 프로젝트가 막으려는 사고다. 산사태 조회가 403 으로
실패해도 기상 데이터는 오기 때문에, 화면은 아무 문제 없어 보인다.

---

## 데이터 출처가 셋이다 — 섞이면 안 된다

| 출처 | 표시 | 성격 |
| --- | --- | --- |
| GB SafeData | `envelope.records[].source` | 공공데이터. 기관·라이선스·관측시각이 레코드마다 붙는다 |
| 자체 예측 모델 | `is_derived: true` | **어느 기관도 보증하지 않는다.** 공식 위험등급이 아니다 |
| 운영 상태 | `data_mode` | 이 서비스가 소유. 주민 정보는 `synthetic` |

주민 연락처·대피 확인·현장 통제는 공개 데이터로 얻을 수 없다. 데모 데이터는 전부
`data_mode="synthetic"` 이고, 화면에서 실데이터와 섞이면 안 된다.

---

## API

전체 스펙은 [`docs/api-spec.md`](docs/api-spec.md), 대화형 문서는 `/docs`.

| 묶음 | 하는 일 |
| --- | --- |
| `/api/v1/meta` | 지역 22개, 재난 13종 가용성, 데이터셋 라이선스 판정 |
| `/api/v1/situation` | 관측 조회 — 봉투를 줄이지 않고 전달 |
| `/api/v1/predictions` | 자체 모델 추론 (Triton) 과 실행 이력 |
| `/api/v1/incidents` | 상황 개시·변경·타임라인 |
| `/api/v1/incidents/{id}/plans`, `/api/v1/plans` | 대피계획 기안·승인·개정 |
| `/api/v1/incidents/{id}/contacts` | 주민 연락 명단과 결과 |
| `/api/v1/incidents/{id}/tasks`, `/api/v1/tasks` | 현장 임무와 보고 |
| `/api/v1/communities`, `/api/v1/shelters` | 마을·대피소 (대피소 조회는 `hazard` 필수) |
| `/api/v1/routing` | 위험 구역을 피해 대피소로 가는 경로 (OSM, 이동수단별) |
| `/api/v1/assistant` | 챗봇용 도구 정의·시스템 프롬프트 중계 |
| `/api/v1/public` | 주민 화면용 요약 (인증 불필요, 개인정보 없음) |

### 운영 루프가 절차를 강제한다

```
상황 개시 ──▶ 계획 기안 ──승인──▶ 주민 연락 ──▶ 현장 임무 ──보고──▶ 재승인 필요 ──▶ 개정
             (operator)  (approver)  (승인 후에만)   (field)      (계획이 되돌아간다)
```

- 승인되지 않은 계획으로 연락을 개시하면 **409** 를 돌려준다
- 현장 보고에 접근 제약이 실리면 승인된 계획이 `reapproval_required` 로 바뀐다
- 승인·연락개시·보고접수는 전부 `audit_events` 에 남고 `/timeline` 이 그대로 읽는다

---

## 대피 경로

OSM 도로망 위에서 이동수단별로 경로를 낸다. 산불처럼 **퍼지는 재난은 시간에 따라 커지는
위험**을 반영한다 — 각 지점을 지나는 시각의 위험으로 판단하므로, 같은 요청이라도 출발이
늦으면 경로가 달라진다.

### 도로망을 넣는 세 가지 방법

| 방법 | 이미지 | 기동 | 언제 |
| --- | --- | --- | --- |
| **① 첫 기동에 받기** (기본) | 깨끗 | 첫 기동만 네트워크 | 대부분의 경우 |
| ② 미리 만든 파일 마운트 | 깨끗 | 네트워크 불필요 | 폐쇄망, 큰 범위 |
| ③ 이미지에 굽기 | **ODbL 대상** | 즉시 | 자체 레지스트리, 재현성 |

```bash
# ① bbox 만 주면 첫 기동에 받아 볼륨에 남는다. 재기동 시 다시 받지 않는다.
SALGIL_ROAD_NETWORK_BBOX=129.10,36.36,129.22,36.44 docker compose up -d

# ② 미리 만들어 붙인다
uv run python scripts/build_road_network.py \
    --bbox 129.10,36.36,129.22,36.44 --output data/local/roads.geojson
ROAD_NETWORK_DIR=./data/local docker compose up -d

# ③ 이미지에 굽는다 — 마운트도 볼륨도 없이 바로 돈다
docker build --build-arg ROAD_NETWORK_BBOX=129.10,36.36,129.22,36.44 -t salgil/backend .
```

**③을 고르면 이미지가 ODbL 파생물이 된다.** 레지스트리에 올리는 순간 배포이므로 출처 표시와
파생 데이터베이스 제공 의무가 이미지에 따라붙는다. 그 사실이 이미지에 남도록
`NOTICE-OSM.txt` 와 `org.opencontainers.image.licenses=Apache-2.0 AND ODbL-1.0` 라벨이 붙는다.
**KOGL 정부 데이터를 같은 배포물에 병합하면 share-alike 가 정부 데이터에까지 얹힌다.**

경로는 하나다(`/data/road/roads.geojson`). 볼륨을 붙이면 구운 파일을 덮는다 — 마운트가
이기는 것이 기대되는 우선순위다.

도로망이 없으면 경로 계산만 **거절되고** 나머지(상황·계획·연락·현장)는 정상 동작한다.
도로망 없이 경로를 내면 지도 위에 그럴듯한 직선이 그려질 뿐이다.

현장 보고에 통제 구간이 실리면 그 지점이 차단으로 들어간다 — 예측은 확률이지만 현장 보고는
사람이 가서 본 것이라 더 강하다.

**여기서 나오는 경로는 제안이지 공식 안전경로가 아니다.** OSM 파생물이므로 응답의
`attribution` 을 지우면 안 된다.

---

## 인증

정적 API 키. 헤더는 `Authorization: Bearer <key>` 또는 `X-API-Key`.

```bash
SALGIL_API_KEYS=ops-key:operator,chief-key:approver,field-key:field
```

| 역할 | 할 수 있는 것 |
| --- | --- |
| `field` | 임무 상태 변경, 현장 보고 제출 |
| `operator` | + 상황 개시, 계획 기안, 연락 기록, 예측 실행 |
| `approver` | + **계획 승인** |
| `admin` | 전부 |

감사 이력에 담당자 이름을 남기려면 `X-Actor: 홍길동` 헤더를 같이 보낸다. 없으면 역할과 키
앞자리만 남아 누가 승인했는지 알 수 없다.

---

## 개발

```bash
uv run pytest -q            # 테스트 (상류 호출은 전부 목)
uv run ruff check .         # 린트
uv run ruff format .        # 포맷
uv run salgil check         # 상류 연결 점검
uv run salgil routes        # 라우트 목록
uv run alembic revision --autogenerate -m "..."
```

## 연관 레포

| 레포 | 관계 |
| --- | --- |
| [jxkr2026-platform-frontend](https://github.com/jxkr2026algorix/jxkr2026-platform-frontend) | 이 API 를 소비한다 |
| [jxkr2026-gbsafedata](https://github.com/jxkr2026algorix/jxkr2026-gbsafedata) | 공공데이터 상류 (읽기 전용) |
| [jxkr2026-datasets](https://github.com/jxkr2026algorix/jxkr2026-datasets) | 데이터 취득·검증 계층. 가용성의 단일 출처 |
| [jxkr2026-mlengine](https://github.com/jxkr2026algorix/jxkr2026-mlengine) | 학습 엔진 + `serving/` 추론 서버 |
