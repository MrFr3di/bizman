from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bizman.collector.unit_economics import extract_unit_economics_payload
from bizman.current import (
    APPLICATION_ID,
    USER_VERSION,
    CatalogResolver,
    CatalogResolverError,
    CompanyState,
    CurrentProjectionSpec,
    CurrentStateIntegrityError,
    CurrentStateOperationError,
    CurrentStateStore,
    ObservedProduct,
    OrphanUnitProductObservation,
    ProductSurfaceState,
    ReplaySession,
    UnitProductState,
    UnitState,
    build_current_snapshot,
    build_replay_snapshot,
    catalog_resolver_sha256,
    is_unit_goods_event,
    rebuild_current_state,
)
from bizman.current.products import (
    PARSER_STALE_REASON,
    SURFACE,
)
from bizman.foundation.fingerprint import canonical_json_bytes
from bizman.foundation.redaction import load_redaction_policy
from bizman.foundation.unit_economics import (
    CONTRACT_VERSION,
    RESPONSE_BODY_KIND,
    SCHEMA,
    parse_unit_economics_payload,
    unit_economics_payload,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_A = "01991c7d-a400-7000-8000-000000000511"
SESSION_B = "01991c7d-a400-7000-8000-000000000512"
EVENT_A0 = "01991c7d-a400-7000-8000-000000000611"
EVENT_A1 = "01991c7d-a400-7000-8000-000000000612"
EVENT_A2 = "01991c7d-a400-7000-8000-000000000613"
EVENT_B0 = "01991c7d-a400-7000-8000-000000000614"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
UNIT_ID = "33670"
UNIT_ID_2 = "33676"
COMPANY_ID = "13393"
PRODUCT_1 = 880001
PRODUCT_2 = 880002
PRODUCT_3 = 880003
V4_TABLES = {
    "projection_meta",
    "replayed_session",
    "company",
    "unit",
    "observed_product",
    "unit_product",
    "orphan_unit_product",
    "product_surface_state",
}
_ROSTER_TEXT = (
    "Компания Paradise\nКомпания\nПредприятия\n"
    "Город\nПредприятие\nУровень\nЭффект.\n"
    "Анкара\nДетский магазин #33670\n1\n"
    "Анкара\nАптека #33676\n1"
)


def _redaction():
    return load_redaction_policy(REPO_ROOT / "config" / "redaction-policy.json")


def _put_artifact(data_dir: Path, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    path = data_dir / "artifacts" / "sha256" / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return f"sha256:{digest}"


def _roster_artifact(text: str = _ROSTER_TEXT) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": "1.0",
            "sanitizer_version": 2,
            "media_type": "text/html",
            "title": "Компания Paradise · Предприятия",
            "text": text,
        }
    )


def _roster_event(
    session_id: str,
    sequence: int,
    event_id: str,
    ref: str,
    *,
    observed_at: str,
    page: str = "1",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "session_id": session_id,
        "sequence": sequence,
        "observed_at": observed_at,
        "monotonic_time": float(sequence),
        "source": "cdp.network",
        "event_type": "http.response_body",
        "confidence": "observed",
        "request_id": f"r-{event_id[-4:]}",
        "method": "GET",
        "url_path": "/company/",
        "status_code": 200,
        "query": {"id": [COMPANY_ID], "tab": ["units"], "p": [page]},
        "response_body_ref": ref,
    }


def _typed_row(product: int, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "product_numeric_id": product,
        "revenue": 1000,
        "profit": 100,
        "stock_qty": 10,
        "stock_quality": 3.5,
        "our_price": 150,
        "city_quality": 4.25,
        "city_price": 160,
        "sales_volume": 12,
        "supply_qty": 7,
        "supply_cost": 120,
    }
    value.update(overrides)
    return value


def _goods_artifact(
    unit_id: str,
    rows: list[dict[str, object]],
    *,
    schema: str = SCHEMA,
    contract_version: object = CONTRACT_VERSION,
) -> bytes:
    return canonical_json_bytes(
        {
            "schema": schema,
            "contract_version": contract_version,
            "unit_id": unit_id,
            "coverage": "observed",
            "rows": rows,
        }
    )


