"""Ablage der Prognosen, der später nachgetragenen Ist-Werte und der Laufprotokolle."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from .evaluate import DayEvaluation
from .timeutil import UTC, iso_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    day TEXT NOT NULL,
    scenario TEXT NOT NULL DEFAULT 'standard',
    generated_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    import_kwh REAL,
    pv_kwh REAL,
    saving_eur REAL,
    saving_ideal_eur REAL,
    saving_unshifted_eur REAL,
    cost_smart_eur REAL,
    cost_fixed_eur REAL,
    window_start TEXT,
    window_end TEXT,
    window_price_ct REAL,
    day_price_ct REAL,
    verdict TEXT,
    needed_kwh REAL,
    shifted_kwh REAL,
    unused_capacity_kwh REAL,
    binding TEXT,
    PRIMARY KEY (day, scenario)
);

CREATE TABLE IF NOT EXISTS actuals (
    day TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    import_kwh REAL,
    cost_smart_eur REAL,
    cost_fixed_eur REAL,
    saving_eur REAL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    day TEXT,
    status TEXT NOT NULL,
    message TEXT
);
"""


@dataclass
class ForecastRow:
    day: date
    scenario: str
    generated_at: str
    payload: dict[str, Any]

    @property
    def savings(self) -> dict[str, float]:
        return self.payload.get("savings", {})

    @property
    def storage(self) -> dict[str, Any]:
        return self.payload.get("storage", {})


@dataclass
class ActualRow:
    day: date
    updated_at: str
    payload: dict[str, Any]


class Store:
    """Dünne SQLite-Schicht; die Details liegen als JSON in payload."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -------------------------------------------------------------- Prognose

    def save_forecast(self, evaluation: DayEvaluation) -> None:
        payload = evaluation.as_dict()
        storage = payload["storage"]
        window = payload["window"]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO forecasts (day, scenario, generated_at, payload, import_kwh, pv_kwh,
                    saving_eur, saving_ideal_eur, saving_unshifted_eur, cost_smart_eur, cost_fixed_eur,
                    window_start, window_end, window_price_ct, day_price_ct, verdict, needed_kwh,
                    shifted_kwh, unused_capacity_kwh, binding)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(day, scenario) DO UPDATE SET
                    generated_at=excluded.generated_at, payload=excluded.payload,
                    import_kwh=excluded.import_kwh, pv_kwh=excluded.pv_kwh,
                    saving_eur=excluded.saving_eur, saving_ideal_eur=excluded.saving_ideal_eur,
                    saving_unshifted_eur=excluded.saving_unshifted_eur,
                    cost_smart_eur=excluded.cost_smart_eur, cost_fixed_eur=excluded.cost_fixed_eur,
                    window_start=excluded.window_start, window_end=excluded.window_end,
                    window_price_ct=excluded.window_price_ct, day_price_ct=excluded.day_price_ct,
                    verdict=excluded.verdict, needed_kwh=excluded.needed_kwh,
                    shifted_kwh=excluded.shifted_kwh, unused_capacity_kwh=excluded.unused_capacity_kwh,
                    binding=excluded.binding
                """,
                (
                    payload["day"], payload["scenario"], payload["generated_at"],
                    json.dumps(payload, ensure_ascii=False),
                    payload["energy"]["import_kwh"], payload["energy"]["pv_kwh"],
                    payload["savings"]["vs_fixed_eur"], payload["savings"]["vs_fixed_ideal_eur"],
                    payload["savings"]["vs_fixed_unshifted_eur"],
                    payload["costs"]["smart_shifted_eur"], payload["costs"]["fixed_eur"],
                    window["start_utc"], window["end_utc"], window["avg_price_ct"],
                    payload["prices"]["day_avg_ct"], storage["verdict"], storage["needed_kwh"],
                    storage["shifted_kwh"], storage["unused_capacity_kwh"],
                    ",".join(storage.get("binding", [])),
                ),
            )

    def get_forecast(self, day: date, scenario: str = "standard") -> ForecastRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM forecasts WHERE day=? AND scenario=?", (day.isoformat(), scenario)
            ).fetchone()
        return _to_forecast(row) if row else None

    def list_forecasts(
        self, start: date | None = None, end: date | None = None, scenario: str = "standard"
    ) -> list[ForecastRow]:
        query = "SELECT * FROM forecasts WHERE scenario=?"
        params: list[Any] = [scenario]
        if start:
            query += " AND day >= ?"
            params.append(start.isoformat())
        if end:
            query += " AND day <= ?"
            params.append(end.isoformat())
        query += " ORDER BY day"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_to_forecast(row) for row in rows]

    # -------------------------------------------------------------------- Ist

    def save_actual(self, day: date, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO actuals (day, updated_at, payload, import_kwh, cost_smart_eur,
                    cost_fixed_eur, saving_eur)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(day) DO UPDATE SET
                    updated_at=excluded.updated_at, payload=excluded.payload,
                    import_kwh=excluded.import_kwh, cost_smart_eur=excluded.cost_smart_eur,
                    cost_fixed_eur=excluded.cost_fixed_eur, saving_eur=excluded.saving_eur
                """,
                (
                    day.isoformat(),
                    iso_utc(datetime.now(tz=UTC)),
                    json.dumps(payload, ensure_ascii=False),
                    payload.get("import_kwh"),
                    payload.get("cost_smart_eur"),
                    payload.get("cost_fixed_eur"),
                    payload.get("saving_eur"),
                ),
            )

    def get_actual(self, day: date) -> ActualRow | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM actuals WHERE day=?", (day.isoformat(),)).fetchone()
        if not row:
            return None
        return ActualRow(date.fromisoformat(row["day"]), row["updated_at"], json.loads(row["payload"]))

    def list_actuals(self, start: date | None = None, end: date | None = None) -> list[ActualRow]:
        query = "SELECT * FROM actuals WHERE 1=1"
        params: list[Any] = []
        if start:
            query += " AND day >= ?"
            params.append(start.isoformat())
        if end:
            query += " AND day <= ?"
            params.append(end.isoformat())
        query += " ORDER BY day"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            ActualRow(date.fromisoformat(r["day"]), r["updated_at"], json.loads(r["payload"]))
            for r in rows
        ]

    def days_without_actual(self, before: date, limit: int = 14) -> list[date]:
        """Tage mit Prognose, aber ohne Ist-Werte; für den Nachtrag."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT f.day FROM forecasts f
                LEFT JOIN actuals a ON a.day = f.day
                WHERE a.day IS NULL AND f.day < ? AND f.scenario='standard'
                ORDER BY f.day DESC LIMIT ?
                """,
                (before.isoformat(), limit),
            ).fetchall()
        return [date.fromisoformat(row["day"]) for row in rows]

    # ---------------------------------------------------------------- Läufe

    def start_run(self, day: date | None) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (started_at, day, status) VALUES (?,?,?)",
                (iso_utc(datetime.now(tz=UTC)), day.isoformat() if day else None, "läuft"),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, message: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?, status=?, message=? WHERE id=?",
                (iso_utc(datetime.now(tz=UTC)), status, message[:2000], run_id),
            )

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


def _to_forecast(row: sqlite3.Row) -> ForecastRow:
    return ForecastRow(
        day=date.fromisoformat(row["day"]),
        scenario=row["scenario"],
        generated_at=row["generated_at"],
        payload=json.loads(row["payload"]),
    )
