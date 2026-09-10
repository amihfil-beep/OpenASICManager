#!/usr/bin/env python3

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from anomalies.service import AnomalyRuntime
from api.audit_auth import create_audit_auth_router
from api.control import create_control_router
from api.discovery import create_discovery_router
from api.inventory import create_inventory_router
from api.notifications import create_notifications_router
from api.operations import create_operations_router
from api.remote_web import create_remote_web_router
from api.scheduler import create_scheduler_router
from api.system import create_system_router
from api.telemetry import create_telemetry_router
from audit.identity import (
    audit_actor_from_remote_user,
    audit_source,
    bind_audit_actor,
    reset_audit_actor,
)
from audit.service import AuditRuntime
from control.queue import QueueRuntime
from control.worker import ControlRuntime
from db import init_db
from monitoring.service import MonitoringRuntime
from notifications.telegram import TelegramRuntime, telegram_event_async
from scheduler.repository import desired_state, next_transition
from scheduler.service import SchedulerRuntime
from telemetry.service import TelemetryRuntime
from ui.dashboard import dashboard_html


# Shared application lifecycle signal used by background services.
stop_event = threading.Event()


# Runtime composition.
audit_runtime = AuditRuntime(
    notify_event=telegram_event_async,
)
log_event = audit_runtime.log_event

telegram_runtime = TelegramRuntime(
    log_event=log_event,
    stop_event=stop_event,
)
telegram_summary_loop = telegram_runtime.summary_loop

telemetry_runtime = TelemetryRuntime(
    log_event=log_event,
    stop_event=stop_event,
)
telemetry_loop = telemetry_runtime.run

monitoring_runtime = MonitoringRuntime(
    stop_event=stop_event,
)
poll_miner = monitoring_runtime.poll_miner
polling_loop = monitoring_runtime.run
delayed_poll = monitoring_runtime.delayed_poll

control_runtime = ControlRuntime(
    poll_miner=poll_miner,
    log_event=log_event,
    stop_event=stop_event,
)

queue_runtime = QueueRuntime(
    audit_source=audit_source,
    log_event=log_event,
    next_transition=next_transition,
    control_runtime=control_runtime,
)
queue_control = queue_runtime.queue_control
queue_reboot = queue_runtime.queue_reboot

scheduler_runtime = SchedulerRuntime(
    log_event=log_event,
    queue_control=queue_control,
    stop_event=stop_event,
)
scheduler_loop = scheduler_runtime.run

anomaly_runtime = AnomalyRuntime(
    log_event=log_event,
    stop_event=stop_event,
    desired_state=desired_state,
)
anomaly_loop = anomaly_runtime.run


@asynccontextmanager
async def lifespan(app):
    init_db()
    stop_event.clear()

    poll_thread = threading.Thread(
        target=polling_loop,
        name="asic-poller",
        daemon=True,
    )
    schedule_thread = threading.Thread(
        target=scheduler_loop,
        name="asic-scheduler",
        daemon=True,
    )
    telemetry_thread = threading.Thread(
        target=telemetry_loop,
        name="asic-telemetry",
        daemon=True,
    )
    anomaly_thread = threading.Thread(
        target=anomaly_loop,
        name="asic-anomaly",
        daemon=True,
    )

    poll_thread.start()
    schedule_thread.start()
    telemetry_thread.start()
    anomaly_thread.start()

    yield

    stop_event.set()


app = FastAPI(
    title="OpenASICManager",
    lifespan=lifespan,
)

app.include_router(
    create_notifications_router(
        telegram_runtime
    )
)
app.include_router(
    create_scheduler_router(
        log_event
    )
)
app.include_router(
    create_telemetry_router()
)
app.include_router(
    create_operations_router()
)
app.include_router(
    create_discovery_router(
        poll_miner,
        log_event,
    )
)
app.include_router(
    create_inventory_router(
        poll_miner,
        log_event,
    )
)
app.include_router(
    create_control_router(
        queue_control,
        queue_reboot,
    )
)
app.include_router(
    create_remote_web_router(
        log_event
    )
)
app.include_router(
    create_audit_auth_router(
        log_event
    )
)
app.include_router(
    create_system_router()
)


@app.middleware("http")
async def audit_user_middleware(
    request,
    call_next,
):
    actor = audit_actor_from_remote_user(
        request.headers.get(
            "x-remote-user",
            "",
        )
    )
    token = bind_audit_actor(actor)

    try:
        return await call_next(request)
    finally:
        reset_audit_actor(token)


@app.on_event("startup")
def start_telegram_summary_loop():
    thread = threading.Thread(
        target=telegram_summary_loop,
        name="telegram-farm-summary",
        daemon=True,
    )
    thread.start()


@app.get(
    "/",
    response_class=HTMLResponse,
)
def index():
    return dashboard_html()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8088,
    )
