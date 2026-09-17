"""SQLite persistence for saved discovery-network profiles."""

import sqlite3
import time

from db import db


def ensure_discovery_profiles_schema():
    conn = db()

    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS discovery_network_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                network TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_discovery_profiles_enabled
            ON discovery_network_profiles(enabled, id);
        """)
        conn.commit()
    finally:
        conn.close()


def _profile_dict(row):
    if row is None:
        return None

    return {
        "id": int(row["id"]),
        "name": row["name"],
        "network": row["network"],
        "enabled": bool(row["enabled"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


def list_discovery_profiles(enabled_only=False):
    ensure_discovery_profiles_schema()
    conn = db()

    try:
        if enabled_only:
            rows = conn.execute("""
                SELECT *
                FROM discovery_network_profiles
                WHERE enabled=1
                ORDER BY id
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT *
                FROM discovery_network_profiles
                ORDER BY id
            """).fetchall()
    finally:
        conn.close()

    return [
        _profile_dict(row)
        for row in rows
    ]


def get_discovery_profile(profile_id):
    ensure_discovery_profiles_schema()
    conn = db()

    try:
        row = conn.execute("""
            SELECT *
            FROM discovery_network_profiles
            WHERE id=?
        """, (profile_id,)).fetchone()
    finally:
        conn.close()

    return _profile_dict(row)


def create_discovery_profile(name, network, enabled=True):
    ensure_discovery_profiles_schema()
    now = int(time.time())
    conn = db()

    try:
        try:
            cursor = conn.execute("""
                INSERT INTO discovery_network_profiles(
                    name,
                    network,
                    enabled,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                name,
                network,
                1 if enabled else 0,
                now,
                now,
            ))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ValueError(
                "Discovery profile name or network already exists"
            ) from exc

        profile_id = cursor.lastrowid
    finally:
        conn.close()

    return get_discovery_profile(profile_id)


def update_discovery_profile(
    profile_id,
    name,
    network,
    enabled,
):
    ensure_discovery_profiles_schema()
    now = int(time.time())
    conn = db()

    try:
        try:
            cursor = conn.execute("""
                UPDATE discovery_network_profiles
                SET
                    name=?,
                    network=?,
                    enabled=?,
                    updated_at=?
                WHERE id=?
            """, (
                name,
                network,
                1 if enabled else 0,
                now,
                profile_id,
            ))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ValueError(
                "Discovery profile name or network already exists"
            ) from exc

        if cursor.rowcount == 0:
            return None
    finally:
        conn.close()

    return get_discovery_profile(profile_id)


def delete_discovery_profile(profile_id):
    ensure_discovery_profiles_schema()
    conn = db()

    try:
        cursor = conn.execute("""
            DELETE FROM discovery_network_profiles
            WHERE id=?
        """, (profile_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


__all__ = (
    "ensure_discovery_profiles_schema",
    "list_discovery_profiles",
    "get_discovery_profile",
    "create_discovery_profile",
    "update_discovery_profile",
    "delete_discovery_profile",
)
