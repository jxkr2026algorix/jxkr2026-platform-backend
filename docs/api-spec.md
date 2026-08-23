# API 스펙

버전 `v1` · 기준 경로 `/api/v1` · 대화형 문서 `/docs` · 기계 판독 `/openapi.json`

이 문서는 **왜 이렇게 갈랐는가**를 설명한다. 필드 단위 상세는 `/openapi.json` 이 단일 출처다.

---

## 0. 설계 원칙 넷

### 1) 이 백엔드가 소유하는 것은 '결정'이지 '데이터'가 아니다

관측·특보·대피소 원본은 GB SafeData 가 소유한다. 여기서 복제해 저장하면 두 개의 진실이 생기고,
어느 쪽이 최신인지 아무도 모르게 된다. 그래서 `/situation/*` 은 **프록시**다 — 조회해서
봉투를 그대로 넘긴다.

반대로 GB SafeData 에는 **없고 있어서도 안 되는 것**이 있다. 그 서버는 스스로 이렇게 말한다.

> 이 API는 조회만 제공합니다. 전화·대피명령·상태변경 기능이 없습니다.

계획을 세우고, 승인하고, 주민에게 연락한 결과를 기록하는 일이 이 백엔드의 몫이다.

### 2) 출처가 다른 값은 응답에서도 갈라져 있어야 한다

한 화면에 세 종류가 같이 놓인다.

| 출처 | 응답에서의 표시 | 뜻 |
| --- | --- | --- |
| 공공데이터 | `envelope.records[].source` | 기관·라이선스·관측시각이 붙는다 |
| 자체 예측 모델 | `is_derived: true` + `derived_notice` | 어느 기관도 보증하지 않는다 |
| 운영 상태 | `data_mode: real \| synthetic` | 사람이 만든 기록. 데모값은 synthetic |

이 표시를 응답에서 지우면 화면이 구분할 방법이 없어진다. **그래서 선택 필드가 아니다.**

### 3) 절차는 HTTP 상태코드로 강제한다

문서에 "승인 후에 연락하세요"라고 적는 것으로는 부족하다. 승인되지 않은 계획으로 연락을
개시하면 **409** 가 나간다.

### 4) 실패는 실패로 남는다

상류가 403 이면 `502` 를 돌려주거나, 봉투 안에 `receipts[].outcome="failed"` 로 남긴다.
빈 배열로 바꾸지 않는다. 조회 실패와 '해당 없음'이 같은 모양이 되는 순간, 장애가 안전으로 읽힌다.

---

## 1. 공통 규약

### 인증

```http
Authorization: Bearer <api-key>
X-Actor: %EA%B9%80%EA%B3%BC%EC%9E%A5      # encodeURIComponent("김과장")
```

`X-Actor` 는 감사 이력에 남을 사람 이름이다. **한글은 퍼센트 인코딩해야 한다** — HTTP 헤더
값은 latin-1 이라 한글을 그대로 넣으면 클라이언트가 인코딩 오류를 낸다.

역할 위계: `field` < `operator` < `approver` < `admin`.

`/api/v1/public/*` 만 인증이 필요 없다.

### 오류

`application/problem+json` (RFC 9457).

```json
{
  "type": "https://salgil.gyeongbuk.kr/problems/workflow-conflict",
  "title": "지금 수행할 수 없는 절차입니다",
  "status": 409,
  "detail": "계획 v1 상태가 draft 입니다 — 승인 후에 연락을 개시할 수 있습니다",
  "instance": "/api/v1/incidents/{id}/contacts"
}
```

상류 오류에는 `upstream` 과 `upstream_status` 가 더 붙는다.

| 상태 | 언제 |
| --- | --- |
| 401 / 403 | 키 없음·불명 / 역할 부족 |
| 404 | 대상 없음 |
| 409 | 절차 위반 (미승인 계획으로 연락, 중복 승인, 대체된 계획 승인) |
| 422 | 요청 형식 오류, 존재하지 않는 마을·대피소, 모델 없는 재난 |
| 502 / 504 | 상류 실패 / 상류 타임아웃 |

