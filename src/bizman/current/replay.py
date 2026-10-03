from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Mapping

from bizman.changes.profile import compile_default_analysis_profile
from bizman.current.company_units import (
    CompanyRosterProjection,
    CompanyUnitsArtifactError,
    CompanyUnitsParserIncompatible,
    project_company_roster_event,
)
from bizman.current.model import (
    PROJECTION_NAME,
    PROJECTION_VERSION,
    CompanyState,
    CurrentProjectionSpec,
    CurrentStateSnapshot,
    ObservedProduct,
    ProductSurfaceState,
    ReplaySession,
    UnitProductState,
    UnitState,
    build_current_snapshot,
)
from bizman.current.products import (
    SURFACE,
    CatalogResolver,
    UnitEconomicsArtifactError,
    UnitEconomicsParserIncompatible,
    is_unit_goods_event,
    project_unit_economics_event,
)
from bizman.current.state import (
    APPLICATION_ID,
    CurrentStateIntegrityError,
    CurrentStateOperationError,
    CurrentStateStore,
)
from bizman.foundation.redaction import RedactionPolicy
from bizman.foundation.unit_economics import unit_economics_semantic_fingerprint
from bizman.sessions.evidence import EvidenceIdentity, EvidenceReader


_PROJECTION_META_COLUMNS = (
    "singleton",
    "projection_name",
    "projection_version",
    "analysis_profile_sha256",
    "input_fingerprint",
    "state_fingerprint",
    "status",
    "stale_reason",
    "session_count",
    "last_session_id",
    "last_sequence",
)
_REPLAYED_SESSION_COLUMNS = (
    "session_id",
    "manifest_sha256",
    "evidence_sha256",
    "started_at",
    "ended_at",
    "status",
    "event_count",
    "last_sequence",
)
_V1_TABLES = frozenset({"projection_meta", "replayed_session"})
_V1_COLUMNS = {
    "projection_meta": _PROJECTION_META_COLUMNS,
    "replayed_session": _REPLAYED_SESSION_COLUMNS,
}
_V2_TABLES = frozenset(
    {"projection_meta", "replayed_session", "company", "unit"}
)
_V2_COLUMNS = {
    "projection_meta": _PROJECTION_META_COLUMNS,
    "replayed_session": _REPLAYED_SESSION_COLUMNS,
    "company": (
        "company_id",
        "name",
        "source_session_id",
        "source_sequence",
        "observed_at",
    ),
    "unit": (
        "unit_id",
        "company_id",
        "display_name",
        "city_name",
        "level",
        "source_session_id",
        "source_sequence",
        "observed_at",
    ),
}


def _instant(value: str) -> datetime:
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise CurrentStateIntegrityError(
            "evidence timestamp is not valid RFC3339"
        ) from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise CurrentStateIntegrityError(
            "evidence timestamp must include timezone"
        )
    return instant.astimezone(UTC)


def _ordered_identities(
    reader: EvidenceReader,
) -> tuple[EvidenceIdentity, ...]:
    identities = tuple(reader.iter_finalized())
    for identity in identities:
        if not isinstance(identity, EvidenceIdentity):
            raise TypeError("EvidenceReader must yield EvidenceIdentity values")
    return tuple(
        sorted(
            identities,
            key=lambda item: (
                _instant(item.started_at),
                item.session_id,
            ),
        )
    )


def _verified_event_sequence(event: Mapping[str, object], expected: int) -> int:
    sequence = event.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise TypeError("validated evidence sequence must be an integer")
    if sequence != expected:
        raise CurrentStateIntegrityError(
            "validated evidence sequence is not contiguous"
        )
    return sequence


def _roster_projection_or_stale(
    reader: EvidenceReader,
    event: Mapping[str, object],
) -> tuple[CompanyRosterProjection | None, bool]:
    try:
        return project_company_roster_event(reader, event), False
    except CompanyUnitsParserIncompatible:
        return None, True
    except CompanyUnitsArtifactError as exc:
        raise CurrentStateIntegrityError(
            "company/unit evidence artifact violates Current State contract"
        ) from exc


