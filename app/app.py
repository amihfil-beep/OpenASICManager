#!/usr/bin/env python3

import ipaddress
import json
import socket
import sqlite3
import threading
import time
import os
import hmac
import hashlib
import base64
import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from requests.auth import HTTPDigestAuth

import config as app_config

from remote_web import (
    remote_host_for_ip
    as build_remote_web_host
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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, RedirectResponse

# ============================================================
# USER AUDIT
# ============================================================

audit_actor_context = (
    contextvars.ContextVar(
        "asic_manager_audit_actor",
        default=None,
    )
)


def sanitize_audit_username(
    value,
):

    value = str(
        value or ""
    ).strip()


    if not value:
        return None


    safe = "".join(
        character
        for character
        in value
        if (
            character.isalnum()
            or
            character
            in (
                ".",
                "_",
                "-",
                "@",
            )
        )
    )


    if not safe:
        return None


    return safe[:64]


def current_audit_actor():

    actor = (
        audit_actor_context.get()
    )


    if actor:
        return actor


    return "LOCAL"


def audit_source(
    source,
):

    source = str(
        source or "SYSTEM"
    )


    # Already-attributed sources are preserved.

    if (
        source == "LOCAL"
        or
        source.startswith(
            "WEB:"
        )
    ):
        return source


    actor = (
        audit_actor_context.get()
    )


    # Only user-initiated MANUAL/SYSTEM
    # events are rewritten.
    #
    # Background scheduler/system activity
    # has no request context and therefore
    # remains SCHEDULER/SYSTEM.

    if (
        actor
        and
        source
        in (
            "MANUAL",
            "SYSTEM",
        )
    ):
        return actor


    return source







TIMEZONE_NAME = app_config.TIMEZONE

MOSCOW = ZoneInfo(
    TIMEZONE_NAME
)

POLL_INTERVAL = 15
SCHEDULER_INTERVAL = 20
CONTROL_COOLDOWN = 30

CONTROL_VERIFY_INTERVAL = 5
CONTROL_RETRY_DELAY_ON_ERROR = 10

CONTROL_MAX_ATTEMPTS = 3

# Awesome / AnthillOS
CONTROL_AWESOME_RETRY_AFTER = 30
CONTROL_AWESOME_PAUSE_TIMEOUT = 90
CONTROL_AWESOME_RESUME_TIMEOUT = 180

# Bitmain Stock
#
# Stock T21 can spend more than 3 minutes
# in STARTING before real hashrate appears.
CONTROL_STOCK_RETRY_AFTER = 60
CONTROL_STOCK_PAUSE_TIMEOUT = 180
CONTROL_STOCK_RESUME_TIMEOUT = 360

CONTROL_WORKERS = 32

# Full ASIC reboot verification
REBOOT_POLL_INTERVAL = 5

# How long we wait to observe the actual reboot transition.
REBOOT_TRANSITION_TIMEOUT = 120

# How long we allow the ASIC to come back.
REBOOT_RETURN_TIMEOUT = 600

# Require several consecutive successful polls after reboot.
REBOOT_STABLE_POLLS = 2


# ============================================================
# TELEGRAM
# ============================================================


TELEGRAM_BOT_TOKEN = (
    app_config.TELEGRAM_BOT_TOKEN
)


TELEGRAM_CHAT_ID = (
    app_config.TELEGRAM_CHAT_ID
)


TELEGRAM_NOTIFICATIONS_ENABLED = (
    app_config.TELEGRAM_ENABLED
)


ASIC_MANAGER_NAME = app_config.APP_NAME

TELEGRAM_TIMEOUT = 10



TELEGRAM_PROXY = (
    app_config.TELEGRAM_PROXY
)

TELEGRAM_EVENT_ACTIONS = {
    "ISSUE_OPEN",
    "ISSUE_RESOLVED",
    "PAUSE_FAILED",
    "RESUME_FAILED",
    "REBOOT_FAILED",
}


# Telegram farm summary

TELEGRAM_SUMMARY_ENABLED = (
    os.getenv(
        "TELEGRAM_SUMMARY_ENABLED",
        "1",
    ).strip().lower()
    in (
        "1",
        "true",
        "yes",
        "on",
    )
)

TELEGRAM_SUMMARY_HOUR = int(
    os.getenv(
        "TELEGRAM_SUMMARY_HOUR",
        "21",
    )
)

TELEGRAM_SUMMARY_MINUTE = int(
    os.getenv(
        "TELEGRAM_SUMMARY_MINUTE",
        "15",
    )
)

TELEGRAM_SUMMARY_WINDOW_MINUTES = 30
TELEGRAM_SUMMARY_INTERVAL = 30

# Monday=0 ... Friday=4
TELEGRAM_SUMMARY_WEEKDAYS = {
    0, 1, 2, 3, 4
}


# Historical telemetry
TELEMETRY_INTERVAL = 300
TELEMETRY_RETENTION_DAYS = 90

# Anomaly detection
ANOMALY_INTERVAL = 30

ANOMALY_OFFLINE_GRACE = 180

ANOMALY_HOT_TEMP = 85
ANOMALY_HOT_CLEAR = 82
ANOMALY_HOT_GRACE = 180

ANOMALY_SCHEDULE_GRACE = 600

control_queue_lock = threading.Lock()

control_executor = ThreadPoolExecutor(
    max_workers=CONTROL_WORKERS
)


stop_event = threading.Event()

token_cache = {}
token_lock = threading.Lock()


# ============================================================
# DATABASE
# ============================================================















# ============================================================
# ACTION LOG
# ============================================================

def log_event(
    source,
    action,
    miner=None,
    success=True,
    message=None,
):
    source = audit_source(source)

    """
    Audit trail for manual and scheduler commands.

    Logging must never break ASIC control.
    """

    try:
        miner_id = None
        ip = None
        name = None

        if miner is not None:

            miner_id = miner["id"]
            ip = miner["ip"]
            name = miner["name"]

        if message is None:
            message = ""

        message = str(message)

        if len(message) > 1000:
            message = message[:1000]

        conn = db()

        conn.execute("""
            INSERT INTO action_log
            (
                ts,
                source,
                action,
                miner_id,
                ip,
                name,
                success,
                message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            int(time.time()),
            str(source),
            str(action),
            miner_id,
            ip,
            name,
            1 if success else 0,
            message,
        ))

        conn.commit()
        conn.close()

    except Exception:
        # Управление ASIC не должно ломаться
        # из-за ошибки записи журнала.
        pass


    # Telegram notifications are triggered only for
    # important state changes and failed control jobs.
    telegram_event_async(
        source=source,
        action=action,
        miner=miner,
        success=success,
        message=message,
    )


# ============================================================
# SCHEDULE
# ============================================================


# ============================================================
# SCHEDULE RULE ENGINE v1.5
# ============================================================


SCHEDULE_DAY_NAMES = (
    "Mon",
    "Tue",
    "Wed",
    "Thu",
    "Fri",
    "Sat",
    "Sun",
)




def schedule_time_string(
    time_minutes,
):

    value = int(
        time_minutes
    )

    hour = (
        value
        // 60
    )

    minute = (
        value
        % 60
    )

    return (
        f"{hour:02d}:"
        f"{minute:02d}"
    )


def schedule_days_string(
    days_mask,
):

    mask = int(
        days_mask
    )


    if mask == 127:
        return "Daily"

    if mask == 31:
        return "Mon-Fri"

    if mask == 96:
        return "Sat-Sun"


    result = []

    for index, name in enumerate(
        SCHEDULE_DAY_NAMES
    ):

        if mask & (
            1 << index
        ):

            result.append(
                name
            )


    return ",".join(
        result
    )


def schedule_action_state(
    action,
):

    action = str(
        action
    ).upper()


    if action == "RESUME":
        return "MINING"

    if action == "PAUSE":
        return "PAUSED"


    return None


def schedule_rule_next_run(
    rule,
    now=None,
):

    if now is None:

        now = datetime.now(
            MOSCOW
        )


    if not bool(
        rule["enabled"]
    ):
        return None


    hour = (
        int(
            rule["time_minutes"]
        )
        // 60
    )

    minute = (
        int(
            rule["time_minutes"]
        )
        % 60
    )

    mask = int(
        rule["days_mask"]
    )


    for offset in range(
        0,
        9,
    ):

        day = (
            now
            +
            timedelta(
                days=offset
            )
        ).date()


        if not (
            mask
            &
            (
                1
                <<
                day.weekday()
            )
        ):
            continue


        candidate = datetime(
            day.year,
            day.month,
            day.day,
            hour,
            minute,
            0,
            tzinfo=MOSCOW,
        )


        if candidate > now:

            return candidate


    return None


def schedule_state_details(
    now=None,
):

    ensure_schedule_rules_schema()


    if now is None:

        now = datetime.now(
            MOSCOW
        )


    conn = db()

    rules = conn.execute("""
        SELECT *

        FROM schedule_rules

        WHERE enabled=1
    """).fetchall()

    conn.close()


    winner_rule = None
    winner_occurrence = None


    # Seven days are enough for a weekly schedule.
    # Eight gives us one extra safe boundary day.

    for rule in rules:

        hour = (
            int(
                rule["time_minutes"]
            )
            // 60
        )

        minute = (
            int(
                rule["time_minutes"]
            )
            % 60
        )

        mask = int(
            rule["days_mask"]
        )

        effective_from = int(
            rule["effective_from"]
            or 0
        )


        for offset in range(
            0,
            8,
        ):

            day = (
                now
                -
                timedelta(
                    days=offset
                )
            ).date()


            if not (
                mask
                &
                (
                    1
                    <<
                    day.weekday()
                )
            ):
                continue


            occurrence = datetime(
                day.year,
                day.month,
                day.day,
                hour,
                minute,
                0,
                tzinfo=MOSCOW,
            )


            if occurrence > now:
                continue


            # A newly-created rule is never applied
            # retroactively to an occurrence that
            # happened before the rule existed.

            if (
                effective_from
                and
                int(
                    occurrence.timestamp()
                )
                <
                effective_from
            ):
                continue


            if (
                winner_occurrence is None
                or
                occurrence
                >
                winner_occurrence
            ):

                winner_occurrence = (
                    occurrence
                )

                winner_rule = rule


            break


    if winner_rule is None:

        return (
            None,
            None,
            None,
        )


    return (
        schedule_action_state(
            winner_rule["action"]
        ),
        winner_rule,
        winner_occurrence,
    )


def desired_state(
    now=None,
):

    state, _, _ = (
        schedule_state_details(
            now
        )
    )

    return state


def schedule_mark_rule_seen(
    rule,
    occurrence,
):

    if (
        rule is None
        or
        occurrence is None
    ):
        return


    run_key = (
        occurrence.strftime(
            "%Y-%m-%dT%H:%M%z"
        )
    )


    conn = db()

    cur = conn.execute("""
        UPDATE schedule_rules

        SET last_run_key=?

        WHERE
            id=?
            AND COALESCE(
                last_run_key,
                ''
            ) <> ?
    """, (
        run_key,
        rule["id"],
        run_key,
    ))


    changed = (
        cur.rowcount
        > 0
    )

    conn.commit()
    conn.close()


    if changed:

        log_event(
            source="SCHEDULER",
            action="SCHEDULE_RULE_ACTIVE",
            success=True,
            message=(
                f"Rule #{rule['id']} "
                f"{rule['action']} "
                f"{schedule_time_string(rule['time_minutes'])} "
                f"{schedule_days_string(rule['days_mask'])}"
                +
                (
                    f" - {rule['comment']}"
                    if rule["comment"]
                    else ""
                )
            ),
        )


def schedule_rule_dict(
    rule,
    now=None,
):

    if now is None:

        now = datetime.now(
            MOSCOW
        )


    next_run = (
        schedule_rule_next_run(
            rule,
            now
        )
    )


    return {
        "id":
            int(
                rule["id"]
            ),

        "enabled":
            bool(
                rule["enabled"]
            ),

        "action":
            str(
                rule["action"]
            ),

        "time_minutes":
            int(
                rule["time_minutes"]
            ),

        "time":
            schedule_time_string(
                rule["time_minutes"]
            ),

        "days_mask":
            int(
                rule["days_mask"]
            ),

        "days":
            schedule_days_string(
                rule["days_mask"]
            ),

        "scope":
            str(
                rule["scope"]
            ),

        "comment":
            str(
                rule["comment"]
                or ""
            ),

        "effective_from":
            int(
                rule["effective_from"]
                or 0
            ),

        "next_run":
            (
                next_run.isoformat()
                if next_run
                else None
            ),

        "next_run_label":
            (
                (
                    next_run.strftime(
                        "%a %d.%m %H:%M"
                    )
                    +
                    " "
                    +
                    TIMEZONE_NAME
                )
                if next_run
                else None
            ),
    }


def schedule_parse_bool(
    value,
):

    if isinstance(
        value,
        bool
    ):
        return value


    if isinstance(
        value,
        int
    ):
        return bool(
            value
        )


    return str(
        value
    ).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def schedule_normalize_input(
    data,
    current=None,
):

    if not isinstance(
        data,
        dict
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid JSON body",
        )


    def current_value(
        name,
        default=None,
    ):

        if current is None:
            return default

        return current[
            name
        ]


    action = str(
        data.get(
            "action",
            current_value(
                "action",
                "PAUSE",
            ),
        )
    ).strip().upper()


    if action not in (
        "PAUSE",
        "RESUME",
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Action must be "
                "PAUSE or RESUME"
            ),
        )


    if "time_minutes" in data:

        try:

            time_minutes = int(
                data[
                    "time_minutes"
                ]
            )

        except Exception:

            raise HTTPException(
                status_code=400,
                detail="Invalid time",
            )


    elif "time" in data:

        try:

            hour_text, minute_text = (
                str(
                    data["time"]
                )
                .strip()
                .split(
                    ":",
                    1,
                )
            )

            time_minutes = (
                int(
                    hour_text
                )
                * 60
                +
                int(
                    minute_text
                )
            )

        except Exception:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Time must be HH:MM"
                ),
            )


    else:

        time_minutes = int(
            current_value(
                "time_minutes",
                420,
            )
        )


    if not (
        0
        <=
        time_minutes
        <=
        1439
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid time",
        )


    try:

        days_mask = int(
            data.get(
                "days_mask",
                current_value(
                    "days_mask",
                    31,
                ),
            )
        )

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid days",
        )


    if not (
        1
        <=
        days_mask
        <=
        127
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Select at least one day"
            ),
        )


    enabled = schedule_parse_bool(
        data.get(
            "enabled",
            current_value(
                "enabled",
                1,
            ),
        )
    )


    comment = str(
        data.get(
            "comment",
            current_value(
                "comment",
                "",
            ),
        )
        or ""
    ).strip()


    if len(
        comment
    ) > 120:

        raise HTTPException(
            status_code=400,
            detail=(
                "Comment is limited "
                "to 120 characters"
            ),
        )


    return {
        "enabled":
            enabled,

        "action":
            action,

        "time_minutes":
            time_minutes,

        "days_mask":
            days_mask,

        "scope":
            "SCHEDULED",

        "comment":
            comment,
    }


def schedule_conflicting_rule(
    normalized,
    exclude_id=None,
):

    if not normalized[
        "enabled"
    ]:

        return None


    ensure_schedule_rules_schema()


    conn = db()

    sql = """
        SELECT *

        FROM schedule_rules

        WHERE
            enabled=1
            AND time_minutes=?
            AND (
                days_mask & ?
            ) != 0
    """

    params = [
        normalized[
            "time_minutes"
        ],
        normalized[
            "days_mask"
        ],
    ]


    if exclude_id is not None:

        sql += """
            AND id != ?
        """

        params.append(
            int(
                exclude_id
            )
        )


    sql += """
        ORDER BY id
        LIMIT 1
    """


    row = conn.execute(
        sql,
        tuple(
            params
        ),
    ).fetchone()

    conn.close()


    return row






def next_transition(
    now=None,
):

    ensure_schedule_rules_schema()


    if now is None:

        now = datetime.now(
            MOSCOW
        )


    conn = db()

    rules = conn.execute("""
        SELECT *

        FROM schedule_rules

        WHERE enabled=1
    """).fetchall()

    conn.close()


    candidates = []


    for rule in rules:

        candidate = (
            schedule_rule_next_run(
                rule,
                now
            )
        )


        if candidate:

            candidates.append(
                candidate
            )


    if not candidates:

        return None


    return min(
        candidates
    )



# ============================================================
# CGMINER
# ============================================================

def cgminer_query(
    host,
    command,
):
    payload = json.dumps(
        {
            "command": command
        },
        separators=(",", ":"),
    ).encode("utf-8")

    data = bytearray()

    with socket.create_connection(
        (
            host,
            4028,
        ),
        timeout=3,
    ) as sock:

        sock.settimeout(3)

        sock.sendall(
            payload
        )

        while True:

            try:
                chunk = sock.recv(
                    65536
                )

            except socket.timeout:
                break

            if not chunk:
                break

            data.extend(
                chunk
            )

            if b"\x00" in chunk:
                break

    raw = (
        bytes(data)
        .replace(
            b"\x00",
            b"",
        )
        .decode(
            "utf-8",
            errors="replace",
        )
        .strip()
    )

    if not raw:
        raise RuntimeError(
            "Empty CGMiner response"
        )

    return json.loads(
        raw
    )


# ============================================================
# STOCK BITMAIN
# ============================================================

def stock_status(miner):
    host = miner["ip"]

    summary_data = cgminer_query(
        host,
        "summary",
    )

    stats_data = cgminer_query(
        host,
        "stats",
    )

    pools_data = cgminer_query(
        host,
        "pools",
    )

    devs_data = cgminer_query(
        host,
        "devs",
    )

    summary = {}

    if summary_data.get(
        "SUMMARY"
    ):
        summary = (
            summary_data[
                "SUMMARY"
            ][0]
        )

    ghs_5s = float(
        summary.get(
            "GHS 5s",
            0,
        )
        or 0
    )

    ghs_av = float(
        summary.get(
            "GHS av",
            0,
        )
        or 0
    )

    stats_items = (
        stats_data.get(
            "STATS",
            [],
        )
    )

    stats = {}

    for item in stats_items:

        if (
            item.get("ID")
            == "BTM_SOC0"
        ):
            stats = item
            break

    model = "Antminer"

    if stats_items:

        model = (
            stats_items[0]
            .get("Type")
            or model
        )

    pools = (
        pools_data.get(
            "POOLS",
            [],
        )
    )

    alive_pools = [
        p
        for p in pools
        if str(
            p.get(
                "Status",
                "",
            )
        ).lower()
        == "alive"
    ]

    disabled_pools = [
        p
        for p in pools
        if str(
            p.get(
                "Status",
                "",
            )
        ).lower()
        == "disabled"
    ]

    has_asc = bool(
        devs_data.get(
            "DEVS"
        )
    )

    if ghs_5s > 1000:

        state = "MINING"

    elif (
        not has_asc
        and pools
        and len(
            disabled_pools
        ) == len(pools)
    ):

        state = "PAUSED"

    elif alive_pools:

        state = "STARTING"

    else:

        state = "IDLE"

    temps = []

    for key in (
        "temp1",
        "temp2_1",
        "temp2",
        "temp2_2",
        "temp3",
        "temp2_3",
    ):

        value = stats.get(
            key
        )

        if isinstance(
            value,
            (int, float),
        ):

            if value > 0:
                temps.append(
                    float(value)
                )

    max_temp = (
        max(temps)
        if temps
        else None
    )

    pool = None

    if alive_pools:

        pool = (
            alive_pools[0]
            .get("URL")
        )

    power = (
        stats.get(
            "power_consumption"
        )
    )

    try:
        if power is not None:
            power = float(
                power
            )
    except Exception:
        power = None

    return {
        "state": state,
        "model": model,
        "firmware":
            (
                miner["firmware"]
                or
                "Bitmain Stock"
            ),
        "hashrate":
            ghs_5s / 1000,
        "avg_hashrate":
            ghs_av / 1000,
        "temp":
            max_temp,
        "power":
            power,
        "pool":
            pool,
    }


def stock_control(
    miner,
    action,
):
    if action not in (
        "pause",
        "resume",
    ):
        raise RuntimeError(
            "Invalid action"
        )

    mode = (
        1
        if action == "pause"
        else 0
    )

    url = (
        f"http://{miner['ip']}"
        "/cgi-bin/"
        "set_miner_conf.cgi"
    )

    response = requests.post(
        url,
        json={
            "miner-mode": mode
        },
        auth=HTTPDigestAuth(
            miner["username"]
            or BITMAIN_USERNAME,

            miner["password"]
            or BITMAIN_PASSWORD,
        ),
        timeout=8,
    )

    if not (
        200
        <= response.status_code
        < 300
    ):

        raise RuntimeError(
            f"HTTP "
            f"{response.status_code}: "
            f"{response.text[:200]}"
        )

    return (
        response.text.strip()
    )


# ============================================================
# AWESOME / ANTHILLOS
# ============================================================

def awesome_unlock(miner):
    url = (
        f"http://{miner['ip']}"
        "/api/v1/unlock"
    )

    response = requests.post(
        url,
        json={
            "pw":
                miner["password"]
                or AWESOME_PASSWORD
        },
        timeout=5,
    )

    if (
        response.status_code
        != 200
    ):

        raise RuntimeError(
            f"Unlock HTTP "
            f"{response.status_code}"
        )

    data = response.json()

    token = data.get(
        "token"
    )

    if not token:
        raise RuntimeError(
            "Unlock response "
            "has no token"
        )

    with token_lock:

        token_cache[
            miner["ip"]
        ] = token

    return token


def awesome_token(
    miner,
    force=False,
):
    with token_lock:
        token = (
            token_cache.get(
                miner["ip"]
            )
        )

    if (
        force
        or not token
    ):
        return awesome_unlock(
            miner
        )

    return token


def awesome_status(miner):
    url = (
        f"http://{miner['ip']}"
        "/api/v1/summary"
    )

    response = requests.get(
        url,
        timeout=5,
    )

    if (
        response.status_code
        == 401
    ):

        token = awesome_token(
            miner,
            force=True,
        )

        response = requests.get(
            url,
            headers={
                "Authorization":
                    token
            },
            timeout=5,
        )

    if (
        response.status_code
        != 200
    ):

        raise RuntimeError(
            f"Summary HTTP "
            f"{response.status_code}"
        )

    data = response.json()

    root = data.get(
        "miner",
        data,
    )

    miner_status = root.get(
        "miner_status",
        {},
    )

    raw_state = str(
        miner_status.get(
            "miner_state",
            "",
        )
    ).lower()

    if raw_state == "mining":

        state = "MINING"

    elif raw_state == "stopped":

        state = "PAUSED"

    elif raw_state in (
        "starting",
        "initializing",
        "tuning",
    ):

        state = "STARTING"

    else:

        state = (
            raw_state.upper()
            if raw_state
            else "IDLE"
        )

    hr = (
        root.get(
            "hr_realtime"
        )
        or root.get(
            "instant_hashrate"
        )
        or 0
    )

    avg = (
        root.get(
            "hr_average"
        )
        or root.get(
            "average_hashrate"
        )
        or 0
    )

    try:
        hr = float(
            hr
        ) / 1000
    except Exception:
        hr = 0

    try:
        avg_raw = float(
            avg
        )

        if avg_raw > 1000:
            avg = (
                avg_raw / 1000
            )
        else:
            avg = avg_raw

    except Exception:
        avg = 0

    chip_temp = root.get(
        "chip_temp",
        {},
    )

    temp = chip_temp.get(
        "max"
    )

    try:
        if temp is not None:
            temp = float(
                temp
            )
    except Exception:
        temp = None

    power = (
        root.get(
            "power_consumption"
        )
        or root.get(
            "power_usage"
        )
    )

    try:
        if power is not None:
            power = float(
                power
            )
    except Exception:
        power = None

    pool = None

    for p in root.get(
        "pools",
        [],
    ):

        if (
            p.get("pool_type")
            == "UserPool"
            and p.get("status")
            == "active"
        ):

            pool = p.get(
                "url"
            )

            break

    return {
        "state":
            state,

        "model":
            (
                miner["model"]
                or
                root.get(
                    "miner_type",
                    "Antminer",
                )
            ),

        "firmware":
            (
                miner["firmware"]
                or
                "Awesome / AnthillOS"
            ),

        "hashrate":
            hr,

        "avg_hashrate":
            avg,

        "temp":
            temp,

        "power":
            power,

        "pool":
            pool,
    }


def awesome_control(
    miner,
    action,
):
    if action == "pause":

        endpoint = (
            "mining/stop"
        )

    elif action == "resume":

        endpoint = (
            "mining/start"
        )

    else:

        raise RuntimeError(
            "Invalid action"
        )

    url = (
        f"http://{miner['ip']}"
        f"/api/v1/{endpoint}"
    )

    token = awesome_token(
        miner
    )

    for attempt in range(2):

        response = requests.post(
            url,
            headers={
                "Authorization":
                    token,

                "Content-Type":
                    "application/json",
            },
            data=b"",
            timeout=8,
        )

        if (
            200
            <= response.status_code
            < 300
        ):

            return (
                response.text.strip()
            )

        if (
            response.status_code
            == 401
        ):

            token = (
                awesome_token(
                    miner,
                    force=True,
                )
            )

            continue

        raise RuntimeError(
            f"HTTP "
            f"{response.status_code}: "
            f"{response.text[:200]}"
        )

    raise RuntimeError(
        "Authorization failed "
        "after token refresh"
    )


# ============================================================
# DRIVER
# ============================================================

def read_status(miner):
    driver = miner["driver"]

    if driver == "bitmain_stock":

        return stock_status(
            miner
        )

    if driver == "awesome":

        return awesome_status(
            miner
        )

    raise RuntimeError(
        "ASIC firmware "
        "is not configured"
    )


def control(
    miner,
    action,
):
    driver = miner["driver"]

    if driver == "bitmain_stock":

        return stock_control(
            miner,
            action,
        )

    if driver == "awesome":

        return awesome_control(
            miner,
            action,
        )

    raise RuntimeError(
        "ASIC firmware "
        "is not configured"
    )


# ============================================================
# MONITORING
# ============================================================

def poll_miner(miner_id):
    miner = get_miner(
        miner_id
    )

    if not miner:
        return

    if not miner["enabled"]:
        return

    if miner["driver"] == "unset":
        return

    now = int(
        time.time()
    )

    try:
        status = read_status(
            miner
        )

        conn = db()

        conn.execute("""
            UPDATE miners

            SET
                model=?,
                firmware=?,
                last_state=?,
                hashrate=?,
                avg_hashrate=?,
                temp=?,
                power=?,
                pool=?,
                last_seen=?,
                last_error=NULL

            WHERE id=?
        """, (
            status.get(
                "model"
            ),

            status.get(
                "firmware"
            ),

            status.get(
                "state"
            ),

            status.get(
                "hashrate"
            ),

            status.get(
                "avg_hashrate"
            ),

            status.get(
                "temp"
            ),

            status.get(
                "power"
            ),

            status.get(
                "pool"
            ),

            now,
            miner_id,
        ))

        conn.commit()
        conn.close()

    except Exception as exc:

        conn = db()

        conn.execute("""
            UPDATE miners

            SET
                last_state='OFFLINE',
                last_error=?

            WHERE id=?
        """, (
            (
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
            miner_id,
        ))

        conn.commit()
        conn.close()


def polling_loop():
    while not stop_event.is_set():

        miners = get_poll_miners()

        if miners:

            workers = min(
                12,
                len(miners),
            )

            with ThreadPoolExecutor(
                max_workers=workers
            ) as executor:

                futures = [
                    executor.submit(
                        poll_miner,
                        miner["id"],
                    )
                    for miner in miners
                ]

                for future in as_completed(
                    futures
                ):

                    try:
                        future.result()

                    except Exception:
                        pass

        stop_event.wait(
            POLL_INTERVAL
        )


# ============================================================
# HISTORICAL TELEMETRY
# ============================================================

def save_telemetry_snapshot():

    now = int(
        time.time()
    )

    conn = db()

    miners = conn.execute("""
        SELECT
            id,
            ip,
            name,
            driver,
            enabled,
            last_state,
            hashrate,
            avg_hashrate,
            temp,
            power

        FROM miners

        WHERE
            enabled=1
            AND driver IN (
                'awesome',
                'bitmain_stock'
            )
    """).fetchall()


    rows = []

    for miner in miners:

        rows.append((
            now,
            miner["id"],
            miner["ip"],
            miner["name"],
            miner["driver"],
            miner["last_state"],
            miner["hashrate"],
            miner["avg_hashrate"],
            miner["temp"],
            miner["power"],
        ))


    if rows:

        conn.executemany("""
            INSERT INTO telemetry
            (
                ts,
                miner_id,
                ip,
                name,
                driver,
                state,
                hashrate,
                avg_hashrate,
                temp,
                power
            )

            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, rows)


    retention_limit = (
        now
        -
        TELEMETRY_RETENTION_DAYS
        * 86400
    )


    conn.execute("""
        DELETE FROM telemetry
        WHERE ts < ?
    """, (
        retention_limit,
    ))


    conn.commit()
    conn.close()


def telemetry_loop():

    # Даём poller сначала получить
    # актуальные данные после старта сервиса.
    if stop_event.wait(30):
        return


    while not stop_event.is_set():

        try:

            save_telemetry_snapshot()

        except Exception as exc:

            log_event(
                source="SYSTEM",
                action="TELEMETRY_ERROR",
                success=False,
                message=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )


        if stop_event.wait(
            TELEMETRY_INTERVAL
        ):

            return


# ============================================================
# TELEGRAM NOTIFICATIONS
# ============================================================

def telegram_configured():

    return bool(
        TELEGRAM_BOT_TOKEN
        and
        TELEGRAM_CHAT_ID
    )


def telegram_send_message(
    message,
    force=False,
):

    if not telegram_configured():

        return (
            False,
            "Telegram is not configured",
        )


    if (
        not TELEGRAM_NOTIFICATIONS_ENABLED
        and
        not force
    ):

        return (
            False,
            "Telegram notifications are disabled",
        )


    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )


    response = requests.post(
        url,
        json={
            "chat_id":
                TELEGRAM_CHAT_ID,

            "text":
                message,

            "disable_web_page_preview":
                True,
        },
        timeout=TELEGRAM_TIMEOUT,
        proxies=(
            {
                "http": TELEGRAM_PROXY,
                "https": TELEGRAM_PROXY,
            }
            if TELEGRAM_PROXY
            else None
        ),
    )


    response.raise_for_status()


    data = response.json()


    if not data.get("ok"):

        raise RuntimeError(
            "Telegram API returned ok=false"
        )


    message_id = (
        data.get(
            "result",
            {},
        ).get(
            "message_id"
        )
    )


    return (
        True,
        f"message_id={message_id}",
    )


def telegram_row_value(
    row,
    key,
    default=None,
):

    if row is None:
        return default

    try:
        return row[key]
    except Exception:
        pass

    try:
        return getattr(
            row,
            key,
            default,
        )
    except Exception:
        return default


def telegram_current_miner(
    miner,
):

    if miner is None:
        return None

    miner_id = telegram_row_value(
        miner,
        "id",
    )

    if miner_id is None:
        return miner

    try:

        current = get_miner(
            miner_id
        )

        if current is not None:
            return current

    except Exception:
        pass

    return miner


def telegram_driver_label(
    driver,
):

    labels = {
        "awesome":
            "Awesome / AnthillOS",

        "bitmain_stock":
            "Bitmain Stock",

        "unset":
            "Unknown",
    }

    return labels.get(
        str(driver or ""),
        str(driver or "Unknown"),
    )


def telegram_format_hashrate(
    value,
):

    if value is None:
        return "—"

    try:
        value = float(value)
    except Exception:
        return str(value)

    if abs(value) < 0.05:
        return "0 TH/s"

    return f"{value:.1f} TH/s"


def telegram_format_temperature(
    value,
):

    if value is None:
        return "—"

    try:
        value = float(value)
    except Exception:
        return str(value)

    if value.is_integer():
        return f"{int(value)}°C"

    return f"{value:.1f}°C"


def telegram_format_power(
    value,
):

    if value is None:
        return "—"

    try:
        value = float(value)
    except Exception:
        return str(value)

    if value <= 0:
        return "0 kW"

    return f"{value / 1000.0:.2f} kW"


