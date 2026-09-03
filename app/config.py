import os
from pathlib import Path


# ============================================================
# HELPERS
# ============================================================

def env_string(
    name,
    default="",
):
    return os.getenv(
        name,
        default,
    ).strip()


def env_int(
    name,
    default,
):
    try:
        return int(
            os.getenv(
                name,
                str(default),
            )
        )
    except ValueError:
        return int(default)


def env_bool(
    name,
    default=False,
):
    value = os.getenv(
        name,
        "1" if default else "0",
    ).strip().lower()

    return value in (
        "1",
        "true",
        "yes",
        "on",
    )


# ============================================================
# APPLICATION
# ============================================================

APP_NAME = env_string(
    "ASIC_MANAGER_NAME",
    "OpenASICManager",
)

DATABASE_PATH = env_string(
    "ASIC_MANAGER_DB",
    "/var/lib/openasicmanager/openasicmanager.db",
)

TIMEZONE = env_string(
    "ASIC_MANAGER_TIMEZONE",
    "UTC",
)


# ============================================================
# ASIC CREDENTIALS
#
# Passwords intentionally have NO defaults.
# ============================================================

BITMAIN_USERNAME = env_string(
    "BITMAIN_USERNAME",
    "root",
)

BITMAIN_PASSWORD = env_string(
    "BITMAIN_PASSWORD",
    "",
)

AWESOME_USERNAME = env_string(
    "AWESOME_USERNAME",
    "",
)

AWESOME_PASSWORD = env_string(
    "AWESOME_PASSWORD",
    "",
)


# ============================================================
# DISCOVERY
# ============================================================

DISCOVERY_DEFAULT_NETWORK = env_string(
    "ASIC_DISCOVERY_DEFAULT_NETWORK",
    "192.168.1.0/24",
)

DISCOVERY_MAX_HOSTS = env_int(
    "ASIC_DISCOVERY_MAX_HOSTS",
    4094,
)


# ============================================================
# REMOTE ASIC WEB
# ============================================================

REMOTE_WEB_ENABLED = env_bool(
    "REMOTE_WEB_ENABLED",
    False,
)

REMOTE_WEB_SECRET = env_string(
    "REMOTE_WEB_SECRET",
    "",
)

REMOTE_WEB_BASE_DOMAIN = env_string(
    "REMOTE_WEB_BASE_DOMAIN",
    env_string(
        "PUBLIC_DOMAIN",
        "manager.example.com",
    ),
).lower()

REMOTE_WEB_COOKIE_DOMAIN = env_string(
    "REMOTE_WEB_COOKIE_DOMAIN",
    "",
)

REMOTE_WEB_ALLOWED_CIDR = env_string(
    "REMOTE_WEB_ALLOWED_CIDR",
    "192.168.1.0/24",
)

REMOTE_WEB_TTL = max(
    60,
    min(
        env_int(
            "REMOTE_WEB_TTL",
            1800,
        ),
        86400,
    ),
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_ENABLED = env_bool(
    "TELEGRAM_ENABLED",
    False,
)

TELEGRAM_BOT_TOKEN = env_string(
    "TELEGRAM_BOT_TOKEN",
    "",
)

TELEGRAM_CHAT_ID = env_string(
    "TELEGRAM_CHAT_ID",
    "",
)

TELEGRAM_PROXY = env_string(
    "TELEGRAM_PROXY",
    "",
)


# ============================================================
# PUBLIC WEB
# ============================================================

PUBLIC_DOMAIN = env_string(
    "PUBLIC_DOMAIN",
    "manager.example.com",
).lower()


# Certificate used by Remote ASIC Web.
#
# May be:
#   - wildcard certificate
#   - Let's Encrypt SAN certificate
#   - administrator supplied certificate
#
REMOTE_WEB_CERT = env_string(
    "REMOTE_WEB_CERT",
    "",
)

REMOTE_WEB_KEY = env_string(
    "REMOTE_WEB_KEY",
    "",
)
