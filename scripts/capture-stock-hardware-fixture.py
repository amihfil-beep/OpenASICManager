#!/usr/bin/env python3
"""Read-only, sanitized ANTMINER stock-firmware fixture capture.

This utility never sends miner control or reboot requests, does not read
OpenASICManager's database, and does not save raw ASIC HTTP/CGMiner responses.
"""

import argparse
from datetime import datetime, timezone
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import socket

import requests
from requests.auth import HTTPDigestAuth


CGMINER_COMMANDS = ("version", "summary", "stats", "pools", "devs")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(item)
    for item in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
CAPTURE_DIR = Path.home() / "openasicmanager-hardware-fixtures"

SENSITIVE_KEY = re.compile(
    r"pass|pwd|token|secret|cookie|auth|session|wallet|worker|"
    r"username|userid|user|url|endpoint|hostname|host|ip|"
    r"mac|serial|address|stratum",
    re.IGNORECASE,
)
SAFE_MODEL = re.compile(r"^[A-Za-z0-9 _().+-]{1,80}$")
SAFE_VERSION = re.compile(r"^[A-Za-z0-9 _().:+/-]{1,80}$")
NUMERIC_TEXT = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)$")
SAFE_ID = re.compile(r"^(?:BTM_SOC[0-9]+|ASC[0-9]+)$", re.IGNORECASE)
SAFE_POOL_STATUS = {
    "Alive", "Disabled", "Dead", "Rejecting", "Unknown",
    "alive", "disabled", "dead", "rejecting", "unknown",
}
SAFE_STATUS = {"S", "W", "E", "I", "F", "success", "warning", "error"}


def validate_target(address):
    """Only query an explicitly entered RFC1918 IPv4 device."""
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError as exc:
        raise ValueError("Expected an IPv4 address") from exc
    if ip.version != 4 or not any(ip in network for network in PRIVATE_NETWORKS):
        raise ValueError("Only RFC1918 IPv4 addresses are permitted")
    return str(ip)


def sanitize_value(value, field=""):
    """Conservative allowlist: keep structural keys/types and useful telemetry."""
    name = str(field)
    lower = name.lower()

    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            key_text = str(key)
            if len(key_text) > 100 or re.search(r"[@/\\]", key_text):
                key_text = "REDACTED_FIELD"
            clean[key_text] = sanitize_value(item, key_text)
        return clean

    if isinstance(value, list):
        return [sanitize_value(item, name) for item in value[:500]]

    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        # Numeric serials / identity values must not be retained.
        return 0 if SENSITIVE_KEY.search(name) else value

    if not isinstance(value, str):
        return "REDACTED"

    if lower == "url":
        return "stratum+tcp://pool.example.invalid:3333"

    if lower in {"user", "username", "worker", "workername"}:
        return "fixture.worker"

    if "ip" == lower or lower.endswith("_ip") or lower == "ip address":
        return "192.0.2.1"

    if SENSITIVE_KEY.search(name):
        return "REDACTED"

    if lower in {"status", "stratum status"} and value in SAFE_POOL_STATUS:
        return value

    if lower == "status" and value in SAFE_STATUS:
        return value

    if name == "ID" and SAFE_ID.fullmatch(value):
        return value

    if lower in {"type", "minertype", "miner_type"}:
        return value if SAFE_MODEL.fullmatch(value) else "REDACTED"

    if lower in {
        "cgminer", "version", "system_filesystem_version",
        "miner_version", "firmware_version", "bmminer",
    } and SAFE_VERSION.fullmatch(value):
        # Do not allow an IP-like version to become a covert host leak.
        if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", value):
            return "REDACTED"
        return value

    if NUMERIC_TEXT.fullmatch(value) and len(value) <= 32:
        return value

    return "REDACTED"


def read_cgminer(host, command):
    if command not in CGMINER_COMMANDS:
        raise ValueError("CGMiner command not in read-only allowlist")

    payload = json.dumps(
        {"command": command}, separators=(",", ":")
    ).encode("ascii")
    data = bytearray()

    with socket.create_connection((host, 4028), timeout=3) as conn:
        conn.settimeout(3)
        conn.sendall(payload)
        while True:
            try:
                part = conn.recv(65536)
            except socket.timeout:
                break
            if not part:
                break
            data.extend(part)
            if len(data) > MAX_RESPONSE_BYTES:
                raise ValueError("CGMiner response exceeds size limit")
            if b"\x00" in part:
                break

    raw = bytes(data).split(b"\x00", 1)[0].decode(
        "utf-8", errors="replace"
    ).strip()
    if not raw:
        raise ValueError("Empty CGMiner response")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Unexpected CGMiner response type")
    return result


