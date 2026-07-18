"""Build the 263 x 263 directed shortest-travel-time matrix for Baseline 2."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import heapq
import math
from pathlib import Path

import pyarrow.parquet as pq


ZONE_COUNT = 263
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRAIN_PATH = PROJECT_ROOT / "data/processed/train.parquet"
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "data/processed/travel_time_matrix_dijkstra.csv"
)


def build_matrix(train_path: Path, output_path: Path) -> None:
    """Aggregate OD edges, run all-source Dijkstra, and write a CSV matrix."""
    duration_sum = [[0.0] * ZONE_COUNT for _ in range(ZONE_COUNT)]
    trip_count = [[0] * ZONE_COUNT for _ in range(ZONE_COUNT)]

    parquet = pq.ParquetFile(train_path)
    columns = [
        "tpep_pickup_datetime",
        "tpep_dropoff_datetime",
        "PULocationID",
        "DOLocationID",
    ]
    for batch in parquet.iter_batches(columns=columns, batch_size=65_536):
        pickup_times = batch.column("tpep_pickup_datetime").to_pylist()
        dropoff_times = batch.column("tpep_dropoff_datetime").to_pylist()
        pickup_zones = batch.column("PULocationID").to_pylist()
        dropoff_zones = batch.column("DOLocationID").to_pylist()
        for pickup_time, dropoff_time, raw_pickup, raw_dropoff in zip(
            pickup_times,
            dropoff_times,
            pickup_zones,
            dropoff_zones,
        ):
            if not isinstance(pickup_time, datetime) or not isinstance(
                dropoff_time, datetime
            ):
                continue
            if raw_pickup is None or raw_dropoff is None:
                continue
            pickup = int(raw_pickup)
            dropoff = int(raw_dropoff)
            if not (1 <= pickup <= ZONE_COUNT and 1 <= dropoff <= ZONE_COUNT):
                continue
            duration = (dropoff_time - pickup_time).total_seconds() / 60.0
            if not 0.0 < duration <= 240.0:
                continue
            origin = pickup - 1
            destination = dropoff - 1
            duration_sum[origin][destination] += duration
            trip_count[origin][destination] += 1

    graph: list[list[tuple[int, float]]] = [[] for _ in range(ZONE_COUNT)]
    for origin in range(ZONE_COUNT):
        for destination in range(ZONE_COUNT):
            count = trip_count[origin][destination]
            if count > 0:
                mean_duration = duration_sum[origin][destination] / count
                graph[origin].append((destination, mean_duration))

    matrix = [_dijkstra(graph, source) for source in range(ZONE_COUNT)]
    _write_matrix(output_path, matrix)


def _dijkstra(
    graph: list[list[tuple[int, float]]],
    source: int,
) -> list[float]:
    distances = [math.inf] * ZONE_COUNT
    distances[source] = 0.0
    queue = [(0.0, source)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        for neighbor, weight in graph[node]:
            candidate = distance + weight
            if candidate < distances[neighbor]:
                distances[neighbor] = candidate
                heapq.heappush(queue, (candidate, neighbor))
    return distances


def _write_matrix(output_path: Path, matrix: list[list[float]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["origin_location_id", *range(1, ZONE_COUNT + 1)])
        for origin, distances in enumerate(matrix, start=1):
            writer.writerow(
                [
                    origin,
                    *(
                        "inf" if math.isinf(value) else f"{value:.6f}"
                        for value in distances
                    ),
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the directed Dijkstra travel-time matrix."
    )
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    build_matrix(args.train, args.output)
    print(f"wrote {ZONE_COUNT} x {ZONE_COUNT} matrix to {args.output}")


if __name__ == "__main__":
    main()