### 페이지네이션

`limit` / `offset`, 응답은 `{items, total, limit, offset}`.

---

## 2. 관측 — `/situation`

### `GET /situation/context?region=&hazard=`

여러 원천을 병렬 조회한 봉투를 **줄이지 않고** 돌려준다.

```jsonc
{
  "region": { "code": "47280", "name": "문경시",
              "kma_grid": {"nx": 81, "ny": 106}, "asos_station": 273 },
  "hazard": "landslide",
  "hazard_korean": "산사태",
  "capability": { "readiness": "ready", "can_detect": true, "can_say_where_to_go": true },
  "state": "DATA",                      // ← 화면은 이것만 보면 된다
  "headline_caveat": "원천 landslide_forecast, landslide_roadside 을(를) 읽지 못했습니다 — …",
  "envelope": {
    "records": [ { "payload": {...}, "source": {...}, "freshness": {...} } ],
    "citations": [...],
    "receipts": [ {"connector": "landslide_forecast", "outcome": "failed",
                   "detail": "HTTP 403 — 개발단계 심의승인 대상"} ],
    "degradations": [...],
    "complete": false,
    "absence_confirmed": false,
    "failed_sources": ["landslide_forecast", "landslide_roadside"]
  }
}
```

**`state` 계산 규칙** — 백엔드가 이미 계산했다. 프론트엔드가 다시 구현하면 갈라진다.

```
records 가 있으면                        → DATA
complete && absence_confirmed 이면       → NONE
그 외                                    → UNVERIFIED
```

| `state` | 화면 | 하면 안 되는 것 |
| --- | --- | --- |
| `DATA` | 값과 관측시각을 함께 | 관측시각을 빼기 |
| `NONE` | "발효 중 없음" | — |
| `UNVERIFIED` | "확인 불가" + 사유 | **초록·안심 색을 쓰기** |

`headline_caveat` 은 그대로 화면에 띄우라고 만든 문장이다. 우선순위는
① 읽지 못한 원천 → ② 재난 자체의 한계(partial) → ③ 오래된 값 → ④ 상류 caveat.

### `GET /situation/overview?region=`

세 축이 다 있는 재난 5종(호우·홍수·산사태·산불·태풍)을 한 번에. 콘솔 첫 화면용이다.
재난 하나가 실패해도 나머지를 돌려주고, 실패한 것은 `state=UNVERIFIED` 로 남는다.

`partial` 재난(지진·폭염 등)을 여기에 넣지 않은 이유가 있다 — 첫 화면에 나란히 놓이면
"다 대응된다"로 읽힌다. **지진은 발생을 알려주지만 어느 대피소로 보낼지 모른다.**

### `GET /situation/weather?region=`

기상청 실황·단기예보를 시군 하나 분으로 추려 준다. 기온·습도·풍속·풍향·강수를 꺼내 두고
나머지 관측은 `readings` 로 함께 나간다.

```jsonc
{
  "state": "DATA",
  "temperature_c": 24.6, "humidity_pct": 96.0,
  "wind_speed_ms": 0.4, "wind_direction_deg": 194.0, "rainfall_1h_mm": 0.0,
  "observed_at": "2026-08-23T08:00:00+09:00",
  "stale": false,
  "attribution": "기상청 「기상청 단기예보」 · KOGL-1 · …",
  "caveats": ["관측지점이 마을에서 3.2km 떨어져 있습니다"]
}
```

**값이 없으면 지어내지 않는다.** `state=UNVERIFIED` 는 못 읽은 것이고, 화면이 그걸 '맑음'이나
0 으로 그리면 조회 실패가 안전으로 읽힌다. 강수 `null` 과 강수 `0.0` 은 다른 뜻이다.

`stale=true` 면 갱신주기를 넘긴 값이므로 `observed_at` 을 함께 띄운다. `attribution` 은
KOGL 출처 표기라 화면에서 지우면 안 된다.

