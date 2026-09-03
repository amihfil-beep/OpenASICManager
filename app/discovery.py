#!/usr/bin/env python3

import argparse
import ipaddress
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.auth import HTTPDigestAuth

from config import (
    BITMAIN_USERNAME,
    BITMAIN_PASSWORD,
    DISCOVERY_MAX_HOSTS,
)


CGMINER_TIMEOUT = 1.0
HTTP_TIMEOUT = 1.5

WORKERS = 48
MAX_HOSTS = DISCOVERY_MAX_HOSTS

STOCK_USER = BITMAIN_USERNAME
STOCK_PASSWORD = BITMAIN_PASSWORD


RFC1918_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]


def validate_network(value):

    try:

        network = ipaddress.ip_network(
            value,
            strict=False,
        )

    except ValueError as exc:

        raise ValueError(
            f"Invalid CIDR network: {exc}"
        )


    if network.version != 4:

        raise ValueError(
            "Only IPv4 networks are supported"
        )


    if not any(
        network.subnet_of(private)
        for private in RFC1918_NETWORKS
    ):

        raise ValueError(
            "Discovery is allowed only "
            "inside RFC1918 private networks"
        )


    host_count = len(
        list(network.hosts())
    )


    if host_count > MAX_HOSTS:

        raise ValueError(
            f"Network is too large: "
            f"{host_count} hosts. "
            f"Maximum: {MAX_HOSTS}"
        )


    return network


def cgminer_query(
    ip,
    command,
    timeout=CGMINER_TIMEOUT,
):

    payload = json.dumps(
        {
            "command": command
        },
        separators=(",", ":"),
    ).encode("utf-8")


    data = bytearray()


    try:

        with socket.create_connection(
            (
                str(ip),
                4028,
            ),
            timeout=timeout,
        ) as sock:

            sock.settimeout(
                timeout
            )

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


    except Exception:

        return None


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

        return None


    try:

        result = json.loads(
            raw
        )

    except Exception:

        return None


    if not isinstance(
        result,
        dict,
    ):

        return None


    return result


def valid_cgminer(ip):

    result = cgminer_query(
        ip,
        "version",
    )


    if not result:

        return None


    if (
        "STATUS" not in result
        and
        "VERSION" not in result
    ):

        return None


    return result


def detect_awesome(ip):

    try:

        response = requests.get(
            f"http://{ip}/api/v1/summary",
            timeout=HTTP_TIMEOUT,
        )

    except Exception:

        return None


    if response.status_code != 200:

        return None


    try:

        data = response.json()

    except Exception:

        return None


    if not isinstance(
        data,
        dict,
    ):

        return None


    miner = data.get(
        "miner",
        data,
    )


    if not isinstance(
        miner,
        dict,
    ):

        return None


    miner_status = miner.get(
        "miner_status"
    )

    miner_type = str(
        miner.get(
            "miner_type",
            "",
        )
        or ""
    ).strip()


    if (
        not isinstance(
            miner_status,
            dict,
        )
        and
        not miner_type
    ):

        return None


    # Examples:
    #
    # Antminer T21 (Awesome 1.2.7)
    # Antminer T21 (Awesome 1.3.3)
    #
    # Keep model and firmware separately.

    model = (
        miner_type
        or
        "Antminer"
    )

    firmware = None


    marker = " (Awesome "

    if (
        marker in miner_type
        and
        miner_type.endswith(")")
    ):

        model_part, version_part = (
            miner_type.rsplit(
                marker,
                1,
            )
        )

        version_part = (
            version_part[:-1]
            .strip()
        )

        model = (
            model_part.strip()
            or
            "Antminer"
        )

        if version_part:

            firmware = (
                "Awesome "
                +
                version_part
            )


    # Fallback only if Awesome version
    # wasn't present in miner_type.

    if not firmware:

        stats = cgminer_query(
            ip,
            "stats",
        )


        if stats:

            items = stats.get(
                "STATS",
                [],
            )


            if items:

                first = items[0]

                cgminer_version = (
                    first.get(
                        "Cgminer"
                    )
                )


                if cgminer_version:

                    firmware = (
                        "CGMiner "
                        +
                        str(
                            cgminer_version
                        )
                    )


    return {
        "ip":
            str(ip),

        "driver":
            "awesome",

        "type":
            "Awesome / AnthillOS",

        "model":
            str(model),

        "firmware":
            firmware,

        "signature":
            "CGMiner 4028 + Awesome API",
    }




