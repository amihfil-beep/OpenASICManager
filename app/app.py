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

from config import (
    BITMAIN_USERNAME,
    BITMAIN_PASSWORD,
    AWESOME_USERNAME,
    AWESOME_PASSWORD,
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

from discovery import scan_network, detect_host

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

from monitoring.service import (
    POLL_INTERVAL,
    MonitoringRuntime,
)

from inventory.repository import (
    list_miners,
    set_miner_enabled,
    set_miner_schedule_enabled,
    set_all_schedule_enabled,
    clear_manual_overrides,
    set_miner_manual_driver,
    set_miner_detection_auto,
    set_miner_manual_firmware,
    set_miner_detected_firmware,
    convert_unconfigured_to_stock,
    list_discovery_miners,
    get_miner_by_ip,
    miner_name_exists,
    create_discovered_miner,
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
    "/api/miners/{miner_id}/driver"
)
def set_driver(
    miner_id: int,
    payload: dict,
):

    # Legacy compatibility endpoint.
    # Manual driver selection now means MANUAL lock.

    driver = str(
        payload.get(
            "driver",
            "",
        )
    ).strip()


    if driver not in (
        "unset",
        "awesome",
        "bitmain_stock",
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid driver",
        )


    miner = get_miner(
        miner_id
    )


    if not miner:

        raise HTTPException(
            status_code=404,
            detail="Miner not found",
        )


    if driver == "awesome":

        username = AWESOME_USERNAME
        password = AWESOME_PASSWORD

    elif driver == "bitmain_stock":

        username = BITMAIN_USERNAME
        password = BITMAIN_PASSWORD

    else:

        username = ""
        password = ""


    set_miner_manual_driver(
        miner_id,
        driver,
        username,
        password,
    )


    updated = get_miner(
        miner_id
    )


    log_event(
        source="SYSTEM",
        action="FIRMWARE_MANUAL_DRIVER",
        miner=updated,
        success=True,
        message=(
            f"Manual firmware mode; "
            f"driver={driver}"
        ),
    )


    if driver != "unset":

        threading.Thread(
            target=poll_miner,
            args=(
                miner_id,
            ),
            daemon=True,
        ).start()


    return {
        "success": True,
        "driver": driver,
        "detection_mode": "MANUAL",
    }


@app.post(
    "/api/miners/{miner_id}/firmware-settings"
)
def firmware_settings(
    miner_id: int,
    payload: dict,
):

    miner = get_miner(
        miner_id
    )


    if not miner:

        raise HTTPException(
            status_code=404,
            detail="Miner not found",
        )


    mode = str(
        payload.get(
            "detection_mode",
            "AUTO",
        )
    ).strip().upper()


    if mode not in (
        "AUTO",
        "MANUAL",
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Detection mode must be "
                "AUTO or MANUAL"
            ),
        )


    if mode == "AUTO":

        set_miner_detection_auto(
            miner_id
        )


        updated = get_miner(
            miner_id
        )


        log_event(
            source="SYSTEM",
            action="FIRMWARE_MODE_AUTO",
            miner=updated,
            success=True,
            message=(
                "Firmware detection mode "
                "changed to AUTO"
            ),
        )


        return {
            "success": True,
            "detection_mode": "AUTO",
        }


    # --------------------------------------------------------
    # MANUAL
    # --------------------------------------------------------

    driver = str(
        payload.get(
            "driver",
            miner["driver"],
        )
    ).strip()


    if driver not in (
        "unset",
        "awesome",
        "bitmain_stock",
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid driver",
        )


    model = str(
        payload.get(
            "model",
            miner["model"]
            or "",
        )
        or ""
    ).strip()


    firmware = str(
        payload.get(
            "firmware",
            miner["firmware"]
            or "",
        )
        or ""
    ).strip()


    if len(model) > 120:

        raise HTTPException(
            status_code=400,
            detail="Model is too long",
        )


    if len(firmware) > 160:

        raise HTTPException(
            status_code=400,
            detail="Firmware is too long",
        )


    if driver == "awesome":

        username = AWESOME_USERNAME
        password = AWESOME_PASSWORD

    elif driver == "bitmain_stock":

        username = BITMAIN_USERNAME
        password = BITMAIN_PASSWORD

    else:

        username = ""
        password = ""


    driver_changed = (
        driver
        !=
        miner["driver"]
    )


    set_miner_manual_firmware(
        miner_id,
        driver,
        username,
        password,
        model,
        firmware,
        driver_changed,
    )


    updated = get_miner(
        miner_id
    )


    log_event(
        source="SYSTEM",
        action="FIRMWARE_MODE_MANUAL",
        miner=updated,
        success=True,
        message=(
            f"Manual firmware configuration; "
            f"driver={driver}; "
            f"model={model or '-'}; "
            f"firmware={firmware or '-'}"
        ),
    )


    if driver != "unset":

        threading.Thread(
            target=poll_miner,
            args=(
                miner_id,
            ),
            daemon=True,
        ).start()


    return {
        "success": True,
        "detection_mode": "MANUAL",
        "driver": driver,
        "model": model or None,
        "firmware": firmware or None,
    }


