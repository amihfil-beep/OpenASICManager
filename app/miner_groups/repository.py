"""Persistence for operational miner groups."""

import sqlite3
import time

from db import db


def list_groups():
    conn = db()

    try:
        return list(
            conn.execute("""
                SELECT
                    g.id,
                    g.name,
                    g.normalized_name,
                    g.created_by,
                    g.created_at,
                    g.updated_by,
                    g.updated_at,
                    COUNT(m.id) AS member_count

                FROM miner_groups g

                LEFT JOIN miners m
                    ON m.group_id=g.id

                GROUP BY g.id

                ORDER BY
                    g.name COLLATE NOCASE,
                    g.id
            """).fetchall()
        )

    finally:
        conn.close()


def get_group(group_id):
    conn = db()

    try:
        return conn.execute("""
            SELECT
                g.id,
                g.name,
                g.normalized_name,
                g.created_by,
                g.created_at,
                g.updated_by,
                g.updated_at,
                (
                    SELECT COUNT(*)
                    FROM miners m
                    WHERE m.group_id=g.id
                ) AS member_count

            FROM miner_groups g

            WHERE g.id=?
        """, (
            int(group_id),
        )).fetchone()

    finally:
        conn.close()


def create_group(
    name,
    normalized_name,
    actor,
):
    conn = db()

    try:
        now = int(
            time.time()
        )

        cursor = conn.execute("""
            INSERT INTO miner_groups(
                name,
                normalized_name,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            name,
            normalized_name,
            actor,
            now,
        ))

        group_id = (
            cursor.lastrowid
        )

        conn.commit()

    except sqlite3.IntegrityError as exc:
        conn.rollback()

        raise ValueError(
            "Group name already exists"
        ) from exc

    finally:
        conn.close()

    return get_group(
        group_id
    )


def rename_group(
    group_id,
    name,
    normalized_name,
    actor,
):
    conn = db()

    try:
        existing = conn.execute("""
            SELECT id
            FROM miner_groups
            WHERE id=?
        """, (
            int(group_id),
        )).fetchone()

        if existing is None:
            return None

        try:
            conn.execute("""
                UPDATE miner_groups

                SET
                    name=?,
                    normalized_name=?,
                    updated_by=?,
                    updated_at=?

                WHERE id=?
            """, (
                name,
                normalized_name,
                actor,
                int(time.time()),
                int(group_id),
            ))

        except sqlite3.IntegrityError as exc:
            conn.rollback()

            raise ValueError(
                "Group name already exists"
            ) from exc

        conn.commit()

    finally:
        conn.close()

    return get_group(
        group_id
    )


def delete_group(group_id):
    conn = db()

    try:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        row = conn.execute("""
            SELECT
                id,
                name,
                normalized_name

            FROM miner_groups

            WHERE id=?
        """, (
            int(group_id),
        )).fetchone()

        if row is None:
            conn.rollback()
            return None

        member_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM miners
            WHERE group_id=?
        """, (
            int(group_id),
        )).fetchone()["count"]

        conn.execute("""
            DELETE FROM
                group_anomaly_policy_overrides
            WHERE group_id=?
        """, (
            int(group_id),
        ))

        conn.execute("""
            UPDATE miners
            SET group_id=NULL
            WHERE group_id=?
        """, (
            int(group_id),
        ))

        conn.execute("""
            DELETE FROM miner_groups
            WHERE id=?
        """, (
            int(group_id),
        ))

        conn.commit()

        return {
            "id": row["id"],
            "name": row["name"],
            "normalized_name":
                row["normalized_name"],
            "member_count":
                int(member_count),
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def set_miner_group(
    miner_id,
    group_id,
):
    conn = db()

    try:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        miner = conn.execute("""
            SELECT
                id,
                group_id

            FROM miners

            WHERE id=?
        """, (
            int(miner_id),
        )).fetchone()

        if miner is None:
            conn.rollback()

            return {
                "status":
                    "MINER_NOT_FOUND",
            }

        old_group_id = (
            miner["group_id"]
        )

        if group_id is not None:

            group = conn.execute("""
                SELECT id
                FROM miner_groups
                WHERE id=?
            """, (
                int(group_id),
            )).fetchone()

            if group is None:
                conn.rollback()

                return {
                    "status":
                        "GROUP_NOT_FOUND",
                    "old_group_id":
                        old_group_id,
                }

            group_id = int(
                group_id
            )

        if old_group_id == group_id:
            conn.rollback()

            return {
                "status":
                    "UNCHANGED",
                "old_group_id":
                    old_group_id,
                "new_group_id":
                    group_id,
            }

        conn.execute("""
            UPDATE miners
            SET group_id=?
            WHERE id=?
        """, (
            group_id,
            int(miner_id),
        ))

        conn.commit()

        return {
            "status":
                "UPDATED",
            "old_group_id":
                old_group_id,
            "new_group_id":
                group_id,
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


__all__ = (
    "list_groups",
    "get_group",
    "create_group",
    "rename_group",
    "delete_group",
    "set_miner_group",
)