def detect_stock(ip):

    try:

        response = requests.get(
            (
                f"http://{ip}"
                "/cgi-bin/"
                "get_system_info.cgi"
            ),

            auth=HTTPDigestAuth(
                STOCK_USER,
                STOCK_PASSWORD,
            ),

            timeout=HTTP_TIMEOUT,
        )

    except Exception:

        return None


    if response.status_code != 200:

        return None


    try:

        data = response.json()

    except Exception:

        return None


    if not isinstance(
        data,
        dict,
    ):

        return None


    minertype = str(
        data.get(
            "minertype",
            "",
        )
    ).strip()


    if not minertype.lower().startswith(
        "antminer"
    ):

        return None


    return {
        "ip": str(ip),
        "driver": "bitmain_stock",
        "type": "Bitmain Stock",
        "model": minertype,
        "firmware":
            data.get(
                "system_filesystem_version"
            ),
        "signature":
            (
                "CGMiner 4028 + "
                "Bitmain system API"
            ),
    }


def detect_host(ip):

    ip = str(ip)


    # --------------------------------------------------
    # СНАЧАЛА настоящий CGMiner.
    #
    # Если его нет, устройство нам вообще
    # не интересно и HTTP мы не трогаем.
    # --------------------------------------------------

    version = valid_cgminer(
        ip
    )


    if not version:

        return None


    # --------------------------------------------------
    # AWESOME / ANTHILLOS
    # --------------------------------------------------

    result = detect_awesome(
        ip
    )


    if result:

        return result


    # --------------------------------------------------
    # STOCK BITMAIN
    # --------------------------------------------------

    result = detect_stock(
        ip
    )


    if result:

        return result


    # --------------------------------------------------
    # Настоящий CGMiner есть,
    # но известный firmware API не определён.
    # --------------------------------------------------

    description = None


    try:

        status = version.get(
            "STATUS",
            [],
        )

        if status:

            description = status[0].get(
                "Description"
            )

    except Exception:

        pass


    return {
        "ip": ip,
        "driver": "unknown_asic",
        "type": "Unknown ASIC",
        "model":
            description
            or "CGMiner compatible device",
        "firmware": None,
        "signature":
            "CGMiner 4028",
    }


def ip_sort(item):

    return tuple(
        int(part)
        for part
        in item["ip"].split(".")
    )


def scan_network(network_value):

    network = validate_network(
        network_value
    )


    hosts = list(
        network.hosts()
    )


    started = time.monotonic()

    found = []


    worker_count = min(
        WORKERS,
        max(
            1,
            len(hosts),
        ),
    )


    with ThreadPoolExecutor(
        max_workers=worker_count
    ) as executor:

        futures = [
            executor.submit(
                detect_host,
                str(ip),
            )

            for ip in hosts
        ]


        for future in as_completed(
            futures
        ):

            try:

                result = (
                    future.result()
                )


                if result:

                    found.append(
                        result
                    )


            except Exception:

                pass


    found.sort(
        key=ip_sort
    )


    duration = (
        time.monotonic()
        - started
    )


    awesome = sum(
        1
        for item in found
        if item["driver"]
        == "awesome"
    )


    stock = sum(
        1
        for item in found
        if item["driver"]
        == "bitmain_stock"
    )


    unknown = sum(
        1
        for item in found
        if item["driver"]
        == "unknown_asic"
    )


    return {
        "network": str(network),
        "hosts_scanned": len(hosts),
        "duration_seconds":
            round(
                duration,
                2,
            ),
        "total": len(found),
        "awesome": awesome,
        "bitmain_stock": stock,
        "unknown": unknown,
        "devices": found,
    }


def print_human(result):

    print("=" * 115)
    print("ASIC DISCOVERY")
    print("=" * 115)

    print(
        "Network        :",
        result["network"],
    )

    print(
        "Hosts checked  :",
        result["hosts_scanned"],
    )

    print(
        "Duration       :",
        f"{result['duration_seconds']} sec",
    )

    print(
        "Mode           :",
        "READ ONLY",
    )

    print()


    print(
        f"{'IP':<17}"
        f"{'TYPE':<20}"
        f"{'MODEL':<45}"
        f"FIRMWARE"
    )

    print("-" * 115)


    for item in result[
        "devices"
    ]:

        print(
            f"{item['ip']:<17}"
            f"{item['type']:<20}"
            f"{str(item.get('model') or '-'):<45}"
            f"{item.get('firmware') or '-'}"
        )


    print()
    print("=" * 115)

    print(
        "ASIC TOTAL      :",
        result["total"],
    )

    print(
        "AWESOME         :",
        result["awesome"],
    )

    print(
        "BITMAIN STOCK   :",
        result["bitmain_stock"],
    )

    print(
        "UNKNOWN ASIC    :",
        result["unknown"],
    )

    print("=" * 115)


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Discover ASIC miners "
            "inside an IPv4 CIDR network"
        )
    )


    parser.add_argument(
        "network",
        help=(
            "Network in CIDR format, "
            "for example 192.168.1.0/24"
        ),
    )


    parser.add_argument(
        "--json",
        action="store_true",
        help="Return JSON",
    )


    args = parser.parse_args()


    try:

        result = scan_network(
            args.network
        )


    except ValueError as exc:

        parser.error(
            str(exc)
        )


    if args.json:

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )

    else:

        print_human(
            result
        )


if __name__ == "__main__":
    main()
