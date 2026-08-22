"""운영 CLI.

uv run salgil seed          데모용 마을·대피소 적재 (전부 synthetic 표시)
uv run salgil check         상류 연결 점검
uv run salgil routes        라우트 목록
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging


async def _seed() -> int:
    from app.db.session import get_sessionmaker
    from app.services.seed import seed_demo

    factory = get_sessionmaker()
    async with factory() as session:
        result = await seed_demo(session)
        await session.commit()
    print(
        f"마을 {result['communities']}건, 대피소 {result['shelters']}건 적재 "
        f"(기존 유지 {result['skipped']}건)"
    )
    print("모두 data_mode=synthetic 입니다 — 실시간 화면에서 실데이터와 섞이면 안 됩니다.")
    return 0


async def _check() -> int:
    from app.clients.gbsafe import GbSafeClient
    from app.clients.mlengine import MlEngineClient

    settings = get_settings()
    gb = GbSafeClient(settings)
    ml = MlEngineClient(settings)
    exit_code = 0
    try:
        ok, detail, latency = await gb.ping()
        print(
            f"gbsafedata  {'OK ' if ok else 'FAIL'}  {settings.gbsafe_base_url}"
            f"  {f'{latency}ms' if latency else ''}  {detail or ''}"
        )
        if not ok:
            exit_code = 1

        ok, detail, latency = await ml.ping()
        target = settings.mlengine_base_url if settings.mlengine_mode == "http" else "stub"
        print(
            f"mlengine    {'OK ' if ok else 'FAIL'}  {target}"
            f"  {f'{latency}ms' if latency else ''}  {detail or ''}"
        )
        if not ok:
            exit_code = 1
    finally:
        await gb.aclose()
        await ml.aclose()
    return exit_code


def _routes() -> int:
    from app.main import app

    # 라우트 트리를 직접 걷지 않는다 — Starlette 버전마다 중첩 구조가 다르다.
    paths = app.openapi()["paths"]
    for path in sorted(paths):
        methods = ",".join(sorted(m.upper() for m in paths[path]))
        print(f"{methods:<18} {path}")
    return 0


def _push_keys() -> int:
    """VAPID 한 쌍을 만들어 출력한다. 저장하지 않는다 — 어디에 둘지는 사람이 정한다."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1())
    raw_public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    raw_private = private.private_numbers().private_value.to_bytes(32, "big")

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    print("# .env 에 넣으세요. 비밀키는 저장소에 커밋하지 마세요 —")
    print("# 그것을 가진 사람은 이 도메인 이름으로 주민 잠금화면에 무엇이든 띄웁니다.")
    print(f"SALGIL_VAPID_PUBLIC_KEY={b64(raw_public)}")
    print(f"SALGIL_VAPID_PRIVATE_KEY={b64(raw_private)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="salgil", description="SALGIL 플랫폼 백엔드 CLI")
    parser.add_argument("command", choices=["seed", "check", "routes", "push-keys"])
    args = parser.parse_args(argv)

    configure_logging(get_settings())

    if args.command == "seed":
        return asyncio.run(_seed())
    if args.command == "check":
        return asyncio.run(_check())
    if args.command == "push-keys":
        return _push_keys()
    return _routes()


if __name__ == "__main__":
    sys.exit(main())