def _apply_goods_event(
    reader: EvidenceReader,
    event: Mapping[str, object],
    *,
    observations: dict[tuple[str, int], UnitProductState],
    surfaces: dict[str, ProductSurfaceState],
    observed_ids: set[int],
) -> None:
    if not is_unit_goods_event(event):
        return
    try:
        projection = project_unit_economics_event(reader, event)
    except UnitEconomicsParserIncompatible as exc:
        if (
            exc.unit_id is None
            or exc.session_id is None
            or exc.sequence is None
            or exc.observed_at is None
        ):
            raise CurrentStateIntegrityError(
                "unit economics incompatibility lacks surface context"
            ) from exc
        surfaces[exc.unit_id] = ProductSurfaceState(
            unit_id=exc.unit_id,
            surface=SURFACE,
            status="stale",
            stale_reason=exc.reason,
            source_session_id=exc.session_id,
            source_sequence=exc.sequence,
            observed_at=exc.observed_at,
        )
        return
    except UnitEconomicsArtifactError as exc:
        raise CurrentStateIntegrityError(
            "unit economics evidence artifact violates Current State contract"
        ) from exc
    if projection is None:
        return

    for row in projection.page.rows:
        observed_ids.add(row.product_numeric_id)
        observations[(projection.unit_id, row.product_numeric_id)] = (
            UnitProductState(
                unit_id=projection.unit_id,
                product_numeric_id=row.product_numeric_id,
                revenue=row.revenue,
                profit=row.profit,
                stock_qty=row.stock_qty,
                stock_quality=row.stock_quality,
                our_price=row.our_price,
                city_quality=row.city_quality,
                city_price=row.city_price,
                sales_volume=row.sales_volume,
                supply_qty=row.supply_qty,
                supply_cost=row.supply_cost,
                source_session_id=projection.session_id,
                source_sequence=projection.sequence,
                observed_at=projection.observed_at,
            )
        )
    surfaces[projection.unit_id] = ProductSurfaceState(
        unit_id=projection.unit_id,
        surface=SURFACE,
        status="ready",
        stale_reason=None,
        source_session_id=projection.session_id,
        source_sequence=projection.sequence,
        observed_at=projection.observed_at,
    )


def _replay_session(
    reader: EvidenceReader,
    identity: EvidenceIdentity,
    *,
    companies: dict[str, CompanyState],
    units: dict[str, UnitState],
    observations: dict[tuple[str, int], UnitProductState],
    surfaces: dict[str, ProductSurfaceState],
    observed_ids: set[int],
) -> tuple[ReplaySession, bool]:
    event_count = 0
    last_sequence: int | None = None
    parser_incompatible = False
    for event in reader.iter_events(
        identity.session_id,
        expected_identity=identity,
    ):
        if not isinstance(event, Mapping):
            raise TypeError("EvidenceReader event must be a mapping")
        sequence = _verified_event_sequence(event, event_count)
        projection, incompatible = _roster_projection_or_stale(reader, event)
        parser_incompatible = parser_incompatible or incompatible

        if projection is not None:
            companies[projection.company.company_id] = projection.company
            for unit in projection.units:
                units[unit.unit_id] = unit

        _apply_goods_event(
            reader,
            event,
            observations=observations,
            surfaces=surfaces,
            observed_ids=observed_ids,
        )

        event_count += 1
        last_sequence = sequence

    try:
        replay = ReplaySession(
            started_at=identity.started_at,
            session_id=identity.session_id,
            manifest_sha256=identity.manifest_sha256,
            evidence_sha256=identity.evidence_sha256,
            ended_at=identity.ended_at,
            status=identity.status,
            event_count=event_count,
            last_sequence=last_sequence,
        )
    except ValueError as exc:
        raise CurrentStateIntegrityError(
            "evidence identity is incompatible with Current State replay"
        ) from exc
    return replay, parser_incompatible


