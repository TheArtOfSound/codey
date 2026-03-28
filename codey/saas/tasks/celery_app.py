from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "codey",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,  # 24 hours
    task_soft_time_limit=600,  # 10 minutes
    task_time_limit=900,  # 15 minutes hard limit
)

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
    "nightly-autonomous-repos": {
        "task": "codey.saas.tasks.autonomous.run_all_autonomous_repos",
        "schedule": crontab(hour=3, minute=0),  # 3:00 AM UTC nightly
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
