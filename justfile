compose_file := "./docker-compose.local.yaml"

init:
    uv sync

check:
    set -e; \
    uv run ruff format; \
    uv run ruff check --fix; \
    uv run mypy backend/ tests/; \
    uv run python .claude/hooks/guard_layers.py < /dev/null

stop-reminder:
    uv run python .claude/hooks/stop_reminder.py

stop-reflect:
    uv run python .claude/hooks/stop_reflection.py

plan-guard:
    uv run python .claude/hooks/plan_guard.py

workflow-reminder:
    uv run python .claude/hooks/workflow_reminder.py

caveman-activate:
    node .claude/hooks/caveman-activate.js

caveman-track:
    node .claude/hooks/caveman-mode-tracker.js

notify:
    uv run python .claude/hooks/notify.py

status:
    @echo "branch: $(git branch --show-current)"
    @git diff --stat HEAD 2>/dev/null || true
    @echo "---"
    @docker compose -f {{ compose_file }} ps 2>/dev/null || echo "docker: not running"

test:
    uv run nox -s integration

test-unit:
    uv run nox -s unit

test-all:
    uv run nox --tags tests

flusher:
    uv run backend flusher

start *svc:
    docker compose -f {{ compose_file }} up -d {{ svc }}

stop:
    docker compose -f {{ compose_file }} stop

delete:
    docker compose -f {{ compose_file }} down -v

logs:
    docker compose -f {{ compose_file }} logs

migrate:
    uv run backend alembic upgrade head

migration msg:
    uv run backend alembic revision --autogenerate -m "{{ msg }}"