def _http_get(session, host, auth=None):
    # Deliberately do not allow arbitrary paths, redirects or proxy settings.
    return session.get(
        "http://" + host + "/cgi-bin/get_system_info.cgi",
        auth=auth,
        timeout=6,
        allow_redirects=False,
        stream=False,
    )


def capture_system_info(host, username, password):
    result = {
        "path": "/cgi-bin/get_system_info.cgi",
        "method": "GET",
        "unauthenticated_status": None,
        "authenticated_status": None,
        "authentication_scheme": "unknown",
        "sanitized_json": None,
    }
    session = requests.Session()
    session.trust_env = False
    try:
        try:
            response = _http_get(session, host)
            result["unauthenticated_status"] = response.status_code
            challenge = response.headers.get("WWW-Authenticate", "")
            scheme = challenge.split(None, 1)[0].strip().lower()
            if scheme in ("digest", "basic"):
                result["authentication_scheme"] = scheme
            if response.status_code == 200:
                try:
                    result["sanitized_json"] = sanitize_value(response.json())
                except (ValueError, TypeError):
                    result["parse_status"] = "non_json"
        except requests.RequestException:
            result["unauthenticated_error"] = "request_failed"

        if password:
            try:
                response = _http_get(
                    session, host, HTTPDigestAuth(username, password)
                )
                result["authenticated_status"] = response.status_code
                if response.status_code == 200:
                    try:
                        result["sanitized_json"] = sanitize_value(response.json())
                    except (ValueError, TypeError):
                        result["parse_status"] = "non_json"
            except requests.RequestException:
                result["authenticated_error"] = "request_failed"
        else:
            result["authenticated_status"] = "not_attempted"
    finally:
        session.close()
    return result


def collect_fixture(host, model, username, password):
    validated_host = validate_target(host)
    fixture = {
        "schema_version": 1,
        "expected_model": model,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "capture_mode": "read_only",
        "host_redacted": True,
        "cgminer": {},
        "http_system_info": None,
        "deferred_state_changing_probes": (
            "miner-mode writes, reboot method/status, PAUSE, RESUME, REBOOT"
        ),
    }
    for command in CGMINER_COMMANDS:
        try:
            fixture["cgminer"][command] = {
                "available": True,
                "data": sanitize_value(read_cgminer(validated_host, command)),
            }
        except (OSError, ValueError, TypeError, RuntimeError):
            fixture["cgminer"][command] = {
                "available": False,
                "error": "read_or_parse_failed",
            }

    fixture["http_system_info"] = capture_system_info(
        validated_host, username, password
    )
    return fixture


def save_fixture(fixture, directory=CAPTURE_DIR):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError("Output directory must not be a symlink")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    model = fixture["expected_model"].lower().replace("-", "_")
    destination = directory / (model + "_stock_" + timestamp + ".json")

    fd = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(fixture, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=("S21", "S21-Pro"))
    args = parser.parse_args()

    print("READ-ONLY capture: CGMiner and get_system_info.cgi only.")
    print("No ASIC control actions will be sent.")
    try:
        host = validate_target(input("Miner private IPv4: "))
    except (EOFError, ValueError) as exc:
        print("ERROR:", type(exc).__name__, "Invalid or missing private IPv4")
        return 2

    try:
        username = input("HTTP Digest user [root]: ").strip() or "root"
        password = getpass.getpass(
            "HTTP Digest password (hidden; Enter to skip): "
        )
    except (EOFError, KeyboardInterrupt):
        print("Capture cancelled")
        return 2

    fixture = collect_fixture(host, args.model, username, password)
    try:
        path = save_fixture(fixture)
    except (OSError, ValueError):
        print("ERROR: Could not save sanitized capture")
        return 1

    print("Sanitized fixture:", path)
    for command, result in fixture["cgminer"].items():
        print("CGMiner", command, "OK" if result["available"] else "UNAVAILABLE")
    print(
        "System info HTTP status:",
        fixture["http_system_info"]["authenticated_status"],
    )
    print("No raw responses, host IP or credentials saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
