from __future__ import annotations

import importlib

import codey.saas.intelligence.cache as cache_module
import codey.saas.tasks.celery_app as celery_app_module


def test_celery_app_tolerates_invalid_autonomous_interval_env(monkeypatch) -> None:
    monkeypatch.setenv("CODEY_AUTONOMOUS_INTERVAL_MINUTES", "not-a-number")

    reloaded = importlib.reload(celery_app_module)

    assert reloaded.AUTONOMOUS_INTERVAL_MINUTES == 5


def test_celery_app_falls_back_to_local_redis_when_env_is_whitespace(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "   ")

    reloaded = importlib.reload(celery_app_module)

    assert reloaded.REDIS_URL == "redis://localhost:6379/0"


def test_celery_routes_match_deployment_worker_queues() -> None:
    routes = celery_app_module.celery_app.conf.task_routes

    assert celery_app_module.celery_app.conf.task_default_queue == "default"
    assert routes["codey.saas.tasks.autonomous.run_all_autonomous_repos"] == {
        "queue": "autonomous",
    }
    assert routes["codey.saas.tasks.autonomous.run_autonomous_repo"] == {
        "queue": "autonomous",
    }
    assert routes["codey.saas.tasks.billing.reset_monthly_credits"] == {
        "queue": "billing",
    }
    assert routes["codey.saas.tasks.billing.check_grace_period"] == {
        "queue": "billing",
    }
    assert routes["codey.saas.tasks.builds.run_build_phase"] == {
        "queue": "builds",
    }


def test_celery_worker_requeues_lost_late_ack_tasks() -> None:
    conf = celery_app_module.celery_app.conf

    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.broker_connection_retry_on_startup is True


def test_intelligence_cache_falls_back_to_local_redis_when_env_is_whitespace(
    monkeypatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "   ")

    reloaded = importlib.reload(cache_module)

    assert reloaded.REDIS_URL == "redis://localhost:6379/0"