def build_replay_snapshot(
    repo_root: Path,
    data_dir: Path,
    redaction: RedactionPolicy,
    *,
    projection_name: str = PROJECTION_NAME,
    projection_version: int = PROJECTION_VERSION,
) -> CurrentStateSnapshot:
    root = Path(repo_root).expanduser().resolve()
    data = Path(data_dir).expanduser().resolve(strict=False)
    if not isinstance(redaction, RedactionPolicy):
        raise TypeError("redaction must be RedactionPolicy")

    profile_compilation = compile_default_analysis_profile(root, redaction)
    catalog = CatalogResolver.load(root)
    spec = CurrentProjectionSpec(
        analysis_profile_sha256=profile_compilation.profile.sha256,
        unit_economics_contract=unit_economics_semantic_fingerprint(),
        catalog_resolver_sha256=catalog.semantic_fingerprint(),
        projection_name=projection_name,
        projection_version=projection_version,
    )
    reader = EvidenceReader(root, data)
    companies: dict[str, CompanyState] = {}
    units: dict[str, UnitState] = {}
    observations: dict[tuple[str, int], UnitProductState] = {}
    surfaces: dict[str, ProductSurfaceState] = {}
    observed_ids: set[int] = set()
    sessions: list[ReplaySession] = []
    parser_incompatible = False

    for identity in _ordered_identities(reader):
        replay, incompatible = _replay_session(
            reader,
            identity,
            companies=companies,
            units=units,
            observations=observations,
            surfaces=surfaces,
            observed_ids=observed_ids,
        )
        sessions.append(replay)
        parser_incompatible = parser_incompatible or incompatible

    products = tuple(
        ObservedProduct(
            product_numeric_id=numeric_id,
            catalog_key=catalog_key,
            resolution=resolution,
        )
        for numeric_id in sorted(observed_ids)
        for catalog_key, resolution in (catalog.resolve(numeric_id),)
    )
    # Orphan evidence (a unit absent from the verified P4-B roster) still
    # contributes observable product identity, but cannot become an FK-backed
    # unit association or surface row. It is skipped deterministically, never
    # joined by label and never treated as a deletion.
    unit_ids = set(units)
    unit_products = tuple(
        sorted(
            (
                value
                for value in observations.values()
                if value.unit_id in unit_ids
            ),
            key=lambda item: (item.unit_id, item.product_numeric_id),
        )
    )
    # Every verified unit has an explicit shop.goods coverage row. Absence of
    # goods evidence is UNKNOWN, not an implicit empty/known product roster.
    projected_surfaces = tuple(
        sorted(
            (
                surfaces.get(unit_id)
                or ProductSurfaceState(
                    unit_id=unit_id,
                    surface=SURFACE,
                    status="unknown",
                    stale_reason=None,
                    source_session_id=None,
                    source_sequence=None,
                    observed_at=None,
                )
                for unit_id in unit_ids
            ),
            key=lambda item: (item.unit_id, item.surface),
        )
    )

    return build_current_snapshot(
        spec,
        tuple(sessions),
        companies=tuple(
            sorted(companies.values(), key=lambda item: item.company_id)
        ),
        units=tuple(sorted(units.values(), key=lambda item: item.unit_id)),
        products=products,
        unit_products=unit_products,
        surfaces=projected_surfaces,
        status="stale" if parser_incompatible else "ready",
        stale_reason=(
            "company_units_parser_v1_incompatible"
            if parser_incompatible
            else None
        ),
    )


