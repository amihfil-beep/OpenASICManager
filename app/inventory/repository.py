"""Persistence helpers for ASIC inventory state."""

from db import db


__all__ = (
    "list_miners",
    "set_miner_enabled",
    "set_miner_schedule_enabled",
    "set_all_schedule_enabled",
    "clear_manual_overrides",
    "set_miner_manual_driver",
    "set_miner_detection_auto",
    "set_miner_manual_firmware",
    "set_miner_detected_firmware",
)


def list_miners():
    conn = db()
    try:
        return list(
            conn.execute("""
                SELECT *
                FROM miners
            """).fetchall()
        )
    finally:
        conn.close()


def set_miner_enabled(
    miner_id,
    enabled,
):
    conn = db()
    try:
        conn.execute("""
            UPDATE miners
            SET enabled=?
            WHERE id=?
        """, (
            1 if enabled else 0,
            int(miner_id),
        ))
        conn.commit()
    finally:
        conn.close()


def set_miner_schedule_enabled(
    miner_id,
    enabled,
):
    conn = db()
    try:
        conn.execute("""
            UPDATE miners
            SET schedule_enabled=?
            WHERE id=?
        """, (
            1 if enabled else 0,
            int(miner_id),
        ))
        conn.commit()
    finally:
        conn.close()


def set_all_schedule_enabled(enabled):
    conn = db()
    try:
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
            1 if enabled else 0,
        ))
        changed = cur.rowcount
        conn.commit()
        return changed
    finally:
        conn.close()


def clear_manual_overrides():
    conn = db()
    try:
        cur = conn.execute("""
            UPDATE miners
            SET manual_override_until=NULL
            WHERE manual_override_until IS NOT NULL
        """)
        changed = cur.rowcount
        conn.commit()
        return changed
    finally:
        conn.close()



def set_miner_manual_driver(
    miner_id,
    driver,
    username,
    password,
):
    conn = db()
    try:
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
            int(miner_id),
        ))

        if driver == "unset":
            conn.execute("""
                UPDATE miners
                SET schedule_enabled=0
                WHERE id=?
            """, (
                int(miner_id),
            ))

        conn.commit()
    finally:
        conn.close()


def set_miner_detection_auto(miner_id):
    conn = db()
    try:
        conn.execute("""
            UPDATE miners
            SET detection_mode='AUTO'
            WHERE id=?
        """, (
            int(miner_id),
        ))
        conn.commit()
    finally:
        conn.close()


def set_miner_manual_firmware(
    miner_id,
    driver,
    username,
    password,
    model,
    firmware,
    driver_changed,
):
    conn = db()
    try:
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
            int(miner_id),
        ))

        if driver == "unset":
            conn.execute("""
                UPDATE miners
                SET schedule_enabled=0
                WHERE id=?
            """, (
                int(miner_id),
            ))

        conn.commit()
    finally:
        conn.close()


def set_miner_detected_firmware(
    miner_id,
    driver,
    username,
    password,
    model,
    firmware,
    driver_changed,
):
    conn = db()
    try:
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
            int(miner_id),
        ))
        conn.commit()
    finally:
        conn.close()