def telegram_format_duration(
    seconds,
):

    try:
        seconds = int(seconds)
    except Exception:
        return None

    if seconds < 0:
        return None

    days, seconds = divmod(
        seconds,
        86400,
    )

    hours, seconds = divmod(
        seconds,
        3600,
    )

    minutes, seconds = divmod(
        seconds,
        60,
    )

    parts = []

    if days:
        parts.append(
            f"{days}d"
        )

    if hours:
        parts.append(
            f"{hours}h"
        )

    if minutes:
        parts.append(
            f"{minutes}m"
        )

    if seconds or not parts:
        parts.append(
            f"{seconds}s"
        )

    return " ".join(
        parts
    )


def telegram_problem_from_message(
    message,
):

    text = str(
        message or ""
    ).upper()

    for code in (
        "SCHEDULE_MISMATCH",
        "OVERHEAT",
        "OFFLINE",
    ):
        if code in text:
            return code

    return None


def telegram_issue_context(
    miner_id,
    action,
):

    result = {}

    if miner_id is None:
        return result

    conn = None

    try:

        conn = db()

        schema = conn.execute(
            "PRAGMA table_info(issues)"
        ).fetchall()

        columns = {
            row["name"]
            for row in schema
        }

        if (
            not columns
            or
            "miner_id" not in columns
        ):
            return result


        where = [
            "miner_id=?"
        ]

        params = [
            miner_id
        ]


        if "status" in columns:

            if action == "ISSUE_OPEN":

                where.append(
                    "status='ACTIVE'"
                )

            elif action == "ISSUE_RESOLVED":

                where.append(
                    "status='RESOLVED'"
                )


        order_column = "id"

        for candidate in (
            "resolved_at",
            "closed_at",
            "last_seen",
            "opened_at",
            "created_at",
            "id",
        ):
            if candidate in columns:

                order_column = candidate
                break


        sql = (
            "SELECT * "
            "FROM issues "
            "WHERE "
            + " AND ".join(where)
            + f" ORDER BY {order_column} DESC "
            "LIMIT 1"
        )


        row = conn.execute(
            sql,
            params,
        ).fetchone()


        # Some older issue schemas may use another
        # status value. Fall back to the newest issue.
        if row is None:

            row = conn.execute(
                """
                SELECT *
                FROM issues
                WHERE miner_id=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    miner_id,
                ),
            ).fetchone()


        if row is None:
            return result


        result = dict(row)


        start_ts = None
        end_ts = None


        for column in (
            "opened_at",
            "created_at",
            "first_seen",
            "started_at",
        ):

            if (
                column in result
                and
                result[column] is not None
            ):
                start_ts = result[column]
                break


        for column in (
            "resolved_at",
            "closed_at",
            "last_seen",
            "updated_at",
        ):

            if (
                column in result
                and
                result[column] is not None
            ):
                end_ts = result[column]
                break


        if (
            action == "ISSUE_RESOLVED"
            and
            start_ts is not None
            and
            end_ts is not None
        ):

            try:

                duration = (
                    int(end_ts)
                    -
                    int(start_ts)
                )

                if duration >= 0:

                    result[
                        "_duration"
                    ] = duration

            except Exception:
                pass


        return result


    except Exception as exc:

        print(
            "Telegram issue context failed: "
            f"{type(exc).__name__}: {exc}"
        )

        return result


    finally:

        if conn is not None:

            try:
                conn.close()
            except Exception:
                pass


def telegram_parse_control_message(
    message,
):

    import re

    text = str(
        message or ""
    )

    result = {}


    match = re.search(
        r"Expected\s*=\s*"
        r"([A-Za-z0-9_-]+)",
        text,
        re.IGNORECASE,
    )

    if match:

        result[
            "expected"
        ] = match.group(1).upper()


    match = re.search(
        r"actual\s*=\s*"
        r"([A-Za-z0-9_-]+)",
        text,
        re.IGNORECASE,
    )

    if match:

        result[
            "actual"
        ] = match.group(1).upper()


    match = re.search(
        r"attempts\s*=\s*"
        r"([0-9]+/[0-9]+)",
        text,
        re.IGNORECASE,
    )

    if match:

        result[
            "attempts"
        ] = match.group(1)


    return result


def telegram_format_event(
    source,
    action,
    miner,
    success,
    message,
):

    message = str(
        message or ""
    )

    timestamp = (
        datetime.now(
            MOSCOW
        ).strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )
    )


    # Always try to use the newest telemetry,
    # rather than the stale Row supplied by log_event().
    miner = telegram_current_miner(
        miner
    )


    miner_id = telegram_row_value(
        miner,
        "id",
    )

    name = telegram_row_value(
        miner,
        "name",
    )

    ip = telegram_row_value(
        miner,
        "ip",
    )

    driver = telegram_row_value(
        miner,
        "driver",
    )

    model = telegram_row_value(
        miner,
        "model",
    )

    firmware = telegram_row_value(
        miner,
        "firmware",
    )

    state = telegram_row_value(
        miner,
        "last_state",
        "UNKNOWN",
    )

    hashrate = telegram_row_value(
        miner,
        "hashrate",
    )

    temperature = telegram_row_value(
        miner,
        "temp",
    )

    power = telegram_row_value(
        miner,
        "power",
    )


    issue = {}

    if action in (
        "ISSUE_OPEN",
        "ISSUE_RESOLVED",
    ):

        issue = telegram_issue_context(
            miner_id,
            action,
        )


    problem = (
        issue.get("code")
        or
        telegram_problem_from_message(
            message
        )
    )


    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    if action == "ISSUE_OPEN":

        if problem == "OVERHEAT":

            icon = "🔥"
            title = "ASIC OVERHEAT"

        elif problem == "OFFLINE":

            icon = "🚨"
            title = "ASIC OFFLINE"

        elif problem == "SCHEDULE_MISMATCH":

            icon = "⚠️"
            title = "ASIC SCHEDULE MISMATCH"

        else:

            icon = "🚨"
            title = "ASIC ISSUE"


    elif action == "ISSUE_RESOLVED":

        icon = "✅"
        title = "ASIC RECOVERED"


    else:

        icon = "❌"
        title = "ASIC CONTROL FAILED"


    lines = [
        f"{icon} {title}",
        "",
        f"Farm: {ASIC_MANAGER_NAME}",
    ]


    # --------------------------------------------------------
    # ASIC INFORMATION
    # --------------------------------------------------------

    if name:
        lines.append(
            f"ASIC: {name}"
        )

    if ip:
        lines.append(
            f"IP: {ip}"
        )

    if model:
        lines.append(
            f"Model: {model}"
        )

    if firmware:
        lines.append(
            f"Firmware: {firmware}"
        )

    if driver:
        lines.append(
            "Driver: "
            + telegram_driver_label(
                driver
            )
        )


    lines.extend([
        f"State: {state or 'UNKNOWN'}",

        "Hashrate: "
        + telegram_format_hashrate(
            hashrate
        ),

        "Temperature: "
        + telegram_format_temperature(
            temperature
        ),

        "Power: "
        + telegram_format_power(
            power
        ),
    ])


    # --------------------------------------------------------
    # ISSUE
    # --------------------------------------------------------

    if action in (
        "ISSUE_OPEN",
        "ISSUE_RESOLVED",
    ):

        lines.append("")

        if problem:

            lines.append(
                f"Problem: {problem}"
            )


        severity = issue.get(
            "severity"
        )

        if severity:

            lines.append(
                f"Severity: {severity}"
            )


        if (
            action
            == "ISSUE_RESOLVED"
        ):

            duration = (
                telegram_format_duration(
                    issue.get(
                        "_duration"
                    )
                )
            )

            if duration:

                lines.append(
                    f"Duration: {duration}"
                )


    # --------------------------------------------------------
    # FAILED CONTROL
    # --------------------------------------------------------

    else:

        command = (
            action
            .replace(
                "_FAILED",
                "",
            )
            .upper()
        )

        parsed = (
            telegram_parse_control_message(
                message
            )
        )

        lines.extend([
            "",
            f"Command: {command}",
        ])


        if parsed.get(
            "expected"
        ):

            lines.append(
                "Expected: "
                + parsed["expected"]
            )


        if parsed.get(
            "actual"
        ):

            lines.append(
                "Actual: "
                + parsed["actual"]
            )


        if parsed.get(
            "attempts"
        ):

            lines.append(
                "Attempts: "
                + parsed["attempts"]
            )


    # --------------------------------------------------------
    # DETAILS / SOURCE / TIME
    # --------------------------------------------------------

    if message:

        lines.extend([
            "",
            f"Details: {message}",
        ])


    lines.extend([
        f"Source: {source}",
        f"Time: {timestamp}",
    ])


    return "\n".join(
        lines
    )



def telegram_event_worker(
    source,
    action,
    miner,
    success,
    message,
):

    try:

        notification = (
            telegram_format_event(
                source=source,
                action=action,
                miner=miner,
                success=success,
                message=message,
            )
        )


        sent, result = (
            telegram_send_message(
                notification
            )
        )


        if not sent:
            return


        # Deliberately do not call log_event here.
        # This avoids notification recursion.

        print(
            f"Telegram sent: "
            f"{action}; {result}"
        )


    except Exception as exc:

        # Telegram failure must NEVER break
        # miner control or anomaly processing.

        print(
            "Telegram notification failed: "
            f"{action}; "
            f"{type(exc).__name__}: "
            f"{exc}"
        )


def telegram_event_async(
    source,
    action,
    miner,
    success,
    message,
):

    if not TELEGRAM_NOTIFICATIONS_ENABLED:
        return


    if action not in TELEGRAM_EVENT_ACTIONS:
        return


    threading.Thread(
        target=telegram_event_worker,
        kwargs={
            "source":
                source,

            "action":
                action,

            "miner":
                miner,

            "success":
                success,

            "message":
                message,
        },
        name=(
            "telegram-"
            + action.lower()
        ),
        daemon=True,
    ).start()


# ============================================================
# ANOMALY DETECTION
# ============================================================

def active_issue_exists(
    miner_id,
    code,
):

    conn = db()

    row = conn.execute("""
        SELECT id

        FROM issues

        WHERE
            miner_id=?
            AND code=?
            AND status='ACTIVE'

        ORDER BY id DESC
        LIMIT 1
    """, (
        miner_id,
        code,
    )).fetchone()

    conn.close()

    return bool(row)


def set_anomaly_condition(
    miner,
    code,
    severity,
    observed,
    grace_seconds,
    message,
):

    now = int(
        time.time()
    )

    opened = False
    resolved = False

    conn = db()


    active = conn.execute("""
        SELECT *

        FROM issues

        WHERE
            miner_id=?
            AND code=?
            AND status='ACTIVE'

        ORDER BY id DESC

        LIMIT 1
    """, (
        miner["id"],
        code,
    )).fetchone()


    candidate = conn.execute("""
        SELECT *

        FROM anomaly_candidates

        WHERE
            miner_id=?
            AND code=?
    """, (
        miner["id"],
        code,
    )).fetchone()


    if observed:

        if active:

            conn.execute("""
                UPDATE issues

                SET
                    last_seen=?,
                    message=?,
                    severity=?

                WHERE id=?
            """, (
                now,
                message,
                severity,
                active["id"],
            ))


        else:

            if not candidate:

                conn.execute("""
                    INSERT INTO anomaly_candidates
                    (
                        miner_id,
                        code,
                        since_ts,
                        last_seen_ts
                    )

                    VALUES (?, ?, ?, ?)
                """, (
                    miner["id"],
                    code,
                    now,
                    now,
                ))

            else:

                conn.execute("""
                    UPDATE anomaly_candidates

                    SET last_seen_ts=?

                    WHERE
                        miner_id=?
                        AND code=?
                """, (
                    now,
                    miner["id"],
                    code,
                ))


                if (
                    now
                    - candidate["since_ts"]
                    >= grace_seconds
                ):

                    conn.execute("""
                        INSERT INTO issues
                        (
                            miner_id,
                            ip,
                            name,
                            code,
                            severity,
                            status,
                            first_seen,
                            last_seen,
                            message
                        )

                        VALUES (
                            ?, ?, ?, ?, ?,
                            'ACTIVE',
                            ?, ?, ?
                        )
                    """, (
                        miner["id"],
                        miner["ip"],
                        miner["name"],
                        code,
                        severity,
                        candidate["since_ts"],
                        now,
                        message,
                    ))


                    conn.execute("""
                        DELETE FROM anomaly_candidates

                        WHERE
                            miner_id=?
                            AND code=?
                    """, (
                        miner["id"],
                        code,
                    ))

                    opened = True


    else:

        conn.execute("""
            DELETE FROM anomaly_candidates

            WHERE
                miner_id=?
                AND code=?
        """, (
            miner["id"],
            code,
        ))


        if active:

            conn.execute("""
                UPDATE issues

                SET
                    status='RESOLVED',
                    last_seen=?,
                    resolved_at=?,
                    message=?

                WHERE id=?
            """, (
                now,
                now,
                message,
                active["id"],
            ))

            resolved = True


    conn.commit()
    conn.close()


    if opened:

        log_event(
            source="SYSTEM",
            action="ISSUE_OPEN",
            miner=miner,
            success=False,
            message=(
                f"{severity} {code}: "
                f"{message}"
            ),
        )


    if resolved:

        log_event(
            source="SYSTEM",
            action="ISSUE_RESOLVED",
            miner=miner,
            success=True,
            message=(
                f"{code}: {message}"
            ),
        )



def anomaly_desired_state():

    return desired_state(
        datetime.now(
            MOSCOW
        )
    )



def anomaly_scan():

    now = int(
        time.time()
    )

    conn = db()


    scheduler_row = conn.execute("""
        SELECT value

        FROM settings

        WHERE key='scheduler_enabled'
    """).fetchone()


    scheduler_enabled = bool(
        scheduler_row
        and
        str(
            scheduler_row["value"]
        ) == "1"
    )


    miners = conn.execute("""
        SELECT
            m.*,

            EXISTS(
                SELECT 1

                FROM control_jobs cj

                WHERE
                    cj.miner_id=m.id
                    AND cj.status IN (
                        'QUEUED',
                        'RUNNING'
                    )
            ) AS has_control_job,

            (
                SELECT cj.action

                FROM control_jobs cj

                WHERE
                    cj.miner_id=m.id
                    AND cj.status IN (
                        'QUEUED',
                        'RUNNING'
                    )

                ORDER BY cj.id DESC

                LIMIT 1
            ) AS active_control_action

        FROM miners m

        WHERE driver IN (
            'awesome',
            'bitmain_stock'
        )
    """).fetchall()


    conn.close()


    desired = (
        anomaly_desired_state()
    )


    for miner in miners:

        enabled = bool(
            miner["enabled"]
        )


        # ----------------------------------------------------
        # Disabled ASICs do not generate active health issues.
        # ----------------------------------------------------

        if not enabled:

            for code in (
                "OFFLINE",
                "OVERHEAT",
                "SCHEDULE_MISMATCH",
            ):

                set_anomaly_condition(
                    miner=miner,
                    code=code,
                    severity="INFO",
                    observed=False,
                    grace_seconds=0,
                    message=(
                        "ASIC disabled"
                    ),
                )

            continue


        state = (
            miner["last_state"]
            or "UNKNOWN"
        )


        # ----------------------------------------------------
        # OFFLINE
        #
        # Three-minute grace prevents a short Stock firmware
        # restart during Pause/Resume becoming an incident.
        # ----------------------------------------------------

        intentional_reboot = (
            miner[
                "active_control_action"
            ]
            == "reboot"
        )


        offline = (
            state == "OFFLINE"
            and
            not intentional_reboot
        )


        set_anomaly_condition(
            miner=miner,
            code="OFFLINE",
            severity="CRITICAL",
            observed=offline,
            grace_seconds=(
                ANOMALY_OFFLINE_GRACE
            ),
            message=(
                "ASIC is offline"
                if offline
                else
                f"ASIC reachable; state={state}"
            ),
        )


        # ----------------------------------------------------
        # TEMPERATURE WITH HYSTERESIS
        #
        # Open:  >= 85 C for 3 minutes
        # Clear: < 82 C
        # ----------------------------------------------------

        temp = miner["temp"]


        try:

            temp_value = (
                float(temp)
                if temp is not None
                else None
            )

        except Exception:

            temp_value = None


        hot_active = (
            active_issue_exists(
                miner["id"],
                "OVERHEAT",
            )
        )


        if hot_active:

            hot = (
                temp_value is not None
                and
                temp_value
                >= ANOMALY_HOT_CLEAR
            )

        else:

            hot = (
                temp_value is not None
                and
                temp_value
                >= ANOMALY_HOT_TEMP
            )


        set_anomaly_condition(
            miner=miner,
            code="OVERHEAT",
            severity="CRITICAL",
            observed=hot,
            grace_seconds=(
                ANOMALY_HOT_GRACE
            ),
            message=(
                (
                    f"Temperature "
                    f"{temp_value:.1f} C"
                )
                if temp_value is not None
                else
                "Temperature unavailable"
            ),
        )


        # ----------------------------------------------------
        # SCHEDULE MISMATCH
        #
        # Ignore:
        # - global scheduler OFF
        # - per-ASIC schedule OFF
        # - active manual override
        # - active verified control job
        # ----------------------------------------------------

        override_active = bool(
            miner[
                "manual_override_until"
            ]
            and
            miner[
                "manual_override_until"
            ] > now
        )


        schedule_applicable = (
            scheduler_enabled
            and
            desired is not None
            and
            bool(
                miner[
                    "schedule_enabled"
                ]
            )
            and
            not override_active
            and
            not bool(
                miner[
                    "has_control_job"
                ]
            )
        )


        mismatch = False


        if schedule_applicable:

            mismatch = (
                state != desired
            )


        set_anomaly_condition(
            miner=miner,
            code="SCHEDULE_MISMATCH",
            severity="WARNING",
            observed=mismatch,
            grace_seconds=(
                ANOMALY_SCHEDULE_GRACE
            ),
            message=(
                (
                    f"Expected {desired}; "
                    f"actual {state}"
                )
                if schedule_applicable
                else
                "Schedule condition not applicable"
            ),
        )


def anomaly_loop():

    # Let normal polling establish a stable state
    # before anomaly candidates are created.
    if stop_event.wait(30):
        return


    while not stop_event.is_set():

        try:

            anomaly_scan()

        except Exception as exc:

            log_event(
                source="SYSTEM",
                action="ANOMALY_ERROR",
                success=False,
                message=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )


        if stop_event.wait(
            ANOMALY_INTERVAL
        ):

            return


# ============================================================
# FULL ASIC REBOOT
# ============================================================

def send_awesome_reboot(
    miner,
):

    ip = miner["ip"]

    password = (
        miner["password"]
        or AWESOME_PASSWORD
    )


    unlock = requests.post(
        f"http://{ip}/api/v1/unlock",
        json={
            "pw": password,
        },
        timeout=10,
    )

    unlock.raise_for_status()


    try:

        data = unlock.json()

    except Exception as exc:

        raise RuntimeError(
            "Awesome unlock returned invalid JSON"
        ) from exc


    token = (
        data.get("token")
        or
        data.get("access_token")
    )


    if not token:

        raise RuntimeError(
            "Awesome unlock token missing"
        )


    try:

        response = requests.post(
            f"http://{ip}/api/v1/system/reboot",
            headers={
                "Authorization":
                    token,
            },
            timeout=10,
        )


        response.raise_for_status()


        return (
            f"HTTP {response.status_code}"
        )


    except requests.exceptions.ReadTimeout:

        # Device may reboot before it has time
        # to finish the HTTP response.
        return (
            "HTTP read interrupted by reboot"
        )


    except requests.exceptions.ConnectionError:

        # Same situation: command can already be accepted
        # while network connectivity disappears.
        return (
            "Connection dropped after reboot request"
        )


def send_stock_reboot(
    miner,
):

    ip = miner["ip"]

    username = (
        miner["username"]
        or BITMAIN_USERNAME
    )

    password = (
        miner["password"]
        or BITMAIN_PASSWORD
    )


    auth = HTTPDigestAuth(
        username,
        password,
    )


    url = (
        f"http://{ip}"
        f"/cgi-bin/reboot.cgi"
    )


    try:

        response = requests.get(
            url,
            auth=auth,
            timeout=10,
        )


        # Some stock builds may expose the endpoint
        # as POST rather than GET.
        if response.status_code in (
            404,
            405,
        ):

            response = requests.post(
                url,
                auth=auth,
                timeout=10,
            )


        response.raise_for_status()


        return (
            f"HTTP {response.status_code}"
        )


    except requests.exceptions.ReadTimeout:

        return (
            "HTTP read interrupted by reboot"
        )


    except requests.exceptions.ConnectionError:

        return (
            "Connection dropped after reboot request"
        )


def send_reboot_command(
    miner,
):

    driver = miner["driver"]


    if driver == "awesome":

        return send_awesome_reboot(
            miner
        )


    if driver == "bitmain_stock":

        return send_stock_reboot(
            miner
        )


    raise RuntimeError(
        "Reboot unsupported for this firmware"
    )


def reboot_worker(
    job_id,
):

    job = get_control_job(
        job_id
    )


    if not job:
        return


    miner_id = job[
        "miner_id"
    ]


    miner = get_miner(
        miner_id
    )


    if not miner:

        update_control_job(
            job_id,
            status="FAILED",
            completed_at=int(
                time.time()
            ),
            message="Miner not found",
        )

        return


    source = job[
        "source"
    ]


    initial_state = (
        miner["last_state"]
        or "UNKNOWN"
    )


    update_control_job(
        job_id,
        status="RUNNING",
        started_at=int(
            time.time()
        ),
        attempts=1,
        final_state=
            initial_state,
        message=(
            "Sending full reboot command"
        ),
    )


    # --------------------------------------------------------
    # SEND EXACTLY ONCE
    # --------------------------------------------------------

    try:

        result = (
            send_reboot_command(
                miner
            )
        )


        set_last_command(
            miner_id,
            "reboot",
        )


        log_event(
            source=source,
            action="REBOOT_SENT",
            miner=miner,
            success=True,
            message=(
                f"Attempt 1/1; "
                f"response={result}"
            ),
        )


    except Exception as exc:

        finish_control_job(
            job_id=job_id,
            miner=miner,
            source=source,
            action="reboot",
            success=False,
            final_state=
                initial_state,
            message=(
                "Unable to send reboot command: "
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        )

        return


    # --------------------------------------------------------
    # PHASE 1:
    # ACTUAL REBOOT TRANSITION MUST BE OBSERVED
    # --------------------------------------------------------

    transition_deadline = (
        time.monotonic()
        +
        REBOOT_TRANSITION_TIMEOUT
    )


    transition_seen = False
    transition_state = None


    while (
        not stop_event.is_set()
        and
        time.monotonic()
        < transition_deadline
    ):

        if stop_event.wait(
            REBOOT_POLL_INTERVAL
        ):

            return


        try:

            poll_miner(
                miner_id
            )

        except Exception:
            pass


        fresh = get_miner(
            miner_id
        )


        if not fresh:
            continue


        state = (
            fresh["last_state"]
            or "UNKNOWN"
        )


        update_control_job(
            job_id,
            final_state=state,
            message=(
                "Waiting for reboot transition; "
                f"current={state}"
            ),
        )


        # OFFLINE is the strongest evidence.
        #
        # STARTING / RESTARTING is also accepted if the
        # previous state was different. This prevents a
        # very fast reboot being incorrectly reported as
        # failed simply because the 5 second poll missed
        # the offline interval.

        if state in (
            "OFFLINE",
            "UNKNOWN",
        ):

            transition_seen = True
            transition_state = state

            break


        if (
            state in (
                "STARTING",
                "RESTARTING",
            )
            and
            state != initial_state
        ):

            transition_seen = True
            transition_state = state

            break


    if not transition_seen:

        fresh = get_miner(
            miner_id
        )


        final_state = (
            fresh["last_state"]
            if fresh
            else initial_state
        )


        finish_control_job(
            job_id=job_id,
            miner=(
                fresh
                or miner
            ),
            source=source,
            action="reboot",
            success=False,
            final_state=
                final_state,
            message=(
                "Reboot command was sent, "
                "but reboot transition was not observed "
                f"within {REBOOT_TRANSITION_TIMEOUT}s"
            ),
        )

        return


    log_event(
        source=source,
        action="REBOOT_TRANSITION",
        miner=(
            get_miner(
                miner_id
            )
            or miner
        ),
        success=True,
        message=(
            "Reboot transition observed; "
            f"state={transition_state}"
        ),
    )


    # --------------------------------------------------------
    # PHASE 2:
    # WAIT FOR DEVICE TO RETURN
    # --------------------------------------------------------

    return_deadline = (
        time.monotonic()
        +
        REBOOT_RETURN_TIMEOUT
    )


    stable_polls = 0
    final_state = transition_state


    while (
        not stop_event.is_set()
        and
        time.monotonic()
        < return_deadline
    ):

        if stop_event.wait(
            REBOOT_POLL_INTERVAL
        ):

            return


        try:

            poll_miner(
                miner_id
            )

        except Exception:
            pass


        fresh = get_miner(
            miner_id
        )


        if not fresh:

            stable_polls = 0
            continue


        state = (
            fresh["last_state"]
            or "UNKNOWN"
        )


        final_state = state


        update_control_job(
            job_id,
            final_state=state,
            message=(
                "Waiting for ASIC to return; "
                f"current={state}; "
                f"stable="
                f"{stable_polls}/"
                f"{REBOOT_STABLE_POLLS}"
            ),
        )


        # Reboot is considered complete only when
        # the miner reaches a stable operational state.
        #
        # STARTING / RESTARTING proves that the device
        # returned, but does not yet mean the reboot
        # completed successfully.

        if state in (
            "MINING",
            "PAUSED",
        ):

            stable_polls += 1

        else:

            stable_polls = 0


        if (
            stable_polls
            >= REBOOT_STABLE_POLLS
        ):

            finish_control_job(
                job_id=job_id,
                miner=fresh,
                source=source,
                action="reboot",
                success=True,
                final_state=state,
                message=(
                    "Full reboot verified; "
                    f"transition={transition_state}; "
                    f"returned_state={state}; "
                    f"stable_polls="
                    f"{stable_polls}"
                ),
            )

            return


    # --------------------------------------------------------
    # RETURN TIMEOUT
    # --------------------------------------------------------

    fresh = get_miner(
        miner_id
    )


    finish_control_job(
        job_id=job_id,
        miner=(
            fresh
            or miner
        ),
        source=source,
        action="reboot",
        success=False,
        final_state=(
            (
                fresh["last_state"]
                if fresh
                else final_state
            )
            or "UNKNOWN"
        ),
        message=(
            "Reboot transition was observed, "
            "but ASIC did not return reliably "
            f"within {REBOOT_RETURN_TIMEOUT}s"
        ),
    )


def queue_reboot(
    miner_id,
):

    miner = get_miner(
        miner_id
    )


    if not miner:

        raise RuntimeError(
            "Miner not found"
        )


    if not miner["enabled"]:

        raise RuntimeError(
            "ASIC is disabled"
        )


    if miner["driver"] not in (
        "awesome",
        "bitmain_stock",
    ):

        raise RuntimeError(
            "Firmware does not support reboot"
        )


    # IMPORTANT:
    # Manual reboot does NOT create a manual schedule
    # override. Scheduler remains responsible for the
    # desired state after the device returns.

    with control_queue_lock:

        active = (
            get_active_control_job(
                miner_id
            )
        )


        if active:

            return {
                "queued":
                    False,

                "already_active":
                    True,

                "job_id":
                    active["id"],

                "status":
                    active["status"],

                "action":
                    active["action"],

                "target_state":
                    active[
                        "target_state"
                    ],
            }


        now = int(
            time.time()
        )


        conn = db()


        cur = conn.execute("""
            INSERT INTO control_jobs
            (
                created_at,
                miner_id,
                ip,
                name,
                source,
                action,
                target_state,
                status,
                attempts,
                max_attempts
            )

            VALUES (
                ?,
                ?,
                ?,
                ?,
                'MANUAL',
                'reboot',
                'REBOOTED',
                'QUEUED',
                0,
                1
            )
        """, (
            now,
            miner_id,
            miner["ip"],
            miner["name"],
        ))


        job_id = (
            cur.lastrowid
        )


        conn.execute(
            """
            UPDATE control_jobs
            SET source=?
            WHERE id=?
            """,
            (
                audit_source(
                    "MANUAL"
                ),
                job_id,
            ),
        )


        conn.commit()
        conn.close()


    log_event(
        source="MANUAL",
        action="REBOOT_QUEUED",
        miner=miner,
        success=True,
        message=(
            f"Job #{job_id}; "
            "full device reboot"
        ),
    )


    control_executor.submit(
        reboot_worker,
        job_id,
    )


    return {
        "queued":
            True,

        "already_active":
            False,

        "job_id":
            job_id,

        "status":
            "QUEUED",

        "action":
            "reboot",

        "target_state":
            "REBOOTED",
    }


# ============================================================
# VERIFIED CONTROL
# ============================================================

def delayed_poll(miner_id):

    time.sleep(3)

    poll_miner(
        miner_id
    )


def get_control_job(job_id):

    conn = db()

    row = conn.execute("""
        SELECT *
        FROM control_jobs
        WHERE id=?
    """, (
        job_id,
    )).fetchone()

    conn.close()

    return row


def get_active_control_job(miner_id):

    conn = db()

    row = conn.execute("""
        SELECT *
        FROM control_jobs

        WHERE
            miner_id=?
            AND status IN (
                'QUEUED',
                'RUNNING'
            )

        ORDER BY id DESC

        LIMIT 1
    """, (
        miner_id,
    )).fetchone()

    conn.close()

    return row


def update_control_job(
    job_id,
    **fields,
):

    if not fields:
        return

    allowed = {
        "started_at",
        "completed_at",
        "status",
        "attempts",
        "final_state",
        "message",
    }

    unknown = (
        set(fields)
        - allowed
    )

    if unknown:
        raise RuntimeError(
            "Invalid control job field: "
            + ", ".join(
                sorted(unknown)
            )
        )

    columns = []
    values = []

    for key, value in fields.items():

        columns.append(
            f"{key}=?"
        )

        values.append(
            value
        )

    values.append(
        job_id
    )

    conn = db()

    conn.execute(
        """
        UPDATE control_jobs
        SET %s
        WHERE id=?
        """
        % ", ".join(columns),
        values,
    )

    conn.commit()
    conn.close()


def set_last_command(
    miner_id,
    action,
):

    conn = db()

    conn.execute("""
        UPDATE miners

        SET
            last_action=?,
            last_action_at=?

        WHERE id=?
    """, (
        action.upper(),
        int(time.time()),
        miner_id,
    ))

    conn.commit()
    conn.close()


def control_target(action):

    if action == "pause":
        return "PAUSED"

    if action == "resume":
        return "MINING"

    raise RuntimeError(
        "Invalid control action"
    )


def control_timeout(
    action,
    miner,
):

    driver = miner["driver"]


    if driver == "bitmain_stock":

        if action == "pause":
            return CONTROL_STOCK_PAUSE_TIMEOUT

        if action == "resume":
            return CONTROL_STOCK_RESUME_TIMEOUT


    if driver == "awesome":

        if action == "pause":
            return CONTROL_AWESOME_PAUSE_TIMEOUT

        if action == "resume":
            return CONTROL_AWESOME_RESUME_TIMEOUT


    raise RuntimeError(
        "Invalid control action or driver"
    )


def control_retry_after(miner):

    if (
        miner["driver"]
        == "bitmain_stock"
    ):

        return (
            CONTROL_STOCK_RETRY_AFTER
        )

    return (
        CONTROL_AWESOME_RETRY_AFTER
    )


def should_retry_command(
    action,
    state,
):

    if action == "pause":

        return state in (
            "MINING",
            "STARTING",
            "IDLE",
            "UNKNOWN",
        )

    if action == "resume":

        # STARTING — нормальное состояние разгона.
        # Повторный Resume в этот момент не посылаем.
        return state in (
            "PAUSED",
            "IDLE",
            "UNKNOWN",
        )

    return False


def finish_control_job(
    job_id,
    miner,
    source,
    action,
    success,
    final_state,
    message,
):

    now = int(
        time.time()
    )

    update_control_job(
        job_id,

        status=(
            "VERIFIED"
            if success
            else "FAILED"
        ),

        completed_at=now,
        final_state=final_state,
        message=message,
    )

    log_event(
        source=source,

        action=(
            f"{action.upper()}_VERIFIED"
            if success
            else f"{action.upper()}_FAILED"
        ),

        miner=miner,
        success=success,

        message=message,
    )


def verified_control_worker(job_id):

    job = get_control_job(
        job_id
    )

    if not job:
        return


    miner_id = job[
        "miner_id"
    ]

    action = job[
        "action"
    ]

    source = job[
        "source"
    ]

    target = job[
        "target_state"
    ]


    miner = get_miner(
        miner_id
    )

    if not miner:

        update_control_job(
            job_id,
            status="FAILED",
            completed_at=int(
                time.time()
            ),
            message="Miner not found",
        )

        return


    update_control_job(
        job_id,
        status="RUNNING",
        started_at=int(
            time.time()
        ),
    )


    timeout_seconds = (
        control_timeout(
            action,
            miner,
        )
    )

    retry_after = (
        control_retry_after(
            miner
        )
    )

    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    attempts = 0
    last_send_monotonic = 0
    last_state = (
        miner["last_state"]
        or "UNKNOWN"
    )


    while (
        not stop_event.is_set()
        and time.monotonic()
        < deadline
    ):

        # ----------------------------------------------------
        # SEND / RETRY
        # ----------------------------------------------------

        need_send = False

        if attempts == 0:

            need_send = True

        elif (
            attempts
            < CONTROL_MAX_ATTEMPTS
            and
            (
                time.monotonic()
                - last_send_monotonic
            )
            >= retry_after
            and
            should_retry_command(
                action,
                last_state,
            )
        ):

            need_send = True


        if need_send:

            attempts += 1

            update_control_job(
                job_id,
                attempts=attempts,
            )


            try:

                miner = get_miner(
                    miner_id
                )

                result = control(
                    miner,
                    action,
                )

                last_send_monotonic = (
                    time.monotonic()
                )

                set_last_command(
                    miner_id,
                    action,
                )

                log_event(
                    source=source,

                    action=(
                        f"{action.upper()}_SENT"
                        if attempts == 1
                        else
                        f"{action.upper()}_RETRY"
                    ),

                    miner=miner,
                    success=True,

                    message=(
                        f"Attempt "
                        f"{attempts}/"
                        f"{CONTROL_MAX_ATTEMPTS}"
                        f"; response="
                        f"{result or 'OK'}"
                    ),
                )


            except Exception as exc:

                last_send_monotonic = (
                    time.monotonic()
                )

                message = (
                    f"Attempt "
                    f"{attempts}/"
                    f"{CONTROL_MAX_ATTEMPTS}"
                    f": "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                log_event(
                    source=source,
                    action=(
                        f"{action.upper()}_SEND_FAILED"
                    ),
                    miner=miner,
                    success=False,
                    message=message,
                )


                if (
                    attempts
                    >= CONTROL_MAX_ATTEMPTS
                ):

                    finish_control_job(
                        job_id=job_id,
                        miner=miner,
                        source=source,
                        action=action,
                        success=False,
                        final_state=last_state,
                        message=(
                            "Command could not be sent "
                            "after maximum attempts. "
                            + message
                        ),
                    )

                    return


                stop_event.wait(
                    CONTROL_RETRY_DELAY_ON_ERROR
                )

                continue


        # ----------------------------------------------------
        # VERIFY REAL ASIC STATE
        # ----------------------------------------------------

        if stop_event.wait(
            CONTROL_VERIFY_INTERVAL
        ):

            return


        poll_miner(
            miner_id
        )


        fresh = get_miner(
            miner_id
        )

        if not fresh:
            return


        last_state = (
            fresh["last_state"]
            or "UNKNOWN"
        )


        update_control_job(
            job_id,
            final_state=last_state,

            message=(
                f"Waiting for "
                f"{target}; "
                f"current={last_state}; "
                f"attempt="
                f"{attempts}/"
                f"{CONTROL_MAX_ATTEMPTS}; "
                f"timeout="
                f"{timeout_seconds}s"
            ),
        )


        if last_state == target:

            finish_control_job(
                job_id=job_id,
                miner=fresh,
                source=source,
                action=action,
                success=True,
                final_state=last_state,

                message=(
                    f"Verified {target}; "
                    f"attempts={attempts}"
                ),
            )

            return


    # --------------------------------------------------------
    # TIMEOUT
    # --------------------------------------------------------

    miner = get_miner(
        miner_id
    )

    if miner:

        last_state = (
            miner["last_state"]
            or last_state
        )


    finish_control_job(
        job_id=job_id,
        miner=miner,
        source=source,
        action=action,
        success=False,
        final_state=last_state,

        message=(
            f"Verification timeout. "
            f"Expected={target}; "
            f"actual={last_state}; "
            f"attempts={attempts}/"
            f"{CONTROL_MAX_ATTEMPTS}"
        ),
    )


def queue_control(
    miner_id,
    action,
    manual=False,
):

    if action not in (
        "pause",
        "resume",
    ):

        raise RuntimeError(
            "Invalid action"
        )


    miner = get_miner(
        miner_id
    )

    if not miner:

        raise RuntimeError(
            "Miner not found"
        )


    if not miner["enabled"]:

        raise RuntimeError(
            "ASIC is disabled"
        )


    if miner["driver"] not in (
        "awesome",
        "bitmain_stock",
    ):

        raise RuntimeError(
            "Firmware is not configured"
        )


    source = (
        audit_source(
            "MANUAL"
        )
        if manual
        else "SCHEDULER"
    )


    target = control_target(
        action
    )


    with control_queue_lock:

        active = (
            get_active_control_job(
                miner_id
            )
        )


        if active:

            return {
                "queued": False,
                "already_active": True,
                "job_id": active["id"],
                "status": active["status"],
                "action": active["action"],
                "target_state":
                    active["target_state"],
            }


        now = int(
            time.time()
        )


        if manual:

            override_until = int(
                next_transition()
                .timestamp()
            )

            conn = db()

            conn.execute("""
                UPDATE miners

                SET manual_override_until=?

                WHERE id=?
            """, (
                override_until,
                miner_id,
            ))

            conn.commit()
            conn.close()


        conn = db()

        cur = conn.execute("""
            INSERT INTO control_jobs
            (
                created_at,
                miner_id,
                ip,
                name,
                source,
                action,
                target_state,
                status,
                attempts,
                max_attempts
            )

            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                'QUEUED',
                0,
                ?
            )
        """, (
            now,
            miner_id,
            miner["ip"],
            miner["name"],
            source,
            action,
            target,
            CONTROL_MAX_ATTEMPTS,
        ))

        job_id = (
            cur.lastrowid
        )

        conn.commit()
        conn.close()


    log_event(
        source=source,
        action=(
            f"{action.upper()}_QUEUED"
        ),
        miner=miner,
        success=True,
        message=(
            f"Job #{job_id}; "
            f"target={target}"
        ),
    )


    control_executor.submit(
        verified_control_worker,
        job_id,
    )


    return {
        "queued": True,
        "already_active": False,
        "job_id": job_id,
        "status": "QUEUED",
        "action": action,
        "target_state": target,
    }


# ============================================================
# SCHEDULER
# ============================================================

def scheduler_loop():
    while not stop_event.is_set():

        scheduler_enabled = (
            get_setting(
                "scheduler_enabled",
                "0",
            )
            == "1"
        )

        if scheduler_enabled:

            now = datetime.now(
                MOSCOW
            )

            now_epoch = int(
                now.timestamp()
            )

            (
                target,
                active_rule,
                active_occurrence,
            ) = schedule_state_details(
                now
            )


            if active_rule is not None:

                schedule_mark_rule_seen(
                    active_rule,
                    active_occurrence,
                )

            conn = db()

            miners = conn.execute("""
                SELECT *
                FROM miners
                WHERE
                    enabled=1
                    AND schedule_enabled=1
                    AND driver IN (
                        'awesome',
                        'bitmain_stock'
                    )
            """).fetchall()

            conn.close()

            for miner in miners:

                override_until = (
                    miner[
                        "manual_override_until"
                    ]
                    or 0
                )

                if (
                    override_until
                    > now_epoch
                ):
                    continue

                state = (
                    miner[
                        "last_state"
                    ]
                    or "UNKNOWN"
                )

                last_seen = (
                    miner[
                        "last_seen"
                    ]
                    or 0
                )

                last_action_at = (
                    miner[
                        "last_action_at"
                    ]
                    or 0
                )

                if (
                    now_epoch
                    - last_seen
                    > 60
                ):
                    continue

                if (
                    now_epoch
                    - last_action_at
                    < CONTROL_COOLDOWN
                ):
                    continue

                action = None

                if target == "MINING":

                    if state in (
                        "PAUSED",
                        "IDLE",
                    ):

                        action = (
                            "resume"
                        )

                elif target == "PAUSED":

                    if state in (
                        "MINING",
                        "STARTING",
                        "IDLE",
                    ):

                        action = (
                            "pause"
                        )

                if action:

                    try:
                        queue_control(
                            miner["id"],
                            action,
                            manual=False,
                        )

                    except Exception:
                        pass

        stop_event.wait(
            SCHEDULER_INTERVAL
        )


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


REMOTE_WEB_SECRET = (
    app_config.REMOTE_WEB_SECRET
    .encode("utf-8")
)


REMOTE_WEB_BASE_DOMAIN = (
    app_config.REMOTE_WEB_BASE_DOMAIN
)


REMOTE_WEB_COOKIE_DOMAIN = (
    app_config.REMOTE_WEB_COOKIE_DOMAIN
    or
    (
        "."
        +
        REMOTE_WEB_BASE_DOMAIN
    )
)


REMOTE_WEB_ALLOWED_CIDR = (
    app_config.REMOTE_WEB_ALLOWED_CIDR
)


REMOTE_WEB_NETWORK = (
    ipaddress.ip_network(
        REMOTE_WEB_ALLOWED_CIDR,
        strict=False,
    )
)


REMOTE_WEB_TTL = app_config.REMOTE_WEB_TTL

REMOTE_WEB_COOKIE_NAME = (
    "asic_remote_session"
)


def remote_web_cookie_scope_valid():

    cookie_domain = (
        str(
            REMOTE_WEB_COOKIE_DOMAIN
            or ""
        )
        .strip()
        .lower()
        .lstrip(".")
    )

    public_domain = (
        str(
            app_config.PUBLIC_DOMAIN
            or ""
        )
        .strip()
        .lower()
        .strip(".")
    )


    if (
        not cookie_domain
        or
        not public_domain
    ):
        return False


    return (
        public_domain == cookie_domain
        or
        public_domain.endswith(
            "."
            +
            cookie_domain
        )
    )


def remote_web_b64encode(value):

    return (
        base64
        .urlsafe_b64encode(value)
        .rstrip(b"=")
        .decode("ascii")
    )


def remote_web_b64decode(value):

    value = str(value)

    value += (
        "="
        *
        (-len(value) % 4)
    )

    return base64.urlsafe_b64decode(
        value.encode("ascii")
    )


def remote_web_make_token(actor):

    if not REMOTE_WEB_SECRET:
        raise RuntimeError(
            "REMOTE_WEB_SECRET is not configured"
        )

    payload = {
        "actor": str(actor),
        "exp":
            int(time.time())
            +
            REMOTE_WEB_TTL,
    }

    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    signature = hmac.new(
        REMOTE_WEB_SECRET,
        raw,
        hashlib.sha256,
    ).digest()

    return (
        remote_web_b64encode(raw)
        +
        "."
        +
        remote_web_b64encode(signature)
    )


def remote_web_clear_cookie(
    response,
):

    response.delete_cookie(
        key=REMOTE_WEB_COOKIE_NAME,

        path="/",

        domain=(
            REMOTE_WEB_COOKIE_DOMAIN
        ),

        secure=True,
        httponly=True,
        samesite="lax",
    )

    return response


def remote_web_verify_token(token):

    if (
        not REMOTE_WEB_SECRET
        or
        not token
    ):
        return None

    try:

        encoded_payload, encoded_signature = (
            str(token).split(".", 1)
        )

        raw = remote_web_b64decode(
            encoded_payload
        )

        supplied_signature = (
            remote_web_b64decode(
                encoded_signature
            )
        )

        expected_signature = hmac.new(
            REMOTE_WEB_SECRET,
            raw,
            hashlib.sha256,
        ).digest()

        if not hmac.compare_digest(
            supplied_signature,
            expected_signature,
        ):
            return None

        payload = json.loads(
            raw.decode("utf-8")
        )

        if int(
            payload.get("exp", 0)
        ) < int(time.time()):
            return None

        actor = str(
            payload.get("actor", "")
        )

        if not actor.startswith("WEB:"):
            return None

        return payload

    except Exception:
        return None


def remote_web_host_for_ip(
    ip_value,
):

    if not app_config.REMOTE_WEB_ENABLED:
        return None


    return build_remote_web_host(
        ip_value,
        REMOTE_WEB_NETWORK,
        REMOTE_WEB_BASE_DOMAIN,
    )



def remote_web_miner_for_host(host):

    host = (
        str(host or "")
        .split(":", 1)[0]
        .strip()
        .lower()
    )

    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM miners
    """).fetchall()

    conn.close()

    for row in rows:

        expected = remote_web_host_for_ip(
            row["ip"]
        )

        if (
            expected
            and
            expected == host
        ):
            return row

    return None