def _is_rebuildable_older_state(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        # Keep explicit SQL transactions compatible with Python 3.11 and 3.14.
        connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        try:
            application_id_row = connection.execute(
                "PRAGMA application_id"
            ).fetchone()
            user_version_row = connection.execute(
                "PRAGMA user_version"
            ).fetchone()
            if application_id_row is None or user_version_row is None:
                return False
            application_id_raw = next(iter(application_id_row), None)
            user_version_raw = next(iter(user_version_row), None)
            if application_id_raw is None or user_version_raw is None:
                return False
            application_id = int(application_id_raw)
            user_version = int(user_version_raw)
            if application_id != APPLICATION_ID:
                return False
            if user_version == 1:
                expected_tables = _V1_TABLES
                expected_columns = _V1_COLUMNS
            elif user_version == 2:
                expected_tables = _V2_TABLES
                expected_columns = _V2_COLUMNS
            else:
                return False
            tables = {
                str(name)
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if tables != expected_tables:
                return False
            strict = {
                str(name): int(is_strict)
                for name, is_strict in connection.execute(
                    "SELECT name, strict FROM pragma_table_list "
                    "WHERE name IN ("
                    + ", ".join("?" for _ in sorted(expected_tables))
                    + ")",
                    tuple(sorted(expected_tables)),
                )
            }
            if strict != {table: 1 for table in expected_tables}:
                return False
            for table in sorted(expected_tables):
                columns = tuple(
                    str(name)
                    for (name,) in connection.execute(
                        f"SELECT name FROM pragma_table_info('{table}')"
                    )
                )
                if columns != expected_columns[table]:
                    return False
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return True


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        directory_fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def _best_effort_remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _replace_rebuildable_older_state(
    path: Path,
    snapshot: CurrentStateSnapshot,
) -> CurrentStateSnapshot:
    older_sidecars = tuple(
        path.with_name(path.name + suffix)
        for suffix in ("-wal", "-shm")
    )
    if any(sidecar.exists() for sidecar in older_sidecars):
        raise CurrentStateOperationError(
            "cannot replace older Current State while SQLite sidecars exist"
        )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.p4c-",
            suffix=".sqlite3",
            dir=path.parent,
        )
        os.close(descriptor)
    except OSError as exc:
        raise CurrentStateOperationError(
            "cannot stage Current State schema replacement"
        ) from exc

    temporary = Path(temporary_name)
    try:
        with CurrentStateStore.open_rw(temporary) as store:
            store.replace_snapshot(snapshot)
            store.verify_storage_integrity()
            staged = store.snapshot()
        if staged != snapshot:
            raise CurrentStateIntegrityError(
                "staged Current State does not match requested snapshot"
            )

        try:
            # Windows requires a writable handle for fsync; the staged database
            # is fully closed and writable at this point.
            with temporary.open("rb+") as handle:
                os.fsync(handle.fileno())

            os.replace(temporary, path)
            _fsync_directory(path.parent)
        except OSError as exc:
            raise CurrentStateOperationError(
                "cannot atomically replace older Current State schema"
            ) from exc

        verified_store = CurrentStateStore.open_read_only_if_exists(path)
        if verified_store is None:
            raise CurrentStateIntegrityError(
                "Current State disappeared after schema replacement"
            )
        with verified_store:
            persisted = verified_store.snapshot()
        if persisted != snapshot:
            raise CurrentStateIntegrityError(
                "replaced Current State does not match requested snapshot"
            )
        return persisted
    finally:
        _best_effort_remove(temporary)
        _best_effort_remove(temporary.with_name(temporary.name + "-wal"))
        _best_effort_remove(temporary.with_name(temporary.name + "-shm"))


def rebuild_current_state(
    repo_root: Path,
    data_dir: Path,
    redaction: RedactionPolicy,
    *,
    projection_name: str = PROJECTION_NAME,
    projection_version: int = PROJECTION_VERSION,
) -> CurrentStateSnapshot:
    data = Path(data_dir).expanduser().resolve(strict=False)
    snapshot = build_replay_snapshot(
        repo_root,
        data,
        redaction,
        projection_name=projection_name,
        projection_version=projection_version,
    )
    state_path = data / "state" / "current.sqlite3"
    if _is_rebuildable_older_state(state_path):
        return _replace_rebuildable_older_state(state_path, snapshot)

    with CurrentStateStore.open_rw(state_path) as store:
        store.replace_snapshot(snapshot)
        persisted = store.snapshot()
    if persisted is None:
        raise RuntimeError("Current State disappeared after committed replay")
    return persisted


__all__ = [
    "build_replay_snapshot",
    "rebuild_current_state",
]