def _goods_event(
    session_id: str,
    sequence: int,
    event_id: str,
    ref: str | None,
    *,
    observed_at: str,
    unit_id: str = UNIT_ID,
    kind: object = RESPONSE_BODY_KIND,
    include_kind: bool = True,
) -> dict[str, object]:
    event: dict[str, object] = {
        "schema_version": "1.0",
        "event_id": event_id,
        "session_id": session_id,
        "sequence": sequence,
        "observed_at": observed_at,
        "monotonic_time": float(sequence),
        "source": "cdp.network",
        "event_type": "http.response_body",
        "confidence": "observed",
        "request_id": f"g-{event_id[-4:]}",
        "method": "GET",
        "url_path": "/units/shop/",
        "status_code": 200,
        "query": {"id": [unit_id], "tab": ["goods"]},
        "response_body_ref": ref,
    }
    if include_kind:
        event["response_body_kind"] = kind
    return event


def _write_session(
    data_dir: Path,
    *,
    session_id: str,
    started_at: str,
    ended_at: str,
    events: list[dict[str, object]],
) -> None:
    event_rel = f"events/2026-09-30/{session_id}.jsonl"
    event_path = data_dir / event_rel
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text(
        "".join(
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for item in events
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0",
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": "completed",
        "collector": {"name": "bizman-cdp", "version": "0.test"},
        "browser": {"product": "Chrome/Test", "version": "1"},
        "protocol": {
            "name": "cdp",
            "version": "1.3",
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": [event_rel],
        "artifact_count": 2,
        "warnings": [],
    }
    manifest_path = data_dir / "sessions" / session_id / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _session_record(
    session_id: str = SESSION_A,
    *,
    started_at: str = "2026-09-30T10:00:00Z",
    event_count: int = 1,
) -> ReplaySession:
    return ReplaySession(
        session_id=session_id,
        manifest_sha256=SHA_A,
        evidence_sha256=SHA_B,
        started_at=started_at,
        ended_at="2026-09-30T10:01:00Z",
        status="completed",
        event_count=event_count,
        last_sequence=event_count - 1 if event_count else None,
    )


def _company() -> CompanyState:
    return CompanyState(
        company_id=COMPANY_ID,
        name="Paradise",
        source_session_id=SESSION_A,
        source_sequence=0,
        observed_at="2026-09-30T10:00:30Z",
    )


def _unit(unit_id: str = UNIT_ID) -> UnitState:
    return UnitState(
        unit_id=unit_id,
        company_id=COMPANY_ID,
        display_name="Детский магазин",
        city_name="Анкара",
        level=1,
        source_session_id=SESSION_A,
        source_sequence=0,
        observed_at="2026-09-30T10:00:30Z",
    )


def _unit_product(product: int = PRODUCT_1) -> UnitProductState:
    return UnitProductState(
        unit_id=UNIT_ID,
        product_numeric_id=product,
        revenue=1000,
        profit=100,
        stock_qty=10,
        stock_quality=3.5,
        our_price=150,
        city_quality=4.25,
        city_price=160,
        sales_volume=12,
        supply_qty=7,
        supply_cost=120,
        source_session_id=SESSION_A,
        source_sequence=0,
        observed_at="2026-09-30T10:00:30Z",
    )


def _spec(
    *,
    contract: str = "unit-economics-contract-fixture",
    catalog: str = SHA_D,
    profile: str = SHA_C,
) -> CurrentProjectionSpec:
    return CurrentProjectionSpec(
        analysis_profile_sha256=profile,
        unit_economics_contract=contract,
        catalog_resolver_sha256=catalog,
    )


def _write_v2_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 2")
        connection.executescript(
            """
            CREATE TABLE projection_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                projection_name TEXT NOT NULL,
                projection_version INTEGER NOT NULL
                    CHECK (projection_version > 0),
                analysis_profile_sha256 TEXT NOT NULL,
                input_fingerprint TEXT NOT NULL,
                state_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('ready', 'stale')),
                stale_reason TEXT,
                session_count INTEGER NOT NULL CHECK (session_count >= 0),
                last_session_id TEXT,
                last_sequence INTEGER
                    CHECK (last_sequence IS NULL OR last_sequence >= 0)
            ) STRICT;
            CREATE TABLE replayed_session (
                session_id TEXT PRIMARY KEY,
                manifest_sha256 TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('completed', 'cancelled')),
                event_count INTEGER NOT NULL CHECK (event_count >= 0),
                last_sequence INTEGER
                    CHECK (last_sequence IS NULL OR last_sequence >= 0)
            ) STRICT;
            CREATE INDEX replayed_session_order_idx
            ON replayed_session(started_at, session_id);
            CREATE TABLE company (
                company_id TEXT PRIMARY KEY,
                name TEXT NOT NULL CHECK (length(name) > 0),
                source_session_id TEXT NOT NULL,
                source_sequence INTEGER NOT NULL CHECK (source_sequence >= 0),
                observed_at TEXT NOT NULL,
                FOREIGN KEY (source_session_id)
                    REFERENCES replayed_session(session_id)
            ) STRICT;
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
            ) STRICT;
            CREATE INDEX unit_company_idx ON unit(company_id, unit_id);
            """
        )
        connection.commit()
    finally:
        connection.close()


def _user_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def _table_names(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(name)
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()


def _real_unique_catalog_mapping() -> tuple[int, str]:
    directory = REPO_ROOT / "knowledge" / "domain" / "products"
    index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
    claims: dict[int, set[str]] = {}
    for part in index["parts"]:
        data = json.loads(
            (directory / part["path"]).read_text(encoding="utf-8")
        )
        for item in data["items"]:
            for numeric_id in item["numeric_ids"]:
                claims.setdefault(int(numeric_id), set()).add(str(item["id"]))
    unique = sorted(
        (numeric_id, next(iter(keys)))
        for numeric_id, keys in claims.items()
        if len(keys) == 1
    )
    return unique[0]


def _write_temp_catalog(root: Path, items: list[dict[str, object]]) -> Path:
    directory = root / "knowledge" / "domain" / "products"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.json").write_text(
        json.dumps({"parts": [{"path": "part-000.json"}]}),
        encoding="utf-8",
    )
    (directory / "part-000.json").write_text(
        json.dumps({"items": items}),
        encoding="utf-8",
    )
    return directory


class GoodsEventRecognitionTests(unittest.TestCase):
    def test_event_requires_exact_route_and_kind_marker(self):
        base = _goods_event(
            SESSION_A,
            0,
            EVENT_A0,
            "sha256:" + "a" * 64,
            observed_at="2026-09-30T10:00:30Z",
        )
        self.assertTrue(is_unit_goods_event(base))

        without_kind = json.loads(json.dumps(base))
        del without_kind["response_body_kind"]
        self.assertFalse(is_unit_goods_event(without_kind))

        foreign_kind = json.loads(json.dumps(base))
        foreign_kind["response_body_kind"] = "unit-economics.v2"
        self.assertFalse(is_unit_goods_event(foreign_kind))

        extra_query = json.loads(json.dumps(base))
        extra_query["query"]["p"] = ["2"]
        self.assertFalse(is_unit_goods_event(extra_query))

        supply_surface = json.loads(json.dumps(base))
        supply_surface["query"]["tab"] = ["supply"]
        self.assertFalse(is_unit_goods_event(supply_surface))

        non_200 = json.loads(json.dumps(base))
        non_200["status_code"] = 500
        self.assertFalse(is_unit_goods_event(non_200))

    def test_markerless_goods_event_is_not_interpreted_by_url_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            ref = _put_artifact(
                data_dir,
                _goods_artifact(UNIT_ID, [_typed_row(PRODUCT_1)]),
            )
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-30T10:00:00Z",
                ended_at="2026-09-30T10:01:00Z",
                events=[
                    _goods_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        ref,
                        observed_at="2026-09-30T10:00:30Z",
                        include_kind=False,
                    )
                ],
            )
            snapshot = build_replay_snapshot(REPO_ROOT, data_dir, _redaction())

        self.assertEqual(snapshot.products, ())
        self.assertEqual(snapshot.unit_products, ())
        self.assertEqual(snapshot.surfaces, ())
        self.assertEqual(len(snapshot.orphan_unit_products), 1)
        orphan = snapshot.orphan_unit_products[0]
        self.assertEqual(orphan.unit_id, UNIT_ID)
        self.assertEqual(orphan.product_numeric_id, PRODUCT_1)
        self.assertEqual(orphan.surface, SURFACE)
        self.assertEqual(orphan.source_session_id, SESSION_A)
        self.assertEqual(orphan.source_sequence, 0)
        self.assertEqual(orphan.observed_at, "2026-09-30T10:00:30Z")
        self.assertEqual(orphan.artifact_sha256, ref.split(":", 1)[1])
        self.assertEqual(orphan.artifact_schema, SCHEMA)
        self.assertEqual(orphan.reason, "unit_not_in_verified_roster")
        self.assertEqual(snapshot.metadata.status, "ready")