app = FastAPI(
    title="OpenASICManager",
    lifespan=lifespan,
)



@app.middleware(
    "http"
)
async def audit_user_middleware(
    request,
    call_next,
):

    remote_user = (
        request.headers.get(
            "x-remote-user",
            "",
        )
    )


    username = (
        sanitize_audit_username(
            remote_user
        )
    )


    if username:

        actor = (
            "WEB:"
            +
            username
        )

    else:

        actor = "LOCAL"


    token = (
        audit_actor_context.set(
            actor
        )
    )


    try:

        response = await call_next(
            request
        )

        return response

    finally:

        audit_actor_context.reset(
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
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM miners
    """).fetchall()


    active_job_rows = conn.execute("""
        SELECT *

        FROM control_jobs

        WHERE status IN (
            'QUEUED',
            'RUNNING'
        )

        ORDER BY id DESC
    """).fetchall()


    conn.close()


    active_jobs = {}

    for job in active_job_rows:

        if (
            job["miner_id"]
            not in active_jobs
        ):

            active_jobs[
                job["miner_id"]
            ] = job


    miners = []

    for row in rows:

        miners.append(
            row
        )

    miners.sort(
        key=lambda row:
            ipaddress.ip_address(
                row["ip"]
            )
    )

    now = datetime.now(
        MOSCOW
    )

    result = []

    for row in miners:

        state = (
            row["last_state"]
            or "UNKNOWN"
        )

        if (
            row["driver"]
            == "unset"
        ):
            state = (
                "CONFIG_REQUIRED"
            )

        result.append({
            "id":
                row["id"],

            "name":
                row["name"],

            "ip":
                row["ip"],

            "driver":
                row["driver"],

            "detection_mode":
                (
                    row["detection_mode"]
                    or "AUTO"
                ),

            "enabled":
                bool(
                    row["enabled"]
                ),

            "schedule_enabled":
                bool(
                    row[
                        "schedule_enabled"
                    ]
                ),

            "model":
                row["model"],

            "firmware":
                row["firmware"],

            "state":
                state,

            "hashrate":
                row["hashrate"],

            "avg_hashrate":
                row[
                    "avg_hashrate"
                ],

            "temp":
                row["temp"],

            "power":
                row["power"],

            "pool":
                row["pool"],

            "last_seen":
                row["last_seen"],

            "last_error":
                row["last_error"],

            "last_action":
                row["last_action"],

            "manual_override_until":
                row[
                    "manual_override_until"
                ],

            "control_job":
                (
                    {
                        "id":
                            active_jobs[
                                row["id"]
                            ]["id"],

                        "status":
                            active_jobs[
                                row["id"]
                            ]["status"],

                        "action":
                            active_jobs[
                                row["id"]
                            ]["action"],

                        "target_state":
                            active_jobs[
                                row["id"]
                            ]["target_state"],

                        "attempts":
                            active_jobs[
                                row["id"]
                            ]["attempts"],

                        "max_attempts":
                            active_jobs[
                                row["id"]
                            ]["max_attempts"],

                        "message":
                            active_jobs[
                                row["id"]
                            ]["message"],
                    }

                    if row["id"]
                    in active_jobs

                    else None
                ),
        })

    return {
        "version": "0.1.2",

        "now":
            now.isoformat(),

        "scheduler_enabled":
            (
                get_setting(
                    "scheduler_enabled",
                    "0",
                )
                == "1"
            ),

        "desired_state":
            desired_state(
                now
            ),

        "next_transition":
            (
                next_transition(
                    now
                ).isoformat()
                if next_transition(
                    now
                )
                else None
            ),

        "miners":
            result,
    }




# ============================================================
# SCHEDULE RULES API
# ============================================================

@app.get(
    "/api/schedule/rules"
)
def api_schedule_rules():

    ensure_schedule_rules_schema()

    now = datetime.now(
        MOSCOW
    )

    conn = db()

    rules = conn.execute("""
        SELECT *

        FROM schedule_rules

        ORDER BY
            time_minutes,
            id
    """).fetchall()

    conn.close()


    desired, active_rule, _ = (
        schedule_state_details(
            now
        )
    )


    upcoming = next_transition(
        now
    )


    return {
        "timezone":
            TIMEZONE_NAME,

        "scheduler_enabled":
            (
                get_setting(
                    "scheduler_enabled",
                    "0",
                )
                ==
                "1"
            ),

        "desired_state":
            desired,

        "active_rule_id":
            (
                int(
                    active_rule[
                        "id"
                    ]
                )
                if active_rule
                else None
            ),

        "next_transition":
            (
                upcoming.isoformat()
                if upcoming
                else None
            ),

        "next_transition_label":
            (
                (
                    upcoming.strftime(
                        "%a %d.%m %H:%M"
                    )
                    +
                    " "
                    +
                    TIMEZONE_NAME
                )
                if upcoming
                else None
            ),

        "rules": [
            schedule_rule_dict(
                rule,
                now
            )
            for rule
            in rules
        ],
    }


@app.post(
    "/api/schedule/rules"
)
async def api_schedule_rule_create(
    request: Request,
):

    ensure_schedule_rules_schema()


    try:

        data = await request.json()

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid JSON body",
        )


    normalized = (
        schedule_normalize_input(
            data
        )
    )


    conflict = (
        schedule_conflicting_rule(
            normalized
        )
    )


    if conflict:

        raise HTTPException(
            status_code=409,
            detail=(
                "Schedule conflict with "
                f"Rule #{conflict['id']} "
                f"at "
                f"{schedule_time_string(conflict['time_minutes'])}"
            ),
        )


    now_epoch = int(
        time.time()
    )


    conn = db()

    cur = conn.execute("""
        INSERT INTO schedule_rules
        (
            enabled,
            action,
            time_minutes,
            days_mask,
            scope,
            comment,
            effective_from,
            created_at,
            updated_at
        )

        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, (
        1
        if normalized[
            "enabled"
        ]
        else 0,

        normalized[
            "action"
        ],

        normalized[
            "time_minutes"
        ],

        normalized[
            "days_mask"
        ],

        normalized[
            "scope"
        ],

        normalized[
            "comment"
        ],

        # New rules never affect an occurrence
        # that already happened before creation.
        now_epoch,

        now_epoch,
        now_epoch,
    ))


    rule_id = (
        cur.lastrowid
    )

    conn.commit()


    rule = conn.execute("""
        SELECT *

        FROM schedule_rules

        WHERE id=?
    """, (
        rule_id,
    )).fetchone()


    conn.close()


    log_event(
        source="SYSTEM",
        action="SCHEDULE_RULE_CREATE",
        success=True,
        message=(
            f"Rule #{rule_id}: "
            f"{normalized['action']} "
            f"{schedule_time_string(normalized['time_minutes'])} "
            f"{schedule_days_string(normalized['days_mask'])}"
            +
            (
                f" - {normalized['comment']}"
                if normalized[
                    "comment"
                ]
                else ""
            )
        ),
    )


    return {
        "success":
            True,

        "rule":
            schedule_rule_dict(
                rule
            ),
    }


@app.put(
    "/api/schedule/rules/{rule_id}"
)
async def api_schedule_rule_update(
    rule_id: int,
    request: Request,
):

    ensure_schedule_rules_schema()


    conn = db()

    current = conn.execute("""
        SELECT *

        FROM schedule_rules

        WHERE id=?
    """, (
        rule_id,
    )).fetchone()

    conn.close()


    if not current:

        raise HTTPException(
            status_code=404,
            detail="Schedule rule not found",
        )


    try:

        data = await request.json()

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid JSON body",
        )


    normalized = (
        schedule_normalize_input(
            data,
            current=current,
        )
    )


    conflict = (
        schedule_conflicting_rule(
            normalized,
            exclude_id=rule_id,
        )
    )


    if conflict:

        raise HTTPException(
            status_code=409,
            detail=(
                "Schedule conflict with "
                f"Rule #{conflict['id']} "
                f"at "
                f"{schedule_time_string(conflict['time_minutes'])}"
            ),
        )


    schedule_changed = (
        str(
            current[
                "action"
            ]
        )
        !=
        normalized[
            "action"
        ]

        or

        int(
            current[
                "time_minutes"
            ]
        )
        !=
        normalized[
            "time_minutes"
        ]

        or

        int(
            current[
                "days_mask"
            ]
        )
        !=
        normalized[
            "days_mask"
        ]

        or

        (
            not bool(
                current[
                    "enabled"
                ]
            )
            and
            normalized[
                "enabled"
            ]
        )
    )


    now_epoch = int(
        time.time()
    )


    effective_from = int(
        current[
            "effective_from"
        ]
        or 0
    )


    if (
        normalized[
            "enabled"
        ]
        and
        schedule_changed
    ):

        effective_from = (
            now_epoch
        )


    conn = db()

    conn.execute("""
        UPDATE schedule_rules

        SET
            enabled=?,
            action=?,
            time_minutes=?,
            days_mask=?,
            scope=?,
            comment=?,
            effective_from=?,
            last_run_key=NULL,
            updated_at=?

        WHERE id=?
    """, (
        1
        if normalized[
            "enabled"
        ]
        else 0,

        normalized[
            "action"
        ],

        normalized[
            "time_minutes"
        ],

        normalized[
            "days_mask"
        ],

        normalized[
            "scope"
        ],

        normalized[
            "comment"
        ],

        effective_from,
        now_epoch,
        rule_id,
    ))


    conn.commit()


    rule = conn.execute("""
        SELECT *

        FROM schedule_rules

        WHERE id=?
    """, (
        rule_id,
    )).fetchone()


    conn.close()


    log_event(
        source="SYSTEM",
        action="SCHEDULE_RULE_UPDATE",
        success=True,
        message=(
            f"Rule #{rule_id}: "
            f"{normalized['action']} "
            f"{schedule_time_string(normalized['time_minutes'])} "
            f"{schedule_days_string(normalized['days_mask'])}"
        ),
    )


    return {
        "success":
            True,

        "rule":
            schedule_rule_dict(
                rule
            ),
    }


@app.post(
    "/api/schedule/rules/{rule_id}/toggle"
)
def api_schedule_rule_toggle(
    rule_id: int,
):

    ensure_schedule_rules_schema()


    conn = db()

    current = conn.execute("""
        SELECT *

        FROM schedule_rules

        WHERE id=?
    """, (
        rule_id,
    )).fetchone()

    conn.close()


    if not current:

        raise HTTPException(
            status_code=404,
            detail="Schedule rule not found",
        )


    new_enabled = (
        not bool(
            current[
                "enabled"
            ]
        )
    )


    normalized = {
        "enabled":
            new_enabled,

        "action":
            current[
                "action"
            ],

        "time_minutes":
            current[
                "time_minutes"
            ],

        "days_mask":
            current[
                "days_mask"
            ],

        "scope":
            current[
                "scope"
            ],

        "comment":
            current[
                "comment"
            ],
    }


    if new_enabled:

        conflict = (
            schedule_conflicting_rule(
                normalized,
                exclude_id=rule_id,
            )
        )


        if conflict:

            raise HTTPException(
                status_code=409,
                detail=(
                    "Schedule conflict with "
                    f"Rule #{conflict['id']}"
                ),
            )


    now_epoch = int(
        time.time()
    )


    conn = db()

    conn.execute("""
        UPDATE schedule_rules

        SET
            enabled=?,
            effective_from=?,
            last_run_key=NULL,
            updated_at=?

        WHERE id=?
    """, (
        1
        if new_enabled
        else 0,

        (
            now_epoch
            if new_enabled
            else int(
                current[
                    "effective_from"
                ]
                or 0
            )
        ),

        now_epoch,
        rule_id,
    ))


    conn.commit()


    rule = conn.execute("""
        SELECT *

        FROM schedule_rules

        WHERE id=?
    """, (
        rule_id,
    )).fetchone()


    conn.close()


    log_event(
        source="SYSTEM",
        action=(
            "SCHEDULE_RULE_ENABLE"
            if new_enabled
            else
            "SCHEDULE_RULE_DISABLE"
        ),
        success=True,
        message=(
            f"Rule #{rule_id}"
        ),
    )


    return {
        "success":
            True,

        "rule":
            schedule_rule_dict(
                rule
            ),
    }


@app.delete(
    "/api/schedule/rules/{rule_id}"
)
def api_schedule_rule_delete(
    rule_id: int,
):

    ensure_schedule_rules_schema()


    conn = db()

    current = conn.execute("""
        SELECT *

        FROM schedule_rules

        WHERE id=?
    """, (
        rule_id,
    )).fetchone()


    if not current:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Schedule rule not found",
        )


    conn.execute("""
        DELETE FROM schedule_rules

        WHERE id=?
    """, (
        rule_id,
    ))


    conn.commit()
    conn.close()


    log_event(
        source="SYSTEM",
        action="SCHEDULE_RULE_DELETE",
        success=True,
        message=(
            f"Rule #{rule_id}: "
            f"{current['action']} "
            f"{schedule_time_string(current['time_minutes'])} "
            f"{schedule_days_string(current['days_mask'])}"
        ),
    )


    return {
        "success":
            True,

        "deleted":
            rule_id,
    }

@app.post(
    "/api/scheduler/toggle"
)
def scheduler_toggle():

    current = (
        get_setting(
            "scheduler_enabled",
            "0",
        )
        == "1"
    )

    new_value = not current

    set_setting(
        "scheduler_enabled",
        (
            "1"
            if new_value
            else "0"
        ),
    )

    log_event(
        source="SYSTEM",
        action=(
            "SCHEDULER_ON"
            if new_value
            else "SCHEDULER_OFF"
        ),
        success=True,
        message="Global scheduler changed",
    )

    return {
        "scheduler_enabled":
            new_value
    }


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


    conn = db()

    conn.execute("""
        UPDATE miners

        SET
            detection_mode='MANUAL',
            driver=?,
            username=?,
            password=?,
            manual_override_until=NULL,
            last_error=NULL,

            last_state=
                CASE
                    WHEN ?='unset'
                    THEN 'CONFIG_REQUIRED'
                    ELSE 'UNKNOWN'
                END,

            model=
                CASE
                    WHEN driver=?
                    THEN model
                    ELSE NULL
                END,

            firmware=
                CASE
                    WHEN driver=?
                    THEN firmware
                    ELSE NULL
                END

        WHERE id=?
    """, (
        driver,
        username,
        password,
        driver,
        driver,
        driver,
        miner_id,
    ))


    if driver == "unset":

        conn.execute("""
            UPDATE miners
            SET schedule_enabled=0
            WHERE id=?
        """, (
            miner_id,
        ))


    conn.commit()
    conn.close()


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

        conn = db()

        conn.execute("""
            UPDATE miners
            SET detection_mode='AUTO'
            WHERE id=?
        """, (
            miner_id,
        ))

        conn.commit()
        conn.close()


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


    conn = db()

    conn.execute("""
        UPDATE miners

        SET
            detection_mode='MANUAL',
            driver=?,
            username=?,
            password=?,
            model=?,
            firmware=?,
            manual_override_until=NULL,
            last_error=NULL,

            last_state=
                CASE
                    WHEN ?='unset'
                    THEN 'CONFIG_REQUIRED'

                    WHEN ?
                    THEN 'UNKNOWN'

                    ELSE last_state
                END

        WHERE id=?
    """, (
        driver,
        username,
        password,
        model or None,
        firmware or None,
        driver,
        1 if driver_changed else 0,
        miner_id,
    ))


    if driver == "unset":

        conn.execute("""
            UPDATE miners
            SET schedule_enabled=0
            WHERE id=?
        """, (
            miner_id,
        ))


    conn.commit()
    conn.close()


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


    conn = db()

    conn.execute("""
        UPDATE miners

        SET
            detection_mode='AUTO',
            driver=?,
            username=?,
            password=?,
            model=?,
            firmware=?,
            last_error=NULL,

            last_state=
                CASE
                    WHEN ?
                    THEN 'UNKNOWN'
                    ELSE last_state
                END

        WHERE id=?
    """, (
        driver,
        username,
        password,
        model,
        firmware,
        1 if driver_changed else 0,
        miner_id,
    ))

    conn.commit()
    conn.close()


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

    new_value = (
        0
        if miner["enabled"]
        else 1
    )

    conn = db()

    conn.execute("""
        UPDATE miners
        SET enabled=?
        WHERE id=?
    """, (
        new_value,
        miner_id,
    ))

    conn.commit()
    conn.close()

    if new_value:

        threading.Thread(
            target=poll_miner,
            args=(
                miner_id,
            ),
            daemon=True,
        ).start()

    return {
        "enabled":
            bool(new_value)
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

    if (
        miner["driver"]
        == "unset"
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Configure firmware first"
            ),
        )

    new_value = (
        0
        if miner[
            "schedule_enabled"
        ]
        else 1
    )

    conn = db()

    conn.execute("""
        UPDATE miners
        SET schedule_enabled=?
        WHERE id=?
    """, (
        new_value,
        miner_id,
    ))

    conn.commit()
    conn.close()

    return {
        "schedule_enabled":
            bool(new_value)
    }


@app.post(
    "/api/bulk/unconfigured-stock"
)
def unconfigured_to_stock():
    conn = db()

    rows = conn.execute("""
        SELECT id, ip
        FROM miners
        WHERE driver='unset'
    """).fetchall()

    ids = []

    for row in rows:

        conn.execute("""
            UPDATE miners

            SET
                driver='bitmain_stock',
                username=?,
                password=?,
                schedule_enabled=0,
                last_state='UNKNOWN',
                last_error=NULL

            WHERE id=?
        """, (
            app_config.BITMAIN_USERNAME,
            app_config.BITMAIN_PASSWORD,
            row["id"],
        ))

        ids.append(
            row["id"]
        )

    conn.commit()
    conn.close()

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

    value = (
        1
        if state == "on"
        else 0
    )

    conn = db()

    cur = conn.execute("""
        UPDATE miners
        SET schedule_enabled=?
        WHERE
            enabled=1
            AND driver IN (
                'awesome',
                'bitmain_stock'
            )
    """, (
        value,
    ))

    changed = cur.rowcount

    conn.commit()
    conn.close()

    log_event(
        source="SYSTEM",
        action=(
            "SCHEDULE_ALL_ON"
            if value
            else "SCHEDULE_ALL_OFF"
        ),
        success=True,
        message=f"Rows changed: {changed}",
    )

    return {
        "success": True,
        "schedule_enabled": bool(value),
        "changed": changed,
    }