@app.post(
    "/api/miners/{miner_id}/firmware-detect"
)
def firmware_detect_now(
    miner_id: int,
):

    miner = get_miner(
        miner_id
    )


    if not miner:

        raise HTTPException(
            status_code=404,
            detail="Miner not found",
        )


    detected = None


    for attempt in range(3):

        try:

            detected = detect_host(
                miner["ip"]
            )

        except Exception:

            detected = None


        if (
            detected
            and
            detected.get("driver")
            in (
                "awesome",
                "bitmain_stock",
            )
        ):
            break


        if attempt < 2:

            time.sleep(
                0.75
            )


    if (
        not detected
        or
        detected.get("driver")
        not in (
            "awesome",
            "bitmain_stock",
        )
    ):

        raise HTTPException(
            status_code=503,
            detail=(
                "Firmware could not be "
                "detected after 3 attempts"
            ),
        )


    driver = detected[
        "driver"
    ]

    model = (
        detected.get(
            "model"
        )
        or miner["model"]
    )

    firmware = (
        detected.get(
            "firmware"
        )
        or miner["firmware"]
    )


    if driver == "awesome":

        username = AWESOME_USERNAME
        password = AWESOME_PASSWORD

    else:

        username = BITMAIN_USERNAME
        password = BITMAIN_PASSWORD


    driver_changed = (
        driver
        !=
        miner["driver"]
    )


    set_miner_detected_firmware(
        miner_id,
        driver,
        username,
        password,
        model,
        firmware,
        driver_changed,
    )


    updated = get_miner(
        miner_id
    )


    log_event(
        source="SYSTEM",
        action="FIRMWARE_AUTO_DETECT",
        miner=updated,
        success=True,
        message=(
            f"Auto-detected "
            f"{driver}; "
            f"model={model or '-'}; "
            f"firmware={firmware or '-'}"
        ),
    )


    threading.Thread(
        target=poll_miner,
        args=(
            miner_id,
        ),
        daemon=True,
    ).start()


    return {
        "success": True,
        "detection_mode": "AUTO",
        "driver": driver,
        "model": model,
        "firmware": firmware,
    }




@app.post(
    "/api/miners/{miner_id}/enabled"
)
def toggle_enabled(
    miner_id: int,
):
    miner = get_miner(
        miner_id
    )

    if not miner:
        raise HTTPException(
            status_code=404,
            detail="Miner not found",
        )

    new_value = not bool(
        miner["enabled"]
    )

    set_miner_enabled(
        miner_id,
        new_value,
    )

    if new_value:
        threading.Thread(
            target=poll_miner,
            args=(miner_id,),
            daemon=True,
        ).start()

    return {
        "enabled": new_value
    }



