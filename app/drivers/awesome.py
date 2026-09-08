"""
Awesome Miner / AnthillOS firmware driver.

Contains communication, token handling and control primitives
specific to Awesome Miner / AnthillOS firmware.
"""

import threading

import requests

from config import AWESOME_PASSWORD


token_cache = {}
token_lock = threading.Lock()


__all__ = (
    "awesome_unlock",
    "awesome_token",
    "awesome_status",
    "awesome_control",
    "send_awesome_reboot",
)


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