@app.post(
    "/api/overrides/clear"
)
def clear_overrides():

    conn = db()

    cur = conn.execute("""
        UPDATE miners
        SET manual_override_until=NULL
        WHERE manual_override_until IS NOT NULL
    """)

    changed = cur.rowcount

    conn.commit()
    conn.close()

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

    limit = max(
        1,
        min(
            int(limit),
            500,
        ),
    )

    conn = db()

    rows = conn.execute("""
        SELECT
            id,
            ts,
            source,
            action,
            miner_id,
            ip,
            name,
            success,
            message

        FROM action_log

        ORDER BY id DESC

        LIMIT ?
    """, (
        limit,
    )).fetchall()

    conn.close()

    result = []

    for row in rows:

        result.append({
            "id":
                row["id"],

            "time":
                datetime.fromtimestamp(
                    row["ts"],
                    MOSCOW,
                ).isoformat(),

            "source":
                row["source"],

            "action":
                row["action"],

            "miner_id":
                row["miner_id"],

            "ip":
                row["ip"],

            "name":
                row["name"],

            "success":
                bool(
                    row["success"]
                ),

            "message":
                row["message"],
        })

    return {
        "logs": result
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


    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM control_jobs

        ORDER BY id DESC

        LIMIT ?
    """, (
        limit,
    )).fetchall()

    conn.close()


    result = []


    for row in rows:

        result.append({
            "id":
                row["id"],

            "created_at":
                row["created_at"],

            "started_at":
                row["started_at"],

            "completed_at":
                row["completed_at"],

            "miner_id":
                row["miner_id"],

            "ip":
                row["ip"],

            "name":
                row["name"],

            "source":
                row["source"],

            "action":
                row["action"],

            "target_state":
                row["target_state"],

            "status":
                row["status"],

            "attempts":
                row["attempts"],

            "max_attempts":
                row["max_attempts"],

            "final_state":
                row["final_state"],

            "message":
                row["message"],
        })


    return {
        "jobs": result
    }



@app.get(
    "/api/history/{miner_id}"
)
def api_history(
    miner_id: int,
    hours: int = 24,
):

    miner = get_miner(
        miner_id
    )

    if not miner:

        raise HTTPException(
            status_code=404,
            detail="Miner not found",
        )


    allowed_hours = {
        1,
        6,
        12,
        24,
        72,
        168,
        720,
        2160,
    }


    if hours not in allowed_hours:

        raise HTTPException(
            status_code=400,
            detail=(
                "Allowed hours: "
                "1,6,12,24,72,168,720,2160"
            ),
        )


    since = (
        int(time.time())
        -
        hours * 3600
    )


    conn = db()

    rows = conn.execute("""
        SELECT
            ts,
            state,
            hashrate,
            avg_hashrate,
            temp,
            power

        FROM telemetry

        WHERE
            miner_id=?
            AND ts>=?

        ORDER BY ts ASC
    """, (
        miner_id,
        since,
    )).fetchall()

    conn.close()


    points = []

    for row in rows:

        points.append({
            "time":
                datetime.fromtimestamp(
                    row["ts"],
                    MOSCOW,
                ).isoformat(),

            "state":
                row["state"],

            "hashrate":
                row["hashrate"],

            "avg_hashrate":
                row["avg_hashrate"],

            "temp":
                row["temp"],

            "power":
                row["power"],
        })


    return {
        "miner": {
            "id":
                miner["id"],

            "ip":
                miner["ip"],

            "name":
                miner["name"],

            "driver":
                miner["driver"],
        },

        "hours":
            hours,

        "points":
            points,
    }


@app.get(
    "/api/history/stats/summary"
)
def history_stats():

    conn = db()

    row = conn.execute("""
        SELECT
            COUNT(*) AS rows,
            MIN(ts) AS oldest,
            MAX(ts) AS newest

        FROM telemetry
    """).fetchone()

    conn.close()


    return {
        "rows":
            row["rows"],

        "oldest":
            (
                datetime.fromtimestamp(
                    row["oldest"],
                    MOSCOW,
                ).isoformat()

                if row["oldest"]
                else None
            ),

        "newest":
            (
                datetime.fromtimestamp(
                    row["newest"],
                    MOSCOW,
                ).isoformat()

                if row["newest"]
                else None
            ),

        "interval_seconds":
            TELEMETRY_INTERVAL,

        "retention_days":
            TELEMETRY_RETENTION_DAYS,
    }





# ============================================================
# TELEGRAM FARM SUMMARY
# ============================================================

def telegram_summary_last_date():

    conn = db()

    try:

        row = conn.execute(
            """
            SELECT value
            FROM settings
            WHERE key='telegram_summary_last_date'
            """
        ).fetchone()

        if not row:
            return None

        return row["value"]

    finally:
        conn.close()


def telegram_summary_set_last_date(
    value,
):

    conn = db()

    try:

        conn.execute(
            """
            INSERT INTO settings
            (
                key,
                value
            )
            VALUES
            (
                'telegram_summary_last_date',
                ?
            )
            ON CONFLICT(key)
            DO UPDATE SET
                value=excluded.value
            """,
            (
                str(value),
            ),
        )

        conn.commit()

    finally:
        conn.close()


def telegram_farm_summary_data():

    conn = db()

    try:

        miners = conn.execute(
            """
            SELECT
                id,
                name,
                ip,
                driver,
                enabled,
                last_state,
                hashrate,
                avg_hashrate,
                temp,
                power,
                last_seen
            FROM miners
            WHERE enabled=1
            ORDER BY ip
            """
        ).fetchall()


        issues = conn.execute(
            """
            SELECT
                i.id,
                i.miner_id,
                i.code,
                m.name,
                m.ip
            FROM issues i

            LEFT JOIN miners m
                ON m.id=i.miner_id

            WHERE i.status='ACTIVE'

            ORDER BY i.id DESC
            """
        ).fetchall()


    finally:
        conn.close()


    state_counts = {}

    total_hashrate = 0.0

    temperatures = []

    total_known_power = 0.0
    known_power_count = 0


    for miner in miners:

        if miner["driver"] == "unset":

            state = "CONFIG_REQUIRED"

        else:

            state = (
                miner["last_state"]
                or
                "UNKNOWN"
            ).upper()


        state_counts[state] = (
            state_counts.get(
                state,
                0,
            )
            +
            1
        )


        if miner["hashrate"] is not None:

            try:
                total_hashrate += float(
                    miner["hashrate"]
                )
            except Exception:
                pass


        if miner["temp"] is not None:

            try:
                temperatures.append(
                    float(
                        miner["temp"]
                    )
                )
            except Exception:
                pass


        if miner["power"] is not None:

            try:

                power = float(
                    miner["power"]
                )

                if power > 0:

                    total_known_power += power
                    known_power_count += 1

            except Exception:
                pass


    return {
        "miners":
            miners,

        "enabled_count":
            len(miners),

        "states":
            state_counts,

        "total_hashrate":
            total_hashrate,

        "temperatures":
            temperatures,

        "known_power":
            total_known_power,

        "known_power_count":
            known_power_count,

        "issues":
            issues,
    }


def telegram_summary_hashrate(
    value,
):

    try:
        value = float(value)
    except Exception:
        return "—"


    # Current DB values are TH/s.

    if value >= 1000:

        return (
            f"{value / 1000.0:.2f} PH/s"
        )


    return (
        f"{value:.1f} TH/s"
    )


def telegram_farm_summary():

    data = (
        telegram_farm_summary_data()
    )


    now = datetime.now(
        MOSCOW
    )


    states = data[
        "states"
    ]


    lines = [
        "📊 ASIC FARM SUMMARY",
        "",
        f"Farm: {ASIC_MANAGER_NAME}",
        (
            "Time: "
            +
            now.strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            )
        ),
        "",
        (
            "Enabled ASICs: "
            +
            str(
                data[
                    "enabled_count"
                ]
            )
        ),
        "",
    ]


    preferred_states = (
        "MINING",
        "PAUSED",
        "STARTING",
        "SHUTTING-DOWN",
        "OFFLINE",
        "UNKNOWN",
        "CONFIG_REQUIRED",
    )


    shown = set()


    for state in preferred_states:

        count = states.get(
            state,
            0,
        )

        if count:

            lines.append(
                f"{state}: {count}"
            )

            shown.add(
                state
            )


    # Preserve any future/driver-specific states.

    for state in sorted(
        states
    ):

        if state in shown:
            continue

        lines.append(
            f"{state}: {states[state]}"
        )


    lines.extend([
        "",
        (
            "Total hashrate: "
            +
            telegram_summary_hashrate(
                data[
                    "total_hashrate"
                ]
            )
        ),
    ])


    temperatures = data[
        "temperatures"
    ]


    if temperatures:

        average_temp = (
            sum(temperatures)
            /
            len(temperatures)
        )

        max_temp = max(
            temperatures
        )

        lines.append(
            "Temperature: "
            f"avg {average_temp:.1f}°C"
            " / "
            f"max {max_temp:.1f}°C"
        )

    else:

        lines.append(
            "Temperature: —"
        )


    if data[
        "known_power_count"
    ]:

        lines.append(
            "Known power: "
            f"{data['known_power'] / 1000.0:.2f} kW"
            " "
            f"({data['known_power_count']} ASICs)"
        )

    else:

        lines.append(
            "Known power: —"
        )


    issues = data[
        "issues"
    ]


    lines.extend([
        "",
        f"Active issues: {len(issues)}",
    ])


    if issues:

        lines.extend([
            "",
            "Problems:",
        ])


        max_issues = 10


        for issue in issues[
            :max_issues
        ]:

            name = (
                issue["name"]
                or
                f"ASIC #{issue['miner_id']}"
            )

            ip = (
                issue["ip"]
                or
                "unknown IP"
            )

            code = (
                issue["code"]
                or
                "UNKNOWN"
            )


            lines.append(
                f"• {name} ({ip}): {code}"
            )


        remaining = (
            len(issues)
            -
            max_issues
        )

        if remaining > 0:

            lines.append(
                f"• +{remaining} more"
            )


    return "\n".join(
        lines
    )


def telegram_send_farm_summary(
    force=False,
    source="SUMMARY",
):

    message = (
        telegram_farm_summary()
    )


    try:

        success, result = (
            telegram_send_message(
                message,
                force=force,
            )
        )


        if success:

            log_event(
                source=source,
                action="FARM_SUMMARY_SENT",
                success=True,
                message=result,
            )

            return {
                "success":
                    True,

                "message":
                    result,
            }


        log_event(
            source=source,
            action="FARM_SUMMARY_FAILED",
            success=False,
            message=result,
        )


        return {
            "success":
                False,

            "message":
                result,
        }


    except Exception as exc:

        message = (
            f"{type(exc).__name__}: "
            f"{exc}"
        )


        log_event(
            source=source,
            action="FARM_SUMMARY_FAILED",
            success=False,
            message=message,
        )


        return {
            "success":
                False,

            "message":
                message,
        }


def telegram_summary_due(
    now,
):

    if not TELEGRAM_SUMMARY_ENABLED:
        return False


    if now.weekday() not in (
        TELEGRAM_SUMMARY_WEEKDAYS
    ):

        return False


    target = (
        TELEGRAM_SUMMARY_HOUR
        *
        60
        +
        TELEGRAM_SUMMARY_MINUTE
    )


    current = (
        now.hour
        *
        60
        +
        now.minute
    )


    return (
        target
        <=
        current
        <
        (
            target
            +
            TELEGRAM_SUMMARY_WINDOW_MINUTES
        )
    )


def telegram_summary_loop():

    while not stop_event.wait(
        TELEGRAM_SUMMARY_INTERVAL
    ):

        try:

            if not (
                telegram_configured()
                and
                TELEGRAM_NOTIFICATIONS_ENABLED
                and
                TELEGRAM_SUMMARY_ENABLED
            ):
                continue


            now = datetime.now(
                MOSCOW
            )


            if not telegram_summary_due(
                now
            ):
                continue


            date_key = (
                now.strftime(
                    "%Y-%m-%d"
                )
            )


            if (
                telegram_summary_last_date()
                ==
                date_key
            ):
                continue


            result = (
                telegram_send_farm_summary(
                    force=False,
                    source="SCHEDULE",
                )
            )


            if result.get(
                "success"
            ):

                telegram_summary_set_last_date(
                    date_key
                )


        except Exception as exc:

            print(
                "Telegram summary loop failed: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


@app.on_event("startup")
def start_telegram_summary_loop():

    thread = threading.Thread(
        target=telegram_summary_loop,
        name="telegram-farm-summary",
        daemon=True,
    )

    thread.start()


@app.get(
    "/api/notifications/summary/status"
)
def api_notification_summary_status():

    return {
        "enabled":
            TELEGRAM_SUMMARY_ENABLED,

        "weekdays":
            [
                "MON",
                "TUE",
                "WED",
                "THU",
                "FRI",
            ],

        "time":
            (
                f"{TELEGRAM_SUMMARY_HOUR:02d}:"
                f"{TELEGRAM_SUMMARY_MINUTE:02d}"
            ),

        "timezone":
            TIMEZONE_NAME,

        "window_minutes":
            TELEGRAM_SUMMARY_WINDOW_MINUTES,

        "last_sent_date":
            telegram_summary_last_date(),
    }


@app.post(
    "/api/notifications/summary/test"
)
def api_notification_summary_test():

    result = (
        telegram_send_farm_summary(
            force=True,
            source="MANUAL_TEST",
        )
    )


    if not result.get(
        "success"
    ):

        raise HTTPException(
            status_code=502,
            detail=result.get(
                "message",
                "Summary send failed",
            ),
        )


    return result


# ============================================================
# NOTIFICATION API
# ============================================================


def telegram_transport_health():

    started = time.monotonic()

    if not telegram_configured():

        return {
            "ok": False,
            "proxy": bool(
                TELEGRAM_PROXY
            ),
            "http_status": None,
            "latency_ms": None,
            "error": "NOT_CONFIGURED",
        }


    proxies = (
        {
            "http":
                TELEGRAM_PROXY,

            "https":
                TELEGRAM_PROXY,
        }
        if TELEGRAM_PROXY
        else None
    )


    try:

        response = requests.get(
            "https://api.telegram.org/",
            proxies=proxies,
            timeout=(
                3,
                5,
            ),
            allow_redirects=False,
        )


        latency_ms = int(
            (
                time.monotonic()
                -
                started
            )
            * 1000
        )


        # Any normal HTTP response below 500 proves:
        #
        # OpenASICManager
        #   -> SOCKS
        #   -> SSH relay
        #   -> Telegram TLS/API
        #
        # Telegram root normally returns HTTP 302.

        ok = (
            200
            <=
            response.status_code
            <
            500
        )


        return {
            "ok":
                ok,

            "proxy":
                bool(
                    TELEGRAM_PROXY
                ),

            "http_status":
                response.status_code,

            "latency_ms":
                latency_ms,

            "error":
                None,
        }


    except Exception as exc:

        latency_ms = int(
            (
                time.monotonic()
                -
                started
            )
            * 1000
        )


        return {
            "ok":
                False,

            "proxy":
                bool(
                    TELEGRAM_PROXY
                ),

            "http_status":
                None,

            "latency_ms":
                latency_ms,

            # Do not expose proxy credentials or
            # full exception text through the API.
            "error":
                type(exc).__name__,
        }


@app.get(
    "/api/notifications/health"
)
def api_notifications_health():

    transport = (
        telegram_transport_health()
    )


    return {
        "provider":
            "telegram",

        "configured":
            telegram_configured(),

        "enabled":
            TELEGRAM_NOTIFICATIONS_ENABLED,

        "transport_ok":
            bool(
                transport.get(
                    "ok"
                )
            ),

        "transport":
            transport,
    }


@app.get(
    "/api/notifications/status"
)
def api_notifications_status():

    masked_chat = None


    if TELEGRAM_CHAT_ID:

        if len(
            TELEGRAM_CHAT_ID
        ) <= 4:

            masked_chat = (
                "*" * len(
                    TELEGRAM_CHAT_ID
                )
            )

        else:

            masked_chat = (
                "*"
                * (
                    len(
                        TELEGRAM_CHAT_ID
                    )
                    - 4
                )
                +
                TELEGRAM_CHAT_ID[-4:]
            )


    return {
        "provider":
            "telegram",

        "configured":
            telegram_configured(),

        "enabled":
            TELEGRAM_NOTIFICATIONS_ENABLED,

        "chat_id":
            masked_chat,

        "manager_name":
            ASIC_MANAGER_NAME,

        "events":
            sorted(
                TELEGRAM_EVENT_ACTIONS
            ),
    }


@app.post(
    "/api/notifications/test"
)
def api_notifications_test():

    if not telegram_configured():

        raise HTTPException(
            status_code=400,
            detail=(
                "Telegram is not configured"
            ),
        )


    message = (
        "✅ OpenASICManager Telegram test\\n"
        "\\n"
        f"Farm: {ASIC_MANAGER_NAME}\\n"
        "Status: notification channel works\\n"
        "Time: "
        +
        datetime.now(
            MOSCOW
        ).strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )
    )


    try:

        success, result = (
            telegram_send_message(
                message,
                force=True,
            )
        )


        return {
            "success":
                success,

            "message":
                result,
        }


    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        )


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
        min(
            int(limit),
            500,
        ),
    )


    conn = db()


    active_rows = conn.execute("""
        SELECT *

        FROM issues

        WHERE status='ACTIVE'

        ORDER BY
            CASE severity
                WHEN 'CRITICAL' THEN 1
                WHEN 'WARNING' THEN 2
                ELSE 3
            END,
            first_seen ASC
    """).fetchall()


    resolved_rows = conn.execute("""
        SELECT *

        FROM issues

        WHERE status='RESOLVED'

        ORDER BY resolved_at DESC

        LIMIT ?
    """, (
        limit,
    )).fetchall()


    conn.close()


    def convert(row):

        return {
            "id":
                row["id"],

            "miner_id":
                row["miner_id"],

            "ip":
                row["ip"],

            "name":
                row["name"],

            "code":
                row["code"],

            "severity":
                row["severity"],

            "status":
                row["status"],

            "first_seen":
                datetime.fromtimestamp(
                    row["first_seen"],
                    MOSCOW,
                ).isoformat(),

            "last_seen":
                datetime.fromtimestamp(
                    row["last_seen"],
                    MOSCOW,
                ).isoformat(),

            "resolved_at":
                (
                    datetime.fromtimestamp(
                        row["resolved_at"],
                        MOSCOW,
                    ).isoformat()

                    if row["resolved_at"]
                    else None
                ),

            "message":
                row["message"],
        }


    return {
        "active_count":
            len(active_rows),

        "active":
            [
                convert(row)
                for row in active_rows
            ],

        "recent_resolved":
            [
                convert(row)
                for row in resolved_rows
            ],

        "thresholds": {
            "offline_grace_seconds":
                ANOMALY_OFFLINE_GRACE,

            "hot_open_c":
                ANOMALY_HOT_TEMP,

            "hot_clear_c":
                ANOMALY_HOT_CLEAR,

            "hot_grace_seconds":
                ANOMALY_HOT_GRACE,

            "schedule_grace_seconds":
                ANOMALY_SCHEDULE_GRACE,
        },
    }


# ============================================================
# FARM HISTORY API
# ============================================================

@app.get(
    "/api/farm/history"
)
def api_farm_history(
    hours: int = 24,
):

    allowed_hours = {
        24,
        168,
        720,
        2160,
    }


    if hours not in allowed_hours:

        raise HTTPException(
            status_code=400,
            detail=(
                "Allowed hours: "
                "24,168,720,2160"
            ),
        )


    # --------------------------------------------------------
    # Downsampling
    #
    # 24h  -> 5 min
    # 7d   -> 15 min
    # 30d  -> 1 hour
    # 90d  -> 3 hours
    # --------------------------------------------------------

    if hours <= 24:
        bucket_seconds = 300

    elif hours <= 168:
        bucket_seconds = 900

    elif hours <= 720:
        bucket_seconds = 3600

    else:
        bucket_seconds = 10800


    since = (
        int(time.time())
        -
        hours * 3600
    )


    conn = db()


    # Сначала превращаем каждые 30 строк ASIC
    # в один снимок всей фермы.
    #
    # Затем при необходимости агрегируем
    # несколько снимков в более крупный bucket.

    rows = conn.execute("""
        WITH snapshots AS
        (
            SELECT
                ts,

                SUM(
                    CASE
                        WHEN state='MINING'
                        THEN 1
                        ELSE 0
                    END
                ) AS mining_count,

                SUM(
                    CASE
                        WHEN state='PAUSED'
                        THEN 1
                        ELSE 0
                    END
                ) AS paused_count,

                SUM(
                    CASE
                        WHEN state='STARTING'
                        THEN 1
                        ELSE 0
                    END
                ) AS starting_count,

                SUM(
                    CASE
                        WHEN state='OFFLINE'
                        THEN 1
                        ELSE 0
                    END
                ) AS offline_count,

                COUNT(*) AS total_count,

                SUM(
                    COALESCE(
                        hashrate,
                        0
                    )
                ) AS total_hashrate,

                SUM(
                    COALESCE(
                        power,
                        0
                    )
                ) AS known_power,

                MAX(temp) AS max_temp,

                AVG(
                    CASE
                        WHEN temp IS NOT NULL
                        THEN temp
                    END
                ) AS avg_temp

            FROM telemetry

            WHERE ts >= ?

            GROUP BY ts
        )

        SELECT
            (
                CAST(
                    ts / ?
                    AS INTEGER
                )
                * ?
            ) AS bucket_ts,

            AVG(
                mining_count
            ) AS mining_count,

            AVG(
                paused_count
            ) AS paused_count,

            AVG(
                starting_count
            ) AS starting_count,

            AVG(
                offline_count
            ) AS offline_count,

            AVG(
                total_count
            ) AS total_count,

            AVG(
                total_hashrate
            ) AS total_hashrate,

            AVG(
                known_power
            ) AS known_power,

            MAX(
                max_temp
            ) AS max_temp,

            AVG(
                avg_temp
            ) AS avg_temp

        FROM snapshots

        GROUP BY bucket_ts

        ORDER BY bucket_ts ASC
    """, (
        since,
        bucket_seconds,
        bucket_seconds,
    )).fetchall()


    # --------------------------------------------------------
    # Problem ASICs in selected period
    #
    # Пока проблемой считаем:
    # - хотя бы один OFFLINE snapshot
    # - температура >= 85 C
    #
    # Hashrate threshold намеренно не задаём:
    # у разных профилей он может отличаться.
    # --------------------------------------------------------

    problem_rows = conn.execute("""
        SELECT
            miner_id,
            ip,
            name,
            driver,

            COUNT(*) AS samples,

            SUM(
                CASE
                    WHEN state='OFFLINE'
                    THEN 1
                    ELSE 0
                END
            ) AS offline_samples,

            SUM(
                CASE
                    WHEN temp >= 85
                    THEN 1
                    ELSE 0
                END
            ) AS critical_temp_samples,

            MAX(temp) AS max_temp,

            AVG(
                CASE
                    WHEN state='MINING'
                    AND hashrate IS NOT NULL

                    THEN hashrate
                END
            ) AS avg_mining_hashrate

        FROM telemetry

        WHERE ts >= ?

        GROUP BY
            miner_id,
            ip,
            name,
            driver

        HAVING
            SUM(
                CASE
                    WHEN state='OFFLINE'
                    THEN 1
                    ELSE 0
                END
            ) > 0

            OR

            SUM(
                CASE
                    WHEN temp >= 85
                    THEN 1
                    ELSE 0
                END
            ) > 0

        ORDER BY
            offline_samples DESC,
            critical_temp_samples DESC,
            max_temp DESC
    """, (
        since,
    )).fetchall()


    current_rows = conn.execute("""
        SELECT
            last_state AS state,
            hashrate,
            power,
            temp

        FROM miners

        WHERE
            enabled=1
            AND driver IN (
                'awesome',
                'bitmain_stock'
            )
    """).fetchall()


    conn.close()


    points = []


    for row in rows:

        points.append({
            "time":
                datetime.fromtimestamp(
                    row["bucket_ts"],
                    MOSCOW,
                ).isoformat(),

            "mining":
                row["mining_count"],

            "paused":
                row["paused_count"],

            "starting":
                row["starting_count"],

            "offline":
                row["offline_count"],

            "total":
                row["total_count"],

            "hashrate":
                row["total_hashrate"],

            "power":
                row["known_power"],

            "max_temp":
                row["max_temp"],

            "avg_temp":
                row["avg_temp"],
        })


    problems = []


    for row in problem_rows:

        problems.append({
            "miner_id":
                row["miner_id"],

            "ip":
                row["ip"],

            "name":
                row["name"],

            "driver":
                row["driver"],

            "samples":
                row["samples"],

            "offline_samples":
                row["offline_samples"],

            "critical_temp_samples":
                row["critical_temp_samples"],

            "max_temp":
                row["max_temp"],

            "avg_mining_hashrate":
                row["avg_mining_hashrate"],
        })


    current = {
        "total": len(current_rows),

        "mining":
            sum(
                1
                for row in current_rows
                if row["state"] == "MINING"
            ),

        "paused":
            sum(
                1
                for row in current_rows
                if row["state"] == "PAUSED"
            ),

        "starting":
            sum(
                1
                for row in current_rows
                if row["state"] == "STARTING"
            ),

        "offline":
            sum(
                1
                for row in current_rows
                if row["state"] == "OFFLINE"
            ),

        "hashrate":
            sum(
                float(
                    row["hashrate"]
                    or 0
                )
                for row in current_rows
            ),

        "power":
            sum(
                float(
                    row["power"]
                    or 0
                )
                for row in current_rows
            ),

        "max_temp":
            max(
                [
                    float(row["temp"])
                    for row in current_rows
                    if row["temp"] is not None
                ],
                default=None,
            ),
    }


    return {
        "hours":
            hours,

        "bucket_seconds":
            bucket_seconds,

        "current":
            current,

        "points":
            points,

        "problems":
            problems,
    }


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


    conn = db()

    rows = conn.execute("""
        SELECT
            id,
            ip,
            driver,
            name

        FROM miners
    """).fetchall()

    conn.close()


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

        conn = db()

        existing = conn.execute("""
            SELECT
                id,
                name,
                driver

            FROM miners

            WHERE ip=?
        """, (
            ip,
        )).fetchone()

        conn.close()


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


        conn = db()

        same_name = conn.execute("""
            SELECT id
            FROM miners
            WHERE name=?
        """, (
            candidate_name,
        )).fetchone()


        if same_name:

            candidate_name = (
                "ASIC-"
                + ip.replace(
                    ".",
                    "-",
                )
            )


        try:

            cur = conn.execute("""
                INSERT INTO miners
                (
                    name,
                    ip,
                    driver,
                    username,
                    password,
                    enabled,
                    schedule_enabled,
                    model,
                    firmware,
                    last_state
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    1,
                    0,
                    ?,
                    ?,
                    'UNKNOWN'
                )
            """, (
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
            ))

            miner_id = cur.lastrowid

            conn.commit()

        except sqlite3.IntegrityError:

            conn.rollback()

            existing = conn.execute("""
                SELECT id
                FROM miners
                WHERE ip=?
            """, (
                ip,
            )).fetchone()

            conn.close()

            results.append({
                "ip": ip,
                "success": True,
                "status":
                    "already_managed",
                "id":
                    (
                        existing["id"]
                        if existing
                        else None
                    ),
            })

            continue


        conn.close()


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

HTML = r"""
<!DOCTYPE html>

<html lang="ru">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>OpenASICManager</title>

<style>

:root {
    color-scheme: dark;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 22px;

    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background: #111318;
    color: #e7e9ee;
}

.container {
    max-width: 1800px;
    margin: auto;
}

.header {
    display: flex;
    justify-content: space-between;
    gap: 20px;
    align-items: flex-start;
}

h1 {
    margin: 0;
    font-size: 28px;
}

h3 {
    margin-top: 0;
}

.muted {
    color: #9299a8;
}

.small {
    font-size: 12px;
}

.cards {
    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(140px, 1fr)
        );

    gap: 10px;
    margin: 18px 0;
}

.card {
    background: #1a1d24;

    border:
        1px solid #2b303b;

    border-radius: 12px;

    padding: 15px;
}

.value {
    font-size: 26px;
    font-weight: 700;
    margin-top: 4px;
}

.toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 9px;
    margin: 14px 0;
}

button {
    border: 0;
    border-radius: 8px;

    padding: 9px 13px;

    font-weight: 650;
    cursor: pointer;
}

button:hover {
    opacity: .85;
}

.start {
    background: #36a269;
    color: white;
}

.stop {
    background: #c84b50;
    color: white;
}

.secondary {
    background: #3c4350;
    color: white;
}

.warning-button {
    background: #9a722c;
    color: white;
}

.scheduler-on {
    background: #36a269;
    color: white;
}

.scheduler-off {
    background: #6a707c;
    color: white;
}

.mini {
    padding: 6px 9px;
    font-size: 12px;
}


.sortable-th {
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
}

.sortable-th:hover {
    color: #67b7ff;
}

.sort-mark {
    margin-left: 4px;
    color: #67b7ff;
    font-size: 11px;
}

.miner-search {
    width: 190px;

    background: #111318;
    color: #e7e9ee;

    border:
        1px solid #343b48;

    border-radius: 7px;

    padding: 7px 9px;

    outline: none;
}

.miner-search:focus {
    border-color: #67b7ff;
}

.search-count {
    min-width: 46px;
    text-align: right;
}

.table-wrap {
    overflow-x: auto;

    border:
        1px solid #2b303b;

    border-radius: 12px;
}

table {
    width: 100%;

    min-width: 1450px;

    border-collapse: collapse;
}

th,
td {
    text-align: left;

    padding: 9px 9px;

    border-bottom:
        1px solid #282d36;

    white-space: nowrap;
}

th {
    background: #1b1f27;

    color: #aeb4c0;

    font-size: 12px;
}

td {
    background: #16191f;
}

tr.disabled td {
    opacity: .46;
}

tr.row-error td {
    background: #22181a;
}

.state {
    font-weight: 700;
}

.MINING {
    color: #4fd38a;
}

.PAUSED {
    color: #f1c75b;
}

.STARTING {
    color: #67b7ff;
}

.OFFLINE {
    color: #f26d72;
}

.IDLE,
.UNKNOWN {
    color: #adb4c0;
}

.CONFIG_REQUIRED {
    color: #d09b50;
}

.temp-ok {
    color: #4fd38a;
    font-weight: 700;
}

.temp-warning {
    color: #f1c75b;
    font-weight: 700;
}

.temp-critical {
    color: #f26d72;
    font-weight: 800;
}

.error {
    color: #f26d72;
    font-size: 11px;
    margin-top: 3px;
}

select {
    background: #111318;
    color: white;

    border:
        1px solid #343b48;

    border-radius: 7px;

    padding: 7px;
}

a {
    color: #78b7ff;
    text-decoration: none;
}

a:hover {
    text-decoration: underline;
}

.schedule {
    margin-top: 18px;
}

.log-card {
    margin-top: 18px;
}

.log-table {
    min-width: 900px;
}

.log-ok {
    color: #4fd38a;
    font-weight: 700;
}

.log-fail {
    color: #f26d72;
    font-weight: 700;
}

.source-scheduler {
    color: #67b7ff;
}

.source-manual {
    color: #f1c75b;
}

.source-system {
    color: #adb4c0;
}

.filter-box {
    display: flex;
    align-items: center;
    gap: 8px;

    margin-left: auto;
}


.history-button {
    background: #3c4350;
    color: white;
}

.modal-backdrop {
    display: none;

    position: fixed;
    z-index: 5000;

    left: 0;
    top: 0;

    width: 100vw;
    height: 100vh;

    background: rgba(0, 0, 0, .72);

    overflow-y: auto;

    padding: 25px;
}

.modal-backdrop.open {
    display: block;
}

.history-modal {
    max-width: 1250px;
    margin: 20px auto;

    background: #15181e;

    border:
        1px solid #343b48;

    border-radius: 14px;

    padding: 20px;

    box-shadow:
        0 10px 50px
        rgba(0,0,0,.5);
}

.history-header {
    display: flex;

    justify-content:
        space-between;

    align-items:
        flex-start;

    gap: 15px;

    margin-bottom: 18px;
}

.history-controls {
    display: flex;
    flex-wrap: wrap;

    align-items: center;

    gap: 10px;

    margin-bottom: 18px;
}

.chart-card {
    background: #111318;

    border:
        1px solid #2b303b;

    border-radius: 10px;

    padding: 15px;

    margin-bottom: 14px;
}

.chart-title {
    font-weight: 700;
    margin-bottom: 8px;
}

.chart-summary {
    color: #9299a8;
    font-size: 12px;
    margin-bottom: 8px;
}

.chart-container {
    width: 100%;
    overflow-x: hidden;
}

.chart-container canvas {
    display: block;
    width: 100%;
    height: 240px;
}

.history-status {
    margin: 10px 0;
    color: #9299a8;
}

.close-button {
    background: #c84b50;
    color: white;
}


.farm-summary-grid {
    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(130px, 1fr)
        );

    gap: 10px;

    margin-bottom: 18px;
}