### `GET /situation/sources/{connector}`, `GET /situation/health`

원천 하나 직접 조회 / 원천별 상태와 사유.

---

## 3. 메타 — `/meta`

| 엔드포인트 | 쓸 곳 |
| --- | --- |
| `GET /meta/regions` | 경북 시군 22개 |
| `GET /meta/regions/resolve?q=` | 지역명 → 코드·좌표·기상격자·ASOS 지점 |
| `GET /meta/hazards` | 재난 13종 가용성 (`ready` 5 / `partial` 6 / `blocked` 2) |
| `GET /meta/hazards/map-scenarios` | 프론트엔드 시나리오명 ↔ 정규 재난 코드 |
| `GET /meta/datasets/{id}/verify?operation=` | 이 용도로 써도 되는지 판정 |
| `GET /meta/datasets/{id}/citation` | 보고서용 출처 문구 |
| `GET /meta/quality` | 검증으로 확인된 데이터 결함 |

**`resolve` 를 프론트엔드에 다시 구현하면 안 된다.** 기관마다 식별자가 다르다 — 문경시는
행정표준코드 `47280`, SGIS `37090`, 기상청 격자 `(81,106)` 이다. `(90,95)` 는 문경이 아니라
구미고 71.6km 떨어져 있다.

**시나리오명 매핑도 마찬가지다.** 맵 캔버스는 `rain`/`coldwave`/`snow`/`chemical` 을 쓰고
상류는 `heavy_rain`/`cold_wave`/`heavy_snow`/`chemical_accident` 를 쓴다. 이 표를 양쪽에
각각 적으면 반드시 갈라진다.

**`verify` 는 문서가 아니라 판정이다.** KOGL 3·4(변경금지)는 재투영·클리핑·조인·파생라벨을
막는다. 지도 레이어를 만들기 전에 `operation=derive` 로 물어야 한다.

---

## 4. 예측 — `/predictions`

`POST /predictions`

```jsonc
{
  "recipe": "landslide_risk",          // mlengine 의 recipe registry 와 같은 이름
  "region_code": "47750",
  "hazard": "landslide",
  "horizon_minutes": 180,
  "grid": { "height": 64, "width": 64, "cell_size_m": 100, "crs": "EPSG:5179" },
  "threshold": 0.5,
  "incident_id": "…"                   // 있으면 상황 타임라인에 묶인다
}
```

응답에는 항상 이 셋이 붙는다.

```jsonc
{
  "is_derived": true,
  "derived_notice": "자체 모델이 만든 파생 지표입니다. 어느 기관도 보증하지 않으며 …",
  "feature_mode": "real | synthetic | provided",
  "is_stub": false,
  "model": { "name": "landslide_risk", "version": "3", "backend": "triton" },
  "summary": { "max": 0.87, "mean": 0.21, "p95": 0.63,
               "cells_over_threshold": 128, "total_cells": 4096, "top_cells": [...],
               "channels": [ {"channel": 3, "hazard": "drought",
                              "max": 0.53, "cells_over_threshold": 63} ] }
}
```

**`summary.channels` 가 있으면 그걸 읽어야 한다.** 한 모델이 여러 재난을 한 텐서에 담는
경우가 있다 — `weather_extremes` 는 폭염·한파·대설·가뭄 넷이다. 전체 요약은 채널 축의
최댓값이라 "64셀이 임계를 넘었다"까지만 말하고 **어느 재난인지 말해 주지 않는다.**
실제 값에서 그 64셀 중 63셀이 가뭄이고 한파·대설은 0이었다.

산림청 산사태위험등급 1~5 는 **기관이 보증하는 값**이고 이건 아니다. 같은 화면에서 같은
모양으로 그리면 안 된다.

`GET /predictions/runs` 는 실행 이력이다. 실패도 남는다 — 나중에 "그때 모델이 죽어 있었다"에
답하기 위해서다. 격자 원본은 보관하지 않고 요약만 남긴다.

