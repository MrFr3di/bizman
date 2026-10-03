from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Final

from bizman.current.model import (
    CompanyState,
    CurrentProjectionSpec,
    CurrentStateMetadata,
    CurrentStateSnapshot,
    ObservedProduct,
    ProductSurfaceState,
    ReplaySession,
    UnitProductState,
    UnitState,
    build_current_snapshot,
)


APPLICATION_ID: Final[int] = 0x424D4331
USER_VERSION: Final[int] = 3
_MIN_SQLITE_VERSION: Final[tuple[int, int, int]] = (3, 37, 0)
_EXPECTED_TABLES = frozenset(
    {
        "projection_meta",
        "replayed_session",
        "company",
        "unit",
        "observed_product",
        "unit_product",
        "product_surface_state",
    }
)


class CurrentStateError(RuntimeError):
    """Base class for Current State storage failures."""


class CurrentStateCompatibilityError(CurrentStateError):
    """The SQLite file/runtime is incompatible with Current State schema."""


class CurrentStateIntegrityError(CurrentStateError):
    """Persisted Current State violates deterministic semantic identity."""


class CurrentStateOperationError(CurrentStateError):
    """A Current State SQLite operation failed."""


_SCHEMA = (
    """
    CREATE TABLE projection_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        projection_name TEXT NOT NULL,
        projection_version INTEGER NOT NULL CHECK (projection_version > 0),
        analysis_profile_sha256 TEXT NOT NULL,
        unit_economics_contract TEXT NOT NULL,
        catalog_resolver_sha256 TEXT NOT NULL,
        input_fingerprint TEXT NOT NULL,
        state_fingerprint TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ready', 'stale')),
        stale_reason TEXT,
        session_count INTEGER NOT NULL CHECK (session_count >= 0),
        last_session_id TEXT,
        last_sequence INTEGER CHECK (last_sequence IS NULL OR last_sequence >= 0),
        CHECK (
            (status = 'ready' AND stale_reason IS NULL)
            OR
            (status = 'stale' AND stale_reason IS NOT NULL AND length(stale_reason) > 0)
        ),
        CHECK (
            (session_count = 0 AND last_session_id IS NULL AND last_sequence IS NULL)
            OR
            (session_count > 0 AND last_session_id IS NOT NULL)
        )
    ) STRICT
    """,
    """
    CREATE TABLE replayed_session (
        session_id TEXT PRIMARY KEY,
        manifest_sha256 TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('completed', 'cancelled')),
        event_count INTEGER NOT NULL CHECK (event_count >= 0),
        last_sequence INTEGER CHECK (last_sequence IS NULL OR last_sequence >= 0),
        CHECK (
            (event_count = 0 AND last_sequence IS NULL)
            OR
            (event_count > 0 AND last_sequence = event_count - 1)
        )
    ) STRICT
    """,
    """
    CREATE INDEX replayed_session_order_idx
    ON replayed_session(started_at, session_id)
    """,
    """
    CREATE TABLE company (
        company_id TEXT PRIMARY KEY,
        name TEXT NOT NULL CHECK (length(name) > 0),
        source_session_id TEXT NOT NULL,
        source_sequence INTEGER NOT NULL CHECK (source_sequence >= 0),
        observed_at TEXT NOT NULL,
        FOREIGN KEY (source_session_id)
            REFERENCES replayed_session(session_id)
    ) STRICT
    """,
    """
    CREATE TABLE unit (
        unit_id TEXT PRIMARY KEY,
        company_id TEXT NOT NULL,
        display_name TEXT NOT NULL CHECK (length(display_name) > 0),
        city_name TEXT NOT NULL CHECK (length(city_name) > 0),
        level INTEGER NOT NULL CHECK (level > 0),
        source_session_id TEXT NOT NULL,
        source_sequence INTEGER NOT NULL CHECK (source_sequence >= 0),
        observed_at TEXT NOT NULL,
        FOREIGN KEY (company_id) REFERENCES company(company_id),
        FOREIGN KEY (source_session_id)
            REFERENCES replayed_session(session_id)
    ) STRICT
    """,
    """
    CREATE INDEX unit_company_idx
    ON unit(company_id, unit_id)
    """,
    """
    CREATE TABLE observed_product (
        product_numeric_id INTEGER PRIMARY KEY CHECK (product_numeric_id > 0),
        catalog_key TEXT,
        resolution TEXT NOT NULL CHECK (resolution IN ('resolved', 'unresolved')),
        CHECK (
            (resolution = 'resolved'
                AND catalog_key IS NOT NULL
                AND length(catalog_key) > 0)
            OR
            (resolution = 'unresolved' AND catalog_key IS NULL)
        )
    ) STRICT
    """,
    """
    CREATE TABLE unit_product (
        unit_id TEXT NOT NULL,
        product_numeric_id INTEGER NOT NULL,
        revenue INTEGER NOT NULL CHECK (revenue >= 0),
        profit INTEGER NOT NULL CHECK (profit >= 0),
        stock_qty INTEGER NOT NULL CHECK (stock_qty >= 0),
        stock_quality REAL NOT NULL CHECK (stock_quality >= 0),
        our_price INTEGER NOT NULL CHECK (our_price >= 0),
        city_quality REAL NOT NULL CHECK (city_quality >= 0),
        city_price INTEGER NOT NULL CHECK (city_price >= 0),
        sales_volume INTEGER NOT NULL CHECK (sales_volume >= 0),
        supply_qty INTEGER NOT NULL CHECK (supply_qty >= 0),
        supply_cost INTEGER NOT NULL CHECK (supply_cost >= 0),
        source_session_id TEXT NOT NULL,
        source_sequence INTEGER NOT NULL CHECK (source_sequence >= 0),
        observed_at TEXT NOT NULL,
        PRIMARY KEY (unit_id, product_numeric_id),
        FOREIGN KEY (unit_id) REFERENCES unit(unit_id),
        FOREIGN KEY (product_numeric_id)
            REFERENCES observed_product(product_numeric_id),
        FOREIGN KEY (source_session_id)
            REFERENCES replayed_session(session_id)
    ) STRICT
    """,
    """
    CREATE TABLE product_surface_state (
        unit_id TEXT NOT NULL,
        surface TEXT NOT NULL CHECK (length(surface) > 0),
        status TEXT NOT NULL CHECK (status IN ('ready', 'unknown', 'stale')),
        stale_reason TEXT,
        source_session_id TEXT,
        source_sequence INTEGER CHECK (
            source_sequence IS NULL OR source_sequence >= 0
        ),
        observed_at TEXT,
        PRIMARY KEY (unit_id, surface),
        FOREIGN KEY (unit_id) REFERENCES unit(unit_id),
        FOREIGN KEY (source_session_id)
            REFERENCES replayed_session(session_id),
        CHECK (
            (status = 'unknown'
                AND stale_reason IS NULL
                AND source_session_id IS NULL
                AND source_sequence IS NULL
                AND observed_at IS NULL)
            OR
            (status = 'ready'
                AND stale_reason IS NULL
                AND source_session_id IS NOT NULL
                AND source_sequence IS NOT NULL
                AND observed_at IS NOT NULL)
            OR
            (status = 'stale'
                AND stale_reason IS NOT NULL
                AND length(stale_reason) > 0
                AND source_session_id IS NOT NULL
                AND source_sequence IS NOT NULL
                AND observed_at IS NOT NULL)
        )
    ) STRICT
    """,
)


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _preflight_rw_identity(connection: sqlite3.Connection) -> None:
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    tables = _user_tables(connection)

    if application_id == 0:
        if user_version == 0 and not tables:
            return
        raise CurrentStateCompatibilityError(
            "unidentified non-empty SQLite database cannot be adopted as Current State"
        )
    if application_id != APPLICATION_ID:
        raise CurrentStateCompatibilityError(
            f"foreign Current State database application_id={application_id}"
        )
    if user_version != USER_VERSION:
        relation = "newer" if user_version > USER_VERSION else "older"
        raise CurrentStateCompatibilityError(
            f"{relation} Current State schema {user_version} is unsupported; "
            f"expected {USER_VERSION}"
        )


