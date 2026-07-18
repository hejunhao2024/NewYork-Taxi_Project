from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import random
from typing import Iterable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


RAW_TIME_COLUMN = "tpep_dropoff_datetime"
PICKUP_TIME_COLUMN = "tpep_pickup_datetime"
TRAIN_START = datetime(2023, 1, 1)
TRAIN_END = datetime(2023, 1, 25)
VALIDATION_END = datetime(2023, 2, 1)
EVALUATION_START = datetime(2023, 2, 1)
EVALUATION_END = datetime(2023, 2, 28, 23, 30)


def partition_by_dropoff(
    trips: pa.Table, start: datetime, end: datetime
) -> pa.Table:
    """Return raw trip rows whose drop-off is in the half-open time window."""
    _require_columns(trips, [RAW_TIME_COLUMN])
    dropoff = trips[RAW_TIME_COLUMN]
    mask = pc.and_(
        pc.greater_equal(dropoff, pa.scalar(start)),
        pc.less(dropoff, pa.scalar(end)),
    )
    return trips.filter(mask)


def build_zone_time_statistics(train_trips: pa.Table) -> pa.Table:
    """Aggregate valid pickup zones from raw training rows without cleaning them."""
    _require_columns(
        train_trips,
        [PICKUP_TIME_COLUMN, "PULocationID", "fare_amount", "total_amount"],
    )
    pickup_times = train_trips[PICKUP_TIME_COLUMN]
    weekday = pc.day_of_week(pickup_times, count_from_zero=True, week_start=1)
    time_slot = pc.add(
        pc.multiply(pc.hour(pickup_times), pa.scalar(2, type=pa.int64())),
        pc.cast(
            pc.floor(
                pc.divide(
                    pc.minute(pickup_times), pa.scalar(30, type=pa.int64())
                )
            ),
            pa.int64(),
        ),
    )
    pickup_zone = train_trips["PULocationID"]
    valid_zone = pc.and_(
        pc.greater_equal(pickup_zone, pa.scalar(1)),
        pc.less_equal(pickup_zone, pa.scalar(263)),
    )
    base = pa.table(
        {
            "pickup_location_id": pickup_zone,
            "weekday": weekday,
            "time_slot": time_slot,
            "fare_amount": train_trips["fare_amount"],
            "total_amount": train_trips["total_amount"],
        }
    ).filter(valid_zone)
    grouped = base.group_by(
        ["pickup_location_id", "weekday", "time_slot"]
    ).aggregate(
        [
            ("pickup_location_id", "count"),
            ("fare_amount", "mean"),
            ("total_amount", "mean"),
        ]
    )
    result = grouped.rename_columns(
        [
            "pickup_location_id",
            "weekday",
            "time_slot",
            "pickup_count",
            "mean_fare_amount",
            "mean_total_amount",
        ]
    )
    return result.sort_by(
        [
            ("weekday", "ascending"),
            ("time_slot", "ascending"),
            ("pickup_location_id", "ascending"),
        ]
    )


def sample_test_queries(
    february_trips: pa.Table,
    *,
    start: datetime,
    end: datetime,
    per_stratum: int,
    seed: int,
) -> pa.Table:
    """Create deterministic, unlabeled queries from valid February drop-offs."""
    _require_columns(february_trips, [RAW_TIME_COLUMN, "DOLocationID"])
    sampler = _QuerySampler(start=start, end=end, per_stratum=per_stratum, seed=seed)
    sampler.add_batch(
        february_trips[RAW_TIME_COLUMN].to_pylist(),
        february_trips["DOLocationID"].to_pylist(),
    )
    return sampler.to_table()