@app.post(
    "/api/miners/{miner_id}/schedule"
)
def toggle_schedule(
    miner_id: int,
):
    miner = get_miner(
        miner_id
    )

    if not miner:
        raise HTTPException(
            status_code=404,
            detail="Miner not found",
        )

    if miner["driver"] == "unset":
        raise HTTPException(
            status_code=400,
            detail="Configure firmware first",
        )

    new_value = not bool(
        miner["schedule_enabled"]
    )

    set_miner_schedule_enabled(
        miner_id,
        new_value,
    )

    return {
        "schedule_enabled": new_value
    }



@app.post(
    "/api/bulk/unconfigured-stock"
)
def unconfigured_to_stock():
    ids = convert_unconfigured_to_stock(
        app_config.BITMAIN_USERNAME,
        app_config.BITMAIN_PASSWORD,
    )

    for miner_id in ids:

        threading.Thread(
            target=poll_miner,
            args=(
                miner_id,
            ),
            daemon=True,
        ).start()

    return {
        "success": True,
        "updated": len(ids),
    }


@app.post(
    "/api/schedule/all/{state}"
)
def schedule_all(state: str):
    if state not in (
        "on",
        "off",
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid state",
        )

    enabled = state == "on"
    changed = set_all_schedule_enabled(
        enabled
    )

    log_event(
        source="SYSTEM",
        action=(
            "SCHEDULE_ALL_ON"
            if enabled
            else "SCHEDULE_ALL_OFF"
        ),
        success=True,
        message=f"Rows changed: {changed}",
    )

    return {
        "success": True,
        "schedule_enabled": enabled,
        "changed": changed,
    }



@app.post(
    "/api/overrides/clear"
)
def clear_overrides():
    changed = clear_manual_overrides()

    log_event(
        source="SYSTEM",
        action="CLEAR_OVERRIDES",
        success=True,
        message=f"Rows changed: {changed}",
    )

    return {
        "success": True,
        "changed": changed,
    }



