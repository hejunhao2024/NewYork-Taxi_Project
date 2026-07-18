from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from nyc_taxi_prep.prepare_data import (
    build_zone_time_statistics,
    partition_by_dropoff,
    sample_test_queries,
)


class PrepareDataTests(unittest.TestCase):
    def test_partition_by_dropoff_preserves_raw_columns_and_uses_half_open_window(self) -> None:
        table = pa.table(
            {
                "tpep_pickup_datetime": pa.array(
                    [
                        datetime(2023, 1, 24, 23, 30),
                        datetime(2023, 1, 24, 23, 45),
                        datetime(2023, 1, 25, 0, 0),
                    ]
                ),
                "tpep_dropoff_datetime": pa.array(
                    [
                        datetime(2023, 1, 24, 23, 59),
                        datetime(2023, 1, 25, 0, 0),
                        datetime(2023, 1, 25, 0, 15),
                    ]
                ),
                "PULocationID": pa.array([10, 11, 12], type=pa.int64()),
                "DOLocationID": pa.array([20, 21, 22], type=pa.int64()),
                "raw_only_field": pa.array(["keep-a", "keep-b", "keep-c"]),
            }
        )

        result = partition_by_dropoff(
            table, datetime(2023, 1, 1), datetime(2023, 1, 25)
        )

        self.assertEqual(result.schema, table.schema)
        self.assertEqual(result.column("raw_only_field").to_pylist(), ["keep-a"])

    def test_zone_time_statistics_uses_training_pickups_and_excludes_only_special_zones(self) -> None:
        table = pa.table(
            {
                "tpep_pickup_datetime": pa.array(
                    [
                        datetime(2023, 1, 2, 8, 5),
                        datetime(2023, 1, 2, 8, 29),
                        datetime(2023, 1, 2, 8, 10),
                    ]
                ),
                "PULocationID": pa.array([1, 1, 264], type=pa.int64()),
                "fare_amount": pa.array([10.0, 20.0, 999.0]),
                "total_amount": pa.array([12.0, 24.0, 1000.0]),
            }
        )

        result = build_zone_time_statistics(table)

        self.assertEqual(result.column("pickup_location_id").to_pylist(), [1])
        self.assertEqual(result.column("weekday").to_pylist(), [0])
        self.assertEqual(result.column("time_slot").to_pylist(), [16])
        self.assertEqual(result.column("pickup_count").to_pylist(), [2])
        self.assertEqual(result.column("mean_fare_amount").to_pylist(), [15.0])
        self.assertEqual(result.column("mean_total_amount").to_pylist(), [18.0])

    def test_test_queries_are_unlabeled_eligible_and_deterministic(self) -> None:
        table = pa.table(
            {
                "tpep_dropoff_datetime": pa.array(
                    [
                        datetime(2023, 2, 1, 8, 3),
                        datetime(2023, 2, 1, 8, 9),
                        datetime(2023, 2, 1, 8, 22),
                        datetime(2023, 2, 1, 8, 15),
                        datetime(2023, 2, 28, 23, 45),
                    ]
                ),
                "DOLocationID": pa.array([10, 11, 12, 264, 13], type=pa.int64()),
                "future_target": pa.array([99, 99, 99, 99, 99], type=pa.int64()),
            }
        )

        first = sample_test_queries(
            table,
            start=datetime(2023, 2, 1),
            end=datetime(2023, 2, 28, 23, 30),
            per_stratum=2,
            seed=7,
        )
        second = sample_test_queries(
            table,
            start=datetime(2023, 2, 1),
            end=datetime(2023, 2, 28, 23, 30),
            per_stratum=2,
            seed=7,
        )

        self.assertEqual(
            first.schema.names,
            ["query_id", "current_location_id", "query_time", "weekday", "time_slot"],
        )
        self.assertEqual(first.to_pylist(), second.to_pylist())
        self.assertEqual(len(first), 2)
        self.assertTrue(all(row["current_location_id"] in {10, 11, 12} for row in first.to_pylist()))
        self.assertNotIn("future_target", first.schema.names)

    def test_run_pipeline_writes_declared_artifacts_and_manifest(self) -> None:
        from nyc_taxi_prep.prepare_data import run_pipeline

        january = pa.table(
            {
                "tpep_pickup_datetime": pa.array(
                    [datetime(2023, 1, 24, 8, 0), datetime(2023, 1, 25, 8, 0)]
                ),
                "tpep_dropoff_datetime": pa.array(
                    [datetime(2023, 1, 24, 8, 10), datetime(2023, 1, 25, 8, 10)]
                ),
                "PULocationID": pa.array([10, 11], type=pa.int64()),
                "DOLocationID": pa.array([20, 21], type=pa.int64()),
                "fare_amount": pa.array([10.0, 20.0]),
                "total_amount": pa.array([12.0, 24.0]),
            }
        )
        february = pa.table(
            {
                "tpep_dropoff_datetime": pa.array(
                    [datetime(2023, 2, 1, 8, 10), datetime(2023, 2, 1, 8, 20)]
                ),
                "DOLocationID": pa.array([30, 31], type=pa.int64()),
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            january_path = root / "january.parquet"
            february_path = root / "february.parquet"
            output_dir = root / "processed"
            pq.write_table(january, january_path)
            pq.write_table(february, february_path)

            run_pipeline(
                january_path,
                february_path,
                output_dir,
                per_stratum=1,
                seed=7,
            )

            expected = {
                "train.parquet",
                "validation.parquet",
                "zone_time_statistics.parquet",
                "test_input.parquet",
                "manifest.json",
            }
            self.assertEqual({path.name for path in output_dir.iterdir()}, expected)
            self.assertEqual(pq.read_table(output_dir / "train.parquet").num_rows, 1)
            self.assertEqual(pq.read_table(output_dir / "validation.parquet").num_rows, 1)
            self.assertEqual(pq.read_table(output_dir / "test_input.parquet").num_rows, 1)
            manifest = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(manifest["artifacts"]["train.parquet"]["rows"], 1)


if __name__ == "__main__":
    unittest.main()