class GoodsReplayTests(unittest.TestCase):
    def test_latest_positive_rows_win_without_deleting_omitted_products(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            roster_ref = _put_artifact(data_dir, _roster_artifact())
            first_goods_ref = _put_artifact(
                data_dir,
                _goods_artifact(
                    UNIT_ID,
                    [
                        _typed_row(PRODUCT_1, revenue=1111, profit=111),
                        _typed_row(PRODUCT_2, revenue=2222, profit=222),
                    ],
                ),
            )
            second_goods_ref = _put_artifact(
                data_dir,
                _goods_artifact(
                    UNIT_ID,
                    [_typed_row(PRODUCT_1, revenue=3333, profit=333)],
                ),
            )
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-30T10:00:00Z",
                ended_at="2026-09-30T10:01:00Z",
                events=[
                    _roster_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        roster_ref,
                        observed_at="2026-09-30T10:00:10Z",
                    ),
                    _goods_event(
                        SESSION_A,
                        1,
                        EVENT_A1,
                        first_goods_ref,
                        observed_at="2026-09-30T10:00:30Z",
                    ),
                ],
            )
            _write_session(
                data_dir,
                session_id=SESSION_B,
                started_at="2026-09-30T11:00:00Z",
                ended_at="2026-09-30T11:01:00Z",
                events=[
                    _goods_event(
                        SESSION_B,
                        0,
                        EVENT_B0,
                        second_goods_ref,
                        observed_at="2026-09-30T11:00:30Z",
                    )
                ],
            )

            snapshot = build_replay_snapshot(REPO_ROOT, data_dir, _redaction())

        self.assertEqual(snapshot.metadata.status, "ready")
        self.assertEqual(
            [
                (item.unit_id, item.product_numeric_id)
                for item in snapshot.unit_products
            ],
            [(UNIT_ID, PRODUCT_1), (UNIT_ID, PRODUCT_2)],
        )
        by_product = {
            item.product_numeric_id: item for item in snapshot.unit_products
        }
        self.assertEqual(by_product[PRODUCT_1].revenue, 3333)
        self.assertEqual(by_product[PRODUCT_1].profit, 333)
        self.assertEqual(by_product[PRODUCT_1].source_session_id, SESSION_B)
        self.assertEqual(
            by_product[PRODUCT_1].source_sequence,
            0,
        )
        self.assertEqual(by_product[PRODUCT_2].revenue, 2222)
        self.assertEqual(by_product[PRODUCT_2].source_session_id, SESSION_A)

        self.assertEqual(len(snapshot.surfaces), 2)
        surfaces = {
            (item.unit_id, item.surface): item for item in snapshot.surfaces
        }
        surface = surfaces[(UNIT_ID, SURFACE)]
        self.assertEqual(surface.status, "ready")
        self.assertEqual(surface.source_session_id, SESSION_B)
        self.assertIsNone(surface.stale_reason)
        self.assertEqual(surfaces[(UNIT_ID_2, SURFACE)].status, "unknown")

        self.assertEqual(
            [item.product_numeric_id for item in snapshot.products],
            [PRODUCT_1, PRODUCT_2],
        )
        for product in snapshot.products:
            self.assertEqual(product.resolution, "unresolved")
            self.assertIsNone(product.catalog_key)

    def test_parser_drift_marks_only_that_unit_surface_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            roster_ref = _put_artifact(data_dir, _roster_artifact())
            good_ref = _put_artifact(
                data_dir,
                _goods_artifact(UNIT_ID, [_typed_row(PRODUCT_1)]),
            )
            drifted_ref = _put_artifact(
                data_dir,
                _goods_artifact(
                    UNIT_ID_2,
                    [_typed_row(PRODUCT_2)],
                    contract_version=CONTRACT_VERSION + 1,
                ),
            )
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-30T10:00:00Z",
                ended_at="2026-09-30T10:01:00Z",
                events=[
                    _roster_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        roster_ref,
                        observed_at="2026-09-30T10:00:10Z",
                    ),
                    _goods_event(
                        SESSION_A,
                        1,
                        EVENT_A1,
                        good_ref,
                        observed_at="2026-09-30T10:00:30Z",
                    ),
                    _goods_event(
                        SESSION_A,
                        2,
                        EVENT_A2,
                        drifted_ref,
                        observed_at="2026-09-30T10:00:40Z",
                        unit_id=UNIT_ID_2,
                    ),
                ],
            )

            snapshot = build_replay_snapshot(REPO_ROOT, data_dir, _redaction())

        self.assertEqual(snapshot.metadata.status, "ready")
        self.assertEqual(snapshot.metadata.stale_reason, None)
        surfaces = {
            (item.unit_id, item.surface): item for item in snapshot.surfaces
        }
        self.assertEqual(surfaces[(UNIT_ID, SURFACE)].status, "ready")
        drifted = surfaces[(UNIT_ID_2, SURFACE)]
        self.assertEqual(drifted.status, "stale")
        self.assertEqual(drifted.stale_reason, PARSER_STALE_REASON)
        self.assertEqual(drifted.source_session_id, SESSION_A)
        self.assertEqual(
            [item.product_numeric_id for item in snapshot.unit_products],
            [PRODUCT_1],
        )

    def test_orphan_goods_page_keeps_observed_product_but_no_association(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            ref = _put_artifact(
                data_dir,
                _goods_artifact(UNIT_ID, [_typed_row(PRODUCT_1)]),
            )
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-30T10:00:00Z",
                ended_at="2026-09-30T10:01:00Z",
                events=[
                    _goods_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        ref,
                        observed_at="2026-09-30T10:00:30Z",
                    )
                ],
            )
            snapshot = build_replay_snapshot(REPO_ROOT, data_dir, _redaction())

        self.assertEqual(snapshot.units, ())
        self.assertEqual(
            [item.product_numeric_id for item in snapshot.products],
            [PRODUCT_1],
        )
        self.assertEqual(snapshot.unit_products, ())
        self.assertEqual(snapshot.surfaces, ())
        self.assertEqual(snapshot.metadata.status, "ready")

    def test_malformed_artifact_fails_replay_as_integrity_error(self):
        malformed = (
            b"this is not json",
            canonical_json_bytes(
                {
                    "schema": SCHEMA,
                    "contract_version": CONTRACT_VERSION,
                    "unit_id": UNIT_ID,
                    "coverage": "observed",
                    "rows": [{"product_numeric_id": PRODUCT_1}],
                }
            ),
        )
        for payload in malformed:
            with self.subTest(payload=payload[:32]), tempfile.TemporaryDirectory() as tmp:
                data_dir = Path(tmp) / "BizManData"
                ref = _put_artifact(data_dir, payload)
                _write_session(
                    data_dir,
                    session_id=SESSION_A,
                    started_at="2026-09-30T10:00:00Z",
                    ended_at="2026-09-30T10:01:00Z",
                    events=[
                        _goods_event(
                            SESSION_A,
                            0,
                            EVENT_A0,
                            ref,
                            observed_at="2026-09-30T10:00:30Z",
                        )
                    ],
                )
                with self.assertRaises(CurrentStateIntegrityError):
                    build_replay_snapshot(REPO_ROOT, data_dir, _redaction())

    def test_missing_goods_evidence_materializes_explicit_unknown_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            roster_ref = _put_artifact(data_dir, _roster_artifact())
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-30T10:00:00Z",
                ended_at="2026-09-30T10:01:00Z",
                events=[
                    _roster_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        roster_ref,
                        observed_at="2026-09-30T10:00:10Z",
                    )
                ],
            )
            snapshot = build_replay_snapshot(REPO_ROOT, data_dir, _redaction())

        self.assertEqual(len(snapshot.units), 2)
        self.assertEqual(snapshot.unit_products, ())
        self.assertEqual(len(snapshot.surfaces), 2)
        self.assertEqual(
            {
                (item.unit_id, item.surface, item.status)
                for item in snapshot.surfaces
            },
            {
                (UNIT_ID, SURFACE, "unknown"),
                (UNIT_ID_2, SURFACE, "unknown"),
            },
        )
        for surface in snapshot.surfaces:
            self.assertIsNone(surface.stale_reason)
            self.assertIsNone(surface.source_session_id)
            self.assertIsNone(surface.source_sequence)
            self.assertIsNone(surface.observed_at)

    def test_delete_and_replay_produce_identical_product_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            roster_ref = _put_artifact(data_dir, _roster_artifact())
            goods_ref = _put_artifact(
                data_dir,
                _goods_artifact(
                    UNIT_ID,
                    [_typed_row(PRODUCT_1), _typed_row(PRODUCT_2)],
                ),
            )
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-30T10:00:00Z",
                ended_at="2026-09-30T10:01:00Z",
                events=[
                    _roster_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        roster_ref,
                        observed_at="2026-09-30T10:00:10Z",
                    ),
                    _goods_event(
                        SESSION_A,
                        1,
                        EVENT_A1,
                        goods_ref,
                        observed_at="2026-09-30T10:00:30Z",
                    ),
                ],
            )

            first = rebuild_current_state(REPO_ROOT, data_dir, _redaction())
            state_path = data_dir / "state" / "current.sqlite3"
            state_path.unlink()
            state_path.with_name(state_path.name + "-wal").unlink(missing_ok=True)
            state_path.with_name(state_path.name + "-shm").unlink(missing_ok=True)

            second = rebuild_current_state(REPO_ROOT, data_dir, _redaction())

        self.assertEqual(first, second)
        self.assertEqual(len(first.unit_products), 2)
        self.assertEqual(len(first.products), 2)
        self.assertEqual(len(first.surfaces), 2)
        self.assertEqual(
            {
                (item.unit_id, item.status) for item in first.surfaces
            },
            {
                (UNIT_ID, "ready"),
                (UNIT_ID_2, "unknown"),
            },
        )

    def test_privacy_canary_never_reaches_artifact_or_database(self):
        canary_label = "SYNTHETIC_LABEL_CANARY_99"
        canary_attribute = "SYNTHETIC_ATTRIBUTE_CANARY_99"
        html = _canary_goods_html(canary_label, canary_attribute)
        payload = extract_unit_economics_payload(
            html.encode("utf-8"),
            unit_id=UNIT_ID,
        )
        parsed = parse_unit_economics_payload(payload)
        self.assertEqual(unit_economics_payload(parsed), payload)

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            roster_ref = _put_artifact(data_dir, _roster_artifact())
            goods_ref = _put_artifact(data_dir, payload)
            _write_session(
                data_dir,
                session_id=SESSION_A,
                started_at="2026-09-30T10:00:00Z",
                ended_at="2026-09-30T10:01:00Z",
                events=[
                    _roster_event(
                        SESSION_A,
                        0,
                        EVENT_A0,
                        roster_ref,
                        observed_at="2026-09-30T10:00:10Z",
                    ),
                    _goods_event(
                        SESSION_A,
                        1,
                        EVENT_A1,
                        goods_ref,
                        observed_at="2026-09-30T10:00:30Z",
                    ),
                ],
            )
            snapshot = rebuild_current_state(REPO_ROOT, data_dir, _redaction())
            state_bytes = (data_dir / "state" / "current.sqlite3").read_bytes()

        candidates = (
            canary_label.encode(),
            canary_attribute.encode(),
            b"<table",
            b"purchaseQuantity",
            b"vendorPrice",
        )
        for blob, label in (
            (payload, "artifact"),
            (state_bytes, "database"),
        ):
            for candidate in candidates:
                self.assertNotIn(candidate, blob, msg=label)
        self.assertEqual(len(snapshot.unit_products), 1)
        self.assertEqual(snapshot.unit_products[0].supply_cost, 120)


