"""확산 계산 — 재난이 시간에 따라 어디로 번지는가.

**이 계산은 백엔드가 한다.** 브라우저에서 돌리면 기기마다 다른 답이 나오고, 콘솔과
모바일이 서로 다른 확산도를 보게 된다. 대피 경로를 그 위에서 계산하는 이상 두 화면이
같은 값을 봐야 한다.

한 번 위험해진 칸은 계속 위험한 것으로 둔다. 불이 지나간 자리를 안전으로 읽지 않기
위해서고, 같은 이유가 `app/routing/hazard.py` 에도 적혀 있다 — 두 곳이 같은 규칙을
쓰지 않으면 경로가 방금 탄 자리로 사람을 보낸다.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import struct
from dataclasses import dataclass

from app.schemas.prediction import PredictionRequest, PredictionResult
from app.services import predictions
from app.services.events import Event, broker

logger = logging.getLogger(__name__)

# 확산을 볼 시점들. 대피에 걸리는 시간만큼은 덮어야 한다.
DEFAULT_HORIZONS: tuple[int, ...] = (0, 15, 30, 60, 120)

# 격자 한 변. 12 km 창에 512 면 한 칸이 23 m — 하천 한 줄이 분해되는 크기다.
FRAME_SIZE = 512


@dataclass(slots=True)
class SpreadWindow:
    """확산을 계산할 지리 범위."""

    west: float
    south: float
    east: float
    north: float

    @classmethod
    def around(cls, lat: float, lon: float, size_m: float) -> SpreadWindow:
        import math

        d_lat = size_m / 2 / 110_574
        d_lon = size_m / 2 / (111_320 * math.cos(math.radians(lat)))
        return cls(lon - d_lon, lat - d_lat, lon + d_lon, lat + d_lat)


def encode_values(values: list[float]) -> str:
    """float32 리틀엔디언 배열을 base64 로. JSON 숫자 배열보다 6배 작다."""
    return base64.b64encode(
        struct.pack(f"<{len(values)}f", *values)
    ).decode("ascii")


def frame_from_result(
    result: PredictionResult, window: SpreadWindow, hazard: str
) -> dict | None:
    """추론 결과를 프론트가 그릴 수 있는 한 프레임으로."""
    grid = result.grid
    if grid is None or not result.outputs:
        return None
    tensor = result.outputs[0]
    expected = grid.width * grid.height
    if len(tensor.data) < expected:
        logger.warning(
            "prediction %s returned %d cells for a %dx%d grid",
            result.prediction_id,
            len(tensor.data),
            grid.width,
            grid.height,
        )
        return None
    bbox = grid.bbox or [window.west, window.south, window.east, window.north]
    return {
        "prediction_id": result.prediction_id,
        "recipe": result.recipe.value,
        "hazard": hazard,
        "horizon_minutes": result.horizon_minutes or 0,
        "width": grid.width,
        "height": grid.height,
        "bbox": bbox,
        # row-major, 북쪽 행이 먼저. 프론트의 map:set-hazard-field 와 같은 순서다.
        "values_b64": encode_values(list(tensor.data[:expected])),
        # 자체 모델 산출값이라는 표시는 프레임마다 붙어 다닌다.
        "is_derived": True,
        "is_stub": result.is_stub,
        "feature_mode": result.feature_mode.value,
        "derived_notice": result.derived_notice
        if hasattr(result, "derived_notice")
        else None,
    }


async def run_spread(
    session,
    client,
    *,
    hazard: str,
    lat: float,
    lon: float,
    incident_id: str | None = None,
    region_code: str | None = None,
    size_m: float = 12_000,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> int:
    """시점별로 추론하고, 나오는 대로 프레임을 발행한다.

    앞 시점을 기다렸다가 다음을 계산한다. 화면은 첫 프레임을 몇 초 안에 받고 나머지는
    이어서 채운다 — 전부 끝나고 한 번에 주면 그 사이 화면이 비어 있다.
    """
    recipe = predictions.recipe_for_hazard(hazard)
    window = SpreadWindow.around(lat, lon, size_m)
    sent = 0

    for horizon in horizons:
        request = PredictionRequest(
            recipe=recipe,
            hazard=hazard,
            region_code=region_code,
            horizon_minutes=horizon,
            incident_id=incident_id,
            grid={
                "height": FRAME_SIZE,
                "width": FRAME_SIZE,
                "crs": "EPSG:4326",
                "bbox": [window.west, window.south, window.east, window.north],
            },
        )
        try:
            result = await predictions.run(session, client, request, actor="spread")
        except Exception:
            # 한 시점이 실패해도 나머지는 계속한다. 30분 뒤를 못 구했다고 지금을
            # 지우면 화면이 더 나빠진다.
            logger.exception("spread horizon %s failed for %s", horizon, hazard)
            continue

        frame = frame_from_result(result, window, hazard)
        if frame is None:
            continue
        broker.publish(
            Event(kind="prediction.frame", data=frame, incident_id=incident_id)
        )
        sent += 1
        # 다음 시점 전에 이벤트 루프를 놓아 준다.
        await asyncio.sleep(0)

    broker.publish(
        Event(
            kind="spread.complete",
            data={"hazard": hazard, "frames": sent, "horizons": list(horizons)},
            incident_id=incident_id,
        )
    )
    return sent
