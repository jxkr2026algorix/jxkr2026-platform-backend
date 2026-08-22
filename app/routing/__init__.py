"""대피 경로 계산.

도로망은 OSM 에서 온다. **산출물은 ODbL 파생물이다** — 응답에 출처를 싣고,
가공한 도로망을 저장소에 커밋하지 않는다. KOGL 정부 데이터와 병합해 배포하면
share-alike 가 정부 데이터에까지 얹힌다 (`jxkr2026-datasets/docs/acquisition-priority.md`).

여기서 나온 경로는 **제안이지 공식 안전경로가 아니다.** 검증되지 않은 경로를 공식
안전경로로 표시하는 것은 datasets 레포가 명시적으로 금지한 항목이다.
"""

from app.routing.graph import RoadGraph, load_road_graph
from app.routing.hazard import HazardField, HazardSlice
from app.routing.planner import RouteResult, plan_route
from app.routing.profiles import PROFILES, TransportMode, profile_for

__all__ = [
    "PROFILES",
    "HazardField",
    "HazardSlice",
    "RoadGraph",
    "RouteResult",
    "TransportMode",
    "load_road_graph",
    "plan_route",
    "profile_for",
]