class ProductFingerprintTests(unittest.TestCase):
    def test_contract_and_catalog_identity_enter_input_and_state_fingerprints(self):
        sessions = (_session_record(),)
        base = build_current_snapshot(_spec(), sessions)
        other_contract = build_current_snapshot(
            _spec(contract="unit-economics-contract-other"),
            sessions,
        )
        other_catalog = build_current_snapshot(
            _spec(catalog=SHA_A),
            sessions,
        )

        for other in (other_contract, other_catalog):
            with self.subTest(other=other.metadata.input_fingerprint):
                self.assertNotEqual(
                    base.metadata.input_fingerprint,
                    other.metadata.input_fingerprint,
                )
                self.assertNotEqual(
                    base.metadata.state_fingerprint,
                    other.metadata.state_fingerprint,
                )

    def test_product_rows_participate_in_state_fingerprint_not_input(self):
        sessions = (_session_record(),)
        company = _company()
        unit = _unit()
        product = ObservedProduct(
            product_numeric_id=PRODUCT_1,
            catalog_key=None,
            resolution="unresolved",
        )
        without_rows = build_current_snapshot(
            _spec(),
            sessions,
            companies=(company,),
            units=(unit,),
        )
        with_rows = build_current_snapshot(
            _spec(),
            sessions,
            companies=(company,),
            units=(unit,),
            products=(product,),
            unit_products=(_unit_product(),),
        )

        self.assertEqual(
            without_rows.metadata.input_fingerprint,
            with_rows.metadata.input_fingerprint,
        )
        self.assertNotEqual(
            without_rows.metadata.state_fingerprint,
            with_rows.metadata.state_fingerprint,
        )

    def test_orphan_row_participates_in_state_fingerprint_not_input(self):
        sessions = (_session_record(),)
        product = ObservedProduct(
            product_numeric_id=PRODUCT_1,
            catalog_key=None,
            resolution="unresolved",
        )
        orphan = OrphanUnitProductObservation(
            unit_id=UNIT_ID,
            product_numeric_id=PRODUCT_1,
            surface=SURFACE,
            source_session_id=SESSION_A,
            source_sequence=0,
            observed_at="2026-09-30T10:00:30Z",
            artifact_sha256=SHA_A,
            artifact_schema=SCHEMA,
            reason="unit_not_in_verified_roster",
        )
        without_orphan = build_current_snapshot(
            _spec(),
            sessions,
            products=(product,),
        )
        with_orphan = build_current_snapshot(
            _spec(),
            sessions,
            products=(product,),
            orphan_unit_products=(orphan,),
        )

        self.assertEqual(
            without_orphan.metadata.input_fingerprint,
            with_orphan.metadata.input_fingerprint,
        )
        self.assertNotEqual(
            without_orphan.metadata.state_fingerprint,
            with_orphan.metadata.state_fingerprint,
        )

    def test_unknown_surface_row_participates_in_state_fingerprint(self):
        sessions = (_session_record(),)
        company = _company()
        unit = _unit()
        unknown = ProductSurfaceState(
            unit_id=UNIT_ID,
            surface=SURFACE,
            status="unknown",
            stale_reason=None,
            source_session_id=None,
            source_sequence=None,
            observed_at=None,
        )
        without_surface = build_current_snapshot(
            _spec(),
            sessions,
            companies=(company,),
            units=(unit,),
        )
        with_surface = build_current_snapshot(
            _spec(),
            sessions,
            companies=(company,),
            units=(unit,),
            surfaces=(unknown,),
        )

        self.assertEqual(
            without_surface.metadata.input_fingerprint,
            with_surface.metadata.input_fingerprint,
        )
        self.assertNotEqual(
            without_surface.metadata.state_fingerprint,
            with_surface.metadata.state_fingerprint,
        )


