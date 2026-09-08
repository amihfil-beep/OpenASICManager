"""
Bitmain stock firmware driver.

Contains communication and control primitives specific to
Bitmain stock firmware.
"""

import json
import socket

import requests
from requests.auth import HTTPDigestAuth

from config import (
    BITMAIN_USERNAME,
    BITMAIN_PASSWORD,
)


__all__ = (
    "cgminer_query",
    "stock_status",
    "stock_control",
    "send_stock_reboot",
)


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
