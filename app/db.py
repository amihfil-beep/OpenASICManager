import sqlite3
import time

import config as app_config


def db():
    conn = sqlite3.connect(
        app_config.DATABASE_PATH,
        timeout=10,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    return conn


def get_setting(
    key,
    default=None,
):
    conn = db()

    row = conn.execute(
        """
        SELECT value
        FROM settings
        WHERE key=?
        """,
        (key,),
    ).fetchone()

    conn.close()

    if not row:
        return default

    return row["value"]


def set_setting(
    key,
    value,
):
    conn = db()

    conn.execute("""
        INSERT INTO settings(
            key,
            value
        )
        VALUES (?, ?)

        ON CONFLICT(key)
        DO UPDATE SET
            value=excluded.value
    """, (
        key,
        str(value),
    ))

    conn.commit()
    conn.close()


def get_miner(
    miner_id,
):
    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM miners
        WHERE id=?
        """,
        (miner_id,),
    ).fetchone()

    conn.close()

    return row


def get_poll_miners():
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM miners
        WHERE
            enabled=1
            AND driver IN (
                'awesome',
                'bitmain_stock'
            )
    """).fetchall()

    conn.close()

    return list(rows)


def get_control_miners():
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM miners
        WHERE
            enabled=1
            AND driver IN (
                'awesome',
                'bitmain_stock'
            )
    """).fetchall()

    conn.close()

    return list(rows)


# ============================================================
# DATABASE SCHEMA / INITIALIZATION
# ============================================================

def init_db():
    conn = db()

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS miners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        name TEXT NOT NULL,
        ip TEXT NOT NULL UNIQUE,

        driver TEXT NOT NULL,

        username TEXT,
        password TEXT,

        enabled INTEGER NOT NULL DEFAULT 1,

        schedule_enabled INTEGER
            NOT NULL DEFAULT 1,

        manual_override_until INTEGER,

        model TEXT,
        firmware TEXT,

        last_state TEXT DEFAULT 'UNKNOWN',

        hashrate REAL,
        avg_hashrate REAL,

        temp REAL,
        power REAL,

        pool TEXT,

        last_seen INTEGER,
        last_error TEXT,

        last_action TEXT,
        last_action_at INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)

    # Firmware detection mode:
    #
    # AUTO   - periodic detector may update metadata
    # MANUAL - administrator owns driver/model/firmware
    #
    miner_columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(miners)"
        ).fetchall()
    }

    if "detection_mode" not in miner_columns:

        conn.execute("""
            ALTER TABLE miners
            ADD COLUMN detection_mode TEXT
                NOT NULL DEFAULT 'AUTO'
        """)

    conn.execute("""
        UPDATE miners
        SET detection_mode='AUTO'
        WHERE
            detection_mode IS NULL
            OR detection_mode NOT IN (
                'AUTO',
                'MANUAL'
            )
    """)


    conn.executescript("""
    CREATE TABLE IF NOT EXISTS action_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        source TEXT NOT NULL,
        action TEXT NOT NULL,
        miner_id INTEGER,
        ip TEXT,
        name TEXT,
        success INTEGER NOT NULL,
        message TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_action_log_ts
    ON action_log(ts DESC);

    CREATE TABLE IF NOT EXISTS control_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        created_at INTEGER NOT NULL,
        started_at INTEGER,
        completed_at INTEGER,

        miner_id INTEGER NOT NULL,
        ip TEXT NOT NULL,
        name TEXT NOT NULL,

        source TEXT NOT NULL,
        action TEXT NOT NULL,
        target_state TEXT NOT NULL,

        status TEXT NOT NULL,

        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,

        final_state TEXT,
        message TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_control_jobs_miner
    ON control_jobs(miner_id, id DESC);

    CREATE INDEX IF NOT EXISTS idx_control_jobs_status
    ON control_jobs(status);

    CREATE TABLE IF NOT EXISTS telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        ts INTEGER NOT NULL,

        miner_id INTEGER NOT NULL,
        ip TEXT NOT NULL,
        name TEXT NOT NULL,

        driver TEXT,
        state TEXT,

        hashrate REAL,
        avg_hashrate REAL,

        temp REAL,
        power REAL
    );

    CREATE INDEX IF NOT EXISTS idx_telemetry_miner_ts
    ON telemetry(miner_id, ts);

    CREATE INDEX IF NOT EXISTS idx_telemetry_ts
    ON telemetry(ts);

    CREATE TABLE IF NOT EXISTS anomaly_candidates (
        miner_id INTEGER NOT NULL,
        code TEXT NOT NULL,

        since_ts INTEGER NOT NULL,
        last_seen_ts INTEGER NOT NULL,

        PRIMARY KEY (
            miner_id,
            code
        )
    );

    CREATE TABLE IF NOT EXISTS issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        miner_id INTEGER NOT NULL,
        ip TEXT NOT NULL,
        name TEXT NOT NULL,

        code TEXT NOT NULL,
        severity TEXT NOT NULL,

        status TEXT NOT NULL,

        first_seen INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        resolved_at INTEGER,

        message TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_issues_status
    ON issues(status, id DESC);

    CREATE INDEX IF NOT EXISTS idx_issues_miner
    ON issues(miner_id, id DESC);
    """)

    conn.execute("""
        INSERT OR IGNORE INTO settings(
            key,
            value
        )
        VALUES (
            'scheduler_enabled',
            '0'
        )
    """)

    conn.execute("""
        UPDATE control_jobs

        SET
            status='ABORTED',
            completed_at=?,
            message='OpenASICManager restarted before verification completed'

        WHERE status IN (
            'QUEUED',
            'RUNNING'
        )
    """, (
        int(time.time()),
    ))

    conn.commit()
    conn.close()


SCHEDULE_RULES_SCHEMA_READY = False


def ensure_schedule_rules_schema():

    global SCHEDULE_RULES_SCHEMA_READY

    if SCHEDULE_RULES_SCHEMA_READY:
        return


    conn = db()

    try:

        # Serialize the first-run migration in case
        # scheduler/anomaly threads start simultaneously.
        conn.execute(
            "BEGIN IMMEDIATE"
        )


        conn.execute("""
            CREATE TABLE IF NOT EXISTS schedule_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                enabled INTEGER
                    NOT NULL DEFAULT 1,

                action TEXT
                    NOT NULL,

                time_minutes INTEGER
                    NOT NULL,

                days_mask INTEGER
                    NOT NULL,

                scope TEXT
                    NOT NULL DEFAULT 'SCHEDULED',

                comment TEXT
                    NOT NULL DEFAULT '',

                effective_from INTEGER
                    NOT NULL DEFAULT 0,

                last_run_key TEXT,

                created_at INTEGER
                    NOT NULL,

                updated_at INTEGER
                    NOT NULL
            )
        """)


        conn.execute("""
            CREATE INDEX IF NOT EXISTS
                idx_schedule_rules_enabled_time

            ON schedule_rules(
                enabled,
                time_minutes
            )
        """)


        # Public installations start with no schedule rules.
        #
        # The global scheduler is disabled by default and
        # administrators create the rules appropriate for
        # their own environment.



        conn.commit()

        SCHEDULE_RULES_SCHEMA_READY = True


    except Exception:

        conn.rollback()
        raise


    finally:

        conn.close()

# ============================================================
# CONTROL PERSISTENCE
# ============================================================

def get_control_job(job_id):

    conn = db()

    row = conn.execute("""
        SELECT *
        FROM control_jobs
        WHERE id=?
    """, (
        job_id,
    )).fetchone()

    conn.close()

    return row


def get_active_control_job(miner_id):

    conn = db()

    row = conn.execute("""
        SELECT *
        FROM control_jobs

        WHERE
            miner_id=?
            AND status IN (
                'QUEUED',
                'RUNNING'
            )

        ORDER BY id DESC

        LIMIT 1
    """, (
        miner_id,
    )).fetchone()

    conn.close()

    return row


def update_control_job(
    job_id,
    **fields,
):

    if not fields:
        return

    allowed = {
        "started_at",
        "completed_at",
        "status",
        "attempts",
        "final_state",
        "message",
    }

    unknown = (
        set(fields)
        - allowed
    )

    if unknown:
        raise RuntimeError(
            "Invalid control job field: "
            + ", ".join(
                sorted(unknown)
            )
        )

    columns = []
    values = []

    for key, value in fields.items():

        columns.append(
            f"{key}=?"
        )

        values.append(
            value
        )

    values.append(
        job_id
    )

    conn = db()

    conn.execute(
        """
        UPDATE control_jobs
        SET %s
        WHERE id=?
        """
        % ", ".join(columns),
        values,
    )

    conn.commit()
    conn.close()


def set_last_command(
    miner_id,
    action,
):

    conn = db()

    conn.execute("""
        UPDATE miners

        SET
            last_action=?,
            last_action_at=?

        WHERE id=?
    """, (
        action.upper(),
        int(time.time()),
        miner_id,
    ))

    conn.commit()
    conn.close()