def run_pipeline(
    january_path: Path | str,
    february_path: Path | str,
    output_dir: Path | str,
    *,
    per_stratum: int = 30,
    seed: int = 20230717,
) -> dict[str, object]:
    """Write the course artifacts from the two official raw monthly files."""
    january_path = Path(january_path)
    february_path = Path(february_path)
    output_dir = Path(output_dir)
    _require_file(january_path)
    _require_file(february_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    january = pq.read_table(january_path)
    train = partition_by_dropoff(january, TRAIN_START, TRAIN_END)
    validation = partition_by_dropoff(january, TRAIN_END, VALIDATION_END)
    statistics = build_zone_time_statistics(train)
    test_inputs = _sample_test_queries_from_parquet(
        february_path,
        start=EVALUATION_START,
        end=EVALUATION_END,
        per_stratum=per_stratum,
        seed=seed,
    )

    artifact_tables = {
        "train.parquet": train,
        "validation.parquet": validation,
        "zone_time_statistics.parquet": statistics,
        "test_input.parquet": test_inputs,
    }
    for name, table in artifact_tables.items():
        _write_parquet_atomically(table, output_dir / name)

    manifest: dict[str, object] = {
        "version": 1,
        "sources": {
            "january": str(january_path),
            "february": str(february_path),
        },
        "windows": {
            "train": [TRAIN_START.isoformat(), TRAIN_END.isoformat()],
            "validation": [TRAIN_END.isoformat(), VALIDATION_END.isoformat()],
            "evaluation": [EVALUATION_START.isoformat(), EVALUATION_END.isoformat()],
        },
        "test_input_sampling": {
            "strategy": "reservoir sample by Monday-based weekday and 30-minute slot",
            "per_stratum": per_stratum,
            "seed": seed,
            "labels_included": False,
        },
        "artifacts": {
            name: {"rows": table.num_rows, "columns": table.schema.names}
            for name, table in artifact_tables.items()
        },
        "travel_time_matrix_included": False,
    }
    _write_json_atomically(manifest, output_dir / "manifest.json")
    return manifest


def _sample_test_queries_from_parquet(
    path: Path,
    *,
    start: datetime,
    end: datetime,
    per_stratum: int,
    seed: int,
) -> pa.Table:
    file = pq.ParquetFile(path)
    sampler = _QuerySampler(start=start, end=end, per_stratum=per_stratum, seed=seed)
    for batch in file.iter_batches(
        columns=[RAW_TIME_COLUMN, "DOLocationID"], batch_size=65_536
    ):
        sampler.add_batch(
            batch.column(RAW_TIME_COLUMN).to_pylist(),
            batch.column("DOLocationID").to_pylist(),
        )
    return sampler.to_table()


class _QuerySampler:
    def __init__(
        self, *, start: datetime, end: datetime, per_stratum: int, seed: int
    ) -> None:
        if per_stratum <= 0:
            raise ValueError("per_stratum must be positive")
        self.start = start
        self.end = end
        self.per_stratum = per_stratum
        self.rng = random.Random(seed)
        self.seen: dict[tuple[int, int], int] = {}
        self.samples: dict[tuple[int, int], list[dict[str, object]]] = {}

    def add_batch(
        self, dropoff_times: Iterable[datetime | None], location_ids: Iterable[object]
    ) -> None:
        for query_time, raw_location_id in zip(dropoff_times, location_ids):
            if not isinstance(query_time, datetime) or not self.start <= query_time < self.end:
                continue
            if raw_location_id is None:
                continue
            location_id = int(raw_location_id)
            if not 1 <= location_id <= 263:
                continue
            weekday = query_time.weekday()
            time_slot = (query_time.hour * 60 + query_time.minute) // 30
            stratum = (weekday, time_slot)
            row = {
                "current_location_id": location_id,
                "query_time": query_time,
                "weekday": weekday,
                "time_slot": time_slot,
            }
            seen = self.seen.get(stratum, 0) + 1
            self.seen[stratum] = seen
            bucket = self.samples.setdefault(stratum, [])
            if len(bucket) < self.per_stratum:
                bucket.append(row)
                continue
            replacement_index = self.rng.randrange(seen)
            if replacement_index < self.per_stratum:
                bucket[replacement_index] = row

    def to_table(self) -> pa.Table:
        rows = [row for bucket in self.samples.values() for row in bucket]
        rows.sort(key=lambda row: (row["query_time"], row["current_location_id"]))
        output = [
            {"query_id": index, **row} for index, row in enumerate(rows, start=1)
        ]
        return pa.Table.from_pylist(
            output,
            schema=pa.schema(
                [
                    pa.field("query_id", pa.int64()),
                    pa.field("current_location_id", pa.int64()),
                    pa.field("query_time", pa.timestamp("us")),
                    pa.field("weekday", pa.int64()),
                    pa.field("time_slot", pa.int64()),
                ]
            ),
        )


def _require_columns(table: pa.Table, columns: list[str]) -> None:
    missing = [column for column in columns if column not in table.schema.names]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)


def _write_parquet_atomically(table: pa.Table, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(destination)


def _write_json_atomically(payload: dict[str, object], destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--january", type=Path, required=True)
    parser.add_argument("--february", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-stratum", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20230717)
    arguments = parser.parse_args()
    manifest = run_pipeline(
        arguments.january,
        arguments.february,
        arguments.output,
        per_stratum=arguments.per_stratum,
        seed=arguments.seed,
    )
    print(json.dumps(manifest["artifacts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
