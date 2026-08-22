#!/usr/bin/env bash
# 기동 순서: DB 를 기다린다 → 마이그레이션 → (선택) 시드 → 서버.
#
# 마이그레이션을 엔트리포인트에서 도는 것은 단일 인스턴스 배포라서 안전하다.
# 여러 replica 로 늘릴 때는 SALGIL_RUN_MIGRATIONS=0 으로 끄고 별도 job 으로 돌린다.
set -euo pipefail

RUN_MIGRATIONS="${SALGIL_RUN_MIGRATIONS:-1}"
RUN_SEED="${SALGIL_RUN_SEED:-0}"
WAIT_SECONDS="${SALGIL_DB_WAIT_SECONDS:-60}"

wait_for_db() {
  local waited=0
  until python - <<'PY'
import asyncio, sys
from sqlalchemy import text
from app.db.session import get_sessionmaker

async def main():
    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(text("SELECT 1"))

try:
    asyncio.run(main())
except Exception as exc:
    print(f"db not ready: {type(exc).__name__}", file=sys.stderr)
    sys.exit(1)
PY
  do
    if [ "$waited" -ge "$WAIT_SECONDS" ]; then
      echo "데이터베이스가 ${WAIT_SECONDS}초 안에 준비되지 않았습니다" >&2
      exit 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

# 도로망이 지정됐는데 파일이 없고 bbox 가 있으면 받아 온다.
# 이미지에 굽지 않으므로 이미지는 ODbL 대상이 아니고, 받은 파일은 볼륨에 남아
# 다음 기동에서 재사용된다. 첫 기동에만 네트워크가 필요하다.
if [ -n "${SALGIL_ROAD_NETWORK_PATH:-}" ] && [ ! -f "$SALGIL_ROAD_NETWORK_PATH" ]; then
  if [ -n "${SALGIL_ROAD_NETWORK_BBOX:-}" ]; then
    echo "▸ 도로망 내려받기 (OSM/ODbL) — bbox=${SALGIL_ROAD_NETWORK_BBOX}"
    if python scripts/build_road_network.py \
        --bbox "$SALGIL_ROAD_NETWORK_BBOX" \
        --output "$SALGIL_ROAD_NETWORK_PATH"; then
      :
    else
      # 경로 계산만 못 하게 두고 서비스는 뜬다. 상황·계획·연락은 도로망 없이도 돌아간다.
      echo "  도로망을 받지 못했습니다 — 경로 계산은 거절되고 나머지는 정상 동작합니다" >&2
    fi
  else
    echo "▸ 도로망 파일이 없습니다: $SALGIL_ROAD_NETWORK_PATH" >&2
    echo "  SALGIL_ROAD_NETWORK_BBOX 를 주면 첫 기동에 받아 옵니다" >&2
  fi
fi

if [ "$RUN_MIGRATIONS" = "1" ]; then
  echo "▸ 데이터베이스 대기"
  wait_for_db
  echo "▸ 마이그레이션 적용"
  alembic upgrade head
fi

if [ "$RUN_SEED" = "1" ]; then
  echo "▸ 데모 시드 (data_mode=synthetic)"
  salgil seed || echo "시드를 건너뜁니다"
fi

exec "$@"
