"""시간 의존 최단경로.

비용은 거리가 아니라 **도착 시각**이다. 위험이 시간에 따라 커지므로, 어떤 간선을
지날 수 있는지는 그 간선에 언제 도착하느냐에 달려 있다.

`HazardField.risk_at` 이 시간에 대해 단조증가하도록 구성돼 있어(한 번 위험해진 칸은
계속 위험) 일찍 도착하는 것이 결코 손해가 아니다(FIFO). 그래서 다익스트라가 최적을 준다.
그 성질이 깨지면 이 탐색은 최적이 아니게 되므로, 위험장 쪽을 바꿀 때 같이 봐야 한다.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from app.routing.graph import NodeId, RoadGraph, haversine_m
from app.routing.hazard import BlockedPoint, HazardField, HazardPolicy
from app.routing.profiles import ModeProfile


@dataclass
class RouteResult:
    found: bool
    reason: str | None = None
    coordinates: list[tuple[float, float]] = field(default_factory=list)  # [(lon, lat)]
    distance_m: float = 0.0
    duration_s: float = 0.0
    max_risk: float = 0.0
    mean_risk: float = 0.0
    # 위험해서 지나지 않은 간선 수. 0 이면 위험을 피한 것이 아니라 애초에 없었을 수 있다.
    avoided_edges: int = 0
    blocked_by_reports: list[BlockedPoint] = field(default_factory=list)
    explored_nodes: int = 0

    @property
    def duration_minutes(self) -> float:
        return round(self.duration_s / 60.0, 1)


def plan_route(
    graph: RoadGraph,
    profile: ModeProfile,
    hazard: HazardField,
    policy: HazardPolicy,
    *,
    origin: tuple[float, float],
    destination: tuple[float, float],
    depart_after_s: float = 0.0,
    max_duration_s: float = 4 * 3600,
    snap_distance_m: float = 2000.0,
) -> RouteResult:
    """origin 에서 destination 까지, 시간에 따라 커지는 위험을 피해서.

    경로가 없으면 **빈 경로가 아니라 사유를 돌려준다.** 빈 경로를 받은 화면은
    "가까운 대피소가 없다"와 "길이 전부 막혔다"를 구분할 수 없다.
    """
    start = graph.nearest_node(origin[0], origin[1], max_distance_m=snap_distance_m)
    goal = graph.nearest_node(destination[0], destination[1], max_distance_m=snap_distance_m)

    if start is None:
        return RouteResult(
            found=False,
            reason=(
                f"출발지에서 {snap_distance_m:.0f}m 안에 {profile.korean_name} 통행로가 "
                "없습니다 — 도로망 범위 밖이거나 이 수단이 지날 수 있는 길이 없습니다"
            ),
        )
    if goal is None:
        return RouteResult(
            found=False,
            reason=(
                f"대피소에서 {snap_distance_m:.0f}m 안에 {profile.korean_name} 통행로가 없습니다"
            ),
        )

    blocked_start = hazard.blocked_by_field_report(start[0], start[1])
    if blocked_start is not None:
        return RouteResult(
            found=False,
            reason=f"출발 지점이 현장 통제 구간입니다 ({blocked_start.kind})",
            blocked_by_reports=[blocked_start],
        )

    # 출발 지점 자체가 위험 구역이면 일반적인 "길이 없습니다"로 뭉뚱그리지 않는다.
    # 그 둘은 현장에서 할 일이 다르다 — 우회로를 찾는 것과 그 자리를 벗어나는 것.
    start_risk = hazard.risk_at(start[0], start[1], depart_after_s)
    if policy.edge_multiplier(start_risk) is None:
        return RouteResult(
            found=False,
            reason=(
                f"출발 지점이 이미 위험 구역입니다 (위험도 {start_risk:.2f} ≥ "
                f"{policy.block_threshold:.2f}) — 경로를 계산할 수 있는 상태가 아닙니다"
            ),
            max_risk=round(start_risk, 5),
        )

    # (도착시각, 노드)
    queue: list[tuple[float, NodeId]] = [(depart_after_s, start)]
    best: dict[NodeId, float] = {start: depart_after_s}
    previous: dict[NodeId, NodeId] = {}
    avoided = 0
    blocked_hits: list[BlockedPoint] = []
    explored = 0

    while queue:
        arrival, node = heapq.heappop(queue)
        if arrival > best.get(node, float("inf")):
            continue
        explored += 1
        if node == goal:
            return _build_result(
                graph,
                hazard,
                previous,
                start,
                goal,
                arrival - depart_after_s,
                avoided,
                blocked_hits,
                explored,
                profile.speed_mps,
            )
        if arrival - depart_after_s > max_duration_s:
            continue

        for edge in graph.neighbours(node):
            target = edge.target

            report = hazard.blocked_by_field_report(target[0], target[1])
            if report is not None:
                if report not in blocked_hits:
                    blocked_hits.append(report)
                continue

            travel_s = edge.length_m / profile.speed_mps
            # 간선 끝에 도착하는 시각의 위험으로 판단한다. 간선 중간이 더 위험할 수
            # 있으나, 격자 해상도가 간선보다 성기면 끝점이 대표값이 된다.
            risk = hazard.risk_at(target[0], target[1], arrival + travel_s)
            multiplier = policy.edge_multiplier(risk)
            if multiplier is None:
                avoided += 1
                continue

            candidate = arrival + travel_s * multiplier
            if candidate < best.get(target, float("inf")):
                best[target] = candidate
                previous[target] = node
                heapq.heappush(queue, (candidate, target))

    if avoided or blocked_hits:
        return RouteResult(
            found=False,
            reason=(
                f"위험 구역을 피해서는 대피소에 닿는 길이 없습니다 "
                f"(차단된 간선 {avoided}개, 현장 통제 {len(blocked_hits)}건). "
                "다른 대피소나 다른 이동수단을 확인하세요"
            ),
            avoided_edges=avoided,
            blocked_by_reports=blocked_hits,
            explored_nodes=explored,
        )
    return RouteResult(
        found=False,
        reason=(
            f"도로망에서 출발지와 대피소가 {profile.korean_name}로 이어지지 않습니다 "
            "— 위험 때문이 아니라 연결된 길이 없습니다"
        ),
        explored_nodes=explored,
    )


def _build_result(
    graph: RoadGraph,
    hazard: HazardField,
    previous: dict[NodeId, NodeId],
    start: NodeId,
    goal: NodeId,
    duration_s: float,
    avoided: int,
    blocked_hits: list[BlockedPoint],
    explored: int,
    profile_speed: float,
) -> RouteResult:
    path: list[NodeId] = [goal]
    while path[-1] != start:
        path.append(previous[path[-1]])
    path.reverse()

    # 경로상의 위험은 **그 지점을 지나는 시각**으로 잰다. 전부 출발 시각으로 재면
    # 시간 의존을 계산해 놓고 보고에서 다시 잃는다 — 후반부 구간이 실제보다 안전해 보인다.
    distance = 0.0
    elapsed = 0.0
    risks: list[float] = [hazard.risk_at(path[0][0], path[0][1], 0.0)]
    for index in range(len(path) - 1):
        a, b = path[index], path[index + 1]
        segment = min(
            (edge.length_m for edge in graph.neighbours(a) if edge.target == b),
            default=haversine_m(a[0], a[1], b[0], b[1]),
        )
        distance += segment
        elapsed += segment / max(profile_speed, 1e-6)
        risks.append(hazard.risk_at(b[0], b[1], elapsed))

    return RouteResult(
        found=True,
        coordinates=[(node[1], node[0]) for node in path],  # GeoJSON 은 [lon, lat]
        distance_m=round(distance, 1),
        duration_s=round(duration_s, 1),
        max_risk=round(max(risks), 5) if risks else 0.0,
        mean_risk=round(sum(risks) / len(risks), 5) if risks else 0.0,
        avoided_edges=avoided,
        blocked_by_reports=blocked_hits,
        explored_nodes=explored,
    )