.farm-summary-item {
    background: #111318;

    border:
        1px solid #2b303b;

    border-radius: 9px;

    padding: 11px;
}

.farm-summary-value {
    margin-top: 4px;

    font-size: 21px;
    font-weight: 700;
}

.problem-ok {
    color: #4fd38a;
    font-weight: 700;
}

.problem-warning {
    color: #f1c75b;
    font-weight: 700;
}

.problem-critical {
    color: #f26d72;
    font-weight: 800;
}


.issue-button-active {
    background: #b53c43 !important;
    color: white !important;
}

.issue-critical {
    color: #f26d72;
    font-weight: 800;
}

.issue-warning {
    color: #f1c75b;
    font-weight: 700;
}

.issue-resolved {
    color: #4fd38a;
    font-weight: 700;
}

.issue-section {
    margin-top: 18px;
}


.reboot-button {
    background: #a45c24 !important;
    color: white !important;
}

.reboot-button:hover {
    filter: brightness(1.12);
}

@media (
    max-width: 900px
) {

    body {
        padding: 12px;
    }

    .header {
        display: block;
    }

    .filter-box {
        margin-left: 0;
    }
}



/* ==========================================================
   v0.1.0 Safety & Usability
   ========================================================== */

.status-card-interactive {
    cursor: pointer;
    user-select: none;

    transition:
        transform 0.12s ease,
        border-color 0.12s ease,
        box-shadow 0.12s ease;
}

.status-card-interactive:hover {
    transform: translateY(-1px);
    border-color: #526176;
}

.status-card-active {
    border-color: #67b7ff !important;

    box-shadow:
        0 0 0 1px
        rgba(103, 183, 255, 0.25);
}

.status-card-warning {
    border-color: #b68432 !important;
}

.status-card-alert {
    border-color: #c94f57 !important;

    box-shadow:
        0 0 0 1px
        rgba(201, 79, 87, 0.18);
}

#issuesButton.issues-alert {
    border-color: #c94f57;
    background: #713239;
}

.clear-search-button {
    min-width: 32px;
    padding-left: 8px;
    padding-right: 8px;
}


/*
 Main ASIC table sticky header.
*/
.miner-table thead th {
    position: sticky;
    top: 0;
    z-index: 10;

    background: #1a1e26;

    box-shadow:
        0 1px 0
        rgba(255,255,255,0.08);
}



/* ==========================================================
   v0.1.0 User Session UX
   ========================================================== */

.auth-user-bar {
    display: flex;
    justify-content: flex-end;
    align-items: center;

    gap: 10px;

    margin:
        0
        0
        14px
        0;
}

.auth-user-name {
    color: #e7e9ee;
    font-weight: 700;
}

.auth-local {
    color: #9aa3b2;
}



/* ============================================================
   SCHEDULE RULES v1.5
   ============================================================ */

.schedule-rules-card {
    margin-top: 16px;
}

.schedule-rules-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 15px;
    flex-wrap: wrap;
    margin-bottom: 16px;
}

.schedule-summary-grid {
    display: grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(180px, 1fr)
        );
    gap: 10px;
    margin-bottom: 14px;
}

.schedule-summary-item {
    background: #111318;
    border: 1px solid #2b303b;
    border-radius: 9px;
    padding: 11px 13px;
}

.schedule-summary-item strong {
    display: block;
    margin-top: 4px;
}

.schedule-rules-table td {
    vertical-align: middle;
}

.schedule-rule-status {
    display: inline-block;
    min-width: 44px;
    text-align: center;
    padding: 3px 7px;
    border-radius: 5px;
    font-size: 11px;
    font-weight: 700;
}

.schedule-rule-status.on {
    background: rgba(54, 179, 126, .18);
    color: #6fd6a8;
}

.schedule-rule-status.off {
    background: rgba(130, 139, 154, .15);
    color: #9299a8;
}

.schedule-rule-action {
    font-weight: 700;
}

.schedule-rule-action.pause {
    color: #f1b86b;
}

.schedule-rule-action.resume {
    color: #72d3a8;
}

.schedule-rule-actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
}

.schedule-rule-modal {
    max-width: 720px;
}

.schedule-rule-form-grid {
    display: grid;
    grid-template-columns:
        repeat(
            2,
            minmax(0, 1fr)
        );
    gap: 16px;
}

.schedule-rule-field {
    display: flex;
    flex-direction: column;
    gap: 7px;
}

.schedule-rule-field.full {
    grid-column: 1 / -1;
}

.schedule-rule-field label {
    color: #9299a8;
    font-size: 12px;
}

.schedule-rule-field input[type="text"],
.schedule-rule-field input[type="time"],
.schedule-rule-field select {
    width: 100%;
    box-sizing: border-box;

    background: #111318;
    color: white;

    border: 1px solid #343b48;
    border-radius: 7px;

    padding: 9px 11px;
}

.schedule-days {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
}

.schedule-day {
    display: flex;
    align-items: center;
    gap: 5px;

    background: #111318;
    border: 1px solid #343b48;
    border-radius: 7px;

    padding: 7px 9px;
    cursor: pointer;
}

.schedule-enabled-row {
    display: flex;
    align-items: center;
    gap: 8px;
}

.schedule-rule-form-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 20px;
}

.schedule-rule-error {
    display: none;
    margin-top: 14px;
    padding: 10px 12px;
    border-radius: 7px;

    background: rgba(190, 60, 60, .14);
    border: 1px solid rgba(220, 80, 80, .35);
    color: #f0a0a0;
}

.schedule-rule-error.show {
    display: block;
}

@media (max-width: 700px) {

    .schedule-rule-form-grid {
        grid-template-columns: 1fr;
    }

    .schedule-rule-field.full {
        grid-column: auto;
    }
}




/* ============================================================
   FIRMWARE AUTO / MANUAL v0.1.0
   ============================================================ */

.firmware-cell {
    min-width: 145px;
}

.firmware-mode {
    display: inline-block;

    padding: 2px 6px;
    margin-right: 5px;

    border-radius: 5px;

    font-size: 10px;
    font-weight: 700;
}

.firmware-mode.auto {
    color: #72d3a8;
    background: rgba(54,179,126,.16);
}

.firmware-mode.manual {
    color: #f1b86b;
    background: rgba(221,158,72,.16);
}

.firmware-editor-modal {
    max-width: 720px;
}

.firmware-editor-grid {
    display: grid;
    grid-template-columns:
        repeat(
            2,
            minmax(0,1fr)
        );

    gap: 16px;
}

.firmware-editor-field {
    display: flex;
    flex-direction: column;
    gap: 7px;
}

.firmware-editor-field.full {
    grid-column: 1 / -1;
}

.firmware-editor-field label {
    color: #9299a8;
    font-size: 12px;
}

.firmware-editor-field input,
.firmware-editor-field select {
    box-sizing: border-box;
    width: 100%;

    padding: 9px 11px;

    color: white;
    background: #111318;

    border: 1px solid #343b48;
    border-radius: 7px;
}

.firmware-editor-buttons {
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;

    gap: 10px;
    margin-top: 20px;
}

.firmware-editor-right {
    display: flex;
    gap: 8px;
}

.firmware-editor-error {
    display: none;

    margin-top: 14px;
    padding: 10px 12px;

    color: #f0a0a0;

    background:
        rgba(190,60,60,.14);

    border:
        1px solid
        rgba(220,80,80,.35);

    border-radius: 7px;
}

.firmware-editor-error.show {
    display: block;
}

@media (max-width:700px) {

    .firmware-editor-grid {
        grid-template-columns: 1fr;
    }

    .firmware-editor-field.full {
        grid-column: auto;
    }
}


</style>

</head>


<body>

<div class="container">

<div
    id="notificationHealthPanel"
    class="card"
    style="
        margin:0 0 14px 0;
        padding:10px 14px;
        display:flex;
        align-items:center;
        justify-content:space-between;
        gap:14px;
        flex-wrap:wrap;
    "
>

    <div>
        <strong>
            Notifications
        </strong>

        <div
            id="notificationHealthDetail"
            class="muted small"
            style="margin-top:3px"
        >
            Checking notification channel...
        </div>
    </div>


    <div
        style="
            display:flex;
            gap:8px;
            align-items:center;
            flex-wrap:wrap;
        "
    >

        <span
            id="telegramStatusBadge"
            style="
                display:inline-block;
                padding:6px 10px;
                border-radius:999px;
                background:#555b66;
                color:white;
                font-size:12px;
                font-weight:700;
            "
        >
            Telegram CHECK
        </span>


        <span
            id="telegramTunnelBadge"
            style="
                display:inline-block;
                padding:6px 10px;
                border-radius:999px;
                background:#555b66;
                color:white;
                font-size:12px;
                font-weight:700;
            "
        >
            Tunnel CHECK
        </span>

    </div>

</div>




<div
    id="farmSummaryPanel"
    class="card"
    style="
        margin:0 0 14px 0;
        padding:10px 14px;
        display:flex;
        align-items:center;
        justify-content:space-between;
        gap:14px;
        flex-wrap:wrap;
    "
>

    <div>

        <strong>
            Farm Summary
        </strong>

        <div
            id="farmSummaryDetail"
            class="muted small"
            style="margin-top:3px"
        >
            Loading summary schedule...
        </div>

        <div
            id="farmSummaryResult"
            class="small"
            style="
                margin-top:5px;
                min-height:16px;
            "
        ></div>

    </div>


    <div
        style="
            display:flex;
            gap:8px;
            align-items:center;
            flex-wrap:wrap;
        "
    >

        <span
            id="farmSummaryStatusBadge"
            style="
                display:inline-block;
                padding:6px 10px;
                border-radius:999px;
                background:#555b66;
                color:white;
                font-size:12px;
                font-weight:700;
            "
        >
            Summary CHECK
        </span>


        <button
            id="sendFarmSummaryButton"
            class="secondary mini"
            onclick="sendFarmSummary()"
        >
            SEND SUMMARY
        </button>

    </div>

</div>

<div class="header">

    <div>

        <h1>
            OpenASICManager
            <span class="muted small">
                <span id="appVersion">v0.1.2</span>
            </span>
        </h1>

        <div class="muted">
            Dynamic inventory
            · ASIC discovery
            · Moscow time
        </div>

    </div>

    <div
        id="clock"
        class="muted"
    ></div>

</div>



<div
    id="authUserBar"
    class="auth-user-bar"
>

    <span class="muted">
        Logged in:
    </span>

    <span
        id="authUserName"
        class="auth-user-name"
    >
        —
    </span>

    <button
        id="authLogoutButton"
        class="secondary mini"
        type="button"
        onclick="switchAuthenticatedUser()"
        disabled
    >
        LOG OUT
    </button>

</div>


<div class="cards">

    <div class="card">

        <div class="muted">
            ONLINE
        </div>

        <div
            id="countOnline"
            class="value"
        >
            —
        </div>

    </div>


    <div class="card">

        <div class="muted">
            MINING
        </div>

        <div
            id="countMining"
            class="value"
        >
            —
        </div>

    </div>


    <div class="card">

        <div class="muted">
            PAUSED
        </div>

        <div
            id="countPaused"
            class="value"
        >
            —
        </div>

    </div>


    <div class="card">

        <div class="muted">
            OFFLINE
        </div>

        <div
            id="countOffline"
            class="value"
        >
            —
        </div>

    </div>


    <div class="card">

        <div class="muted">
            SCHEDULE
        </div>

        <div
            id="countSchedule"
            class="value"
        >
            —
        </div>

    </div>


    <div class="card">

        <div class="muted">
            HASHRATE
        </div>

        <div
            id="totalHash"
            class="value"
        >
            —
        </div>

    </div>


    <div class="card">

        <div class="muted">
            KNOWN POWER
        </div>

        <div
            id="totalPower"
            class="value"
        >
            —
        </div>

    </div>


    <div class="card">

        <div class="muted">
            OVERRIDES
        </div>

        <div
            id="countOverrides"
            class="value"
        >
            —
        </div>

    </div>


<div class="card">

    <div class="muted">
        TRANSITION
    </div>

    <div
        class="value"
        id="transitionCount"
    >
        0
    </div>

</div>


<div class="card">

    <div class="muted">
        DISABLED
    </div>

    <div
        class="value"
        id="disabledCount"
    >
        0
    </div>

</div>


<div class="card">

    <div class="muted">
        UNKNOWN/CONFIG
    </div>

    <div
        class="value"
        id="unknownConfigCount"
    >
        0
    </div>

</div>

</div>


<div class="toolbar">

    <button
        class="start"
        onclick="allAction('resume')"
    >
        ▶ RESUME ALL
    </button>

    <button
        class="stop"
        onclick="allAction('pause')"
    >
        ⏸ PAUSE ALL
    </button>

    <button
        id="schedulerButton"
        class="scheduler-off"
        onclick="toggleScheduler()"
    >
        Scheduler: OFF
    </button>

    <button
        class="secondary"
        onclick="scheduleAll('on')"
    >
        Schedule ON ALL
    </button>

    <button
        class="secondary"
        onclick="scheduleAll('off')"
    >
        Schedule OFF ALL
    </button>

    <button
        class="warning-button"
        onclick="clearOverrides()"
    >
        Clear Overrides
    </button>


    <button
        class="secondary"
        onclick="openFarmHistory()"
    >
        FARM HISTORY
    </button>


    <button
        id="issuesButton"
        class="secondary"
        onclick="openIssues()"
    >
        ISSUES 0
    </button>


    
<div class="filter-box">

    <span class="muted">
        Search:
    </span>

    <input
        id="minerSearch"
        class="miner-search"
        type="search"
        placeholder="ASIC / IP / state"
        autocomplete="off"
        oninput="loadStatus()"
    >

    <span
        id="minerSearchCount"
        class="muted small search-count"
    >
        —
    </span>

</div>

<div class="filter-box">

        <span class="muted">
            Filter:
        </span>

        <select
            id="filter"
            onchange="loadStatus()"
        >

            <option value="all">
                All
            </option>

            <option value="mining">
                Mining
            </option>

            <option value="paused">
                Paused
            </option>

            <option value="offline">
                Offline
            </option>

            <option value="awesome">
                Awesome
            </option>

            <option value="stock">
                Stock
            </option>

            <option value="errors">
                Errors
            </option>

        </select>

    </div>


    <div class="filter-box">

        <span class="muted">
            Sort:
        </span>

        <select
            id="sortBy"
            onchange="minerSortChanged()"
        >
            <option value="state">
                State
            </option>

            <option value="hashrate">
                Hashrate
            </option>

            <option value="temp">
                Temperature
            </option>

            <option value="ip">
                IP
            </option>

            <option value="name">
                Name
            </option>
        </select>


        <select
            id="sortDirection"
            onchange="minerSortChanged()"
            title="Sort direction"
        >
            <option value="asc">
                ↑ Asc
            </option>

            <option value="desc">
                ↓ Desc
            </option>
        </select>

    </div>


</div>


<div class="card">

<div
    style="
        display:flex;
        justify-content:space-between;
        gap:15px;
        align-items:flex-end;
        flex-wrap:wrap;
    "
>

    <div style="flex:1; min-width:300px">

        <h3>
            ASIC Discovery
        </h3>

        <div class="muted small">
            Enter IPv4 network in CIDR format.
            Scan is read-only and only devices
            with ASIC signatures are returned.
        </div>

        <div
            style="
                display:flex;
                gap:8px;
                margin-top:12px;
            "
        >

            <input
                id="discoveryNetwork"
                type="text"
                placeholder="CIDR network"
                style="
                    flex:1;
                    min-width:220px;
                    background:#111318;
                    color:white;
                    border:1px solid #343b48;
                    border-radius:7px;
                    padding:9px 11px;
                "
            >

            <button
                id="discoveryScanButton"
                class="secondary"
                onclick="scanNetwork()"
            >
                SCAN
            </button>

        </div>

    </div>


    <div
        style="
            display:flex;
            gap:8px;
            flex-wrap:wrap;
        "
    >

        <button
            id="toggleDiscoveryButton"
            class="secondary"
            onclick="toggleDiscoveryResults()"
            style="display:none"
        >
            COLLAPSE
        </button>

        <button
            id="clearDiscoveryButton"
            class="secondary"
            onclick="clearDiscoveryResults()"
            style="display:none"
        >
            CLEAR
        </button>

        <button
            id="addAllDiscoveryButton"
            class="start"
            onclick="addAllDiscovered()"
            style="display:none"
        >
            ADD ALL NEW
        </button>

    </div>

</div>


<div
    id="discoverySummary"
    class="muted"
    style="margin-top:14px"
>
    No scan performed.
</div>


<div
    id="discoveryResults"
    class="table-wrap"
    style="
        margin-top:12px;
        display:none;
    "
>

<table
    style="min-width:900px"
>

<thead>

<tr>
    <th>IP</th>
    <th>Type</th>
    <th>Model</th>
    <th>Firmware</th>
    <th>Status</th>
    <th>Action</th>
</tr>

</thead>

<tbody id="discoveryRows">
</tbody>

</table>

</div>

</div>


<div class="table-wrap">

<table class="miner-table">

<thead>

<tr>

    <th
        class="sortable-th"
        onclick="sortByHeader('name')"
    >
        ASIC
        <span
            id="sortMark-name"
            class="sort-mark"
        >↕</span>
    </th>


    <th
        class="sortable-th"
        onclick="sortByHeader('ip')"
    >
        IP
        <span
            id="sortMark-ip"
            class="sort-mark"
        >↕</span>
    </th>


    <th>
        Firmware
    </th>

    <th>
        Enabled
    </th>

    <th>
        Schedule
    </th>


    <th
        class="sortable-th"
        onclick="sortByHeader('state')"
    >
        State
        <span
            id="sortMark-state"
            class="sort-mark"
        >↕</span>
    </th>


    <th
        class="sortable-th"
        onclick="sortByHeader('hashrate')"
    >
        Hashrate
        <span
            id="sortMark-hashrate"
            class="sort-mark"
        >↕</span>
    </th>


    <th>
        Avg
    </th>


    <th
        class="sortable-th"
        onclick="sortByHeader('temp')"
    >
        Temp
        <span
            id="sortMark-temp"
            class="sort-mark"
        >↕</span>
    </th>


    <th>
        Power
    </th>

    <th>
        Pool
    </th>

    <th>
        Override
    </th>

    <th>
        Control
    </th>

    <th>
        History
    </th>

    <th>
        Web
    </th>

</tr>

</thead>

<tbody id="minerRows">
</tbody>

</table>

</div>


<div class="card schedule-rules-card">

<div class="schedule-rules-header">

    <div>

        <h3 style="margin:0">
            Schedule Rules
        </h3>

        <div
            class="muted small"
            style="margin-top:5px"
        >
            Scheduler timezone:
            <span id="scheduleTimezone">
                configured
            </span>.
            Rules apply only to enabled ASICs
            with Schedule = ON.
        </div>

    </div>


    <button
        class="start"
        onclick="openScheduleRuleEditor()"
    >
        + ADD RULE
    </button>

</div>


<div class="schedule-summary-grid">

    <div class="schedule-summary-item">

        <div class="muted small">
            Current target
        </div>

        <strong id="desiredState">
            —
        </strong>

    </div>


    <div class="schedule-summary-item">

        <div class="muted small">
            Next event
        </div>

        <strong id="nextTransition">
            —
        </strong>

    </div>


    <div class="schedule-summary-item">

        <div class="muted small">
            Global scheduler
        </div>

        <strong id="scheduleRulesSchedulerState">
            —
        </strong>

    </div>


    <div class="schedule-summary-item">

        <div class="muted small">
            Active rule
        </div>

        <strong id="scheduleRulesActiveRule">
            —
        </strong>

    </div>

</div>


<div
    id="scheduleRulesStatus"
    class="muted small"
    style="margin:10px 0"
>
    Loading schedule rules...
</div>


<div class="table-wrap">

<table
    class="schedule-rules-table"
    style="min-width:920px"
>

<thead>

<tr>
    <th>Status</th>
    <th>Action</th>
    <th>Days</th>
    <th>Time</th>
    <th>Next run</th>
    <th>Comment</th>
    <th>Actions</th>
</tr>

</thead>


<tbody id="scheduleRuleRows">

<tr>
    <td
        colspan="7"
        class="muted"
    >
        Loading...
    </td>
</tr>

</tbody>

</table>

</div>

</div>


<div class="card log-card">

<div
    style="
        display:flex;
        justify-content:space-between;
        gap:12px;
        align-items:center;
    "
>

    <h3 style="margin:0">
        Action journal
    </h3>

    <button
        class="secondary mini"
        onclick="loadLogs()"
    >
        Refresh
    </button>

</div>

<br>

<div class="table-wrap">

<table class="log-table">

<thead>

<tr>
    <th>Time</th>
    <th>Source</th>
    <th>Action</th>
    <th>ASIC</th>
    <th>IP</th>
    <th>Result</th>
    <th>Details</th>
</tr>

</thead>

<tbody id="logRows">

<tr>
    <td colspan="7">
        No actions recorded yet.
    </td>
</tr>

</tbody>

</table>

</div>

</div>

</div>






<div
    id="scheduleRuleBackdrop"
    class="modal-backdrop"
    onclick="scheduleRuleBackdropClick(event)"
>

<div
    class="history-modal schedule-rule-modal"
    onclick="event.stopPropagation()"
>

    <div class="history-header">

        <div>

            <h2
                id="scheduleRuleModalTitle"
                style="margin:0"
            >
                Add Schedule Rule
            </h2>

            <div class="muted">
                Automatic ASIC state transition
                in the configured timezone
            </div>

        </div>


        <button
            class="close-button"
            onclick="closeScheduleRuleEditor()"
        >
            CLOSE
        </button>

    </div>


    <div class="schedule-rule-form-grid">

        <div class="schedule-rule-field">

            <label for="scheduleRuleAction">
                Action
            </label>

            <select id="scheduleRuleAction">

                <option value="RESUME">
                    RESUME
                </option>

                <option value="PAUSE">
                    PAUSE
                </option>

            </select>

        </div>


        <div class="schedule-rule-field">

            <label for="scheduleRuleTime">
                Time
            </label>

            <input
                id="scheduleRuleTime"
                type="time"
                value="13:00"
                step="60"
            >

        </div>


        <div class="schedule-rule-field full">

            <label>
                Days
            </label>

            <div class="schedule-days">

                <label class="schedule-day">
                    <input
                        type="checkbox"
                        class="schedule-day-checkbox"
                        value="1"
                    >
                    Mon
                </label>

                <label class="schedule-day">
                    <input
                        type="checkbox"
                        class="schedule-day-checkbox"
                        value="2"
                    >
                    Tue
                </label>

                <label class="schedule-day">
                    <input
                        type="checkbox"
                        class="schedule-day-checkbox"
                        value="4"
                    >
                    Wed
                </label>

                <label class="schedule-day">
                    <input
                        type="checkbox"
                        class="schedule-day-checkbox"
                        value="8"
                    >
                    Thu
                </label>

                <label class="schedule-day">
                    <input
                        type="checkbox"
                        class="schedule-day-checkbox"
                        value="16"
                    >
                    Fri
                </label>

                <label class="schedule-day">
                    <input
                        type="checkbox"
                        class="schedule-day-checkbox"
                        value="32"
                    >
                    Sat
                </label>

                <label class="schedule-day">
                    <input
                        type="checkbox"
                        class="schedule-day-checkbox"
                        value="64"
                    >
                    Sun
                </label>

            </div>

        </div>


        <div class="schedule-rule-field full">

            <label for="scheduleRuleComment">
                Comment
            </label>

            <input
                id="scheduleRuleComment"
                type="text"
                maxlength="120"
                placeholder="For example: Lunch mining"
            >

        </div>


        <div class="schedule-rule-field full">

            <label class="schedule-enabled-row">

                <input
                    id="scheduleRuleEnabled"
                    type="checkbox"
                    checked
                >

                Rule enabled

            </label>

        </div>

    </div>


    <div
        id="scheduleRuleError"
        class="schedule-rule-error"
    >
    </div>


    <div class="schedule-rule-form-actions">

        <button
            class="secondary"
            onclick="closeScheduleRuleEditor()"
        >
            CANCEL
        </button>

        <button
            id="scheduleRuleSaveButton"
            class="start"
            onclick="saveScheduleRule()"
        >
            SAVE
        </button>

    </div>

</div>

</div>


<div
    id="issuesBackdrop"
    class="modal-backdrop"
    onclick="issuesBackdropClick(event)"
>

<div
    class="history-modal"
    onclick="event.stopPropagation()"
>

    <div class="history-header">

        <div>

            <h2 style="margin:0">
                ASIC Issues
            </h2>

            <div class="muted">
                Persistent health and schedule anomalies
            </div>

        </div>


        <button
            class="close-button"
            onclick="closeIssues()"
        >
            CLOSE
        </button>

    </div>


    <div
        id="issuesStatus"
        class="history-status"
    >
        Loading...
    </div>


    <div class="issue-section">

        <div class="chart-title">
            Active Issues
        </div>

        <div class="table-wrap">

            <table style="min-width:900px">

                <thead>
                    <tr>
                        <th>Severity</th>
                        <th>ASIC</th>
                        <th>IP</th>
                        <th>Issue</th>
                        <th>Since</th>
                        <th>Details</th>
                    </tr>
                </thead>

                <tbody
                    id="activeIssueRows"
                >
                </tbody>

            </table>

        </div>

    </div>


    <div class="issue-section">

        <div class="chart-title">
            Recently Resolved
        </div>

        <div class="table-wrap">

            <table style="min-width:950px">

                <thead>
                    <tr>
                        <th>ASIC</th>
                        <th>IP</th>
                        <th>Issue</th>
                        <th>Started</th>
                        <th>Resolved</th>
                        <th>Details</th>
                    </tr>
                </thead>

                <tbody
                    id="resolvedIssueRows"
                >
                </tbody>

            </table>

        </div>

    </div>

</div>

</div>


<div
    id="farmHistoryBackdrop"
    class="modal-backdrop"
    onclick="farmHistoryBackdropClick(event)"
>

<div
    class="history-modal"
    onclick="event.stopPropagation()"
>

    <div class="history-header">

        <div>

            <h2 style="margin:0">
                Farm History
            </h2>

            <div class="muted">
                Historical telemetry for all managed ASICs
            </div>

        </div>


        <button
            class="close-button"
            onclick="closeFarmHistory()"
        >
            CLOSE
        </button>

    </div>


    <div class="history-controls">

        <span class="muted">
            Period:
        </span>


        <select
            id="farmHistoryPeriod"
            onchange="loadFarmHistory()"
        >

            <option value="24">
                24 hours
            </option>

            <option value="168">
                7 days
            </option>

            <option value="720">
                30 days
            </option>

            <option value="2160">
                90 days
            </option>

        </select>


        <button
            class="secondary"
            onclick="loadFarmHistory()"
        >
            REFRESH
        </button>

    </div>


    <div
        id="farmHistoryStatus"
        class="history-status"
    >
    </div>


    <div class="farm-summary-grid">

        <div class="farm-summary-item">

            <div class="muted small">
                MINING NOW
            </div>

            <div
                id="farmNowMining"
                class="farm-summary-value"
            >
                —
            </div>

        </div>


        <div class="farm-summary-item">

            <div class="muted small">
                PAUSED NOW
            </div>

            <div
                id="farmNowPaused"
                class="farm-summary-value"
            >
                —
            </div>

        </div>


        <div class="farm-summary-item">

            <div class="muted small">
                OFFLINE NOW
            </div>

            <div
                id="farmNowOffline"
                class="farm-summary-value"
            >
                —
            </div>

        </div>


        <div class="farm-summary-item">

            <div class="muted small">
                HASHRATE NOW
            </div>

            <div
                id="farmNowHashrate"
                class="farm-summary-value"
            >
                —
            </div>

        </div>


        <div class="farm-summary-item">

            <div class="muted small">
                KNOWN POWER
            </div>

            <div
                id="farmNowPower"
                class="farm-summary-value"
            >
                —
            </div>

        </div>


        <div class="farm-summary-item">

            <div class="muted small">
                MAX TEMP NOW
            </div>

            <div
                id="farmNowTemp"
                class="farm-summary-value"
            >
                —
            </div>

        </div>

    </div>


    <div class="chart-card">

        <div class="chart-title">
            Total Hashrate
        </div>

        <div
            id="farmHashrateSummary"
            class="chart-summary"
        >
        </div>

        <div class="chart-container">

            <canvas
                id="farmHashrateChart"
            ></canvas>

        </div>

    </div>


    <div class="chart-card">

        <div class="chart-title">
            Known Power
        </div>

        <div
            id="farmPowerSummary"
            class="chart-summary"
        >
        </div>

        <div class="chart-container">

            <canvas
                id="farmPowerChart"
            ></canvas>

        </div>

    </div>


    <div class="chart-card">

        <div class="chart-title">
            ASIC States
        </div>

        <div class="chart-summary">
            MINING / PAUSED / OFFLINE
        </div>

        <div class="chart-container">

            <canvas
                id="farmStateChart"
            ></canvas>

        </div>

    </div>


    <div class="chart-card">

        <div class="chart-title">
            Maximum Temperature
        </div>

        <div
            id="farmTempSummary"
            class="chart-summary"
        >
        </div>

        <div class="chart-container">

            <canvas
                id="farmTempChart"
            ></canvas>

        </div>

    </div>


    <div class="chart-card">

        <div class="chart-title">
            Problems during selected period
        </div>

        <div
            id="farmProblemSummary"
            class="chart-summary"
        >
        </div>


        <div class="table-wrap">

            <table
                style="min-width:850px"
            >

                <thead>

                    <tr>
                        <th>ASIC</th>
                        <th>IP</th>
                        <th>Firmware</th>
                        <th>Offline samples</th>
                        <th>≥85°C samples</th>
                        <th>Max Temp</th>
                        <th>Avg mining hashrate</th>
                    </tr>

                </thead>

                <tbody
                    id="farmProblemRows"
                >
                </tbody>

            </table>

        </div>

    </div>

</div>

</div>


<div
    id="historyBackdrop"
    class="modal-backdrop"
    onclick="historyBackdropClick(event)"
>

<div
    class="history-modal"
    onclick="event.stopPropagation()"
>

    <div class="history-header">

        <div>

            <h2
                id="historyTitle"
                style="margin:0"
            >
                ASIC History
            </h2>

            <div
                id="historySubtitle"
                class="muted"
                style="margin-top:5px"
            >
            </div>

        </div>


        <button
            class="close-button"
            onclick="closeHistory()"
        >
            CLOSE
        </button>

    </div>


    <div class="history-controls">

        <span class="muted">
            Period:
        </span>

        <select
            id="historyPeriod"
            onchange="loadHistory()"
        >

            <option value="24">
                24 hours
            </option>

            <option value="168">
                7 days
            </option>

            <option value="720">
                30 days
            </option>

            <option value="2160">
                90 days
            </option>

        </select>


        <button
            class="secondary"
            onclick="loadHistory()"
        >
            REFRESH
        </button>

    </div>


    <div
        id="historyStatus"
        class="history-status"
    >
    </div>


    <div class="chart-card">

        <div class="chart-title">
            Hashrate
        </div>

        <div
            id="hashrateSummary"
            class="chart-summary"
        >
        </div>

        <div class="chart-container">

            <canvas
                id="hashrateChart"
            ></canvas>

        </div>

    </div>


    <div class="chart-card">

        <div class="chart-title">
            Temperature
        </div>

        <div
            id="temperatureSummary"
            class="chart-summary"
        >
        </div>

        <div class="chart-container">

            <canvas
                id="temperatureChart"
            ></canvas>

        </div>

    </div>


    <div class="chart-card">

        <div class="chart-title">
            Power
        </div>

        <div
            id="powerSummary"
            class="chart-summary"
        >
        </div>

        <div class="chart-container">

            <canvas
                id="powerChart"
            ></canvas>

        </div>

    </div>

</div>

</div>


<script>

function esc(value) {

    if (
        value === null
        ||
        value === undefined
    ) {
        return "";
    }

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
}


function num(
    value,
    digits=1
) {

    if (
        value === null
        ||
        value === undefined
    ) {
        return "—";
    }

    return Number(value)
        .toFixed(digits);
}


function formatEpoch(epoch) {

    if (!epoch) {
        return "—";
    }

    return new Date(
        epoch * 1000
    ).toLocaleString();
}


