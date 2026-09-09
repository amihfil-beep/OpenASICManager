import base64
import hashlib
import hmac
import ipaddress
import json
import time

import config as app_config
from db import db


def remote_host_for_ip(
    ip_value,
    allowed_network,
    base_domain,
):
    try:

        address = ipaddress.ip_address(
            str(ip_value)
        )

    except ValueError:
        return None


    if address.version != 4:
        return None


    if isinstance(
        allowed_network,
        str,
    ):

        try:

            allowed_network = (
                ipaddress.ip_network(
                    allowed_network,
                    strict=False,
                )
            )

        except ValueError:
            return None


    if address not in allowed_network:
        return None


    domain = str(
        base_domain
        or ""
    ).strip().lower().strip(".")


    if not domain:
        return None


    encoded_ip = (
        str(address)
        .replace(
            ".",
            "-",
        )
    )


    return (
        "m"
        +
        encoded_ip
        +
        "."
        +
        domain
    )


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


    return remote_host_for_ip(
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


__all__ = (
    "remote_host_for_ip",
    "REMOTE_WEB_SECRET",
    "REMOTE_WEB_BASE_DOMAIN",
    "REMOTE_WEB_COOKIE_DOMAIN",
    "REMOTE_WEB_ALLOWED_CIDR",
    "REMOTE_WEB_NETWORK",
    "REMOTE_WEB_TTL",
    "REMOTE_WEB_COOKIE_NAME",
    "remote_web_cookie_scope_valid",
    "remote_web_make_token",
    "remote_web_clear_cookie",
    "remote_web_verify_token",
    "remote_web_host_for_ip",
    "remote_web_miner_for_host",
    "remote_web_b64encode",
    "remote_web_b64decode",
)