### recipe ↔ 재난

| 재난 | recipe |
| --- | --- |
| `heavy_rain` | `rain_nowcast` |
| `flood` | `flood_extent` |
| `landslide` | `landslide_risk` |
| `wildfire` | `wildfire_spread` |
| `typhoon` | `typhoon_track_intensity` |
| `heatwave` / `cold_wave` / `heavy_snow` / `drought` | `weather_extremes` (한 모델이 네 재난을 채널로 낸다) |

---

## 4.5 대피 경로 — `/routing`

`POST /routing/evacuation` — 위험 구역을 피해 대피소로 가는 경로.

```jsonc
{
  "community_id": "…",              // 또는 lat/lon
  "hazard": "wildfire",             // 이 재난의 대피소만 후보
  "mode": "foot",                   // foot | assisted | bicycle | car
  "incident_id": "…",               // 주면 현장 보고의 통제 구간이 차단으로 들어간다
  "use_prediction": true,
  "horizons_minutes": [30, 60, 120],
  "depart_after_minutes": 0,
  "block_threshold": 0.5
}
```

### 시간에 따라 커지는 위험을 반영한다

산불처럼 퍼지는 재난은 **지금 안전한 길이 30분 뒤에는 아닐 수 있다.** 예측을 여러 시점으로
받아, 각 지점을 **지나는 시각**의 위험으로 판단한다. 같은 요청이라도 `depart_after_minutes`
가 다르면 경로가 달라진다.

한 번 위험해진 칸은 계속 위험한 것으로 둔다. 불이 지나간 자리를 안전으로 읽지 않기 위해서고,
그 단조성 덕분에 "일찍 도착하는 것이 손해가 아니다"가 성립해 탐색이 최적을 준다.

### 예측 격자에는 좌표 범위가 필요하다

`grid.bbox` 가 없으면 백엔드가 격자 위치를 **가정**할 수밖에 없고, 가정이 틀리면 위험 구역이
실제와 다른 자리에 놓인다. 가정한 경우 `warnings` 에 그 사실이 실린다. ML 서버는
`grid.bbox` 와 `grid.crs`(EPSG:4326)를 실어야 한다. 다른 좌표계는 재투영하지 않고 쓰지 않는다.

### 합성 입력으로 만든 위험장은 길을 막지 않는다

응답의 `feature_mode` 가 `synthetic` 이면 모델은 돌았지만 **입력이 관측이 아니다.**
그 출력은 불이 어디 있는지 말해 주지 않으므로 확산을 경로에 반영하지 않고, 그 사실을
`warnings` 에 적는다.

조회 실패를 '위험 없음'으로 읽으면 안 되는 것과 같은 이유로, **모르는 것을 '위험함'으로
읽어도 안 된다.** 어느 쪽이든 관측하지 않은 것을 단정하는 일이다. 합성값으로 도로를
차단하면 "경로가 없습니다"라는 확신에 찬 오답이 나가는데, 실제로는 '모른다'다.

`feature_mode=real` 이 되면 그때부터 확산이 경로에 반영된다.

### 세 가지 입력이 성격이 다르다

| 입력 | 성격 | 응답 표시 |
| --- | --- | --- |
| OSM 도로망 | ODbL 파생물 | `attribution` — 지우면 안 된다 |
| 자체 예측 | 확률 | `prediction_is_stub`, `feature_mode` |
| 현장 보고 | 확인된 사실 | `field_reports_applied`, `blocked_by_reports` |

**현장 보고가 예측을 이긴다.** 확률이 아니라 차단이다 — 사람이 가서 본 것이기 때문이다.

### 갈 곳을 말할 수 없는 재난이 있다

`shelter_guidance_available: false` 면 **이 재난은 대피소를 안내할 수 없다.** 지진은 발생을
알려주지만 어느 대피소로 보낼지 말할 공개 데이터가 없다 (가용성 `partial`). `hazard_limitation`
에 GB SafeData 의 공식 caveat 이 실리므로 화면에 그대로 띄운다.