function firmwareSelect(m) {

    const mode = (
        m.detection_mode
        ||
        "AUTO"
    ).toUpperCase();


    let driverLabel =
        "NOT SET";


    if (
        m.driver
        ===
        "awesome"
    ) {

        driverLabel =
            "Awesome";
    }
    else if (
        m.driver
        ===
        "bitmain_stock"
    ) {

        driverLabel =
            "Bitmain Stock";
    }


    return `
        <div class="firmware-cell">

            <div>

                <span
                    class="
                        firmware-mode
                        ${
                            mode === "MANUAL"
                            ? "manual"
                            : "auto"
                        }
                    "
                >
                    ${esc(mode)}
                </span>

                <strong>
                    ${esc(driverLabel)}
                </strong>

            </div>


            <div
                class="muted small"
                style="margin-top:3px"
            >
                ${esc(
                    m.firmware
                    ||
                    "—"
                )}
            </div>


            <button
                class="secondary mini"
                style="margin-top:5px"
                onclick="
                    openFirmwareEditor(
                        ${m.id}
                    )
                "
            >
                EDIT
            </button>

        </div>
    `;
}



function tempHTML(value) {

    if (
        value === null
        ||
        value === undefined
    ) {
        return "—";
    }

    const t = Number(value);

    let cls = "temp-ok";

    if (t >= 85) {
        cls = "temp-critical";
    }
    else if (t >= 75) {
        cls = "temp-warning";
    }

    return `
        <span class="${cls}">
            ${t.toFixed(0)} °C
        </span>
    `;
}


function matchesFilter(
    m,
    filter
) {

    if (filter === "all") {
        return true;
    }

    if (
        filter === "mining"
        &&
        m.state === "MINING"
    ) {
        return true;
    }

    if (
        filter === "paused"
        &&
        m.state === "PAUSED"
    ) {
        return true;
    }

    if (
        filter === "offline"
        &&
        m.state === "OFFLINE"
    ) {
        return true;
    }

    if (
        filter === "awesome"
        &&
        m.driver === "awesome"
    ) {
        return true;
    }

    if (
        filter === "stock"
        &&
        m.driver === "bitmain_stock"
    ) {
        return true;
    }

    if (
        filter === "errors"
        &&
        m.last_error
    ) {
        return true;
    }

    return false;
}



let lastDiscovery = null;
let discoveryCollapsed = false;



function toggleDiscoveryResults() {

    if (!lastDiscovery) {
        return;
    }

    discoveryCollapsed =
        !discoveryCollapsed;


    const results =
        document.getElementById(
            "discoveryResults"
        );

    const button =
        document.getElementById(
            "toggleDiscoveryButton"
        );


    if (discoveryCollapsed) {

        results.style.display =
            "none";

        button.textContent =
            "EXPAND";

    }
    else {

        results.style.display =
            "block";

        button.textContent =
            "COLLAPSE";
    }
}


function clearDiscoveryResults() {

    lastDiscovery = null;
    discoveryCollapsed = false;


    document.getElementById(
        "discoveryResults"
    ).style.display =
        "none";


    document.getElementById(
        "discoveryRows"
    ).innerHTML =
        "";


    document.getElementById(
        "discoverySummary"
    ).textContent =
        "No scan performed.";


    document.getElementById(
        "toggleDiscoveryButton"
    ).style.display =
        "none";


    document.getElementById(
        "clearDiscoveryButton"
    ).style.display =
        "none";


    document.getElementById(
        "addAllDiscoveryButton"
    ).style.display =
        "none";
}


async function scanNetwork() {

    const input =
        document.getElementById(
            "discoveryNetwork"
        );

    const button =
        document.getElementById(
            "discoveryScanButton"
        );

    const network =
        input.value.trim();


    if (!network) {

        alert(
            "Enter network in CIDR format."
        );

        return;
    }


    localStorage.setItem(
        "asicDiscoveryNetwork",
        network
    );


    button.disabled = true;
    button.textContent = "SCANNING...";


    document.getElementById(
        "discoverySummary"
    ).textContent =
        "Scanning network...";


    document.getElementById(
        "discoveryResults"
    ).style.display =
        "none";


    document.getElementById(
        "toggleDiscoveryButton"
    ).style.display =
        "none";


    document.getElementById(
        "clearDiscoveryButton"
    ).style.display =
        "none";


    document.getElementById(
        "addAllDiscoveryButton"
    ).style.display =
        "none";


    try {

        const response =
            await fetch(
                "/api/discovery/scan",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify({
                            network: network
                        })
                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.detail
                || "Discovery failed"
            );
        }


        lastDiscovery = data;

        renderDiscovery(
            data
        );

    }

    catch (error) {

        document.getElementById(
            "discoverySummary"
        ).textContent =
            "Discovery failed: "
            + error.message;

        alert(
            error.message
        );

    }

    finally {

        button.disabled = false;
        button.textContent = "SCAN";
    }
}


function renderDiscovery(data) {

    discoveryCollapsed = false;


    document.getElementById(
        "discoverySummary"
    ).innerHTML =
        `
        Network:
        <strong>${esc(data.network)}</strong>
        &nbsp; · &nbsp;

        Checked:
        <strong>${data.hosts_scanned}</strong>
        &nbsp; · &nbsp;

        Found ASIC:
        <strong>${data.total}</strong>
        &nbsp; · &nbsp;

        Managed:
        <strong>${data.managed_count}</strong>
        &nbsp; · &nbsp;

        New:
        <strong>${data.new_known}</strong>
        &nbsp; · &nbsp;

        Unknown:
        <strong>${data.new_unknown}</strong>
        &nbsp; · &nbsp;

        Time:
        <strong>${data.duration_seconds}s</strong>
        `;


    let html = "";


    for (
        const d
        of data.devices
    ) {

        let status;
        let action;


        if (d.managed) {

            status = `
                <span class="temp-ok">
                    MANAGED
                </span>
            `;

            action = "—";

        }

        else if (
            d.driver ===
            "unknown_asic"
        ) {

            status = `
                <span class="temp-warning">
                    UNKNOWN ASIC
                </span>
            `;

            action = `
                <span class="muted small">
                    unsupported
                </span>
            `;

        }

        else {

            status = `
                <span class="STARTING state">
                    NEW
                </span>
            `;

            action = `
                <button
                    class="start mini"
                    onclick="addDiscovered(
                        '${esc(d.ip)}'
                    )"
                >
                    ADD
                </button>
            `;
        }


        html += `
            <tr>

                <td>
                    <strong>
                        ${esc(d.ip)}
                    </strong>
                </td>

                <td>
                    ${esc(d.type)}
                </td>

                <td>
                    ${esc(
                        d.model || "—"
                    )}
                </td>

                <td>
                    ${esc(
                        d.firmware || "—"
                    )}
                </td>

                <td>
                    ${status}
                </td>

                <td>
                    ${action}
                </td>

            </tr>
        `;
    }


    if (!html) {

        html = `
            <tr>
                <td colspan="6">
                    No ASIC devices found.
                </td>
            </tr>
        `;
    }


    document.getElementById(
        "discoveryRows"
    ).innerHTML =
        html;


    document.getElementById(
        "discoveryResults"
    ).style.display =
        "block";


    const toggleButton =
        document.getElementById(
            "toggleDiscoveryButton"
        );

    toggleButton.style.display =
        "inline-block";

    toggleButton.textContent =
        "COLLAPSE";


    document.getElementById(
        "clearDiscoveryButton"
    ).style.display =
        "inline-block";


    const addAll =
        document.getElementById(
            "addAllDiscoveryButton"
        );


    if (
        data.new_known > 0
    ) {

        addAll.style.display =
            "inline-block";

        addAll.textContent =
            "ADD ALL NEW ("
            + data.new_known
            + ")";

    }

    else {

        addAll.style.display =
            "none";
    }
}


async function addDiscovered(ip) {

    if (
        !confirm(
            "Add ASIC "
            + ip
            + " to Manager?\n\n"
            + "Enabled: ON\n"
            + "Schedule: OFF"
        )
    ) {
        return;
    }


    await addDiscoveryIPs(
        [ip]
    );
}


async function addAllDiscovered() {

    if (!lastDiscovery) {
        return;
    }


    const ips =
        lastDiscovery.devices
        .filter(
            d =>
                !d.managed
                &&
                (
                    d.driver === "awesome"
                    ||
                    d.driver ===
                        "bitmain_stock"
                )
        )
        .map(
            d => d.ip
        );


    if (!ips.length) {

        alert(
            "No new supported ASICs."
        );

        return;
    }


    if (
        !confirm(
            "Add "
            + ips.length
            + " new ASIC(s)?\n\n"
            + "Enabled: ON\n"
            + "Schedule: OFF"
        )
    ) {
        return;
    }


    await addDiscoveryIPs(
        ips
    );
}


async function addDiscoveryIPs(
    ips
) {

    const response =
        await fetch(
            "/api/discovery/add",
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json"
                },

                body:
                    JSON.stringify({
                        ips: ips
                    })
            }
        );


    const data =
        await response.json();


    if (!response.ok) {

        alert(
            data.detail
            || "Import failed"
        );

        return;
    }


    const added =
        data.results.filter(
            x =>
                x.status === "added"
        );


    const failed =
        data.results.filter(
            x =>
                !x.success
        );


    let message =
        "Added: "
        + added.length;


    if (failed.length) {

        message +=
            "\nFailed: "
            + failed.length
            + "\n\n"
            +
            failed.map(
                x =>
                    x.ip
                    + ": "
                    + (
                        x.error
                        || x.status
                    )
            ).join("\n");
    }


    alert(
        message
    );


    await loadStatus();
    await loadLogs();

    await scanNetwork();
}






let issuesOpen = false;


function issueLabel(code) {

    const labels = {
        "OFFLINE":
            "Offline",

        "OVERHEAT":
            "Overheat",

        "SCHEDULE_MISMATCH":
            "Schedule mismatch",
    };


    return (
        labels[code]
        || code
    );
}


function issueTime(value) {

    if (!value) {
        return "—";
    }


    return new Date(
        value
    ).toLocaleString();
}


function openIssues() {

    issuesOpen = true;


    document.getElementById(
        "issuesBackdrop"
    ).classList.add(
        "open"
    );


    document.body.style.overflow =
        "hidden";


    loadIssues();
}


function closeIssues() {

    issuesOpen = false;


    document.getElementById(
        "issuesBackdrop"
    ).classList.remove(
        "open"
    );


    document.body.style.overflow =
        "";
}


function issuesBackdropClick(
    event
) {

    if (
        event.target.id
        === "issuesBackdrop"
    ) {

        closeIssues();
    }
}


function renderIssues(
    data
) {

    const button =
        document.getElementById(
            "issuesButton"
        );


    button.textContent =
        "ISSUES "
        + data.active_count;


    if (
        data.active_count > 0
    ) {

        button.classList.add(
            "issue-button-active"
        );

    }

    else {

        button.classList.remove(
            "issue-button-active"
        );
    }


    if (!issuesOpen) {
        return;
    }


    const status =
        document.getElementById(
            "issuesStatus"
        );


    if (
        data.active_count === 0
    ) {

        status.innerHTML =
            '<span class="issue-resolved">'
            + 'No active issues.'
            + '</span>';

    }

    else {

        status.innerHTML =
            '<span class="issue-critical">'
            + data.active_count
            + ' active issue(s)'
            + '</span>';
    }


    const activeBody =
        document.getElementById(
            "activeIssueRows"
        );


    if (
        !data.active.length
    ) {

        activeBody.innerHTML = `
            <tr>
                <td colspan="6">
                    No active issues.
                </td>
            </tr>
        `;

    }

    else {

        let html = "";


        for (
            const issue
            of data.active
        ) {

            const severityClass =
                issue.severity
                === "CRITICAL"
                ? "issue-critical"
                : "issue-warning";


            html += `
                <tr>

                    <td
                        class="${severityClass}"
                    >
                        ${esc(issue.severity)}
                    </td>

                    <td>
                        <strong>
                            ${esc(issue.name)}
                        </strong>
                    </td>

                    <td>
                        ${esc(issue.ip)}
                    </td>

                    <td>
                        ${esc(
                            issueLabel(
                                issue.code
                            )
                        )}
                    </td>

                    <td>
                        ${esc(
                            issueTime(
                                issue.first_seen
                            )
                        )}
                    </td>

                    <td>
                        ${esc(
                            issue.message
                        )}
                    </td>

                </tr>
            `;
        }


        activeBody.innerHTML =
            html;
    }


    const resolvedBody =
        document.getElementById(
            "resolvedIssueRows"
        );


    if (
        !data.recent_resolved.length
    ) {

        resolvedBody.innerHTML = `
            <tr>
                <td colspan="6">
                    No resolved issues recorded.
                </td>
            </tr>
        `;

    }

    else {

        let html = "";


        for (
            const issue
            of data.recent_resolved
        ) {

            html += `
                <tr>

                    <td>
                        <strong>
                            ${esc(issue.name)}
                        </strong>
                    </td>

                    <td>
                        ${esc(issue.ip)}
                    </td>

                    <td>
                        ${esc(
                            issueLabel(
                                issue.code
                            )
                        )}
                    </td>

                    <td>
                        ${esc(
                            issueTime(
                                issue.first_seen
                            )
                        )}
                    </td>

                    <td
                        class="issue-resolved"
                    >
                        ${esc(
                            issueTime(
                                issue.resolved_at
                            )
                        )}
                    </td>

                    <td>
                        ${esc(
                            issue.message
                        )}
                    </td>

                </tr>
            `;
        }


        resolvedBody.innerHTML =
            html;
    }
}


async function loadIssues() {

    try {

        const response =
            await fetch(
                "/api/issues?limit=100"
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.detail
                || "Issues request failed"
            );
        }


        renderIssues(
            data
        );

    }

    catch (error) {

        if (issuesOpen) {

            document.getElementById(
                "issuesStatus"
            ).textContent =
                "Issues error: "
                + error.message;
        }
    }
}


document.addEventListener(
    "keydown",
    function(event) {

        if (
            event.key === "Escape"
            &&
            issuesOpen
        ) {

            closeIssues();
        }
    }
);


let farmHistoryOpen = false;
let lastFarmHistory = null;


function openFarmHistory() {

    farmHistoryOpen = true;


    document.getElementById(
        "farmHistoryBackdrop"
    ).classList.add(
        "open"
    );


    document.body.style.overflow =
        "hidden";


    loadFarmHistory();
}


function closeFarmHistory() {

    farmHistoryOpen = false;


    document.getElementById(
        "farmHistoryBackdrop"
    ).classList.remove(
        "open"
    );


    document.body.style.overflow =
        "";
}


function farmHistoryBackdropClick(
    event
) {

    if (
        event.target.id
        === "farmHistoryBackdrop"
    ) {

        closeFarmHistory();
    }
}


function drawMultiLineChart(
    canvasId,
    points,
    series
) {

    const canvas =
        document.getElementById(
            canvasId
        );


    const rect =
        canvas.getBoundingClientRect();


    const dpr =
        window.devicePixelRatio
        || 1;


    const width =
        Math.max(
            600,
            Math.floor(
                rect.width
            )
        );


    const height = 240;


    canvas.width =
        width * dpr;

    canvas.height =
        height * dpr;


    const ctx =
        canvas.getContext(
            "2d"
        );


    ctx.setTransform(
        dpr,
        0,
        0,
        dpr,
        0,
        0
    );


    ctx.clearRect(
        0,
        0,
        width,
        height
    );


    const left = 52;
    const right = 15;
    const top = 25;
    const bottom = 35;


    const graphWidth =
        width
        - left
        - right;


    const graphHeight =
        height
        - top
        - bottom;


    if (!points.length) {

        ctx.fillStyle =
            "#9299a8";

        ctx.font =
            "11px system-ui";

        ctx.fillText(
            "No historical data",
            left,
            35
        );

        return;
    }


    const times =
        points.map(
            p =>
                new Date(
                    p.time
                ).getTime()
        );


    let minTime =
        Math.min(...times);

    let maxTime =
        Math.max(...times);


    if (minTime === maxTime) {
        maxTime += 1;
    }


    let maxValue = 1;


    for (
        const definition
        of series
    ) {

        for (
            const point
            of points
        ) {

            const value =
                Number(
                    point[
                        definition.field
                    ]
                    || 0
                );


            if (
                Number.isFinite(value)
            ) {

                maxValue =
                    Math.max(
                        maxValue,
                        value
                    );
            }
        }
    }


    maxValue *= 1.08;


    function x(timestamp) {

        return left
            +
            (
                timestamp
                - minTime
            )
            /
            (
                maxTime
                - minTime
            )
            * graphWidth;
    }


    function y(value) {

        return top
            +
            (
                1
                -
                value
                / maxValue
            )
            * graphHeight;
    }


    ctx.font =
        "11px system-ui";


    ctx.strokeStyle =
        "#2b303b";

    ctx.fillStyle =
        "#9299a8";


    for (
        let i = 0;
        i <= 5;
        i++
    ) {

        const yy =
            top
            +
            graphHeight
            * i
            / 5;


        ctx.beginPath();

        ctx.moveTo(
            left,
            yy
        );

        ctx.lineTo(
            width - right,
            yy
        );

        ctx.stroke();


        const value =
            maxValue
            *
            (
                1
                -
                i / 5
            );


        ctx.fillText(
            value.toFixed(1),
            3,
            yy + 4
        );
    }


    for (
        let i = 0;
        i <= 5;
        i++
    ) {

        const timestamp =
            minTime
            +
            (
                maxTime
                - minTime
            )
            * i
            / 5;


        const xx =
            left
            +
            graphWidth
            * i
            / 5;


        const label =
            new Date(
                timestamp
            ).toLocaleString(
                [],
                {
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit"
                }
            );


        ctx.fillText(
            label,
            Math.max(
                left,
                Math.min(
                    xx - 32,
                    width - 75
                )
            ),
            height - 10
        );
    }


    const colors = [
        "#4fd38a",
        "#f1c75b",
        "#f26d72",
        "#67b7ff",
    ];


    series.forEach(
        (
            definition,
            seriesIndex
        ) => {

            ctx.strokeStyle =
                colors[
                    seriesIndex
                    % colors.length
                ];

            ctx.lineWidth = 2;

            ctx.beginPath();

            let started = false;


            for (
                const point
                of points
            ) {

                const timestamp =
                    new Date(
                        point.time
                    ).getTime();


                const value =
                    Number(
                        point[
                            definition.field
                        ]
                        || 0
                    );


                const xx =
                    x(timestamp);

                const yy =
                    y(value);


                if (!started) {

                    ctx.moveTo(
                        xx,
                        yy
                    );

                    started = true;

                }

                else {

                    ctx.lineTo(
                        xx,
                        yy
                    );
                }
            }


            ctx.stroke();
        }
    );


    let legendX = left;


    series.forEach(
        (
            definition,
            seriesIndex
        ) => {

            ctx.fillStyle =
                colors[
                    seriesIndex
                    % colors.length
                ];


            ctx.fillRect(
                legendX,
                5,
                10,
                10
            );


            ctx.fillStyle =
                "#c8ccd4";


            ctx.fillText(
                definition.label,
                legendX + 15,
                14
            );


            legendX +=
                95;
        }
    );
}


function renderFarmProblems(
    problems
) {

    const body =
        document.getElementById(
            "farmProblemRows"
        );


    const summary =
        document.getElementById(
            "farmProblemSummary"
        );


    if (!problems.length) {

        summary.innerHTML =
            '<span class="problem-ok">'
            + 'No offline or critical-temperature '
            + 'events in selected period.'
            + '</span>';


        body.innerHTML = `
            <tr>
                <td colspan="7">
                    No problems recorded.
                </td>
            </tr>
        `;

        return;
    }


    summary.innerHTML =
        '<span class="problem-warning">'
        + problems.length
        + ' ASIC(s) require review'
        + '</span>';


    let html = "";


    for (
        const p
        of problems
    ) {

        const tempClass =
            Number(
                p.max_temp || 0
            ) >= 85
            ? "problem-critical"
            : "";


        html += `
            <tr>

                <td>
                    <strong>
                        ${esc(p.name)}
                    </strong>
                </td>

                <td>
                    ${esc(p.ip)}
                </td>

                <td>
                    ${
                        p.driver === "awesome"
                        ? "Awesome"
                        : "Stock"
                    }
                </td>

                <td class="${
                    p.offline_samples > 0
                    ? "problem-critical"
                    : ""
                }">
                    ${p.offline_samples}
                </td>

                <td class="${
                    p.critical_temp_samples > 0
                    ? "problem-critical"
                    : ""
                }">
                    ${p.critical_temp_samples}
                </td>

                <td class="${tempClass}">
                    ${
                        p.max_temp === null
                        ? "—"
                        :
                        Number(
                            p.max_temp
                        ).toFixed(0)
                        + " °C"
                    }
                </td>

                <td>
                    ${
                        p.avg_mining_hashrate
                        === null
                        ? "—"
                        :
                        Number(
                            p.avg_mining_hashrate
                        ).toFixed(1)
                        + " TH/s"
                    }
                </td>

            </tr>
        `;
    }


    body.innerHTML =
        html;
}


async function loadFarmHistory() {

    if (!farmHistoryOpen) {
        return;
    }


    const hours =
        Number(
            document.getElementById(
                "farmHistoryPeriod"
            ).value
        );


    const status =
        document.getElementById(
            "farmHistoryStatus"
        );


    status.textContent =
        "Loading farm history...";


    try {

        const response =
            await fetch(
                `/api/farm/history?hours=${hours}`
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.detail
                || "Farm history failed"
            );
        }


        lastFarmHistory =
            data;


        const current =
            data.current;


        document.getElementById(
            "farmNowMining"
        ).textContent =
            current.mining
            + "/"
            + current.total;


        document.getElementById(
            "farmNowPaused"
        ).textContent =
            current.paused;


        document.getElementById(
            "farmNowOffline"
        ).textContent =
            current.offline;


        document.getElementById(
            "farmNowHashrate"
        ).textContent =
            Number(
                current.hashrate
                || 0
            ).toFixed(1)
            + " TH/s";


        document.getElementById(
            "farmNowPower"
        ).textContent =
            (
                Number(
                    current.power
                    || 0
                )
                / 1000
            ).toFixed(2)
            + " kW";


        document.getElementById(
            "farmNowTemp"
        ).textContent =
            current.max_temp === null
            ? "—"
            :
            Number(
                current.max_temp
            ).toFixed(0)
            + " °C";


        status.textContent =
            "Historical points: "
            + data.points.length
            + " · Bucket: "
            + (
                data.bucket_seconds
                / 60
            )
            + " min";


        updateChartSummary(
            "farmHashrateSummary",
            data.points,
            "hashrate",
            "TH/s"
        );


        updateChartSummary(
            "farmPowerSummary",
            data.points,
            "power",
            "kW",
            value =>
                value / 1000
        );


        updateChartSummary(
            "farmTempSummary",
            data.points,
            "max_temp",
            "°C"
        );


        drawLineChart(
            "farmHashrateChart",
            data.points,
            "hashrate",
            "TH/s"
        );


        drawLineChart(
            "farmPowerChart",
            data.points,
            "power",
            "kW",
            value =>
                value / 1000
        );


        drawLineChart(
            "farmTempChart",
            data.points,
            "max_temp",
            "°C"
        );


        drawMultiLineChart(
            "farmStateChart",
            data.points,
            [
                {
                    field: "mining",
                    label: "MINING"
                },
                {
                    field: "paused",
                    label: "PAUSED"
                },
                {
                    field: "offline",
                    label: "OFFLINE"
                }
            ]
        );


        renderFarmProblems(
            data.problems
        );

    }

    catch (error) {

        status.textContent =
            "Farm history error: "
            + error.message;
    }
}


document.addEventListener(
    "keydown",
    function(event) {

        if (
            event.key === "Escape"
            &&
            farmHistoryOpen
        ) {

            closeFarmHistory();
        }
    }
);


let currentHistoryMiner = null;


function openHistory(
    minerId,
    name,
    ip
) {

    currentHistoryMiner =
        minerId;


    document.getElementById(
        "historyTitle"
    ).textContent =
        name;


    document.getElementById(
        "historySubtitle"
    ).textContent =
        ip;


    document.getElementById(
        "historyBackdrop"
    ).classList.add(
        "open"
    );


    document.body.style.overflow =
        "hidden";


    loadHistory();
}


function closeHistory() {

    document.getElementById(
        "historyBackdrop"
    ).classList.remove(
        "open"
    );


    document.body.style.overflow =
        "";


    currentHistoryMiner =
        null;
}


function historyBackdropClick(
    event
) {

    if (
        event.target.id
        === "historyBackdrop"
    ) {

        closeHistory();
    }
}


document.addEventListener(
    "keydown",
    function(event) {

        if (
            event.key === "Escape"
        ) {

            closeHistory();
        }
    }
);


function finiteValues(
    points,
    field,
    converter=null
) {

    const values = [];


    for (
        const point
        of points
    ) {

        let value =
            point[field];


        if (
            value === null
            ||
            value === undefined
        ) {
            continue;
        }


        value =
            Number(value);


        if (
            !Number.isFinite(
                value
            )
        ) {
            continue;
        }


        if (converter) {

            value =
                converter(value);
        }


        values.push(
            value
        );
    }


    return values;
}


function updateChartSummary(
    elementId,
    points,
    field,
    unit,
    converter=null
) {

    const values =
        finiteValues(
            points,
            field,
            converter
        );


    const element =
        document.getElementById(
            elementId
        );


    if (!values.length) {

        element.textContent =
            "No data";

        return;
    }


    const min =
        Math.min(...values);

    const max =
        Math.max(...values);

    const avg =
        values.reduce(
            (a, b) => a + b,
            0
        )
        /
        values.length;


    element.textContent =
        "Min "
        + min.toFixed(1)
        + " "
        + unit
        + " · Avg "
        + avg.toFixed(1)
        + " "
        + unit
        + " · Max "
        + max.toFixed(1)
        + " "
        + unit;
}


function drawLineChart(
    canvasId,
    points,
    field,
    unit,
    converter=null
) {

    const canvas =
        document.getElementById(
            canvasId
        );


    const rect =
        canvas.getBoundingClientRect();


    const dpr =
        window.devicePixelRatio
        || 1;


    const width =
        Math.max(
            600,
            Math.floor(
                rect.width
            )
        );


    const height = 240;


    canvas.width =
        width * dpr;

    canvas.height =
        height * dpr;


    const ctx =
        canvas.getContext(
            "2d"
        );


    ctx.setTransform(
        dpr,
        0,
        0,
        dpr,
        0,
        0
    );


    ctx.clearRect(
        0,
        0,
        width,
        height
    );


    const left = 58;
    const right = 15;
    const top = 15;
    const bottom = 35;


    const graphWidth =
        width
        - left
        - right;


    const graphHeight =
        height
        - top
        - bottom;


    const usable = [];


    for (
        const point
        of points
    ) {

        let value =
            point[field];


        if (
            value === null
            ||
            value === undefined
        ) {
            continue;
        }


        value =
            Number(value);


        if (
            !Number.isFinite(value)
        ) {
            continue;
        }


        if (converter) {

            value =
                converter(value);
        }


        const timestamp =
            new Date(
                point.time
            ).getTime();


        if (
            !Number.isFinite(
                timestamp
            )
        ) {
            continue;
        }


        usable.push({
            timestamp,
            value
        });
    }


    ctx.font =
        "11px system-ui";


    if (!usable.length) {

        ctx.fillStyle =
            "#9299a8";

        ctx.fillText(
            "No historical data",
            left,
            35
        );

        return;
    }


    let minTime =
        usable[0].timestamp;

    let maxTime =
        usable[
            usable.length - 1
        ].timestamp;


    if (
        minTime === maxTime
    ) {

        maxTime += 1;
    }


    let minValue =
        Math.min(
            ...usable.map(
                p => p.value
            )
        );


    let maxValue =
        Math.max(
            ...usable.map(
                p => p.value
            )
        );


    if (
        minValue === maxValue
    ) {

        if (
            minValue === 0
        ) {

            maxValue = 1;

        }

        else {

            const padding =
                Math.abs(
                    minValue
                ) * 0.1;

            minValue -= padding;
            maxValue += padding;
        }
    }


    const range =
        maxValue
        - minValue;


    const valuePadding =
        range * 0.08;


    minValue =
        Math.max(
            0,
            minValue
            - valuePadding
        );


    maxValue +=
        valuePadding;


    function x(timestamp) {

        return left
            +
            (
                (
                    timestamp
                    - minTime
                )
                /
                (
                    maxTime
                    - minTime
                )
            )
            * graphWidth;
    }


    function y(value) {

        return top
            +
            (
                1
                -
                (
                    value
                    - minValue
                )
                /
                (
                    maxValue
                    - minValue
                )
            )
            * graphHeight;
    }


    // Grid + Y labels

    ctx.strokeStyle =
        "#2b303b";

    ctx.fillStyle =
        "#9299a8";

    ctx.lineWidth = 1;


    const yLines = 5;


    for (
        let i = 0;
        i <= yLines;
        i++
    ) {

        const yy =
            top
            +
            graphHeight
            * i
            / yLines;


        ctx.beginPath();

        ctx.moveTo(
            left,
            yy
        );

        ctx.lineTo(
            width - right,
            yy
        );

        ctx.stroke();


        const value =
            maxValue
            -
            (
                maxValue
                - minValue
            )
            * i
            / yLines;


        ctx.fillText(
            value.toFixed(1),
            4,
            yy + 4
        );
    }


    // X labels

    const xLabels = 5;


    for (
        let i = 0;
        i <= xLabels;
        i++
    ) {

        const timestamp =
            minTime
            +
            (
                maxTime
                - minTime
            )
            * i
            / xLabels;


        const xx =
            left
            +
            graphWidth
            * i
            / xLabels;


        const date =
            new Date(timestamp);


        const label =
            date.toLocaleString(
                [],
                {
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit"
                }
            );


        ctx.fillText(
            label,
            Math.max(
                left,
                Math.min(
                    xx - 32,
                    width - 75
                )
            ),
            height - 10
        );
    }


    // Actual data line

    ctx.strokeStyle =
        "#7ab8ff";

    ctx.lineWidth = 2;

    ctx.beginPath();


    let first = true;


    for (
        const point
        of usable
    ) {

        const xx =
            x(
                point.timestamp
            );

        const yy =
            y(
                point.value
            );


        if (first) {

            ctx.moveTo(
                xx,
                yy
            );

            first = false;

        }

        else {

            ctx.lineTo(
                xx,
                yy
            );
        }
    }


    ctx.stroke();


    // Unit

    ctx.fillStyle =
        "#9299a8";

    ctx.fillText(
        unit,
        4,
        12
    );
}


async function loadHistory() {

    if (
        currentHistoryMiner === null
    ) {
        return;
    }


    const hours =
        Number(
            document.getElementById(
                "historyPeriod"
            ).value
        );


    const status =
        document.getElementById(
            "historyStatus"
        );


    status.textContent =
        "Loading historical data...";


    try {

        const response =
            await fetch(
                `/api/history/${currentHistoryMiner}?hours=${hours}`
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.detail
                || "History request failed"
            );
        }


        const points =
            data.points || [];


        status.textContent =
            "Points: "
            + points.length
            + " · Period: "
            + (
                hours === 24
                ? "24 hours"
                :
                hours === 168
                ? "7 days"
                :
                hours === 720
                ? "30 days"
                :
                "90 days"
            );


        updateChartSummary(
            "hashrateSummary",
            points,
            "hashrate",
            "TH/s"
        );


        updateChartSummary(
            "temperatureSummary",
            points,
            "temp",
            "°C"
        );


        updateChartSummary(
            "powerSummary",
            points,
            "power",
            "kW",
            value =>
                value / 1000
        );


        drawLineChart(
            "hashrateChart",
            points,
            "hashrate",
            "TH/s"
        );


        drawLineChart(
            "temperatureChart",
            points,
            "temp",
            "°C"
        );


        drawLineChart(
            "powerChart",
            points,
            "power",
            "kW",
            value =>
                value / 1000
        );

    }

    catch (error) {

        status.textContent =
            "History error: "
            + error.message;
    }
}


