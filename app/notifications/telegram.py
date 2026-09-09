"""Telegram notification and farm-summary subsystem."""

import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import config as app_config
from db import (
    db,
    get_setting,
    set_setting,
    get_miner,
)

TIMEZONE_NAME = app_config.TIMEZONE
MOSCOW = ZoneInfo(TIMEZONE_NAME)

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

TELEGRAM_SUMMARY_WEEKDAYS = {
    0, 1, 2, 3, 4
}


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


def telegram_summary_last_date():

    return get_setting(
        "telegram_summary_last_date"
    )


def telegram_summary_set_last_date(
    value,
):

    set_setting(
        "telegram_summary_last_date",
        value,
    )


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
    runtime,
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

            runtime.log_event(
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


        runtime.log_event(
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


        runtime.log_event(
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


def telegram_summary_loop(runtime):

    while not runtime.stop_event.wait(
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
                    runtime,
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

class TelegramRuntime:
    """Application-owned dependencies for scheduled farm summaries."""

    def __init__(self, log_event, stop_event):
        self.log_event = log_event
        self.stop_event = stop_event

    def send_farm_summary(
        self,
        force=False,
        source="SUMMARY",
    ):
        return telegram_send_farm_summary(
            self,
            force=force,
            source=source,
        )

    def summary_loop(self):
        return telegram_summary_loop(self)


__all__ = (
    'TELEGRAM_BOT_TOKEN',
    'TELEGRAM_CHAT_ID',
    'TELEGRAM_NOTIFICATIONS_ENABLED',
    'ASIC_MANAGER_NAME',
    'TELEGRAM_PROXY',
    'TELEGRAM_EVENT_ACTIONS',
    'TELEGRAM_SUMMARY_ENABLED',
    'TELEGRAM_SUMMARY_HOUR',
    'TELEGRAM_SUMMARY_MINUTE',
    'TELEGRAM_SUMMARY_WINDOW_MINUTES',
    'TelegramRuntime',
    'telegram_configured',
    'telegram_send_message',
    'telegram_event_async',
    'telegram_summary_last_date',
    'telegram_transport_health',
    'TELEGRAM_TIMEOUT',
    'TELEGRAM_SUMMARY_INTERVAL',
    'TELEGRAM_SUMMARY_WEEKDAYS',
    'telegram_row_value',
    'telegram_current_miner',
    'telegram_driver_label',
    'telegram_format_hashrate',
    'telegram_format_temperature',
    'telegram_format_power',
    'telegram_format_duration',
    'telegram_problem_from_message',
    'telegram_issue_context',
    'telegram_parse_control_message',
    'telegram_format_event',
    'telegram_event_worker',
    'telegram_summary_set_last_date',
    'telegram_farm_summary_data',
    'telegram_summary_hashrate',
    'telegram_farm_summary',
    'telegram_summary_due',
)