class CatalogResolverTests(unittest.TestCase):
    def test_real_catalog_mapping_resolves_deterministically(self):
        numeric_id, catalog_key = _real_unique_catalog_mapping()
        resolver = CatalogResolver.load(REPO_ROOT)
        self.assertEqual(
            resolver.resolve(numeric_id),
            (catalog_key, "resolved"),
        )
        self.assertEqual(resolver.resolve(999999999), (None, "unresolved"))
        self.assertEqual(
            catalog_resolver_sha256(REPO_ROOT),
            resolver.semantic_fingerprint(),
        )
        self.assertRegex(resolver.semantic_fingerprint(), r"^[0-9a-f]{64}$")

    def test_ambiguous_numeric_candidates_are_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_temp_catalog(
                root,
                [
                    {
                        "id": "bm.product.alpha",
                        "slug": "alpha",
                        "name": "Alpha",
                        "numeric_ids": [10],
                        "evidence": [],
                    },
                    {
                        "id": "bm.product.beta",
                        "slug": "beta",
                        "name": "Beta",
                        "numeric_ids": [10, 20],
                        "evidence": [],
                    },
                    {
                        "id": "bm.product.gamma",
                        "slug": "gamma",
                        "name": "Gamma",
                        "numeric_ids": [],
                        "evidence": [],
                    },
                ],
            )
            resolver = CatalogResolver.load(root)

        self.assertEqual(resolver.resolve(10), (None, "unresolved"))
        self.assertEqual(resolver.resolve(20), ("bm.product.beta", "resolved"))
        self.assertEqual(resolver.resolve(30), (None, "unresolved"))

    def test_semantic_fingerprint_ignores_names_order_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_temp_catalog(
                root,
                [
                    {
                        "id": "bm.product.alpha",
                        "slug": "alpha",
                        "name": "Alpha",
                        "numeric_ids": [10],
                        "evidence": ["src.a"],
                    }
                ],
            )
            first = CatalogResolver.load(root).semantic_fingerprint()

            directory = root / "knowledge" / "domain" / "products"
            (directory / "index.json").write_text(
                json.dumps({"parts": [{"path": "part-000.json"}]}),
                encoding="utf-8",
            )
            (directory / "part-000.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "bm.product.alpha",
                                "slug": "renamed",
                                "name": "Renamed",
                                "numeric_ids": [10],
                                "evidence": ["src.b", "src.c"],
                                "categories": ["x"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            second = CatalogResolver.load(root).semantic_fingerprint()

        self.assertEqual(first, second)

    def test_invalid_catalog_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(CatalogResolverError):
                CatalogResolver.load(root)

            _write_temp_catalog(
                root,
                [
                    {
                        "id": "bm.product.alpha",
                        "numeric_ids": [True],
                    }
                ],
            )
            with self.assertRaises(CatalogResolverError):
                CatalogResolver.load(root)

            _write_temp_catalog(
                root,
                [
                    {
                        "id": "not-a-catalog-key",
                        "numeric_ids": [10],
                    }
                ],
            )
            with self.assertRaises(CatalogResolverError):
                CatalogResolver.load(root)


class CurrentStateV3MigrationTests(unittest.TestCase):
    def test_v2_database_is_upgraded_to_v3_with_staged_swap(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            state_path = data_dir / "state" / "current.sqlite3"
            _write_v2_database(state_path)

            rebuilt = rebuild_current_state(REPO_ROOT, data_dir, _redaction())

            with CurrentStateStore.open_read_only_if_exists(state_path) as store:
                assert store is not None
                persisted = store.snapshot()
                version = store._connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]

            self.assertEqual(version, USER_VERSION)
            self.assertEqual(_table_names(state_path), V3_TABLES)
            self.assertEqual(rebuilt, persisted)
            self.assertEqual(rebuilt.companies, ())
            self.assertEqual(rebuilt.products, ())

    def test_v2_fault_injected_swap_preserves_the_old_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            state_path = data_dir / "state" / "current.sqlite3"
            _write_v2_database(state_path)
            before = state_path.read_bytes()

            with patch(
                "bizman.current.replay.os.replace",
                side_effect=OSError("synthetic replace failure"),
            ):
                with self.assertRaises(CurrentStateOperationError):
                    rebuild_current_state(REPO_ROOT, data_dir, _redaction())

            self.assertEqual(state_path.read_bytes(), before)
            self.assertEqual(_user_version(state_path), 2)
            self.assertEqual(
                _table_names(state_path),
                {"projection_meta", "replayed_session", "company", "unit"},
            )


def _canary_goods_html(label: str, attribute: str) -> str:
    unit = UNIT_ID
    product = PRODUCT_1
    href = f"/units/shop/?id={unit}&tab=goods&product={product}"
    cells = (
        f'<td><a href="{href}"><img src="/i/x.png" alt="{attribute}"></a>'
        f'<input type="hidden" name="product[0]" value="{product}"></td>',
        f'<td><a href="{href}">{label}</a></td>',
        "<td><div title=\"50%\"></div></td>",
        "<td>1 500 p. 250 p. "
        '<span title="Рентабельность: 17%"></span></td>',
        "<td>10%</td>",
        "<td>20</td>",
        "<td>4.0 (3.0)</td>",
        "<td>800 p. (700 p.)</td>",
        '<td><a href="#graph">graph</a></td>',
        '<td><input name="price[0]" value="300">'
        '<input type="hidden" name="price_currency" value="RUB"></td>',
        "<td>4.5</td>",
        "<td>310</td>",
        "<td>5</td>",
        '<td><a href="#dialog">dialog</a></td>',
        '<td><input name="purchaseQuantity[0]" value="3">'
        '<input type="hidden" name="vendorPrice[0]" value="120"></td>',
        '<td><a href="#buy">buy</a></td>',
    )
    header = (
        '<tr class="tblh">'
        + "".join('<td class="tblh">Header</td>' for _ in range(16))
        + "</tr>"
    )
    return (
        "<!doctype html><html><body>"
        f'<table id="goods">{header}{header}'
        f'<tr id="pr{product}">{"".join(cells)}</tr>'
        "</table></body></html>"
    )


if __name__ == "__main__":
    unittest.main()