window.addEventListener(
    "resize",
    function() {

        if (
            farmHistoryOpen
        ) {

            clearTimeout(
                window.farmHistoryResizeTimer
            );


            window.farmHistoryResizeTimer =
                setTimeout(
                    loadFarmHistory,
                    250
                );
        }


        if (
            currentHistoryMiner
            !== null
        ) {

            clearTimeout(
                window.historyResizeTimer
            );


            window.historyResizeTimer =
                setTimeout(
                    loadHistory,
                    250
                );
        }
    }
);



async function rebootMiner(
    minerId,
    ip
) {

    const confirmed =
        confirm(
            "FULL REBOOT ASIC "
            + ip
            + "?\n\n"
            + "The ASIC will temporarily "
            + "go offline and then boot again."
        );


    if (!confirmed) {
        return;
    }


    try {

        const response =
            await fetch(
                `/api/miners/${minerId}/reboot`,
                {
                    method: "POST"
                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.detail
                || "Reboot request failed"
            );
        }


        if (
            data.already_active
        ) {

            alert(
                "ASIC already has an active "
                + "control job: "
                + data.action
            );
        }


        await loadStatus();

        if (
            typeof loadLogs
            === "function"
        ) {

            await loadLogs();
        }

    }

    catch (error) {

        alert(
            "REBOOT ERROR: "
            + error.message
        );
    }
}




// ============================================================
// ASIC TABLE SORTING
// ============================================================

const MINER_STATE_ORDER = {
    "MINING": 0,
    "STARTING": 1,
    "RESTARTING": 2,
    "SHUTTING-DOWN": 3,
    "PAUSED": 4,
    "OFFLINE": 5,
    "UNKNOWN": 6,
    "CONFIG_REQUIRED": 7,
    "DISABLED": 8,
};


function minerEffectiveState(m) {

    if (!m.enabled) {
        return "DISABLED";
    }

    return String(
        m.state || "UNKNOWN"
    ).toUpperCase();
}


function minerIpValue(ip) {

    const parts =
        String(ip || "")
        .split(".")
        .map(
            value => Number(value)
        );


    if (
        parts.length !== 4
        ||
        parts.some(
            value =>
                !Number.isInteger(value)
                ||
                value < 0
                ||
                value > 255
        )
    ) {
        return Number.MAX_SAFE_INTEGER;
    }


    return (
        (
            parts[0] * 16777216
        )
        +
        (
            parts[1] * 65536
        )
        +
        (
            parts[2] * 256
        )
        +
        parts[3]
    );
}


function minerNumericValue(
    value
) {

    if (
        value === null
        ||
        value === undefined
        ||
        value === ""
    ) {
        return null;
    }


    const number =
        Number(value);


    if (!Number.isFinite(number)) {
        return null;
    }


    return number;
}


function compareMinerNumbers(
    a,
    b,
    direction
) {

    // Unknown values always remain at the bottom,
    // regardless of Asc / Desc.

    if (
        a === null
        &&
        b === null
    ) {
        return 0;
    }


    if (a === null) {
        return 1;
    }


    if (b === null) {
        return -1;
    }


    const result =
        a - b;


    return (
        direction === "desc"
        ? -result
        : result
    );
}


function compareMinerText(
    a,
    b,
    direction
) {

    const result =
        String(a || "")
        .localeCompare(
            String(b || ""),
            undefined,
            {
                numeric: true,
                sensitivity: "base",
            }
        );


    return (
        direction === "desc"
        ? -result
        : result
    );
}


function minerSortTieBreak(
    a,
    b
) {

    return (
        minerIpValue(a.ip)
        -
        minerIpValue(b.ip)
    );
}


function sortMiners(
    miners,
    sortBy,
    direction
) {

    const result =
        Array.from(
            miners || []
        );


    result.sort(
        (a, b) => {

            let comparison = 0;


            if (sortBy === "state") {

                const stateA =
                    minerEffectiveState(a);

                const stateB =
                    minerEffectiveState(b);


                const rankA =
                    (
                        MINER_STATE_ORDER[
                            stateA
                        ]
                        ??
                        100
                    );

                const rankB =
                    (
                        MINER_STATE_ORDER[
                            stateB
                        ]
                        ??
                        100
                    );


                comparison =
                    rankA - rankB;


                if (
                    direction === "desc"
                ) {
                    comparison =
                        -comparison;
                }
            }


            else if (
                sortBy === "hashrate"
            ) {

                comparison =
                    compareMinerNumbers(
                        minerNumericValue(
                            a.hashrate
                        ),
                        minerNumericValue(
                            b.hashrate
                        ),
                        direction
                    );
            }


            else if (
                sortBy === "temp"
            ) {

                comparison =
                    compareMinerNumbers(
                        minerNumericValue(
                            a.temp
                        ),
                        minerNumericValue(
                            b.temp
                        ),
                        direction
                    );
            }


            else if (
                sortBy === "ip"
            ) {

                comparison =
                    (
                        minerIpValue(a.ip)
                        -
                        minerIpValue(b.ip)
                    );


                if (
                    direction === "desc"
                ) {
                    comparison =
                        -comparison;
                }
            }


            else if (
                sortBy === "name"
            ) {

                comparison =
                    compareMinerText(
                        a.name,
                        b.name,
                        direction
                    );
            }


            if (comparison !== 0) {
                return comparison;
            }


            return minerSortTieBreak(
                a,
                b
            );
        }
    );


    return result;
}


function minerSortChanged() {

    const sortBy =
        document.getElementById(
            "sortBy"
        );

    const direction =
        document.getElementById(
            "sortDirection"
        );


    if (
        !sortBy
        ||
        !direction
    ) {
        return;
    }


    try {

        localStorage.setItem(
            "asicManager.sortBy",
            sortBy.value
        );

        localStorage.setItem(
            "asicManager.sortDirection",
            direction.value
        );

    }

    catch (_) {
    }


    loadStatus();
}


function restoreMinerSort() {

    const sortBy =
        document.getElementById(
            "sortBy"
        );

    const direction =
        document.getElementById(
            "sortDirection"
        );


    if (
        !sortBy
        ||
        !direction
    ) {
        return;
    }


    let savedSort = null;
    let savedDirection = null;


    try {

        savedSort =
            localStorage.getItem(
                "asicManager.sortBy"
            );

        savedDirection =
            localStorage.getItem(
                "asicManager.sortDirection"
            );

    }

    catch (_) {
    }


    const allowedSort = [
        "state",
        "hashrate",
        "temp",
        "ip",
        "name",
    ];


    if (
        savedSort
        &&
        allowedSort.includes(
            savedSort
        )
    ) {
        sortBy.value =
            savedSort;
    }


    if (
        savedDirection === "asc"
        ||
        savedDirection === "desc"
    ) {
        direction.value =
            savedDirection;
    }
}


window.addEventListener(
    "load",
    function () {

        restoreMinerSort();

        // Re-render after restoring a saved preference.
        loadStatus();
    }
);




// ============================================================
// QUICK SEARCH
// ============================================================

function matchesMinerSearch(
    miner,
    query
) {

    const q =
        String(
            query || ""
        )
        .trim()
        .toLowerCase();


    if (!q) {
        return true;
    }


    const effectiveState =
        minerEffectiveState(
            miner
        );


    const nowEpoch =
        Date.now() / 1000;


    /*
      Exact operational keywords used by
      clickable status cards.
    */

    if (q === "mining") {

        return (
            miner.enabled
            &&
            effectiveState === "MINING"
        );
    }


    if (q === "paused") {

        return (
            miner.enabled
            &&
            effectiveState === "PAUSED"
        );
    }


    if (q === "offline") {

        return (
            miner.enabled
            &&
            effectiveState === "OFFLINE"
        );
    }


    if (q === "disabled") {

        return (
            !miner.enabled
        );
    }


    if (q === "transition") {

        return (
            miner.enabled
            &&
            (
                effectiveState === "STARTING"
                ||
                effectiveState === "RESTARTING"
                ||
                effectiveState === "SHUTTING-DOWN"
            )
        );
    }


    if (
        q === "unknown/config"
        ||
        q === "unknown"
    ) {

        return (
            miner.enabled
            &&
            (
                effectiveState === "UNKNOWN"
                ||
                effectiveState === "CONFIG_REQUIRED"
                ||
                miner.driver === "unset"
            )
        );
    }


    if (q === "online") {

        return (
            miner.enabled
            &&
            miner.driver !== "unset"
            &&
            miner.last_seen
            &&
            (
                nowEpoch
                -
                miner.last_seen
                <= 60
            )
            &&
            effectiveState !== "OFFLINE"
        );
    }


    if (q === "schedule") {

        return (
            miner.enabled
            &&
            miner.schedule_enabled
        );
    }


    if (q === "overrides") {

        return (
            miner.manual_override_until
            &&
            miner.manual_override_until
            > nowEpoch
        );
    }


    const values = [

        miner.name,
        miner.ip,
        miner.model,
        miner.firmware,
        miner.driver,

        minerEffectiveState(
            miner
        ),

        miner.pool,
    ];


    return values.some(
        value =>
            String(
                value || ""
            )
            .toLowerCase()
            .includes(q)
    );
}


// ============================================================
// SORTABLE TABLE HEADERS
// ============================================================

function updateSortHeaderIndicators() {

    const sortBy =
        document.getElementById(
            "sortBy"
        );

    const direction =
        document.getElementById(
            "sortDirection"
        );


    const fields = [

        "name",
        "ip",
        "state",
        "hashrate",
        "temp",

    ];


    for (
        const field
        of fields
    ) {

        const marker =
            document.getElementById(
                "sortMark-"
                +
                field
            );


        if (!marker) {
            continue;
        }


        marker.textContent =
            "↕";


        if (
            sortBy
            &&
            direction
            &&
            sortBy.value === field
        ) {

            marker.textContent =
                (
                    direction.value
                    === "desc"
                )
                ? "↓"
                : "↑";
        }
    }
}


function sortByHeader(
    field
) {

    const sortBy =
        document.getElementById(
            "sortBy"
        );

    const direction =
        document.getElementById(
            "sortDirection"
        );


    if (
        !sortBy
        ||
        !direction
    ) {
        return;
    }


    const allowed = [

        "name",
        "ip",
        "state",
        "hashrate",
        "temp",

    ];


    if (
        !allowed.includes(
            field
        )
    ) {
        return;
    }


    if (
        sortBy.value
        === field
    ) {

        direction.value =
            (
                direction.value
                === "asc"
            )
            ? "desc"
            : "asc";

    }

    else {

        sortBy.value =
            field;


        /*
          For operational values:
          hottest / fastest first.

          Text/state/IP:
          ascending by default.
        */

        direction.value =
            (
                field
                === "hashrate"
                ||
                field
                === "temp"
            )
            ? "desc"
            : "asc";
    }


    updateSortHeaderIndicators();

    minerSortChanged();
}




// ============================================================
// v0.1.0 - SAFETY / USABILITY
// ============================================================


function normalizeUiText(
    value
) {

    return String(
        value || ""
    )
    .replace(
        /\s+/g,
        " "
    )
    .trim()
    .toUpperCase();
}


// ============================================================
// BULK ACTION CONFIRMATIONS
// ============================================================

function bulkTargetCount(
    action
) {

    const data =
        window.__asicLastStatus;


    if (
        !data
        ||
        !Array.isArray(
            data.miners
        )
    ) {
        return null;
    }


    if (
        action === "resume"
        ||
        action === "pause"
    ) {

        return data.miners.filter(
            miner =>
                miner.enabled
                &&
                miner.driver !== "unset"
        ).length;
    }


    if (
        action === "schedule-on"
        ||
        action === "schedule-off"
    ) {

        return data.miners.length;
    }


    if (
        action === "clear-overrides"
    ) {

        const nowEpoch =
            Date.now() / 1000;


        return data.miners.filter(
            miner =>
                miner.manual_override_until
                &&
                miner.manual_override_until
                > nowEpoch
        ).length;
    }


    return data.miners.length;
}


function bulkConfirmationMessage(
    action
) {

    const count =
        bulkTargetCount(
            action
        );


    const countText =
        (
            count === null
        )
        ? "all applicable ASICs"
        : count + " ASIC(s)";


    if (action === "resume") {

        return (
            "RESUME ALL\n\n"
            +
            "Send RESUME to "
            +
            countText
            +
            "?\n\n"
            +
            "This is a farm-wide control operation."
        );
    }


    if (action === "pause") {

        return (
            "PAUSE ALL\n\n"
            +
            "Send PAUSE to "
            +
            countText
            +
            "?\n\n"
            +
            "This is a farm-wide control operation."
        );
    }


    if (action === "schedule-on") {

        return (
            "SCHEDULE ON ALL\n\n"
            +
            "Enable scheduling for "
            +
            countText
            +
            "?"
        );
    }


    if (action === "schedule-off") {

        return (
            "SCHEDULE OFF ALL\n\n"
            +
            "Disable scheduling for "
            +
            countText
            +
            "?"
        );
    }


    if (action === "clear-overrides") {

        return (
            "CLEAR OVERRIDES\n\n"
            +
            "Clear "
            +
            countText
            +
            " active manual override(s)?"
        );
    }


    if (action === "scheduler") {

        const enabled =
            Boolean(
                window.__asicLastStatus
                ?.scheduler_enabled
            );


        return (
            enabled
            ? (
                "SCHEDULER\n\n"
                +
                "Turn the global farm scheduler OFF?"
            )
            : (
                "SCHEDULER\n\n"
                +
                "Turn the global farm scheduler ON?"
            )
        );
    }


    return (
        "Confirm this farm-wide operation?"
    );
}


function wireBulkConfirmations() {

    const mappings = {

        "RESUME ALL":
            "resume",

        "PAUSE ALL":
            "pause",

        "SCHEDULE ON ALL":
            "schedule-on",

        "SCHEDULE OFF ALL":
            "schedule-off",

        "CLEAR OVERRIDES":
            "clear-overrides",

    };


    for (
        const button
        of document.querySelectorAll(
            "button"
        )
    ) {

        const label =
            normalizeUiText(
                button.textContent
            );


        if (
            mappings[label]
        ) {

            button.dataset.bulkConfirm =
                mappings[label];
        }
    }


    const scheduler =
        document.getElementById(
            "schedulerButton"
        );


    if (scheduler) {

        scheduler.dataset.bulkConfirm =
            "scheduler";
    }


    if (
        window.__asicBulkConfirmInstalled
    ) {
        return;
    }


    window.__asicBulkConfirmInstalled =
        true;


    document.addEventListener(
        "click",

        function (
            event
        ) {

            const button =
                event.target.closest(
                    "button[data-bulk-confirm]"
                );


            if (!button) {
                return;
            }


            const action =
                button.dataset.bulkConfirm;


            const message =
                bulkConfirmationMessage(
                    action
                );


            if (
                !window.confirm(
                    message
                )
            ) {

                event.preventDefault();

                event.stopPropagation();

                event.stopImmediatePropagation();
            }

        },

        true
    );
}


// ============================================================
// SEARCH CLEAR BUTTON
// ============================================================

function clearMinerSearch() {

    const search =
        document.getElementById(
            "minerSearch"
        );


    if (!search) {
        return;
    }


    search.value = "";

    refreshStatusCardVisuals();

    loadStatus();

    search.focus();
}


function wireSearchClear() {

    const search =
        document.getElementById(
            "minerSearch"
        );


    if (
        !search
        ||
        document.getElementById(
            "clearMinerSearch"
        )
    ) {
        return;
    }


    const button =
        document.createElement(
            "button"
        );


    button.id =
        "clearMinerSearch";

    button.type =
        "button";

    button.className =
        "secondary mini clear-search-button";

    button.title =
        "Clear search";

    button.textContent =
        "×";


    button.addEventListener(
        "click",
        clearMinerSearch
    );


    search.insertAdjacentElement(
        "afterend",
        button
    );
}


// ============================================================
// CLICKABLE STATUS CARDS
// ============================================================

const STATUS_CARD_QUERY = {

    "ONLINE":
        "online",

    "MINING":
        "mining",

    "PAUSED":
        "paused",

    "OFFLINE":
        "offline",

    "SCHEDULE":
        "schedule",

    "OVERRIDES":
        "overrides",

    "TRANSITION":
        "transition",

    "DISABLED":
        "disabled",

    "UNKNOWN/CONFIG":
        "unknown/config",

    "UNKNOWN/ CONFIG":
        "unknown/config",

};


function statusCardLabel(
    card
) {

    if (!card) {
        return "";
    }


    const muted =
        card.querySelector(
            ".muted"
        );


    if (!muted) {
        return "";
    }


    return normalizeUiText(
        muted.textContent
    );
}


function statusCardValue(
    card
) {

    if (!card) {
        return 0;
    }


    const value =
        card.querySelector(
            ".value"
        );


    const text =
        value
        ? value.textContent
        : card.textContent;


    const match =
        String(text)
        .match(
            /-?\d+/
        );


    return match
        ? Number(
            match[0]
        )
        : 0;
}


function statusCardClick(
    query
) {

    const search =
        document.getElementById(
            "minerSearch"
        );

    const filter =
        document.getElementById(
            "filter"
        );


    if (!search) {
        return;
    }


    if (filter) {
        filter.value = "all";
    }


    const current =
        String(
            search.value || ""
        )
        .trim()
        .toLowerCase();


    if (
        current
        === query.toLowerCase()
    ) {

        search.value = "";

    }

    else {

        search.value =
            query;
    }


    refreshStatusCardVisuals();

    loadStatus();
}


function refreshStatusCardVisuals() {

    const search =
        document.getElementById(
            "minerSearch"
        );


    const currentQuery =
        String(
            search?.value || ""
        )
        .trim()
        .toLowerCase();


    for (
        const card
        of document.querySelectorAll(
            ".cards .card"
        )
    ) {

        const label =
            statusCardLabel(
                card
            );


        const query =
            STATUS_CARD_QUERY[
                label
            ];


        card.classList.remove(
            "status-card-alert",
            "status-card-warning",
            "status-card-active"
        );


        if (
            query
            &&
            currentQuery
            === query.toLowerCase()
        ) {

            card.classList.add(
                "status-card-active"
            );
        }


        const value =
            statusCardValue(
                card
            );


        if (
            (
                label === "OFFLINE"
                ||
                label === "UNKNOWN/CONFIG"
                ||
                label === "UNKNOWN/ CONFIG"
            )
            &&
            value > 0
        ) {

            card.classList.add(
                "status-card-alert"
            );
        }


        if (
            (
                label === "TRANSITION"
                ||
                label === "DISABLED"
            )
            &&
            value > 0
        ) {

            card.classList.add(
                "status-card-warning"
            );
        }
    }


    const issuesButton =
        document.getElementById(
            "issuesButton"
        );


    if (issuesButton) {

        const match =
            String(
                issuesButton.textContent
            )
            .match(
                /(\d+)/
            );


        const count =
            match
            ? Number(
                match[1]
            )
            : 0;


        issuesButton.classList.toggle(
            "issues-alert",
            count > 0
        );
    }
}


function wireStatusCards() {

    const cards =
        document.querySelectorAll(
            ".cards .card"
        );


    for (
        const card
        of cards
    ) {

        const label =
            statusCardLabel(
                card
            );


        const query =
            STATUS_CARD_QUERY[
                label
            ];


        if (!query) {
            continue;
        }


        card.classList.add(
            "status-card-interactive"
        );


        card.title =
            "Click to filter ASIC table";


        if (
            card.dataset.statusCardWired
        ) {
            continue;
        }


        card.dataset.statusCardWired =
            "1";


        card.addEventListener(
            "click",

            function () {

                statusCardClick(
                    query
                );
            }
        );
    }


    refreshStatusCardVisuals();


    const cardsContainer =
        document.querySelector(
            ".cards"
        );


    if (
        cardsContainer
        &&
        !cardsContainer
            .dataset
            .statusObserver
    ) {

        cardsContainer
            .dataset
            .statusObserver =
            "1";


        const observer =
            new MutationObserver(
                function () {

                    refreshStatusCardVisuals();
                }
            );


        observer.observe(
            cardsContainer,
            {
                childList: true,
                characterData: true,
                subtree: true,
            }
        );
    }


    const issuesButton =
        document.getElementById(
            "issuesButton"
        );


    if (
        issuesButton
        &&
        !issuesButton.dataset
            .issueObserver
    ) {

        issuesButton.dataset
            .issueObserver =
            "1";


        const issueObserver =
            new MutationObserver(
                function () {

                    refreshStatusCardVisuals();
                }
            );


        issueObserver.observe(
            issuesButton,
            {
                childList: true,
                characterData: true,
                subtree: true,
            }
        );
    }
}


// ============================================================
// INITIALIZATION
// ============================================================

function initV143() {

    wireBulkConfirmations();

    wireSearchClear();

    wireStatusCards();

    refreshStatusCardVisuals();
}


if (
    document.readyState
    === "loading"
) {

    document.addEventListener(
        "DOMContentLoaded",
        initV143
    );

}

else {

    initV143();
}


async function loadStatus() {

    try {

        const response =
            await fetch(
                "/api/status"
            );

        const data =
            await response.json();


        window.__asicLastStatus =
            data;

        const nowEpoch =
            Date.now() / 1000;

        const filter =
            document.getElementById(
                "filter"
            ).value;


        const minerSearch =
            (
                document.getElementById(
                    "minerSearch"
                )
                ?.value
                ||
                ""
            );


        const sortBy =
            (
                document.getElementById(
                    "sortBy"
                )
                ?.value
                ||
                "state"
            );

        const sortDirection =
            (
                document.getElementById(
                    "sortDirection"
                )
                ?.value
                ||
                "asc"
            );


        const sortedMiners =
            sortMiners(
                data.miners,
                sortBy,
                sortDirection
            );


        const appVersion =
            document.getElementById(
                "appVersion"
            );


        if (appVersion) {

            appVersion.textContent =
                "v"
                +
                (
                    data.version
                    ||
                    "1.4.3"
                );
        }

        document.getElementById(
            "clock"
        ).textContent =
            new Date(
                data.now
            ).toLocaleString();

        const scheduleTimezone =
            document.getElementById(
                "scheduleTimezone"
            );

        if (scheduleTimezone) {

            scheduleTimezone.textContent =
                data.timezone
                ||
                "configured";

        }


        document.getElementById(
            "desiredState"
        ).textContent =
            (
                data.desired_state
                ||
                "—"
            );

        document.getElementById(
            "nextTransition"
        ).textContent =
            (
                data.next_transition
                ? (
                    new Date(
                        data.next_transition
                    ).toLocaleString(
                        "en-GB",
                        {
                            timeZone:
                                data.timezone,

                            day:
                                "2-digit",

                            month:
                                "2-digit",

                            hour:
                                "2-digit",

                            minute:
                                "2-digit",

                            hour12:
                                false,
                        }
                    )
                    +
                    " "
                    +
                    data.timezone
                )
                :
                "—"
            );

        const scheduler =
            document.getElementById(
                "schedulerButton"
            );

        if (
            data.scheduler_enabled
        ) {

            scheduler.className =
                "scheduler-on";

            scheduler.textContent =
                "Scheduler: ON";

        }
        else {

            scheduler.className =
                "scheduler-off";

            scheduler.textContent =
                "Scheduler: OFF";
        }


        let online = 0;
        let mining = 0;
        let paused = 0;
        let offline = 0;

        let transition = 0;
        let disabled = 0;
        let unknownConfig = 0;

        let scheduleCount = 0;
        let overrides = 0;

        let totalHash = 0;
        let totalPower = 0;

        let html = "";
        let visibleCount = 0;


        for (
            const m
            of sortedMiners
        ) {

            const isOnline = (
                m.enabled
                &&
                m.driver !== "unset"
                &&
                m.last_seen
                &&
                (
                    nowEpoch
                    - m.last_seen
                    <= 60
                )
                &&
                m.state !== "OFFLINE"
            );

            if (isOnline) {
                online++;
            }


            if (
                !m.enabled
            ) {
                disabled++;
            }


            if (
                m.enabled
                &&
                (
                    m.state
                    === "STARTING"
                    ||
                    m.state
                    === "RESTARTING"
                    ||
                    m.state
                    === "SHUTTING-DOWN"
                )
            ) {
                transition++;
            }


            if (
                m.enabled
                &&
                (
                    m.state
                    === "UNKNOWN"
                    ||
                    m.state
                    === "CONFIG_REQUIRED"
                    ||
                    m.driver
                    === "unset"
                )
            ) {
                unknownConfig++;
            }

            if (
                m.enabled
                &&
                m.state === "MINING"
            ) {
                mining++;
            }

            if (
                m.enabled
                &&
                m.state === "PAUSED"
            ) {
                paused++;
            }

            if (
                m.enabled
                &&
                m.state === "OFFLINE"
            ) {
                offline++;
            }

            if (
                m.enabled
                &&
                m.schedule_enabled
            ) {
                scheduleCount++;
            }

            if (
                m.manual_override_until
                &&
                m.manual_override_until
                > nowEpoch
            ) {
                overrides++;
            }

            if (
                m.enabled
                &&
                m.driver !== "unset"
            ) {

                totalHash += Number(
                    m.hashrate || 0
                );

                totalPower += Number(
                    m.power || 0
                );
            }


            if (
                !matchesFilter(
                    m,
                    filter
                )
            ) {
                continue;
            }


            if (
                !matchesMinerSearch(
                    m,
                    minerSearch
                )
            ) {
                continue;
            }


            visibleCount++;


            let override = "—";

            if (
                m.manual_override_until
                &&
                m.manual_override_until
                > nowEpoch
            ) {

                override =
                    formatEpoch(
                        m.manual_override_until
                    );
            }


            let error = "";

            if (m.last_error) {

                error = `
                    <div class="error">
                        ${esc(m.last_error)}
                    </div>
                `;
            }


            let displayState =
                m.state;

            if (!m.enabled) {

                displayState =
                    "DISABLED";
            }


            let controlButtons = "—";

            if (
                m.driver !== "unset"
                &&
                m.enabled
                &&
                !m.control_job
            ) {

                controlButtons = `
                    <button
                        class="start mini"
                        onclick="minerAction(
                            ${m.id},
                            'resume'
                        )"
                    >
                        ▶
                    </button>

                    <button
                        class="stop mini"
                        onclick="minerAction(
                            ${m.id},
                            'pause'
                        )"
                    >
                        ⏸
                    </button>
                `;
            }

            else if (
                m.control_job
            ) {

                controlButtons = `
                    <span
                        class="small"
                        style="color:#67b7ff"
                    >
                        VERIFYING
                    </span>
                `;
            }


            const rowClass = [
                m.enabled
                    ? ""
                    : "disabled",

                m.last_error
                    ? "row-error"
                    : ""
            ].join(" ");


            html += `
            <tr class="${rowClass}">

                <td>

                    <strong>
                        ${esc(m.name)}
                    </strong>

                    <div class="muted small">
                        ${esc(
                            m.model || ""
                        )}
                    </div>

                    ${error}

                </td>


                <td>
                    ${esc(m.ip)}
                </td>


                <td>
                    ${firmwareSelect(m)}
                </td>


                <td>

                    <button
                        class="secondary mini"
                        onclick="toggleEnabled(
                            ${m.id}
                        )"
                    >
                        ${
                            m.enabled
                            ? "ON"
                            : "OFF"
                        }
                    </button>

                </td>


                <td>

                    ${
                        m.driver === "unset"
                        ? "—"
                        : `
                            <button
                                class="secondary mini"
                                onclick="toggleSchedule(
                                    ${m.id}
                                )"
                            >
                                ${
                                    m.schedule_enabled
                                    ? "ON"
                                    : "OFF"
                                }
                            </button>
                        `
                    }

                </td>


                <td>

                    <span
                        class="state ${esc(m.state)}"
                    >
                        ${esc(displayState)}
                    </span>

                    ${
                        m.control_job
                        ? `
                            <div
                                class="small"
                                style="
                                    margin-top:4px;
                                    color:#67b7ff;
                                "
                            >
                                ${esc(
                                    m.control_job.action
                                    .toUpperCase()
                                )}
                                ·
                                ${esc(
                                    m.control_job.status
                                )}
                                ·
                                ${m.control_job.attempts}/
                                ${m.control_job.max_attempts}
                            </div>
                        `
                        : ""
                    }

                </td>


                <td>

                    ${
                        m.driver === "unset"
                        ? "—"
                        :
                        num(
                            m.hashrate,
                            1
                        )
                        + " TH/s"
                    }

                </td>


                <td>

                    ${
                        m.driver === "unset"
                        ? "—"
                        :
                        num(
                            m.avg_hashrate,
                            1
                        )
                        + " TH/s"
                    }

                </td>


                <td>
                    ${tempHTML(m.temp)}
                </td>


                <td>

                    ${
                        m.power === null
                        ||
                        m.power === undefined
                        ? "—"
                        :
                        num(
                            m.power / 1000,
                            2
                        )
                        + " kW"
                    }

                </td>


                <td>
                    ${esc(
                        m.pool || "—"
                    )}
                </td>


                <td class="small">
                    ${override}
                </td>


                <td>
                    ${controlButtons}

                    ${
                        (
                            !m.control_job
                            &&
                            m.enabled
                            &&
                            m.driver !== "unset"
                        )
                        ? `
                            <button
                                class="reboot-button mini"
                                onclick="rebootMiner(
                                    ${m.id},
                                    '${esc(m.ip)}'
                                )"
                            >
                                REBOOT
                            </button>
                        `
                        : ""
                    }

                </td>


                <td>

                    <button
                        class="history-button mini"
                        onclick="openHistory(
                            ${m.id},
                            '${esc(m.name)}',
                            '${esc(m.ip)}'
                        )"
                    >
                        History
                    </button>

                </td>


                <td>

                    <a
                        href="/remote/${m.id}"
                        target="_blank"
                        rel="noopener"
                        title="Open ASIC web interface"
                    >
                        Open
                    </a>

                </td>

            </tr>
            `;
        }


        const transitionElement =
            document.getElementById(
                "transitionCount"
            );

        if (
            transitionElement
        ) {

            transitionElement.textContent =
                transition;
        }


        const disabledElement =
            document.getElementById(
                "disabledCount"
            );

        if (
            disabledElement
        ) {

            disabledElement.textContent =
                disabled;
        }


        const unknownConfigElement =
            document.getElementById(
                "unknownConfigCount"
            );

        if (
            unknownConfigElement
        ) {

            unknownConfigElement.textContent =
                unknownConfig;
        }


        const searchCountElement =
            document.getElementById(
                "minerSearchCount"
            );

        if (
            searchCountElement
        ) {

            searchCountElement.textContent =
                visibleCount
                +
                "/"
                +
                data.miners.length;
        }


        updateSortHeaderIndicators();


        document.getElementById(
            "minerRows"
        ).innerHTML =
            html;


        document.getElementById(
            "countOnline"
        ).textContent =
            online
            + "/"
            + data.miners.length;


        document.getElementById(
            "countMining"
        ).textContent =
            mining;


        document.getElementById(
            "countPaused"
        ).textContent =
            paused;


        document.getElementById(
            "countOffline"
        ).textContent =
            offline;


        document.getElementById(
            "countSchedule"
        ).textContent =
            scheduleCount
            + "/"
            + data.miners.length;


        document.getElementById(
            "countOverrides"
        ).textContent =
            overrides;


        document.getElementById(
            "totalHash"
        ).textContent =
            totalHash.toFixed(1)
            + " TH/s";


        document.getElementById(
            "totalPower"
        ).textContent =
            (
                totalPower / 1000
            ).toFixed(2)
            + " kW";

    }

    catch (error) {

        console.error(
            error
        );
    }
}


async function loadLogs() {

    try {

        const response =
            await fetch(
                "/api/logs?limit=100"
            );

        const data =
            await response.json();

        let html = "";

        for (
            const item
            of data.logs
        ) {

            let sourceClass = "";

            if (
                item.source ===
                "SCHEDULER"
            ) {

                sourceClass =
                    "source-scheduler";
            }

            else if (
                item.source ===
                "MANUAL"
                ||
                item.source ===
                "LOCAL"
                ||
                String(
                    item.source || ""
                ).startsWith(
                    "WEB:"
                )
            ) {

                sourceClass =
                    "source-manual";
            }

            else {

                sourceClass =
                    "source-system";
            }


            html += `
            <tr>

                <td>
                    ${esc(
                        new Date(
                            item.time
                        ).toLocaleString()
                    )}
                </td>

                <td
                    class="${sourceClass}"
                >
                    ${esc(item.source)}
                </td>

                <td>
                    <strong>
                        ${esc(item.action)}
                    </strong>
                </td>

                <td>
                    ${esc(
                        item.name || "—"
                    )}
                </td>

                <td>
                    ${esc(
                        item.ip || "—"
                    )}
                </td>

                <td
                    class="${
                        item.success
                        ? "log-ok"
                        : "log-fail"
                    }"
                >
                    ${
                        item.success
                        ? "OK"
                        : "FAILED"
                    }
                </td>

                <td class="small">
                    ${esc(
                        item.message || ""
                    )}
                </td>

            </tr>
            `;
        }


        if (!html) {

            html = `
                <tr>
                    <td colspan="7">
                        No actions recorded yet.
                    </td>
                </tr>
            `;
        }


        document.getElementById(
            "logRows"
        ).innerHTML =
            html;

    }

    catch (error) {

        console.error(
            error
        );
    }
}


