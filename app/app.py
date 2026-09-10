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

from remote_web import (
    REMOTE_WEB_SECRET,
    REMOTE_WEB_BASE_DOMAIN,
    REMOTE_WEB_COOKIE_DOMAIN,
    REMOTE_WEB_ALLOWED_CIDR,
    REMOTE_WEB_NETWORK,
    REMOTE_WEB_TTL,
    REMOTE_WEB_COOKIE_NAME,
    remote_web_cookie_scope_valid,
    remote_web_make_token,
    remote_web_clear_cookie,
    remote_web_verify_token,
    remote_web_host_for_ip,
    remote_web_miner_for_host,
)

from db import (
    db,
    get_setting,
    set_setting,
    get_miner,
    get_poll_miners,
    get_control_miners,
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
    sanitize_audit_username,
    current_audit_actor,
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
from fastapi.responses import HTMLResponse, Response, RedirectResponse

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



@app.get(
    "/api/audit/whoami"
)
def api_audit_whoami():

    return {
        "actor":
            current_audit_actor(),
    }


@app.post(
    "/api/audit/test"
)
def api_audit_test():

    actor = (
        current_audit_actor()
    )


    log_event(
        source="MANUAL",
        action="AUDIT_TEST",
        success=True,
        message=(
            "Audit identity test"
        ),
    )


    return {
        "success":
            True,

        "actor":
            actor,
    }



@app.get(
    "/api/auth/relogin"
)
def api_auth_relogin(
    from_user: str = "",
):

    actor = (
        current_audit_actor()
    )


    # Direct localhost / PuTTY access
    # does not use nginx Basic Auth.

    if not actor.startswith(
        "WEB:"
    ):

        return HTMLResponse(
            content=(
                "User switching is only "
                "available through HTTPS."
            ),
            status_code=400,
            headers={
                "Cache-Control":
                    "no-store",
            },
        )


    current_user = (
        actor[
            len("WEB:"):
        ]
    )


    previous_user = (
        sanitize_audit_username(
            from_user
        )
    )


    if not previous_user:

        return HTMLResponse(
            content="Missing current user.",
            status_code=400,
            headers={
                "Cache-Control":
                    "no-store",
            },
        )


    # The currently cached Basic Auth
    # credentials are intentionally rejected.
    #
    # Browser receives a Basic challenge
    # for the same nginx realm and asks
    # for credentials again.

    if (
        current_user
        ==
        previous_user
    ):

        response = Response(
            status_code=401,
            headers={
                "WWW-Authenticate":
                    'Basic realm="OpenASICManager"',

                "Cache-Control":
                    (
                        "no-store, no-cache, "
                        "must-revalidate"
                    ),

                "Pragma":
                    "no-cache",
            },
        )


        remote_web_clear_cookie(
            response
        )


        return response


    # We reached this point after the browser
    # supplied another valid Basic Auth account.

    log_event(
        source="MANUAL",
        action="USER_SWITCH",
        success=True,
        message=(
            "Previous user: WEB:"
            +
            previous_user
        ),
    )


    response = RedirectResponse(
        url="/",
        status_code=302,
        headers={
            "Cache-Control":
                "no-store",
        },
    )


    remote_web_clear_cookie(
        response
    )


    return response



@app.get(
    "/remote/{miner_id}"
)
def api_remote_web_open(
    miner_id: int,
):

    if not app_config.REMOTE_WEB_ENABLED:

        raise HTTPException(
            status_code=503,
            detail=(
                "Remote ASIC Web is disabled"
            ),
        )


    if not remote_web_cookie_scope_valid():

        raise HTTPException(
            status_code=503,
            detail=(
                "REMOTE_WEB_COOKIE_DOMAIN "
                "is not valid for PUBLIC_DOMAIN"
            ),
        )


    actor = current_audit_actor()


    if not actor.startswith("WEB:"):

        raise HTTPException(
            status_code=403,
            detail=(
                "Remote Web requires "
                "authenticated HTTPS access"
            ),
        )


    miner = get_miner(
        miner_id
    )


    if not miner:

        raise HTTPException(
            status_code=404,
            detail="Miner not found",
        )


    remote_host = (
        remote_web_host_for_ip(
            miner["ip"]
        )
    )


    if not remote_host:

        raise HTTPException(
            status_code=400,
            detail=(
                "Miner IP is outside "
                "Remote Web network"
            ),
        )


    try:

        token = remote_web_make_token(
            actor
        )

    except RuntimeError as exc:

        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )


    log_event(
        source="MANUAL",
        action="REMOTE_WEB_OPEN",
        miner=miner,
        success=True,
        message=(
            "Remote host: "
            +
            remote_host
        ),
    )


    response = RedirectResponse(
        url=(
            "https://"
            +
            remote_host
            +
            "/"
        ),
        status_code=302,
    )


    response.set_cookie(
        key=REMOTE_WEB_COOKIE_NAME,
        value=token,

        max_age=REMOTE_WEB_TTL,

        path="/",

        domain=REMOTE_WEB_COOKIE_DOMAIN,

        secure=True,
        httponly=True,
        samesite="lax",
    )


    return response