def _connect_rw(path: Path) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
        raise CurrentStateCompatibilityError(
            "Current State requires SQLite >= 3.37.0 for STRICT tables"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None leaves BEGIN/COMMIT under the store's control on
        # both supported Python versions without relying on 3.12+ keywords.
        connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        try:
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            _preflight_rw_identity(connection)
            mode = str(
                connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            ).casefold()
            if mode != "wal":
                raise CurrentStateCompatibilityError(
                    "Current State requires SQLite WAL journal mode"
                )
            connection.execute("PRAGMA synchronous = NORMAL")
            return connection
        except BaseException:
            connection.close()
            raise
    except CurrentStateError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise CurrentStateOperationError(
            "cannot open Current State database for writing"
        ) from exc


def _connect_ro(path: Path) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
        raise CurrentStateCompatibilityError(
            "Current State requires SQLite >= 3.37.0 for STRICT tables"
        )
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(
            uri,
            timeout=5.0,
            uri=True,
            isolation_level=None,
        )
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise CurrentStateOperationError(
            "cannot open Current State database read-only"
        ) from exc


class _ImmediateTransaction:
    """One explicit SQLite transaction; no deferred generator cleanup."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise CurrentStateOperationError(
                "Current State transaction failed"
            ) from exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        del exc_type, tb
        try:
            if exc is None:
                self._connection.execute("COMMIT")
            elif self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
        except sqlite3.Error as transaction_error:
            if self._connection.in_transaction:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise CurrentStateOperationError(
                "Current State transaction failed"
            ) from transaction_error

        if isinstance(exc, sqlite3.Error):
            raise CurrentStateOperationError(
                "Current State transaction failed"
            ) from exc
        return False


class CurrentStateStore:
    """Rebuildable deterministic Current State SQLite store."""

    def __init__(
        self,
        path: Path,
        connection: sqlite3.Connection,
        *,
        read_only: bool,
    ) -> None:
        self.path = path
        self._connection = connection
        self._read_only = read_only
        self._closed = False

    @classmethod
    def open_rw(cls, path: Path) -> "CurrentStateStore":
        resolved = Path(path).expanduser().resolve(strict=False)
        connection = _connect_rw(resolved)
        store = cls(resolved, connection, read_only=False)
        try:
            store._bootstrap_or_validate()
        except BaseException:
            connection.close()
            raise
        return store

    @classmethod
    def open_read_only_if_exists(
        cls,
        path: Path,
    ) -> "CurrentStateStore | None":
        resolved = Path(path).expanduser().resolve(strict=False)
        if not resolved.exists():
            return None
        if not resolved.is_file():
            raise CurrentStateCompatibilityError(
                "Current State database path is not a regular file"
            )
        connection = _connect_ro(resolved)
        store = cls(resolved, connection, read_only=True)
        try:
            store._validate_existing()
        except BaseException:
            connection.close()
            raise
        return store

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            self._connection.close()
        finally:
            self._closed = True

    def __enter__(self) -> "CurrentStateStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _immediate_transaction(self) -> _ImmediateTransaction:
        if self._read_only:
            raise CurrentStateCompatibilityError(
                "Current State database is open read-only"
            )
        return _ImmediateTransaction(self._connection)

    def _bootstrap_or_validate(self) -> None:
        application_id = int(
            self._connection.execute("PRAGMA application_id").fetchone()[0]
        )
        user_version = int(
            self._connection.execute("PRAGMA user_version").fetchone()[0]
        )
        tables = _user_tables(self._connection)

        if application_id == 0 and user_version == 0 and not tables:
            self._initialize_schema()
        self._validate_existing()

    def _initialize_schema(self) -> None:
        with self._immediate_transaction():
            for statement in _SCHEMA:
                self._connection.execute(statement)
            self._connection.execute(
                f"PRAGMA application_id = {APPLICATION_ID}"
            )
            self._connection.execute(f"PRAGMA user_version = {USER_VERSION}")

    def _validate_schema_identity(self) -> None:
        application_id = int(
            self._connection.execute("PRAGMA application_id").fetchone()[0]
        )
        user_version = int(
            self._connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if application_id != APPLICATION_ID:
            raise CurrentStateCompatibilityError(
                f"foreign Current State database application_id={application_id}"
            )
        if user_version != USER_VERSION:
            relation = "newer" if user_version > USER_VERSION else "older"
            raise CurrentStateCompatibilityError(
                f"{relation} Current State schema {user_version} is unsupported; "
                f"expected {USER_VERSION}"
            )

        tables = _user_tables(self._connection)
        if tables != _EXPECTED_TABLES:
            raise CurrentStateCompatibilityError(
                "Current State database user tables do not match schema v3"
            )
        strict = {
            str(row[1]): int(row[5])
            for row in self._connection.execute("PRAGMA table_list")
            if str(row[1]) in _EXPECTED_TABLES
        }
        if set(strict) != _EXPECTED_TABLES:
            raise CurrentStateCompatibilityError(
                "Current State database is missing required schema tables"
            )
        if any(value != 1 for value in strict.values()):
            raise CurrentStateCompatibilityError(
                "Current State database tables must all be STRICT"
            )

    def verify_storage_integrity(self) -> None:
        """Run SQLite physical and foreign-key checks on the open database."""

        integrity = [
            str(row[0])
            for row in self._connection.execute("PRAGMA integrity_check")
        ]
        if integrity != ["ok"]:
            raise CurrentStateIntegrityError(
                "Current State storage integrity check failed"
            )
        if tuple(self._connection.execute("PRAGMA foreign_key_check")):
            raise CurrentStateIntegrityError(
                "Current State contains foreign-key violations"
            )

    def _validate_row_integrity(self) -> None:
        meta_count = int(
            self._connection.execute(
                "SELECT count(*) FROM projection_meta"
            ).fetchone()[0]
        )
        ledger_count = int(
            self._connection.execute(
                "SELECT count(*) FROM replayed_session"
            ).fetchone()[0]
        )
        company_count = int(
            self._connection.execute(
                "SELECT count(*) FROM company"
            ).fetchone()[0]
        )
        unit_count = int(
            self._connection.execute(
                "SELECT count(*) FROM unit"
            ).fetchone()[0]
        )
        product_count = int(
            self._connection.execute(
                "SELECT count(*) FROM observed_product"
            ).fetchone()[0]
        )
        unit_product_count = int(
            self._connection.execute(
                "SELECT count(*) FROM unit_product"
            ).fetchone()[0]
        )
        surface_count = int(
            self._connection.execute(
                "SELECT count(*) FROM product_surface_state"
            ).fetchone()[0]
        )
        if meta_count not in {0, 1}:
            raise CurrentStateIntegrityError(
                "Current State must contain at most one metadata row"
            )
        if meta_count == 0 and (
            ledger_count
            or company_count
            or unit_count
            or product_count
            or unit_product_count
            or surface_count
        ):
            raise CurrentStateIntegrityError(
                "Current State rows exist without metadata"
            )
        foreign_key_violations = tuple(
            self._connection.execute("PRAGMA foreign_key_check")
        )
        if foreign_key_violations:
            raise CurrentStateIntegrityError(
                "Current State contains foreign-key violations"
            )
        if meta_count == 1:
            self._snapshot_unchecked()

    def _validate_existing(self) -> None:
        try:
            self._validate_schema_identity()
            self._validate_row_integrity()
        except CurrentStateError:
            raise
        except (TypeError, ValueError) as exc:
            raise CurrentStateIntegrityError(
                "Current State persisted values violate semantic invariants"
            ) from exc
        except sqlite3.Error as exc:
            raise CurrentStateOperationError(
                "Current State validation failed"
            ) from exc

    def _snapshot_unchecked(self) -> CurrentStateSnapshot | None:
        meta = self._connection.execute(
            """
            SELECT projection_name, projection_version,
                   analysis_profile_sha256, unit_economics_contract,
                   catalog_resolver_sha256, input_fingerprint,
                   state_fingerprint, status, stale_reason,
                   session_count, last_session_id, last_sequence
            FROM projection_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if meta is None:
            return None

        sessions = tuple(
            ReplaySession(
                session_id=str(row[0]),
                manifest_sha256=str(row[1]),
                evidence_sha256=str(row[2]),
                started_at=str(row[3]),
                ended_at=str(row[4]),
                status=str(row[5]),
                event_count=int(row[6]),
                last_sequence=(
                    int(row[7]) if row[7] is not None else None
                ),
            )
            for row in self._connection.execute(
                """
                SELECT session_id, manifest_sha256, evidence_sha256,
                       started_at, ended_at, status,
                       event_count, last_sequence
                FROM replayed_session
                ORDER BY started_at, session_id
                """
            )
        )

        companies = tuple(
            CompanyState(
                company_id=str(company_id),
                name=str(name),
                source_session_id=str(source_session_id),
                source_sequence=int(source_sequence),
                observed_at=str(observed_at),
            )
            for (
                company_id,
                name,
                source_session_id,
                source_sequence,
                observed_at,
            ) in self._connection.execute(
                """
                SELECT company_id, name, source_session_id,
                       source_sequence, observed_at
                FROM company
                ORDER BY company_id
                """
            )
        )
        units = tuple(
            UnitState(
                unit_id=str(unit_id),
                company_id=str(company_id),
                display_name=str(display_name),
                city_name=str(city_name),
                level=int(level),
                source_session_id=str(source_session_id),
                source_sequence=int(source_sequence),
                observed_at=str(observed_at),
            )
            for (
                unit_id,
                company_id,
                display_name,
                city_name,
                level,
                source_session_id,
                source_sequence,
                observed_at,
            ) in self._connection.execute(
                """
                SELECT unit_id, company_id, display_name, city_name, level,
                       source_session_id, source_sequence, observed_at
                FROM unit
                ORDER BY unit_id
                """
            )
        )
        products = tuple(
            ObservedProduct(
                product_numeric_id=int(product_numeric_id),
                catalog_key=(
                    str(catalog_key) if catalog_key is not None else None
                ),
                resolution=str(resolution),
            )
            for (
                product_numeric_id,
                catalog_key,
                resolution,
            ) in self._connection.execute(
                """
                SELECT product_numeric_id, catalog_key, resolution
                FROM observed_product
                ORDER BY product_numeric_id
                """
            )
        )
        unit_products = tuple(
            UnitProductState(
                unit_id=str(unit_id),
                product_numeric_id=int(product_numeric_id),
                revenue=int(revenue),
                profit=int(profit),
                stock_qty=int(stock_qty),
                stock_quality=float(stock_quality),
                our_price=int(our_price),
                city_quality=float(city_quality),
                city_price=int(city_price),
                sales_volume=int(sales_volume),
                supply_qty=int(supply_qty),
                supply_cost=int(supply_cost),
                source_session_id=str(source_session_id),
                source_sequence=int(source_sequence),
                observed_at=str(observed_at),
            )
            for (
                unit_id,
                product_numeric_id,
                revenue,
                profit,
                stock_qty,
                stock_quality,
                our_price,
                city_quality,
                city_price,
                sales_volume,
                supply_qty,
                supply_cost,
                source_session_id,
                source_sequence,
                observed_at,
            ) in self._connection.execute(
                """
                SELECT unit_id, product_numeric_id, revenue, profit,
                       stock_qty, stock_quality, our_price, city_quality,
                       city_price, sales_volume, supply_qty, supply_cost,
                       source_session_id, source_sequence, observed_at
                FROM unit_product
                ORDER BY unit_id, product_numeric_id
                """
            )
        )
        surfaces = tuple(
            ProductSurfaceState(
                unit_id=str(unit_id),
                surface=str(surface),
                status=str(status),
                stale_reason=(
                    str(stale_reason) if stale_reason is not None else None
                ),
                source_session_id=(
                    str(source_session_id)
                    if source_session_id is not None
                    else None
                ),
                source_sequence=(
                    int(source_sequence)
                    if source_sequence is not None
                    else None
                ),
                observed_at=(
                    str(observed_at) if observed_at is not None else None
                ),
            )
            for (
                unit_id,
                surface,
                status,
                stale_reason,
                source_session_id,
                source_sequence,
                observed_at,
            ) in self._connection.execute(
                """
                SELECT unit_id, surface, status, stale_reason,
                       source_session_id, source_sequence, observed_at
                FROM product_surface_state
                ORDER BY unit_id, surface
                """
            )
        )

        metadata = CurrentStateMetadata(
            projection_name=str(meta[0]),
            projection_version=int(meta[1]),
            analysis_profile_sha256=str(meta[2]),
            unit_economics_contract=str(meta[3]),
            catalog_resolver_sha256=str(meta[4]),
            input_fingerprint=str(meta[5]),
            state_fingerprint=str(meta[6]),
            status=str(meta[7]),
            stale_reason=(
                str(meta[8]) if meta[8] is not None else None
            ),
            session_count=int(meta[9]),
            last_session_id=(
                str(meta[10]) if meta[10] is not None else None
            ),
            last_sequence=(
                int(meta[11]) if meta[11] is not None else None
            ),
        )
        return CurrentStateSnapshot(
            metadata=metadata,
            sessions=sessions,
            companies=companies,
            units=units,
            products=products,
            unit_products=unit_products,
            surfaces=surfaces,
        )

    def snapshot(self) -> CurrentStateSnapshot | None:
        try:
            value = self._snapshot_unchecked()
        except CurrentStateError:
            raise
        except (TypeError, ValueError) as exc:
            raise CurrentStateIntegrityError(
                "Current State persisted fingerprint or ledger is invalid"
            ) from exc
        except sqlite3.Error as exc:
            raise CurrentStateOperationError(
                "Current State snapshot read failed"
            ) from exc
        return value

    def replace_snapshot(self, snapshot: CurrentStateSnapshot) -> None:
        if not isinstance(snapshot, CurrentStateSnapshot):
            raise TypeError("snapshot must be CurrentStateSnapshot")

        meta = snapshot.metadata
        with self._immediate_transaction():
            self._connection.execute("DELETE FROM product_surface_state")
            self._connection.execute("DELETE FROM unit_product")
            self._connection.execute("DELETE FROM observed_product")
            self._connection.execute("DELETE FROM unit")
            self._connection.execute("DELETE FROM company")
            self._connection.execute("DELETE FROM replayed_session")
            self._connection.execute("DELETE FROM projection_meta")
            self._connection.executemany(
                """
                INSERT INTO replayed_session(
                    session_id, manifest_sha256, evidence_sha256,
                    started_at, ended_at, status,
                    event_count, last_sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        item.session_id,
                        item.manifest_sha256,
                        item.evidence_sha256,
                        item.started_at,
                        item.ended_at,
                        item.status,
                        item.event_count,
                        item.last_sequence,
                    )
                    for item in snapshot.sessions
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO company(
                    company_id, name, source_session_id,
                    source_sequence, observed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        item.company_id,
                        item.name,
                        item.source_session_id,
                        item.source_sequence,
                        item.observed_at,
                    )
                    for item in snapshot.companies
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO unit(
                    unit_id, company_id, display_name, city_name, level,
                    source_session_id, source_sequence, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        item.unit_id,
                        item.company_id,
                        item.display_name,
                        item.city_name,
                        item.level,
                        item.source_session_id,
                        item.source_sequence,
                        item.observed_at,
                    )
                    for item in snapshot.units
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO observed_product(
                    product_numeric_id, catalog_key, resolution
                ) VALUES (?, ?, ?)
                """,
                (
                    (
                        item.product_numeric_id,
                        item.catalog_key,
                        item.resolution,
                    )
                    for item in snapshot.products
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO unit_product(
                    unit_id, product_numeric_id, revenue, profit,
                    stock_qty, stock_quality, our_price, city_quality,
                    city_price, sales_volume, supply_qty, supply_cost,
                    source_session_id, source_sequence, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        item.unit_id,
                        item.product_numeric_id,
                        item.revenue,
                        item.profit,
                        item.stock_qty,
                        item.stock_quality,
                        item.our_price,
                        item.city_quality,
                        item.city_price,
                        item.sales_volume,
                        item.supply_qty,
                        item.supply_cost,
                        item.source_session_id,
                        item.source_sequence,
                        item.observed_at,
                    )
                    for item in snapshot.unit_products
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO product_surface_state(
                    unit_id, surface, status, stale_reason,
                    source_session_id, source_sequence, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        item.unit_id,
                        item.surface,
                        item.status,
                        item.stale_reason,
                        item.source_session_id,
                        item.source_sequence,
                        item.observed_at,
                    )
                    for item in snapshot.surfaces
                ),
            )
            self._connection.execute(
                """
                INSERT INTO projection_meta(
                    singleton, projection_name, projection_version,
                    analysis_profile_sha256, unit_economics_contract,
                    catalog_resolver_sha256, input_fingerprint,
                    state_fingerprint, status, stale_reason,
                    session_count, last_session_id, last_sequence
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meta.projection_name,
                    meta.projection_version,
                    meta.analysis_profile_sha256,
                    meta.unit_economics_contract,
                    meta.catalog_resolver_sha256,
                    meta.input_fingerprint,
                    meta.state_fingerprint,
                    meta.status,
                    meta.stale_reason,
                    meta.session_count,
                    meta.last_session_id,
                    meta.last_sequence,
                ),
            )
            try:
                persisted = self._snapshot_unchecked()
            except (TypeError, ValueError) as exc:
                raise CurrentStateIntegrityError(
                    "persisted Current State violates semantic invariants"
                ) from exc
            if persisted != snapshot:
                raise CurrentStateIntegrityError(
                    "persisted Current State does not match requested snapshot"
                )

    def mark_stale(self, reason: str) -> CurrentStateSnapshot:
        if not isinstance(reason, str) or not reason:
            raise ValueError("stale reason must be a non-empty string")
        current = self.snapshot()
        if current is None:
            raise CurrentStateIntegrityError(
                "cannot mark an unbuilt Current State stale"
            )
        spec = CurrentProjectionSpec(
            projection_name=current.metadata.projection_name,
            projection_version=current.metadata.projection_version,
            analysis_profile_sha256=current.metadata.analysis_profile_sha256,
            unit_economics_contract=current.metadata.unit_economics_contract,
            catalog_resolver_sha256=current.metadata.catalog_resolver_sha256,
        )
        stale = build_current_snapshot(
            spec,
            current.sessions,
            companies=current.companies,
            units=current.units,
            products=current.products,
            unit_products=current.unit_products,
            surfaces=current.surfaces,
            status="stale",
            stale_reason=reason,
        )
        self.replace_snapshot(stale)
        return stale


__all__ = [
    "APPLICATION_ID",
    "USER_VERSION",
    "CurrentStateCompatibilityError",
    "CurrentStateError",
    "CurrentStateIntegrityError",
    "CurrentStateOperationError",
    "CurrentStateStore",
]
