"""데모 시드.

프론트엔드 콘솔(`apps/console-front/src/domain.ts`)이 하드코딩해 둔 청송 마을 넷과
그 대피소를 DB 로 옮긴다. 프론트엔드가 API 로 갈아탈 때 화면이 그대로 뜨게 하기 위한 것이다.

**전부 `data_mode="synthetic"` 이다.** 주민 수·세대수·이동지원 필요 인원은 공개 데이터로
얻을 수 없어서 훈련용 합성값이다. 실시간 화면에서 실제 자료처럼 섞이면 안 된다.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Community, Shelter

REGION_CODE = "47750"
REGION_NAME = "청송군"

# 다섯 축이 다 있는 재난 (ready). 이 목록 밖의 재난에 이 대피소를 자동 전용하면 안 된다.
_ALL_WEATHER = ["heavy_rain", "flood", "landslide", "wildfire", "typhoon"]

COMMUNITIES = [
    {
        "name": "상촌리",
        "name_en": "Sangchon",
        "emd_name": "부동면",
        "residents": 34,
        "households": 21,
        "assisted_mobility_estimate": 12,
        "vulnerability_note": (
            "고령 비율이 높다 — SGIS 읍면동 집계 기반 대리지표이며 개인 단위 판단이 아니다"
        ),
        "lat": 36.3921,
        "lon": 129.1607,
        "map_x": 0.52,
        "map_y": 0.42,
        "notes": "급경사지에 접해 있고 동측 진입로가 취약하다",
        "tags": ["slope", "priority"],
    },
    {
        "name": "월외리",
        "name_en": "Wolwe",
        "emd_name": "부동면",
        "residents": 27,
        "households": 16,
        "assisted_mobility_estimate": 4,
        "lat": 36.4102,
        "lon": 129.1839,
        "map_x": 0.70,
        "map_y": 0.30,
        "notes": "산림 인접 — 산불 확산 시 서측 경로를 비워 둬야 한다",
        "tags": ["forest"],
    },
    {
        "name": "부남면 소재지",
        "name_en": "Bunam",
        "emd_name": "부남면",
        "residents": 15,
        "households": 9,
        "assisted_mobility_estimate": 2,
        "lat": 36.3238,
        "lon": 129.0864,
        "map_x": 0.60,
        "map_y": 0.60,
        "notes": "교량 통제 시 북측 접근이 끊긴다",
        "tags": ["bridge"],
    },
    {
        "name": "주왕산면 상의리",
        "name_en": "Juwangsan",
        "emd_name": "주왕산면",
        "residents": 10,
        "households": 6,
        "assisted_mobility_estimate": 1,
        "lat": 36.3946,
        "lon": 129.1802,
        "map_x": 0.76,
        "map_y": 0.52,
        "notes": "정전·통신 장애가 반복되는 구간",
        "tags": ["comms"],
    },
]

SHELTERS = [
    {
        "name": "진보체육관",
        "address": "경상북도 청송군 진보면",
        "lat": 36.4361,
        "lon": 129.0451,
        "capacity": 320,
        "hazards": _ALL_WEATHER,
        "facility_type": "실내체육시설",
    },
    {
        "name": "부남면사무소",
        "address": "경상북도 청송군 부남면",
        "lat": 36.3241,
        "lon": 129.0871,
        "capacity": 80,
        "hazards": _ALL_WEATHER,
        "facility_type": "공공청사",
    },
    {
        "name": "주왕산 탐방안내소",
        "address": "경상북도 청송군 주왕산면",
        "lat": 36.3928,
        "lon": 129.1785,
        "capacity": 60,
        "hazards": ["heavy_rain", "flood", "typhoon"],
        "facility_type": "안내시설",
    },
]


async def seed_demo(session: AsyncSession) -> dict[str, int]:
    """이미 있으면 건드리지 않는다 — 시드를 다시 돌려도 안전하다."""
    created_communities = 0
    created_shelters = 0
    skipped = 0

    for entry in COMMUNITIES:
        exists = await session.scalar(
            select(Community).where(
                Community.region_code == REGION_CODE, Community.name == entry["name"]
            )
        )
        if exists is not None:
            skipped += 1
            continue
        session.add(
            Community(
                region_code=REGION_CODE,
                region_name=REGION_NAME,
                data_mode="synthetic",
                **entry,
            )
        )
        created_communities += 1

    for entry in SHELTERS:
        exists = await session.scalar(
            select(Shelter).where(Shelter.region_code == REGION_CODE, Shelter.name == entry["name"])
        )
        if exists is not None:
            skipped += 1
            continue
        session.add(
            Shelter(
                region_code=REGION_CODE,
                data_mode="synthetic",
                capacity_basis="demo",
                source_attribution="데모용 합성 데이터 — 공식 대피소 지정 현황이 아닙니다",
                **entry,
            )
        )
        created_shelters += 1

    await session.flush()
    return {
        "communities": created_communities,
        "shelters": created_shelters,
        "skipped": skipped,
    }
