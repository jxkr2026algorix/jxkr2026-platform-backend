# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 의존성을 먼저 굳힌다 — 소스만 바뀌면 이 레이어를 재사용한다.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY app ./app
COPY alembic ./alembic
COPY scripts ./scripts
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ─────────────────────────────────────────────────────────────────────────────
# 도로망을 이미지에 굽는 단계. 기본은 비어 있다.
#
#   docker build --build-arg ROAD_NETWORK_BBOX=129.10,36.36,129.22,36.44 .
#
# **이걸 켜면 이미지가 ODbL 파생물이 된다.** 레지스트리에 올리는 순간 배포이므로
# 출처 표시와 파생 데이터베이스 제공 의무가 이미지에 따라붙는다. KOGL 정부 데이터를
# 같은 배포물에 병합하면 share-alike 가 정부 데이터에까지 얹힌다.
#
# 켜지 않으면 이미지에 OSM 데이터가 들어가지 않고, 실행 시점에 붙이거나 받는다.
FROM builder AS roads

ARG ROAD_NETWORK_BBOX=""
# 굽든 받든 마운트하든 경로는 하나다. 볼륨을 붙이면 구운 파일을 덮는다 —
# 마운트가 이기는 것이 기대되는 우선순위다.
ARG ROAD_NETWORK_PATH=/data/road/roads.geojson

RUN mkdir -p "$(dirname "$ROAD_NETWORK_PATH")" \
 && if [ -n "$ROAD_NETWORK_BBOX" ]; then \
      echo "▸ 도로망을 이미지에 굽는다 (ODbL) — bbox=$ROAD_NETWORK_BBOX" \
   && .venv/bin/python scripts/build_road_network.py \
        --bbox "$ROAD_NETWORK_BBOX" --output "$ROAD_NETWORK_PATH" \
   && printf '%s\n' \
        "This image contains a database derived from OpenStreetMap." \
        "© OpenStreetMap contributors — Open Database License (ODbL) 1.0" \
        "https://www.openstreetmap.org/copyright" \
        "" \
        "Distributing this image distributes that derivative database." \
        "Do not merge KOGL government data into the same distributed artifact:" \
        "share-alike would then attach to the government data as well." \
        > /data/road/NOTICE-OSM.txt; \
    else \
      echo "▸ 도로망을 굽지 않는다 — 실행 시점에 마운트하거나 받는다"; \
    fi


FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    SALGIL_ROAD_NETWORK_PATH=/data/road/roads.geojson

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 salgil \
 # 이름 있는 볼륨은 이미지의 마운트 지점 소유권을 물려받는다. 미리 만들어 두지 않으면
 # root 소유로 생성되고, non-root 로 도는 컨테이너가 도로망을 못 받는다.
 && mkdir -p /data/road \
 && chown -R salgil:salgil /data

WORKDIR /app
COPY --from=roads --chown=salgil:salgil /app /app
# 도로망을 구웠으면 함께 가져온다. 안 구웠으면 빈 디렉터리다.
COPY --from=roads --chown=salgil:salgil /data/road /data/road
COPY --chown=salgil:salgil docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 도로망을 구웠으면 이미지가 무엇을 담고 있는지 라벨로 남긴다. 라벨이 없으면
# 나중에 이 이미지가 ODbL 대상인지 아무도 모른다.
ARG ROAD_NETWORK_BBOX=""
LABEL org.opencontainers.image.licenses="Apache-2.0${ROAD_NETWORK_BBOX:+ AND ODbL-1.0}"
LABEL kr.gyeongbuk.salgil.osm-baked="${ROAD_NETWORK_BBOX:-none}"

USER salgil
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