async function minerAction(
    id,
    action
) {

    if (
        !confirm(
            action.toUpperCase()
            + " this ASIC?"
        )
    ) {
        return;
    }

    const response =
        await fetch(
            `/api/miners/${id}/control/${action}`,
            {
                method: "POST"
            }
        );

    if (!response.ok) {

        const data =
            await response.json();

        alert(
            data.detail
            ||
            "Command failed"
        );
    }

    await loadStatus();
    await loadLogs();
}


async function allAction(
    action
) {

    if (
        !confirm(
            action.toUpperCase()
            +
            " ALL configured "
            +
            "and enabled ASICs?"
        )
    ) {
        return;
    }

    const response =
        await fetch(
            `/api/all/${action}`,
            {
                method: "POST"
            }
        );

    const data =
        await response.json();

    const failed =
        data.results.filter(
            x => !x.success
        );

    if (failed.length) {

        alert(
            "Failed:\n"
            +
            failed.map(
                x =>
                    x.ip
                    + ": "
                    + x.error
            ).join("\n")
        );
    }

    await loadStatus();
    await loadLogs();
}


async function toggleScheduler() {

    const response =
        await fetch(
            "/api/scheduler/toggle",
            {
                method: "POST"
            }
        );

    if (!response.ok) {

        alert(
            "Scheduler toggle failed"
        );

        return;
    }

    await loadStatus();
    await loadLogs();
}


async function scheduleAll(state) {

    const verb =
        state === "on"
        ? "ENABLE"
        : "DISABLE";

    if (
        !confirm(
            verb
            +
            " schedule for all "
            +
            "configured ASICs?"
        )
    ) {
        return;
    }

    const response =
        await fetch(
            `/api/schedule/all/${state}`,
            {
                method: "POST"
            }
        );

    if (!response.ok) {

        const data =
            await response.json();

        alert(
            data.detail
            || "Failed"
        );

        return;
    }

    await loadStatus();
    await loadLogs();
}


async function clearOverrides() {

    if (
        !confirm(
            "Clear all manual overrides?"
        )
    ) {
        return;
    }

    const response =
        await fetch(
            "/api/overrides/clear",
            {
                method: "POST"
            }
        );

    const data =
        await response.json();

    if (!response.ok) {

        alert(
            data.detail
            || "Failed"
        );

        return;
    }

    await loadStatus();
    await loadLogs();
}


async function changeDriver(
    id,
    driver
) {

    if (
        !confirm(
            "Change firmware profile?\n\n"
            +
            "Credentials and telemetry "
            +
            "for this ASIC will be reset."
        )
    ) {

        await loadStatus();
        return;
    }

    const response =
        await fetch(
            `/api/miners/${id}/driver`,
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json"
                },

                body:
                    JSON.stringify({
                        driver: driver
                    })
            }
        );

    if (!response.ok) {

        const data =
            await response.json();

        alert(
            data.detail
            || "Failed"
        );
    }

    await loadStatus();
}


async function toggleEnabled(id) {

    const response =
        await fetch(
            `/api/miners/${id}/enabled`,
            {
                method: "POST"
            }
        );

    if (!response.ok) {

        alert("Failed");
    }

    await loadStatus();
}


async function toggleSchedule(id) {

    const response =
        await fetch(
            `/api/miners/${id}/schedule`,
            {
                method: "POST"
            }
        );

    if (!response.ok) {

        const data =
            await response.json();

        alert(
            data.detail
            || "Failed"
        );
    }

    await loadStatus();
}


const savedDiscoveryNetwork =
    localStorage.getItem(
        "asicDiscoveryNetwork"
    );

if (savedDiscoveryNetwork) {

    document.getElementById(
        "discoveryNetwork"
    ).value =
        savedDiscoveryNetwork;
}


loadStatus();
loadLogs();

setInterval(
    () => {
        loadStatus();
        loadLogs();
    },
    5000
);


loadIssues();

setInterval(
    loadIssues,
    15000
);




// ============================================================
// NOTIFICATION CHANNEL HEALTH
// ============================================================

function setNotificationBadge(
    id,
    ok,
    textOk,
    textFail
) {

    const element =
        document.getElementById(
            id
        );


    if (!element) {
        return;
    }


    element.textContent =
        ok
        ? textOk
        : textFail;


    element.style.background =
        ok
        ? "#246b47"
        : "#7a3038";


    element.style.color =
        "white";
}


async function loadNotificationHealth() {

    const detail =
        document.getElementById(
            "notificationHealthDetail"
        );


    try {

        const response =
            await fetch(
                "/api/notifications/health",
                {
                    cache:
                        "no-store",
                }
            );


        if (!response.ok) {

            throw new Error(
                "HTTP "
                +
                response.status
            );
        }


        const data =
            await response.json();


        const telegramOn = Boolean(
            data.configured
            &&
            data.enabled
        );


        const tunnelOk = Boolean(
            data.transport_ok
        );


        setNotificationBadge(
            "telegramStatusBadge",
            telegramOn,
            "Telegram ON",
            "Telegram OFF"
        );


        setNotificationBadge(
            "telegramTunnelBadge",
            tunnelOk,
            "Tunnel OK",
            "Tunnel DOWN"
        );


        if (detail) {

            const transport =
                data.transport
                || {};


            const parts = [];


            if (
                transport.http_status
                !== null
                &&
                transport.http_status
                !== undefined
            ) {

                parts.push(
                    "Telegram API HTTP "
                    +
                    transport.http_status
                );
            }


            if (
                transport.latency_ms
                !== null
                &&
                transport.latency_ms
                !== undefined
            ) {

                parts.push(
                    transport.latency_ms
                    +
                    " ms"
                );
            }


            if (
                !tunnelOk
                &&
                transport.error
            ) {

                parts.push(
                    transport.error
                );
            }


            detail.textContent =
                parts.length
                ? parts.join(" · ")
                : "Notification channel unavailable";
        }

    }

    catch (error) {

        setNotificationBadge(
            "telegramStatusBadge",
            false,
            "Telegram ON",
            "Telegram UNKNOWN"
        );


        setNotificationBadge(
            "telegramTunnelBadge",
            false,
            "Tunnel OK",
            "Tunnel DOWN"
        );


        if (detail) {

            detail.textContent =
                "Notification health API unavailable";
        }
    }
}


window.addEventListener(
    "load",
    function () {

        loadNotificationHealth();


        setInterval(
            loadNotificationHealth,
            30000
        );
    }
);




// ============================================================
// FARM SUMMARY UI
// ============================================================

function setFarmSummaryBadge(
    enabled
) {

    const badge =
        document.getElementById(
            "farmSummaryStatusBadge"
        );


    if (!badge) {
        return;
    }


    badge.textContent =
        enabled
        ? "Summary ENABLED"
        : "Summary DISABLED";


    badge.style.background =
        enabled
        ? "#246b47"
        : "#6a707c";


    badge.style.color =
        "white";
}


async function loadFarmSummaryStatus() {

    const detail =
        document.getElementById(
            "farmSummaryDetail"
        );


    try {

        const response =
            await fetch(
                "/api/notifications/summary/status",
                {
                    cache:
                        "no-store",
                }
            );


        if (!response.ok) {

            throw new Error(
                "HTTP "
                +
                response.status
            );
        }


        const data =
            await response.json();


        setFarmSummaryBadge(
            Boolean(
                data.enabled
            )
        );


        if (detail) {

            const weekdays =
                Array.isArray(
                    data.weekdays
                )
                ? data.weekdays.join(", ")
                : "—";


            const lastSent =
                data.last_sent_date
                || "—";


            detail.textContent =
                weekdays
                +
                " · "
                +
                (
                    data.time
                    || "—"
                )
                +
                " · Last sent: "
                +
                lastSent;
        }

    }

    catch (error) {

        setFarmSummaryBadge(
            false
        );


        if (detail) {

            detail.textContent =
                "Summary status unavailable";
        }
    }
}


async function sendFarmSummary() {

    const button =
        document.getElementById(
            "sendFarmSummaryButton"
        );

    const result =
        document.getElementById(
            "farmSummaryResult"
        );


    if (button) {

        button.disabled = true;
        button.textContent =
            "SENDING...";
    }


    if (result) {

        result.textContent =
            "Sending farm summary...";
        result.style.color =
            "#9299a8";
    }


    try {

        const response =
            await fetch(
                "/api/notifications/summary/test",
                {
                    method:
                        "POST",

                    cache:
                        "no-store",
                }
            );


        let data = null;


        try {

            data =
                await response.json();

        }

        catch (_) {

            data = {};
        }


        if (!response.ok) {

            const detail =
                data.detail
                || data.message
                || (
                    "HTTP "
                    +
                    response.status
                );


            throw new Error(
                detail
            );
        }


        if (result) {

            result.textContent =
                "✓ Summary sent to Telegram"
                +
                (
                    data.message
                    ? " · " + data.message
                    : ""
                );

            result.style.color =
                "#66c58c";
        }


        await loadFarmSummaryStatus();

    }

    catch (error) {

        if (result) {

            result.textContent =
                "✕ Send failed: "
                +
                error.message;

            result.style.color =
                "#e26a70";
        }
    }

    finally {

        if (button) {

            button.disabled = false;
            button.textContent =
                "SEND SUMMARY";
        }
    }
}


window.addEventListener(
    "load",
    function () {

        loadFarmSummaryStatus();


        setInterval(
            loadFarmSummaryStatus,
            60000
        );
    }
);



// ============================================================
// v0.1.0 - AUTHENTICATED USER / RELOGIN
// ============================================================

window.__asicCurrentUser = null;


async function loadAuthenticatedUser() {

    const userElement =
        document.getElementById(
            "authUserName"
        );

    const logoutButton =
        document.getElementById(
            "authLogoutButton"
        );


    if (!userElement) {
        return;
    }


    try {

        const response =
            await fetch(
                "/api/audit/whoami",
                {
                    cache: "no-store",
                }
            );


        if (!response.ok) {

            throw new Error(
                "HTTP "
                +
                response.status
            );
        }


        const data =
            await response.json();


        const actor =
            String(
                data.actor || "UNKNOWN"
            );


        if (
            actor.startsWith(
                "WEB:"
            )
        ) {

            const username =
                actor.substring(
                    4
                );


            window.__asicCurrentUser =
                username;


            userElement.textContent =
                username;


            userElement.classList.remove(
                "auth-local"
            );


            if (logoutButton) {

                logoutButton.disabled =
                    false;

                logoutButton.title =
                    (
                        "Log out and authenticate "
                        +
                        "as another user"
                    );
            }

        }

        else {

            window.__asicCurrentUser =
                null;


            userElement.textContent =
                actor;


            userElement.classList.add(
                "auth-local"
            );


            if (logoutButton) {

                logoutButton.disabled =
                    true;

                logoutButton.title =
                    (
                        "Basic Auth is not used "
                        +
                        "for LOCAL access"
                    );
            }
        }

    }

    catch (error) {

        window.__asicCurrentUser =
            null;


        userElement.textContent =
            "UNKNOWN";


        if (logoutButton) {

            logoutButton.disabled =
                true;
        }
    }
}


function switchAuthenticatedUser() {

    const username =
        window.__asicCurrentUser;


    if (!username) {

        alert(
            "User switching is available "
            +
            "only through the HTTPS interface."
        );

        return;
    }


    /*
      Navigate instead of fetch().
      A top-level 401 response is required
      so the browser can show its native
      Basic Authentication dialog.
    */

    const url =
        (
            "/api/auth/relogin"
            +
            "?from_user="
            +
            encodeURIComponent(
                username
            )
            +
            "&_="
            +
            Date.now()
        );


    window.location.href =
        url;
}


if (
    document.readyState
    === "loading"
) {

    document.addEventListener(
        "DOMContentLoaded",
        loadAuthenticatedUser
    );

}

else {

    loadAuthenticatedUser();
}



// ============================================================
// SCHEDULE RULES UI v1.5
// ============================================================

window.__scheduleRulesData = null;
window.__scheduleRuleEditingId = null;


function scheduleHtml(value) {

    const map = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
    };


    return String(
        value ?? ""
    ).replace(
        /[&<>"']/g,
        char => map[char]
    );
}


async function scheduleApi(
    url,
    options = {}
) {

    const response = await fetch(
        url,
        {
            cache: "no-store",
            ...options,
        }
    );


    let data = null;


    try {

        data = await response.json();

    }
    catch (_) {

        data = null;
    }


    if (!response.ok) {

        let message =
            "Schedule request failed";


        if (
            data
            &&
            data.detail
        ) {

            message =
                String(
                    data.detail
                );
        }


        throw new Error(
            message
        );
    }


    return data;
}


function scheduleRuleFind(
    ruleId
) {

    const rules =
        window.__scheduleRulesData
        ?.rules
        ||
        [];


    return rules.find(
        rule =>
            Number(rule.id)
            ===
            Number(ruleId)
    );
}


function renderScheduleRules(
    data
) {

    window.__scheduleRulesData =
        data;


    const body =
        document.getElementById(
            "scheduleRuleRows"
        );


    const status =
        document.getElementById(
            "scheduleRulesStatus"
        );


    const desired =
        document.getElementById(
            "desiredState"
        );


    const next =
        document.getElementById(
            "nextTransition"
        );


    const schedulerState =
        document.getElementById(
            "scheduleRulesSchedulerState"
        );


    const activeRule =
        document.getElementById(
            "scheduleRulesActiveRule"
        );


    if (desired) {

        desired.textContent =
            data.desired_state
            ||
            "—";
    }


    if (next) {

        next.textContent =
            data.next_transition_label
            ||
            "—";
    }


    if (schedulerState) {

        schedulerState.textContent =
            data.scheduler_enabled
            ? "ON"
            : "OFF";
    }


    if (activeRule) {

        activeRule.textContent =
            data.active_rule_id
            ? (
                "#"
                +
                data.active_rule_id
            )
            :
            "—";
    }


    const rules =
        data.rules
        ||
        [];


    if (status) {

        status.textContent =
            (
                rules.length
                +
                (
                    rules.length === 1
                    ? " rule"
                    : " rules"
                )
                +
                " configured"
            );
    }


    if (!body) {
        return;
    }


    if (!rules.length) {

        body.innerHTML = `
            <tr>
                <td
                    colspan="7"
                    class="muted"
                >
                    No schedule rules configured.
                    Use ADD RULE to create one.
                </td>
            </tr>
        `;

        return;
    }


    body.innerHTML =
        rules.map(
            rule => {

                const enabledClass =
                    rule.enabled
                    ? "on"
                    : "off";


                const actionClass =
                    String(
                        rule.action
                    ).toLowerCase();


                return `
                    <tr>

                        <td>
                            <span
                                class="
                                    schedule-rule-status
                                    ${enabledClass}
                                "
                            >
                                ${
                                    rule.enabled
                                    ? "ON"
                                    : "OFF"
                                }
                            </span>
                        </td>


                        <td>
                            <span
                                class="
                                    schedule-rule-action
                                    ${actionClass}
                                "
                            >
                                ${scheduleHtml(rule.action)}
                            </span>
                        </td>


                        <td>
                            ${scheduleHtml(rule.days)}
                        </td>


                        <td>
                            <strong>
                                ${scheduleHtml(rule.time)}
                            </strong>
                        </td>


                        <td>
                            ${
                                rule.enabled
                                ? (
                                    scheduleHtml(
                                        rule.next_run_label
                                        ||
                                        "—"
                                    )
                                )
                                :
                                "—"
                            }
                        </td>


                        <td>
                            ${
                                scheduleHtml(
                                    rule.comment
                                    ||
                                    "—"
                                )
                            }
                        </td>


                        <td>

                            <div
                                class="schedule-rule-actions"
                            >

                                <button
                                    class="secondary mini"
                                    onclick="
                                        openScheduleRuleEditor(
                                            ${Number(rule.id)}
                                        )
                                    "
                                >
                                    EDIT
                                </button>


                                <button
                                    class="secondary mini"
                                    onclick="
                                        toggleScheduleRule(
                                            ${Number(rule.id)}
                                        )
                                    "
                                >
                                    ${
                                        rule.enabled
                                        ? "DISABLE"
                                        : "ENABLE"
                                    }
                                </button>


                                <button
                                    class="warning-button mini"
                                    onclick="
                                        deleteScheduleRule(
                                            ${Number(rule.id)}
                                        )
                                    "
                                >
                                    DELETE
                                </button>

                            </div>

                        </td>

                    </tr>
                `;
            }
        ).join("");
}


async function loadScheduleRules() {

    const status =
        document.getElementById(
            "scheduleRulesStatus"
        );


    try {

        const data =
            await scheduleApi(
                "/api/schedule/rules"
            );


        renderScheduleRules(
            data
        );

    }
    catch (error) {

        if (status) {

            status.textContent =
                "Schedule error: "
                +
                error.message;
        }
    }
}


function scheduleSetDays(
    daysMask
) {

    const mask =
        Number(
            daysMask
        );


    for (
        const checkbox
        of document.querySelectorAll(
            ".schedule-day-checkbox"
        )
    ) {

        const bit =
            Number(
                checkbox.value
            );


        checkbox.checked =
            Boolean(
                mask
                &
                bit
            );
    }
}


function scheduleGetDays() {

    let mask = 0;


    for (
        const checkbox
        of document.querySelectorAll(
            ".schedule-day-checkbox"
        )
    ) {

        if (
            checkbox.checked
        ) {

            mask |= Number(
                checkbox.value
            );
        }
    }


    return mask;
}


function scheduleRuleShowError(
    message
) {

    const element =
        document.getElementById(
            "scheduleRuleError"
        );


    if (!element) {
        return;
    }


    if (!message) {

        element.textContent = "";
        element.classList.remove(
            "show"
        );

        return;
    }


    element.textContent =
        message;

    element.classList.add(
        "show"
    );
}


function openScheduleRuleEditor(
    ruleId = null
) {

    window.__scheduleRuleEditingId =
        (
            ruleId === null
            ? null
            : Number(
                ruleId
            )
        );


    const rule =
        (
            ruleId === null
            ? null
            : scheduleRuleFind(
                ruleId
            )
        );


    const title =
        document.getElementById(
            "scheduleRuleModalTitle"
        );


    const action =
        document.getElementById(
            "scheduleRuleAction"
        );


    const time =
        document.getElementById(
            "scheduleRuleTime"
        );


    const comment =
        document.getElementById(
            "scheduleRuleComment"
        );


    const enabled =
        document.getElementById(
            "scheduleRuleEnabled"
        );


    if (rule) {

        title.textContent =
            "Edit Schedule Rule #"
            +
            rule.id;

        action.value =
            rule.action;

        time.value =
            rule.time;

        comment.value =
            rule.comment
            ||
            "";

        enabled.checked =
            Boolean(
                rule.enabled
            );

        scheduleSetDays(
            rule.days_mask
        );

    }
    else {

        title.textContent =
            "Add Schedule Rule";

        action.value =
            "RESUME";

        time.value =
            "13:00";

        comment.value =
            "";

        enabled.checked =
            true;

        // Mon-Fri
        scheduleSetDays(
            31
        );
    }


    scheduleRuleShowError(
        ""
    );


    document.getElementById(
        "scheduleRuleBackdrop"
    ).classList.add(
        "open"
    );
}


function closeScheduleRuleEditor() {

    document.getElementById(
        "scheduleRuleBackdrop"
    ).classList.remove(
        "open"
    );


    window.__scheduleRuleEditingId =
        null;


    scheduleRuleShowError(
        ""
    );
}


function scheduleRuleBackdropClick(
    event
) {

    if (
        event.target.id
        ===
        "scheduleRuleBackdrop"
    ) {

        closeScheduleRuleEditor();
    }
}


async function saveScheduleRule() {

    const button =
        document.getElementById(
            "scheduleRuleSaveButton"
        );


    const ruleId =
        window.__scheduleRuleEditingId;


    const daysMask =
        scheduleGetDays();


    if (!daysMask) {

        scheduleRuleShowError(
            "Select at least one day."
        );

        return;
    }


    const time =
        document.getElementById(
            "scheduleRuleTime"
        ).value;


    if (!time) {

        scheduleRuleShowError(
            "Select a time."
        );

        return;
    }


    const payload = {

        action:
            document.getElementById(
                "scheduleRuleAction"
            ).value,

        time:
            time,

        days_mask:
            daysMask,

        comment:
            document.getElementById(
                "scheduleRuleComment"
            ).value,

        enabled:
            document.getElementById(
                "scheduleRuleEnabled"
            ).checked,
    };


    const url =
        (
            ruleId === null
            ? "/api/schedule/rules"
            : (
                "/api/schedule/rules/"
                +
                ruleId
            )
        );


    const method =
        (
            ruleId === null
            ? "POST"
            : "PUT"
        );


    try {

        button.disabled =
            true;

        scheduleRuleShowError(
            ""
        );


        await scheduleApi(
            url,
            {
                method:
                    method,

                headers: {
                    "Content-Type":
                        "application/json",
                },

                body:
                    JSON.stringify(
                        payload
                    ),
            }
        );


        closeScheduleRuleEditor();

        await loadScheduleRules();


        if (
            typeof loadLogs
            ===
            "function"
        ) {

            loadLogs();
        }

    }
    catch (error) {

        scheduleRuleShowError(
            error.message
        );
    }
    finally {

        button.disabled =
            false;
    }
}


async function toggleScheduleRule(
    ruleId
) {

    const rule =
        scheduleRuleFind(
            ruleId
        );


    if (!rule) {
        return;
    }


    const verb =
        rule.enabled
        ? "Disable"
        : "Enable";


    if (
        !window.confirm(
            verb
            +
            " Rule #"
            +
            rule.id
            +
            "?\n\n"
            +
            rule.action
            +
            " "
            +
            rule.time
            +
            " "
            +
            rule.days
        )
    ) {

        return;
    }


    try {

        await scheduleApi(
            "/api/schedule/rules/"
            +
            rule.id
            +
            "/toggle",
            {
                method:
                    "POST",
            }
        );


        await loadScheduleRules();


        if (
            typeof loadLogs
            ===
            "function"
        ) {

            loadLogs();
        }

    }
    catch (error) {

        window.alert(
            error.message
        );
    }
}


async function deleteScheduleRule(
    ruleId
) {

    const rule =
        scheduleRuleFind(
            ruleId
        );


    if (!rule) {
        return;
    }


    if (
        !window.confirm(
            "DELETE SCHEDULE RULE\n\n"
            +
            "Rule #"
            +
            rule.id
            +
            "\n"
            +
            rule.action
            +
            " "
            +
            rule.time
            +
            " "
            +
            rule.days
            +
            "\n\n"
            +
            "This cannot be undone."
        )
    ) {

        return;
    }


    try {

        await scheduleApi(
            "/api/schedule/rules/"
            +
            rule.id,
            {
                method:
                    "DELETE",
            }
        );


        await loadScheduleRules();


        if (
            typeof loadLogs
            ===
            "function"
        ) {

            loadLogs();
        }

    }
    catch (error) {

        window.alert(
            error.message
        );
    }
}


document.addEventListener(
    "keydown",
    function(event) {

        if (
            event.key
            ===
            "Escape"
            &&
            document.getElementById(
                "scheduleRuleBackdrop"
            )
            ?.classList.contains(
                "open"
            )
        ) {

            closeScheduleRuleEditor();
        }
    }
);


// Keep rule "Next run" information fresh
// without tying it to the miner polling loop.

setTimeout(
    loadScheduleRules,
    500
);


setInterval(
    loadScheduleRules,
    15000
);




// ============================================================
// FIRMWARE AUTO / MANUAL UI v0.1.0
// ============================================================

window.__firmwareEditingMinerId =
    null;


function firmwareCurrentMiner() {

    const id =
        Number(
            window.__firmwareEditingMinerId
        );


    const miners =
        window.__asicLastStatus
        ?.miners
        ||
        [];


    return miners.find(
        miner =>
            Number(miner.id)
            ===
            id
    );
}


function firmwareEditorError(
    message
) {

    const element =
        document.getElementById(
            "firmwareEditorError"
        );


    if (!message) {

        element.textContent = "";

        element.classList.remove(
            "show"
        );

        return;
    }


    element.textContent =
        message;

    element.classList.add(
        "show"
    );
}


function firmwareModeChanged() {

    const mode =
        document.getElementById(
            "firmwareDetectionMode"
        ).value;


    const manual =
        mode === "MANUAL";


    document.getElementById(
        "firmwareDriver"
    ).disabled =
        !manual;


    document.getElementById(
        "firmwareModel"
    ).disabled =
        !manual;


    document.getElementById(
        "firmwareVersion"
    ).disabled =
        !manual;
}


function openFirmwareEditor(
    minerId
) {

    const miners =
        window.__asicLastStatus
        ?.miners
        ||
        [];


    const miner =
        miners.find(
            item =>
                Number(item.id)
                ===
                Number(minerId)
        );


    if (!miner) {

        alert(
            "ASIC data is not available."
        );

        return;
    }


    window.__firmwareEditingMinerId =
        Number(
            minerId
        );


    document.getElementById(
        "firmwareEditorTitle"
    ).textContent =
        (
            "Firmware · "
            +
            miner.name
            +
            " · "
            +
            miner.ip
        );


    document.getElementById(
        "firmwareDetectionMode"
    ).value =
        (
            miner.detection_mode
            ||
            "AUTO"
        );


    document.getElementById(
        "firmwareDriver"
    ).value =
        (
            miner.driver
            ||
            "unset"
        );


    document.getElementById(
        "firmwareModel"
    ).value =
        (
            miner.model
            ||
            ""
        );


    document.getElementById(
        "firmwareVersion"
    ).value =
        (
            miner.firmware
            ||
            ""
        );


    firmwareEditorError(
        ""
    );


    firmwareModeChanged();


    document.getElementById(
        "firmwareEditorBackdrop"
    ).classList.add(
        "open"
    );
}


function closeFirmwareEditor() {

    document.getElementById(
        "firmwareEditorBackdrop"
    ).classList.remove(
        "open"
    );


    window.__firmwareEditingMinerId =
        null;


    firmwareEditorError(
        ""
    );
}


function firmwareEditorBackdropClick(
    event
) {

    if (
        event.target.id
        ===
        "firmwareEditorBackdrop"
    ) {

        closeFirmwareEditor();
    }
}


async function firmwareJsonRequest(
    url,
    options = {}
) {

    const response =
        await fetch(
            url,
            options
        );


    let data = null;


    try {

        data =
            await response.json();

    }
    catch (_) {

        data = null;
    }


    if (!response.ok) {

        throw new Error(
            data?.detail
            ||
            "Firmware operation failed"
        );
    }


    return data;
}


async function saveFirmwareSettings() {

    const id =
        window.__firmwareEditingMinerId;


    if (!id) {
        return;
    }


    const mode =
        document.getElementById(
            "firmwareDetectionMode"
        ).value;


    const payload = {
        detection_mode:
            mode,

        driver:
            document.getElementById(
                "firmwareDriver"
            ).value,

        model:
            document.getElementById(
                "firmwareModel"
            ).value,

        firmware:
            document.getElementById(
                "firmwareVersion"
            ).value,
    };


    if (
        mode === "MANUAL"
        &&
        !confirm(
            "Enable MANUAL firmware mode?\n\n"
            +
            "Automatic detection will no longer "
            +
            "change this ASIC until AUTO is restored."
        )
    ) {

        return;
    }


    const button =
        document.getElementById(
            "firmwareSaveButton"
        );


    try {

        button.disabled =
            true;

        firmwareEditorError(
            ""
        );


        await firmwareJsonRequest(
            `/api/miners/${id}/firmware-settings`,
            {
                method:
                    "POST",

                headers: {
                    "Content-Type":
                        "application/json"
                },

                body:
                    JSON.stringify(
                        payload
                    ),
            }
        );


        closeFirmwareEditor();

        await loadStatus();
        await loadLogs();

    }
    catch (error) {

        firmwareEditorError(
            error.message
        );
    }
    finally {

        button.disabled =
            false;
    }
}


async function runFirmwareAutoDetect() {

    const id =
        window.__firmwareEditingMinerId;


    if (!id) {
        return;
    }


    if (
        !confirm(
            "Run firmware auto detection now?\n\n"
            +
            "Successful detection will set "
            +
            "this ASIC back to AUTO mode."
        )
    ) {

        return;
    }


    const button =
        document.getElementById(
            "firmwareDetectButton"
        );


    try {

        button.disabled =
            true;

        firmwareEditorError(
            "Detecting..."
        );


        const data =
            await firmwareJsonRequest(
                `/api/miners/${id}/firmware-detect`,
                {
                    method:
                        "POST",
                }
            );


        document.getElementById(
            "firmwareDetectionMode"
        ).value =
            "AUTO";


        document.getElementById(
            "firmwareDriver"
        ).value =
            data.driver;


        document.getElementById(
            "firmwareModel"
        ).value =
            data.model
            ||
            "";


        document.getElementById(
            "firmwareVersion"
        ).value =
            data.firmware
            ||
            "";


        firmwareEditorError(
            ""
        );


        firmwareModeChanged();

        await loadStatus();
        await loadLogs();

    }
    catch (error) {

        firmwareEditorError(
            error.message
        );
    }
    finally {

        button.disabled =
            false;
    }
}


</script>


<div
    id="firmwareEditorBackdrop"
    class="modal-backdrop"
    onclick="firmwareEditorBackdropClick(event)"
>

<div
    class="history-modal firmware-editor-modal"
    onclick="event.stopPropagation()"
>

    <div class="history-header">

        <div>

            <h2
                id="firmwareEditorTitle"
                style="margin:0"
            >
                Firmware
            </h2>

            <div class="muted">
                Automatic detection or manual override
            </div>

        </div>


        <button
            class="close-button"
            onclick="closeFirmwareEditor()"
        >
            CLOSE
        </button>

    </div>


    <div class="firmware-editor-grid">

        <div class="firmware-editor-field">

            <label>
                Detection mode
            </label>

            <select
                id="firmwareDetectionMode"
                onchange="firmwareModeChanged()"
            >
                <option value="AUTO">
                    AUTO
                </option>

                <option value="MANUAL">
                    MANUAL
                </option>
            </select>

        </div>


        <div class="firmware-editor-field">

            <label>
                Driver
            </label>

            <select id="firmwareDriver">

                <option value="awesome">
                    Awesome / AnthillOS
                </option>

                <option value="bitmain_stock">
                    Bitmain Stock
                </option>

                <option value="unset">
                    NOT SET
                </option>

            </select>

        </div>


        <div class="firmware-editor-field full">

            <label>
                Model
            </label>

            <input
                id="firmwareModel"
                type="text"
                maxlength="120"
            >

        </div>


        <div class="firmware-editor-field full">

            <label>
                Firmware
            </label>

            <input
                id="firmwareVersion"
                type="text"
                maxlength="160"
            >

        </div>

    </div>


    <div
        id="firmwareEditorError"
        class="firmware-editor-error"
    >
    </div>


    <div class="firmware-editor-buttons">

        <button
            id="firmwareDetectButton"
            class="secondary"
            onclick="runFirmwareAutoDetect()"
        >
            RUN AUTO DETECT
        </button>


        <div class="firmware-editor-right">

            <button
                class="secondary"
                onclick="closeFirmwareEditor()"
            >
                CANCEL
            </button>

            <button
                id="firmwareSaveButton"
                class="start"
                onclick="saveFirmwareSettings()"
            >
                SAVE
            </button>

        </div>

    </div>

</div>

</div>


</body>

</html>
"""


@app.get(
    "/",
    response_class=HTMLResponse,
)
def index():
    return HTML


if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8088,
    )
