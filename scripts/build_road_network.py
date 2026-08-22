#!/usr/bin/env python3
"""OSM 추출본을 경로 계산용 GeoJSON 으로 만든다.

    python scripts/build_road_network.py --bbox 128.9,36.2,129.3,36.6 \
        --output data/local/roads.geojson

기본은 Overpass API 다. 시군 하나 정도는 이걸로 충분하고, 더 넓은 범위는 Geofabrik
덤프에 osmium 을 쓰는 편이 빠르다.

    osmium extract -b <bbox> south-korea-latest.osm.pbf -o area.osm.pbf
    osmium tags-filter area.osm.pbf w/highway -o roads.osm.pbf
    osmium export roads.osm.pbf -f geojsonseq -o data/local/roads.geojsonseq

**산출물을 저장소에 커밋하지 않는다.** OSM 파생물은 ODbL 이고, 커밋하면 그 데이터가
ODbL 이 된다. KOGL 정부 데이터와 병합해 배포하면 share-alike 가 정부 데이터에까지 얹힌다.
`data/local/` 은 gitignore 돼 있다.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def fetch(bbox: tuple[float, float, float, float], timeout_s: int) -> dict:
    min_lon, min_lat, max_lon, max_lat = bbox
    query = (
        f"[out:json][timeout:{timeout_s}];"
        f'way["highway"]({min_lat},{min_lon},{max_lat},{max_lon});'
        "out geom;"
    )
    request = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": "salgil-platform-backend/0.1 (+jxkr2026)"},
    )
    with urllib.request.urlopen(request, timeout=timeout_s + 30) as response:
        return json.loads(response.read().decode())


def to_geojson(payload: dict) -> dict:
    features = []
    for element in payload.get("elements", []):
        geometry = element.get("geometry") or []
        if len(geometry) < 2:
            continue
        features.append(
            {
                "type": "Feature",
                "id": str(element.get("id")),
                "properties": {k: str(v) for k, v in (element.get("tags") or {}).items()},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[p["lon"], p["lat"]] for p in geometry],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        required=True,
        help="minlon,minlat,maxlon,maxlat (예: 129.10,36.36,129.22,36.44)",
    )
    parser.add_argument("--output", type=Path, default=Path("data/local/roads.geojson"))
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    try:
        bbox = tuple(float(v) for v in args.bbox.split(","))
        if len(bbox) != 4:
            raise ValueError
    except ValueError:
        print("bbox 는 minlon,minlat,maxlon,maxlat 형식이어야 합니다", file=sys.stderr)
        return 2

    print(f"▸ Overpass 조회 {bbox}")
    try:
        payload = fetch(bbox, args.timeout)  # type: ignore[arg-type]
    except urllib.error.URLError as exc:
        print(f"Overpass 조회 실패: {exc}", file=sys.stderr)
        return 1

    collection = to_geojson(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(collection), encoding="utf-8")

    grades: dict[str, int] = {}
    for feature in collection["features"]:
        grade = feature["properties"].get("highway", "?")
        grades[grade] = grades.get(grade, 0) + 1

    size_kb = args.output.stat().st_size / 1024
    print(f"▸ way {len(collection['features'])}개 → {args.output} ({size_kb:.0f} KB)")
    print(f"  등급: {dict(sorted(grades.items(), key=lambda kv: -kv[1])[:8])}")
    print()
    print("© OpenStreetMap contributors, ODbL 1.0")
    print("이 파일은 ODbL 파생물입니다 — 저장소에 커밋하지 마세요.")
    print(f"SALGIL_ROAD_NETWORK_PATH={args.output} 로 백엔드에 지정합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