**계산 실패와 다르다.** 404 로 던지면 화면은 "경로를 계산하지 못했습니다"라고만 말하고,
담당자는 없는 데이터를 찾으러 간다. 200 으로 한계를 말해야 그 자리에서 다른 수단을 찾는다.

### 경로가 없으면 사유를 준다

`routes[].found=false` 일 때 `reason` 이 붙는다. 세 상태가 구분된다.

- 도로망에서 애초에 이어지지 않는다
- **출발 지점이 이미 위험 구역이다** — 우회로를 찾을 상황이 아니라 그 자리를 벗어나야 한다
- 위험을 피해서는 닿을 수 없다 (`avoided_edges` 로 몇 개를 막았는지)
- 현장 통제 구간에 막혔다 (`blocked_by_reports`)

빈 경로만 돌려주면 화면은 "가까운 대피소가 없다"와 "길이 전부 막혔다"를 구분할 수 없다.

### 이동수단마다 지날 수 있는 길이 다르다

`GET /routing/modes` 가 규칙을 준다. 모르는 태그는 통행 가능으로 두지 않는다 —
**통행 가능한 길을 빼는 쪽이 막힌 길로 보내는 쪽보다 안전하다.**

- 긴급차량 전용(`access=emergency`)은 주민 자가 대피에 쓸 수 없다
- 조건부 제한(`no @ (wet)`)은 조건을 해석하지 않고 제외한다
- 세월교(`ford`)는 호우 시 가장 먼저 끊기므로 기본 차단
- 보행보조는 계단·비포장을 제외하고 속도도 느리다

### 이 경로는 제안이다

`notice` 와 `is_derived` 를 화면에서 지우면 안 된다. **검증되지 않은 경로를 공식 안전경로로
표시하는 것은 데이터 계층이 명시적으로 금지한 항목이다.**

## 5. 운영 루프

```
POST /incidents                              상황 개시          operator
  └ POST /incidents/{id}/plans               계획 기안 (v1)     operator
      └ POST /plans/{id}/approve             승인               approver ★
          └ POST /incidents/{id}/contacts    주민 연락 개시     operator   (승인 후에만)
              └ PATCH /contacts/{id}         결과 기록          operator
                  └ POST …/tasks/from-unreachable   미연락 → 현장 임무
                      └ POST /tasks/{id}/reports    현장 보고    field
                          └ 계획이 reapproval_required 로 되돌아간다
                              └ POST /incidents/{id}/plans   개정 (v2)
```

### 상황 `/incidents`

상황은 **사람이 연다.** 관측값이 임계를 넘었다는 이유로 시스템이 자동으로 열지 않는다.
`opening_evidence` 에 그 시점 화면의 근거를 남기면 나중에 재구성할 수 있다.

`code` 는 `YYYY-MMDD-NN` 으로 자동 생성된다 (`2026-0822-01`).

`GET /incidents/{id}/timeline` 은 감사 이력을 그대로 읽는다. 승인·연락개시·보고접수가 전부 남고,
`payload` 에 승인자 이름과 사유가 들어 있다.

### 계획 `/plans`

상태 전이는 넷뿐이다.

```
draft ──approve──▶ approved ──현장보고──▶ reapproval_required
  │                    │
  └────────────────────┴──새 계획 기안──▶ superseded (이력은 남는다)
```

- **배열 순서가 대피 순서다.** `order_index` 를 클라이언트가 정하지 않는다
- **승인된 계획을 수정하지 않는다.** 개정은 항상 새 버전이다 — 그래야 "누가 무엇을 승인했는가"가 남는다
- 응답의 `notice` 는 계획의 한계다. 실시간 도로 통제가 반영되지 않았다는 사실을 지우면 안 된다
- `is_actionable` 이 `true` 일 때만 연락을 개시할 수 있다

### 연락 `/contacts`

**발송은 이 시스템이 하지 않는다.** 전화·문자는 별도 채널이고, 여기 남는 것은 명단과 결과다.