@app.get(
    "/api/remote/authorize"
)
def api_remote_web_authorize(
    request: Request,
):

    if not app_config.REMOTE_WEB_ENABLED:

        return Response(
            status_code=404
        )


    token = request.cookies.get(
        REMOTE_WEB_COOKIE_NAME,
        "",
    )


    payload = (
        remote_web_verify_token(
            token
        )
    )


    if not payload:

        return Response(
            status_code=401
        )


    remote_host = (
        request.headers.get(
            "x-remote-host",
            "",
        )
    )


    miner = (
        remote_web_miner_for_host(
            remote_host
        )
    )


    if not miner:

        return Response(
            status_code=401
        )


    return Response(
        status_code=204,
        headers={
            "X-Remote-Actor":
                str(payload["actor"]),
        },
    )






# ============================================================
# API
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.1.2",
        "time":
            datetime.now(
                MOSCOW
            ).isoformat(),
    }


@app.get("/api/status")
def api_status():
    rows = list_miners()
    active_job_rows = list_active_control_jobs()

    now = datetime.now(
        MOSCOW
    )
    upcoming = next_transition(
        now
    )

    return {
        "version": "0.1.2",
        "now": now.isoformat(),
        "scheduler_enabled": (
            get_setting(
                "scheduler_enabled",
                "0",
            )
            == "1"
        ),
        "desired_state": desired_state(
            now
        ),
        "next_transition": (
            upcoming.isoformat()
            if upcoming
            else None
        ),
        "miners": miner_status_items(
            rows,
            active_job_rows,
        ),
    }





# ============================================================
# SCHEDULE RULES API
# ============================================================

















@app.post(
    "/api/miners/{miner_id}/control/{action}"
)
def miner_action(
    miner_id: int,
    action: str,
):

    if action not in (
        "pause",
        "resume",
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid action",
        )


    try:

        result = queue_control(
            miner_id,
            action,
            manual=True,
        )


        return {
            "success": True,
            **result,
        }


    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        )


@app.post(
    "/api/all/{action}"
)
def all_action(action: str):

    if action not in (
        "pause",
        "resume",
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid action",
        )


    miners = (
        get_control_miners()
    )


    results = []


    for miner in miners:

        try:

            result = queue_control(
                miner["id"],
                action,
                manual=True,
            )

            results.append({
                "ip":
                    miner["ip"],

                "success":
                    True,

                **result,
            })


        except Exception as exc:

            results.append({
                "ip":
                    miner["ip"],

                "success":
                    False,

                "error":
                    str(exc),
            })


    return {
        "results": results
    }






























@app.post(
    "/api/miners/{miner_id}/reboot"
)
def api_miner_reboot(
    miner_id: int,
):

    try:

        result = queue_reboot(
            miner_id
        )


        return {
            "success":
                True,

            **result,
        }


    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        )











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
