#!/bin/sh
# M15: docker compose up만으로 구 스키마로 조용히 뜨는 문제를 막는다 — 컨테이너
# 기동 시 alembic upgrade head를 먼저 실행하고 나서야 실제 프로세스(uvicorn 등)를
# 넘겨받는다. docker-compose.yml에 depends_on: condition: service_healthy가 이미
# db 준비를 보장하지만, healthcheck 통과 직후의 짧은 경합에 대비해 방어적으로
# 재시도한다.
set -e

max_attempts=10
attempt=0
until alembic upgrade head; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "alembic upgrade head 실패 — ${max_attempts}회 재시도 후 포기" >&2
    exit 1
  fi
  echo "alembic upgrade head 실패 — 재시도 ${attempt}/${max_attempts} (3초 후)" >&2
  sleep 3
done

exec "$@"
