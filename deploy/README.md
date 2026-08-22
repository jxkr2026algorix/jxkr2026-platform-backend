# 배포

서버: `ubuntu@salgil-aws`. 공개 주소: `https://api.salgil.gyeongbuk.kr`

같은 머신에 GB SafeData(`datainfra.salgil.gyeongbuk.kr`)가 함께 떠 있다.

## 자동 배포

**손으로 배포할 일이 없다.** `main` 에 푸시하면 서버가 5초 안에 알아채고 반영한다.

```
푸시 → 감지 ~7초 → 빌드·교체 → 완료 ~20초
```

서버의 systemd 타이머(`salgil-autodeploy`)가 `git ls-remote` 로 `main` 의 SHA 만
물어보고, 로컬과 다를 때만 fetch 해서 재배포한다. 저장소가 공개라 자격증명이 없고,
서버는 밖으로 나가서 가져오기만 하므로 GitHub 에 배포키를 넣거나 인바운드를 열지
않는다.

```bash
sudo journalctl -u salgil-autodeploy -f      # 배포 로그
systemctl list-timers salgil-autodeploy.timer
```

빌드가 실패하면 **이전 컨테이너를 그대로 둔다.** 깨진 커밋이 서비스를 죽이지 않는다.

## 손으로 배포해야 할 때

`.env` 를 바꿨을 때다. `.env` 는 서버에만 있고 저장소에 없으므로 푸시로 전달되지 않는다.

```bash
ssh ubuntu@salgil-aws
cd /opt/platform-backend
vi .env
docker compose -f docker-compose.yml -f docker-compose.deploy.yml up -d --force-recreate api
```

## 이 서버에서만 다른 것

[`docker-compose.deploy.yml`](../docker-compose.deploy.yml) 이 기본 compose 위에 얹힌다.

| | 기본 | 여기 |
| --- | --- | --- |
| Postgres | 자체 `db` 컨테이너 | salgil-infra 스택의 것을 쓰고 `platform` DB 를 따로 팠다 |
| 포트 | `8000` | `127.0.0.1:8001` — 8000 은 gbsafedata 가 쓴다 |
| TLS | — | 앞단 Caddy 가 맡는다 |

**포트를 루프백에만 연다.** 이 호스트는 공인 IPv4 가 있어서 `0.0.0.0` 에 걸면
Cloudflare 를 우회해 오리진에 직접 닿는 경로가 생긴다.

`ports` 에 `!override` 가 붙어 있는데 빼면 안 된다. compose 는 두 파일의 `ports` 를
**합치기** 때문에, 기본의 `0.0.0.0:8001` 과 여기의 `127.0.0.1:8001` 이 같은 포트를 두 번
잡고 기동이 실패한다. 그때 나오는 "address already in use" 는 점유자가 자기 자신이라는
말을 하지 않아서 원인을 찾기 어렵다.

## 반영 확인

```bash
curl -s https://api.salgil.gyeongbuk.kr/readyz | jq
```

`database` · `gbsafedata` · `mlengine` 셋 다 `ok: true` 여야 한다.

**컨테이너가 healthy 인 것으로 배포 성공을 판단하지 않는다.** 빌드가 성공하고 recreate 가
실패하면 옛 컨테이너가 그대로 살아 있고 헬스체크도 통과한다. 응답 내용으로 본다.

## Caddy

리버스 프록시는 이 compose 에 없다. 서버의 여러 서비스가 공유하므로 시스템 서비스로 돈다.
이 서비스의 설정은 [`deploy/caddy/api.caddy`](caddy/api.caddy) 이고 서버의
`/etc/caddy/sites/api.caddy` 에 대응한다.

```bash
sudo systemctl reload caddy      # 무중단. 다른 서비스 연결이 끊기지 않는다
```

**Cloudflare SSL/TLS 모드는 `Full` 이어야 한다.** `Full (strict)` 는 실패한다 — 오리진
인증서가 Caddy 내부 CA 자체서명이다.