@app.get(
    "/api/logs"
)
def api_logs(
    limit: int = 100,
):
    return {
        "logs": action_log_entries(
            limit,
            MOSCOW,
        )
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


@app.get(
    "/api/control/jobs"
)
def api_control_jobs(
    limit: int = 100,
):
    limit = max(
        1,
        min(
            int(limit),
            500,
        ),
    )

    rows = list_control_jobs(
        limit
    )

    return {
        "jobs": control_job_items(
            rows
        )
    }









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

@app.get(
    "/api/issues"
)
def api_issues(
    limit: int = 100,
):
    limit = max(
        1,
        min(int(limit), 500),
    )
    return issue_report(limit)



# ============================================================
# FARM HISTORY API
# ============================================================



# ============================================================
# DISCOVERY API
# ============================================================

@app.post(
    "/api/discovery/scan"
)
def discovery_scan(
    payload: dict,
):
    network = str(
        payload.get(
            "network",
            "",
        )
    ).strip()

    if not network:

        raise HTTPException(
            status_code=400,
            detail="Network is required",
        )

    try:

        result = scan_network(
            network
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Discovery failed: "
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        )


    rows = list_discovery_miners()


    managed = {
        row["ip"]: row
        for row in rows
    }


    devices = []

    managed_count = 0
    new_known = 0
    new_unknown = 0


    for device in result[
        "devices"
    ]:

        item = dict(
            device
        )

        existing = managed.get(
            item["ip"]
        )

        if existing:

            item["managed"] = True
            item["existing_id"] = (
                existing["id"]
            )
            item["existing_name"] = (
                existing["name"]
            )
            item["existing_driver"] = (
                existing["driver"]
            )

            managed_count += 1

        else:

            item["managed"] = False
            item["existing_id"] = None
            item["existing_name"] = None
            item["existing_driver"] = None

            if (
                item["driver"]
                in (
                    "awesome",
                    "bitmain_stock",
                )
            ):

                new_known += 1

            else:

                new_unknown += 1


        devices.append(
            item
        )


    result["devices"] = devices

    result["managed_count"] = (
        managed_count
    )

    result["new_known"] = (
        new_known
    )

    result["new_unknown"] = (
        new_unknown
    )

    return result


@app.post(
    "/api/discovery/add"
)
def discovery_add(
    payload: dict,
):
    ips = payload.get(
        "ips"
    )

    if not isinstance(
        ips,
        list,
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "ips must be a list"
            ),
        )


    clean_ips = []

    for value in ips:

        value = str(
            value
        ).strip()

        if not value:
            continue

        try:

            addr = ipaddress.ip_address(
                value
            )

        except ValueError:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid IP: "
                    f"{value}"
                ),
            )


        if addr.version != 4:

            raise HTTPException(
                status_code=400,
                detail="IPv4 only",
            )


        allowed = any(
            addr in network

            for network in (
                ipaddress.ip_network(
                    "10.0.0.0/8"
                ),

                ipaddress.ip_network(
                    "172.16.0.0/12"
                ),

                ipaddress.ip_network(
                    "192.168.0.0/16"
                ),
            )
        )


        if not allowed:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"IP outside RFC1918 "
                    f"private networks: "
                    f"{value}"
                ),
            )


        if value not in clean_ips:

            clean_ips.append(
                value
            )


    if not clean_ips:

        raise HTTPException(
            status_code=400,
            detail="No IP addresses supplied",
        )


    if len(clean_ips) > 256:

        raise HTTPException(
            status_code=400,
            detail=(
                "Maximum 256 ASICs "
                "per import operation"
            ),
        )


    results = []


    for ip in clean_ips:

        existing = get_miner_by_ip(
            ip
        )


        if existing:

            results.append({
                "ip": ip,
                "success": True,
                "status":
                    "already_managed",
                "id":
                    existing["id"],
            })

            continue


        try:

            detected = detect_host(
                ip
            )

        except Exception as exc:

            results.append({
                "ip": ip,
                "success": False,
                "status":
                    "detection_failed",
                "error":
                    str(exc),
            })

            continue


        if not detected:

            results.append({
                "ip": ip,
                "success": False,
                "status":
                    "not_asic",
                "error":
                    "ASIC signature not found",
            })

            continue


        driver = detected.get(
            "driver"
        )


        if driver not in (
            "awesome",
            "bitmain_stock",
        ):

            results.append({
                "ip": ip,
                "success": False,
                "status":
                    "unknown_asic",
                "error":
                    "Unsupported ASIC driver",
            })

            continue


        if driver == "awesome":

            username = AWESOME_USERNAME
            password = AWESOME_PASSWORD

        else:

            username = BITMAIN_USERNAME
            password = BITMAIN_PASSWORD


        last_octet = int(
            ip.split(".")[-1]
        )

        candidate_name = (
            f"ASIC-{last_octet:03d}"
        )


        if miner_name_exists(
            candidate_name
        ):

            candidate_name = (
                "ASIC-"
                + ip.replace(
                    ".",
                    "-",
                )
            )


        miner_id, created = create_discovered_miner(
            candidate_name,
            ip,
            driver,
            username,
            password,
            detected.get(
                "model"
            ),
            detected.get(
                "firmware"
            ),
        )

        if not created:

            results.append({
                "ip": ip,
                "success": True,
                "status":
                    "already_managed",
                "id": miner_id,
            })

            continue


        log_event(
            source="SYSTEM",
            action="DISCOVERY_ADD",
            success=True,
            message=(
                f"{ip} "
                f"{driver} "
                f"{detected.get('model')}"
            ),
        )


        threading.Thread(
            target=poll_miner,
            args=(
                miner_id,
            ),
            daemon=True,
        ).start()


        results.append({
            "ip": ip,
            "success": True,
            "status": "added",
            "id": miner_id,
            "driver": driver,
            "name":
                candidate_name,
        })


    return {
        "results": results
    }



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
