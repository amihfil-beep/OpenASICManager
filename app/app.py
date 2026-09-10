#!/usr/bin/env python3

import ipaddress
import json
import threading
import time
import os
import hmac
import hashlib
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config as app_config

from db import (
    db,
    get_setting,
    set_setting,
    get_miner,
    get_poll_miners,
    init_db,
    ensure_schedule_rules_schema,
)

from control.dispatch import (
    read_status,
)

from control.policy import (
    control_target,
)

from control.worker import (
    ControlRuntime,
)

from control.queue import (
    QueueRuntime,
)

from control.repository import (
    list_active_control_jobs,
    list_control_jobs,
)

from control.analytics import (
    control_job_items,
)

from scheduler.policy import (
    schedule_time_string,
    schedule_days_string,
    schedule_action_state,
    schedule_rule_next_run,
    schedule_rule_dict,
)

from scheduler.repository import (
    schedule_state_details,
    desired_state,
    schedule_conflicting_rule,
    next_transition,
    list_schedule_rules,
    get_schedule_rule,
    create_schedule_rule,
    update_schedule_rule,
    set_schedule_rule_enabled,
    delete_schedule_rule,
)

from scheduler.service import (
    SchedulerRuntime,
)

from scheduler.validation import (
    schedule_parse_bool,
    schedule_normalize_input,
)

from telemetry.repository import (
    TELEMETRY_RETENTION_DAYS,
    save_telemetry_snapshot,
    farm_history_rows,
    farm_problem_rows,
    farm_current_rows,
)

from telemetry.service import (
    TELEMETRY_INTERVAL,
    TelemetryRuntime,
)

from telemetry.analytics import (
    history_stats,
    miner_history_points,
    farm_history_points,
    farm_problem_miners,
    farm_current_summary,
)

from anomalies.policy import (
    ANOMALY_OFFLINE_GRACE,
    ANOMALY_HOT_TEMP,
    ANOMALY_HOT_CLEAR,
    ANOMALY_HOT_GRACE,
    ANOMALY_SCHEDULE_GRACE,
)

from anomalies.service import (
    AnomalyRuntime,
)

from anomalies.analytics import issue_report

from notifications.telegram import (
    TelegramRuntime,
    telegram_event_async,
)

from api.notifications import (
    create_notifications_router,
)

from api.scheduler import (
    create_scheduler_router,
)

from api.telemetry import (
    create_telemetry_router,
)

from api.operations import (
    create_operations_router,
)

from api.discovery import (
    create_discovery_router,
)

from api.inventory import (
    create_inventory_router,
)

from api.control import (
    create_control_router,
)

from api.remote_web import (
    create_remote_web_router,
)

from api.audit_auth import (
    create_audit_auth_router,
)

from api.system import (
    create_system_router,
)

from monitoring.service import (
    POLL_INTERVAL,
    MonitoringRuntime,
)

from inventory.repository import (
    list_miners,
)

from inventory.analytics import (
    miner_status_items,
)

from audit.identity import (
    audit_source,
    audit_actor_from_remote_user,
    bind_audit_actor,
    reset_audit_actor,
)

from audit.service import (
    AuditRuntime,
    action_log_entries,
)



from ui.dashboard import dashboard_html

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

# ============================================================
# USER AUDIT
# ============================================================














TIMEZONE_NAME = app_config.TIMEZONE

MOSCOW = ZoneInfo(
    TIMEZONE_NAME
)




# Awesome / AnthillOS

# Bitmain Stock
#
# Stock T21 can spend more than 3 minutes
# in STARTING before real hashrate appears.


# Full ASIC reboot verification

# How long we wait to observe the actual reboot transition.

# How long we allow the ASIC to come back.

# Require several consecutive successful polls after reboot.


# ============================================================
# TELEGRAM
# ============================================================















# Telegram farm summary





# Monday=0 ... Friday=4


# Historical telemetry

# Anomaly detection







stop_event = threading.Event()



# ============================================================
# DATABASE
# ============================================================















# ============================================================
# ACTION LOG
# ============================================================

audit_runtime = AuditRuntime(
    notify_event=telegram_event_async,
)

# Preserve the existing application-facing logging interface.
log_event = audit_runtime.log_event



telegram_runtime = TelegramRuntime(
    log_event=log_event,
    stop_event=stop_event,
)

# Preserve existing application-facing summary-loop interface.
telegram_summary_loop = (
    telegram_runtime.summary_loop
)


telemetry_runtime = TelemetryRuntime(
    log_event=log_event,
    stop_event=stop_event,
)


# Preserve existing thread target interface.
telemetry_loop = (
    telemetry_runtime.run
)



# ============================================================
# SCHEDULE
# ============================================================


# ============================================================
# SCHEDULE RULE ENGINE v1.5
# ============================================================




















































# ============================================================
# DRIVER
# ============================================================





# ============================================================
# MONITORING
# ============================================================

monitoring_runtime = MonitoringRuntime(
    stop_event=stop_event,
)

# Preserve existing application-facing interfaces.
poll_miner = (
    monitoring_runtime.poll_miner
)

polling_loop = (
    monitoring_runtime.run
)

delayed_poll = (
    monitoring_runtime.delayed_poll
)



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


# Preserve the existing application-facing call interface.
queue_control = (
    queue_runtime.queue_control
)

queue_reboot = (
    queue_runtime.queue_reboot
)


scheduler_runtime = SchedulerRuntime(
    log_event=log_event,
    queue_control=queue_control,
    stop_event=stop_event,
)


# Preserve existing thread target interface.
scheduler_loop = (
    scheduler_runtime.run
)







# ============================================================
# HISTORICAL TELEMETRY
# ============================================================





# ============================================================
# TELEGRAM NOTIFICATIONS
# ============================================================
































# ============================================================
# ANOMALY DETECTION
# ============================================================

anomaly_runtime = AnomalyRuntime(
    log_event=log_event,
    stop_event=stop_event,
    desired_state=desired_state,
)

# Preserve existing thread target interface.
anomaly_loop = (
    anomaly_runtime.run
)














# ============================================================
# FULL ASIC REBOOT
# ============================================================











# ============================================================
# VERIFIED CONTROL
# ============================================================

























# ============================================================
# SCHEDULER
# ============================================================



# ============================================================
# FASTAPI
# ============================================================

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




# ============================================================
# REMOTE ASIC WEB
# ============================================================
































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



@app.middleware(
    "http"
)
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

    token = bind_audit_actor(
        actor
    )

    try:
        response = await call_next(
            request
        )

        return response

    finally:
        reset_audit_actor(
            token
        )



















# ============================================================
# API
# ============================================================








# ============================================================
# SCHEDULE RULES API
# ============================================================




























































# ============================================================
# TELEGRAM FARM SUMMARY
# ============================================================

















@app.on_event("startup")
def start_telegram_summary_loop():

    thread = threading.Thread(
        target=telegram_summary_loop,
        name="telegram-farm-summary",
        daemon=True,
    )

    thread.start()






# ============================================================
# NOTIFICATION API
# ============================================================










# ============================================================
# ISSUES API
# ============================================================




# ============================================================
# FARM HISTORY API
# ============================================================



# ============================================================
# DISCOVERY API
# ============================================================






# ============================================================
# WEB
# ============================================================




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