`GET /incidents/{id}/contacts/rollup` 이 `unreachable` 을 따로 세는 이유가 있다. 미확인 세대를
전체에 섞으면 "연락 완료 80%"가 되고, 갈 곳 없는 사람이 나머지 20% 안에서 보이지 않게 된다.

### 현장 `/tasks`

`POST /incidents/{id}/tasks/from-unreachable` 은 미연락 마을을 우선순위 1 임무로 바꾼다.
같은 마을에 열린 임무가 있으면 중복 생성하지 않는다.

`POST /tasks/{id}/reports` 에 `access_constraints` 가 하나라도 실리면(또는 `request_replan=true`),
승인된 계획이 `reapproval_required` 로 바뀌고 연락 개시가 다시 막힌다. 도로가 끊겼다는 보고가
들어왔는데 화면이 계속 같은 계획을 옳다고 말하면 안 되기 때문이다.

---

## 6. 마을·대피소

`GET /shelters?hazard=…` — **`hazard` 는 필수다.**

지진 대피소와 호우 대피소는 다른 시설이다. 화학사고는 화학물질관리법 제23조의4 **법정 지정**
대피장소가 따로 있다. hazard 없이 물을 수 있게 두면 어딘가에서 반드시 자동 전용이 일어난다.

`capacity` 에는 `capacity_basis` 가 따라온다. 연 1회 갱신되는 파일에서 온 정원이지
**실시간 수용현황이 아니다.**

`assisted_mobility_estimate` 는 SGIS 읍면동 고령인구 기반 **대리지표**다. 개인 단위 이동능력을
추정하는 값이 아니다 — 공개 집계통계로 개인을 추정하는 것은 이 프로젝트가 금지하는 일이다.

---

## 7. 챗봇 — `/assistant`

GB SafeData 의 도구 정의 12종과 시스템 프롬프트를 중계한다. 브라우저가 상류를 직접 부르지
않게 하려는 것이다 — 상류는 정부 인증키로 호출한다.

```
GET /assistant/system-prompt     ← 반드시 같이 쓴다
GET /assistant/tools             OpenAI function calling 스키마 그대로
GET /assistant/tools/{name}      도구 실행 (조회 전용)
```

**시스템 프롬프트를 빼고 도구만 붙이면 사고가 난다.** 모델은 기본적으로 도움이 되려 하고,
산사태 조회가 403 으로 실패해 결과가 비면 "산사태 위험 없습니다"라고 답한다. 프롬프트를
복붙하지 않고 매번 받아 쓰는 이유는, 상류가 고치면 자동으로 따라오게 하기 위해서다.

---

## 8. 주민 화면 — `/public`

인증이 필요 없고 **개인정보를 담지 않는다.** 열린 상황 목록, 관측 확인 가능 여부, 안내 문구뿐이다.

상류가 죽어도 이 엔드포인트는 200 을 돌려준다. 다만 `observation_state` 가 `UNVERIFIED` 가 되고
`caveat` 에 "안전하다는 뜻이 아닙니다"가 실린다. 주민 화면이 통째로 죽는 것보다 낫지만,
확인하지 못한 것을 확인한 것처럼 보이게 하지는 않는다.

---

## 9. 프론트엔드가 하지 말아야 할 것

`jxkr2026-datasets` 가 정한 선이다. 백엔드가 지켜도 화면에서 무너지면 의미가 없다.

- 연 1회 갱신 대피소 파일을 **실시간 수용현황**이라 부르기
- 시군구 단위 예보를 **마을 단위 예측**처럼 제시하기
- 공개 집계통계로 **개인의 이동능력** 추정하기
- 지진 대피소를 **호우·산불 대피소로 자동 전용**하기
- 검증되지 않은 경로를 **공식 안전경로**로 표시하기
- `UNVERIFIED` 를 **안심시키는 색**으로 칠하기
- 자체 모델 산출값을 **공식 위험등급과 같은 모양**으로 그리기
