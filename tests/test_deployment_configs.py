from __future__ import annotations

from pathlib import Path


def test_ci_workflow_sets_up_node_before_frontend_build() -> None:
    workflow = Path(".github/workflows/deploy.yml").read_text(encoding="utf-8")

    setup_node_pos = workflow.index("uses: actions/setup-node@v4")
    npm_ci_pos = workflow.index("npm ci")

    assert setup_node_pos < npm_ci_pos
    assert 'node-version: "20"' in workflow
    assert "cache: npm" in workflow
    assert "cache-dependency-path: frontend/package-lock.json" in workflow


def test_dockerignore_excludes_secret_env_files_from_build_context() -> None:
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    patterns = {
        line.strip()
        for line in dockerignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert ".env" in patterns
    assert ".env.*" in patterns
    assert "!.env.example" in patterns
    assert "!.env.prod.example" in patterns


def test_frontend_dockerignore_excludes_generated_build_artifacts() -> None:
    dockerignore = Path("frontend/.dockerignore").read_text(encoding="utf-8")
    patterns = {
        line.strip()
        for line in dockerignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert "node_modules" in patterns
    assert ".next" in patterns
    assert "tsconfig.tsbuildinfo" in patterns
    assert ".env" in patterns
    assert ".env.*" in patterns
    assert "!.env.example" in patterns


def test_compose_frontends_pass_public_stripe_build_arg() -> None:
    compose_paths = [
        Path("docker-compose.yml"),
        Path("docker-compose.prod.yml"),
        Path("docker-compose.droplet.yml"),
    ]

    for compose_path in compose_paths:
        if not compose_path.exists():
            continue
        frontend = _compose_service_block(
            compose_path.read_text(encoding="utf-8"),
            "frontend",
        )

        assert "args:" in frontend, compose_path
        assert (
            "NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY: "
            "${NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY:-}"
        ) in frontend, compose_path
        assert (
            "NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY: ${STRIPE_PUBLISHABLE_KEY"
            not in frontend
        ), compose_path


def test_env_examples_include_next_public_stripe_key() -> None:
    for env_path in (Path(".env.example"), Path(".env.prod.example")):
        env_text = env_path.read_text(encoding="utf-8")

        assert "NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=" in env_text, env_path


def test_gitignore_excludes_secret_env_variants() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    patterns = {
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert ".env" in patterns
    assert ".env.*" in patterns
    assert "!.env.example" in patterns
    assert "!.env.prod.example" in patterns


def test_gitignore_excludes_generated_developer_artifacts() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    patterns = {
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert ".playwright-cli/" in patterns
    assert ".pytest_cache/" in patterns
    assert ".venv/" in patterns
    assert "vscode-extension/out/" in patterns
    assert "vscode-extension/*.vsix" in patterns


def test_deep_scan_uses_configurable_python3_interpreter() -> None:
    script = Path("deploy/codey-deep-scan.sh").read_text(encoding="utf-8")

    assert 'BACKEND_PYTHON_BIN="${CODEY_BACKEND_PYTHON_BIN:-python3}"' in script
    assert (
        'env PYTHONPATH=/app "$BACKEND_PYTHON_BIN" '
        "/app/scripts/deep_repo_scan.py /app"
    ) in script
    assert "env PYTHONPATH=/app python /app/scripts/deep_repo_scan.py /app" not in script


def test_deep_scan_uses_configurable_docker_binary() -> None:
    script = Path("deploy/codey-deep-scan.sh").read_text(encoding="utf-8")

    assert 'DOCKER_BIN="${CODEY_DOCKER_BIN:-/usr/bin/docker}"' in script
    assert '"$DOCKER_BIN" compose -f "$COMPOSE_FILE" up -d backend' in script
    assert '"$DOCKER_BIN" compose -f "$COMPOSE_FILE" exec -T backend' in script
    assert "/usr/bin/docker compose" not in script


def test_deep_scan_preserves_docker_startup_stderr() -> None:
    script = Path("deploy/codey-deep-scan.sh").read_text(encoding="utf-8")

    assert '"$DOCKER_BIN" compose -f "$COMPOSE_FILE" up -d backend >/dev/null' in script
    assert ">/dev/null 2>&1" not in script


def test_codex_loop_json_helpers_tolerate_missing_jsonl_files() -> None:
    script = Path("deploy/codey-codex-loop.sh").read_text(encoding="utf-8")

    assert "except OSError:\n    raise SystemExit(0)" in script
    assert 'except OSError:\n    print("no")\n    raise SystemExit(0)' in script


def test_codex_loop_handles_stale_lock_reacquire_race() -> None:
    script = Path("deploy/codey-codex-loop.sh").read_text(encoding="utf-8")

    assert 'if ! mkdir "$LOCK_DIR" 2>/dev/null; then' in script
    assert 'log "loop lock reacquired by another process"' in script
    assert 'exit 0\n  fi\n  echo "$$" > "$LOCK_DIR/pid"' in script


def _compose_service_block(compose_text: str, service_name: str) -> str:
    marker = f"  {service_name}:\n"
    start = compose_text.index(marker)
    next_service = compose_text.find("\n  ", start + len(marker))
    while next_service != -1 and compose_text[next_service + 3].isspace():
        next_service = compose_text.find("\n  ", next_service + 1)
    if next_service == -1:
        return compose_text[start:]
    return compose_text[start:next_service]


def test_default_compose_celery_beat_waits_for_postgres() -> None:
    compose_text = Path("docker-compose.yml").read_text(encoding="utf-8")

    celery_beat = _compose_service_block(compose_text, "celery_beat")

    assert "depends_on:" in celery_beat
    assert "      postgres:\n        condition: service_healthy" in celery_beat
    assert "      redis:\n        condition: service_healthy" in celery_beat


def test_present_compose_variants_celery_beat_waits_for_postgres() -> None:
    compose_paths = [
        Path("docker-compose.yml"),
        Path("docker-compose.prod.yml"),
        Path("docker-compose.droplet.yml"),
    ]

    for compose_path in compose_paths:
        if not compose_path.exists():
            continue
        celery_beat = _compose_service_block(
            compose_path.read_text(encoding="utf-8"),
            "celery_beat",
        )

        assert "depends_on:" in celery_beat, compose_path
        assert "      postgres:\n        condition: service_healthy" in celery_beat, compose_path
        assert "      redis:\n        condition: service_healthy" in celery_beat, compose_path
