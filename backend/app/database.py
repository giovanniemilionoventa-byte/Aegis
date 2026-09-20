from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config
from .config import DATABASE_URL

_IS_SQLITE = DATABASE_URL.startswith("sqlite")
_IN_MEMORY = _IS_SQLITE and (
    ":memory:" in DATABASE_URL or DATABASE_URL in ("sqlite://", "sqlite:///")
)

connect_args = {}
engine_options: dict = {}
if _IS_SQLITE:
    # Wait for another writer instead of failing at once with "database is locked".
    connect_args = {"check_same_thread": False, "timeout": 5}
if not _IN_MEMORY:
    # Phase 20: a bounded pool that fails fast. The SQLAlchemy default keeps a
    # request waiting 30 s for a connection, and every worker thread queues up
    # behind it; measured (Phase 16C) as a total stall past ~75 concurrent calls.
    engine_options = {
        "pool_size": config.DB_POOL_SIZE,
        "max_overflow": config.DB_MAX_OVERFLOW,
        "pool_timeout": config.DB_POOL_TIMEOUT,
        "pool_pre_ping": True,
    }

engine = create_engine(DATABASE_URL, connect_args=connect_args, **engine_options)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


if _IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        if not _IN_MEMORY:
            # Phase 20: WAL lets readers and the single writer proceed together;
            # the default rollback journal blocks every reader behind each write.
            # Needs a real filesystem (a Docker named volume, not a Windows bind mount).
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


# Phase 20 additive columns. A SQLite database created by an earlier phase gets
# them here; a fresh database on any engine gets them from create_all.
_PHASE20_COLUMNS = (
    ("organizations", "internal_domains", "TEXT"),
    ("approvals", "preview_sealed", "TEXT"),
    ("approvals", "preview_purge_at", "DATETIME"),
    ("agents", "last_seen_at", "DATETIME"),
)


def _add_column(conn, table: str, column: str, ddl_type: str) -> None:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    if rows and column not in {row[1] for row in rows}:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)
    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(events)")).fetchall()
        cols = {row[1] for row in rows}
        if "execution_id" not in cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN execution_id VARCHAR"))
        if "seq" not in cols:
            conn.execute(
                text("ALTER TABLE events ADD COLUMN seq INTEGER DEFAULT 0 NOT NULL")
            )
        if "evidence_hash" not in cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN evidence_hash VARCHAR"))
        if "previous_evidence_hash" not in cols:
            conn.execute(
                text("ALTER TABLE events ADD COLUMN previous_evidence_hash VARCHAR")
            )
        approval_rows = conn.execute(text("PRAGMA table_info(approvals)")).fetchall()
        approval_cols = {row[1] for row in approval_rows}
        for column, ddl in (
            ("execution_id", "ALTER TABLE approvals ADD COLUMN execution_id VARCHAR"),
            ("request_id", "ALTER TABLE approvals ADD COLUMN request_id VARCHAR"),
            ("contract_id", "ALTER TABLE approvals ADD COLUMN contract_id VARCHAR"),
            (
                "contract_version",
                "ALTER TABLE approvals ADD COLUMN contract_version INTEGER",
            ),
            ("param_hash", "ALTER TABLE approvals ADD COLUMN param_hash VARCHAR"),
            ("expires_at", "ALTER TABLE approvals ADD COLUMN expires_at DATETIME"),
            ("consumed_at", "ALTER TABLE approvals ADD COLUMN consumed_at DATETIME"),
            (
                "consumed_event_id",
                "ALTER TABLE approvals ADD COLUMN consumed_event_id VARCHAR",
            ),
        ):
            if column not in approval_cols and approval_rows:
                conn.execute(text(ddl))

        exec_rows = conn.execute(text("PRAGMA table_info(executions)")).fetchall()
        exec_cols = {row[1] for row in exec_rows}
        if "evidence_chain_tip" not in exec_cols:
            conn.execute(
                text("ALTER TABLE executions ADD COLUMN evidence_chain_tip VARCHAR")
            )
        for table, column, ddl_type in _PHASE20_COLUMNS:
            _add_column(conn, table, column, ddl_type)
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        if "runtime_contracts" in tables:
            indexes = {
                row[1]
                for row in conn.execute(
                    text("PRAGMA index_list(runtime_contracts)")
                ).fetchall()
            }
            if "uq_runtime_contract_one_active_per_agent" not in indexes:
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS "
                        "uq_runtime_contract_one_active_per_agent "
                        "ON runtime_contracts (organization_id, agent_id) "
                        "WHERE status = 'ACTIVE'"
                    )
                )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
