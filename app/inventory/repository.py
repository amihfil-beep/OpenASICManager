"""Persistence helpers for ASIC inventory state."""

import sqlite3

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
    "convert_unconfigured_to_stock",
    "list_discovery_miners",
    "get_miner_by_ip",
    "miner_name_exists",
    "create_discovered_miner",
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



def convert_unconfigured_to_stock(
    username,
    password,
):
    conn = db()
    try:
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
                username,
                password,
                row["id"],
            ))
            ids.append(row["id"])

        conn.commit()
        return ids
    finally:
        conn.close()


def list_discovery_miners():
    conn = db()
    try:
        return list(
            conn.execute("""
                SELECT
                    id,
                    ip,
                    driver,
                    name
                FROM miners
            """).fetchall()
        )
    finally:
        conn.close()


def get_miner_by_ip(ip):
    conn = db()
    try:
        return conn.execute("""
            SELECT
                id,
                name,
                driver
            FROM miners
            WHERE ip=?
        """, (
            ip,
        )).fetchone()
    finally:
        conn.close()


def miner_name_exists(name):
    conn = db()
    try:
        row = conn.execute("""
            SELECT id
            FROM miners
            WHERE name=?
        """, (
            name,
        )).fetchone()
        return bool(row)
    finally:
        conn.close()


def create_discovered_miner(
    name,
    ip,
    driver,
    username,
    password,
    model,
    firmware,
):
    conn = db()
    try:
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
                name,
                ip,
                driver,
                username,
                password,
                model,
                firmware,
            ))
            miner_id = cur.lastrowid
            conn.commit()
            return miner_id, True

        except sqlite3.IntegrityError:
            conn.rollback()
            existing = conn.execute("""
                SELECT id
                FROM miners
                WHERE ip=?
            """, (
                ip,
            )).fetchone()
            return (
                existing["id"] if existing else None,
                False,
            )
    finally:
        conn.close()
