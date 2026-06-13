from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


def _is_missing_celery_import(exc: ModuleNotFoundError) -> bool:
    missing_name = exc.name or ""
    return missing_name == "celery" or missing_name.startswith("celery.")


try:
    from celery import Celery
    from celery.schedules import crontab
except ModuleNotFoundError as exc:  # pragma: no cover - exercised via import hook
    if not _is_missing_celery_import(exc):
        raise
    _CELERY_IMPORT_ERROR = exc

    class _FallbackCeleryConfig(dict):
        def __getattr__(self, name: str) -> Any:
            try:
                return self[name]
            except KeyError as exc:
                raise AttributeError(name) from exc

        def __setattr__(self, name: str, value: Any) -> None:
            self[name] = value

    class _FallbackTask:
        def __init__(self, func: Any, *, bind: bool, name: str | None = None) -> None:
            self._func = func
            self._bind = bind
            self.name = name or getattr(func, "__name__", "task")
            self.__name__ = getattr(func, "__name__", self.name)
            self.__doc__ = getattr(func, "__doc__", None)

        def run(self, *args: Any, **kwargs: Any) -> Any:
            if self._bind:
                return self._func(self, *args, **kwargs)
            return self._func(*args, **kwargs)

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return self.run(*args, **kwargs)

        def apply_async(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError(
                "celery is required to enqueue background tasks"
            ) from _CELERY_IMPORT_ERROR

        def delay(self, *args: Any, **kwargs: Any) -> None:
            return self.apply_async(args=args, kwargs=kwargs)

    @dataclass(frozen=True)
    class _FallbackCrontab:
        options: dict[str, Any]

    class Celery:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.conf = _FallbackCeleryConfig()

        def autodiscover_tasks(self, packages: list[str]) -> None:
            return None

        def task(self, *decorator_args: Any, **options: Any) -> Any:
            def decorate(func: Any) -> _FallbackTask:
                return _FallbackTask(
                    func,
                    bind=bool(options.get("bind")),
                    name=options.get("name"),
                )

            if decorator_args and callable(decorator_args[0]):
                return decorate(decorator_args[0])
            return decorate

    def crontab(**kwargs: Any) -> _FallbackCrontab:  # type: ignore[no-redef]
        return _FallbackCrontab(dict(kwargs))
else:
    _CELERY_IMPORT_ERROR = None

from codey.saas.redis_url import normalize_redis_url


def _coerce_autonomous_interval_minutes(value: str | None, default: int = 5) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(1, min(59, parsed))


_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
REDIS_URL = (
    normalize_redis_url(os.environ.get("REDIS_URL", _DEFAULT_REDIS_URL))
    or _DEFAULT_REDIS_URL
)
AUTONOMOUS_INTERVAL_MINUTES = _coerce_autonomous_interval_minutes(
    os.environ.get("CODEY_AUTONOMOUS_INTERVAL_MINUTES", "5")
)

celery_app = Celery(
    "codey",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_default_queue="default",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    result_expires=86400,  # 24 hours
    task_soft_time_limit=600,  # 10 minutes
    task_time_limit=900,  # 15 minutes hard limit
)

celery_app.conf.task_routes = {
    "codey.saas.tasks.autonomous.run_all_autonomous_repos": {"queue": "autonomous"},
    "codey.saas.tasks.autonomous.run_autonomous_repo": {"queue": "autonomous"},
    "codey.saas.tasks.billing.reset_monthly_credits": {"queue": "billing"},
    "codey.saas.tasks.billing.check_grace_period": {"queue": "billing"},
    "codey.saas.tasks.builds.run_build_phase": {"queue": "builds"},
}

# ---------------------------------------------------------------------------
# Auto-discover task modules
# ---------------------------------------------------------------------------
celery_app.autodiscover_tasks(
    [
        "codey.saas.tasks.autonomous",
        "codey.saas.tasks.billing",
        "codey.saas.tasks.builds",
    ]
)

# ---------------------------------------------------------------------------
# Beat schedule — recurring tasks
# ---------------------------------------------------------------------------
celery_app.conf.beat_schedule = {
    "scheduled-autonomous-repos": {
        "task": "codey.saas.tasks.autonomous.run_all_autonomous_repos",
        "schedule": crontab(minute=f"*/{AUTONOMOUS_INTERVAL_MINUTES}"),
        "options": {"queue": "autonomous"},
    },
    "daily-credit-reset": {
        "task": "codey.saas.tasks.billing.reset_monthly_credits",
        "schedule": crontab(hour=0, minute=5),  # 12:05 AM UTC daily (checks day-of-month)
        "options": {"queue": "billing"},
    },
    "hourly-grace-period-check": {
        "task": "codey.saas.tasks.billing.check_grace_period",
        "schedule": crontab(minute=30),  # every hour at :30
        "options": {"queue": "billing"},
    },
}
