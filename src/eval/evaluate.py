"""Evaluate one strategy with formula utility and simulator demand scores."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Sequence

import pyarrow.parquet as pq

from eval.simultor import (
    ZONE_COUNT,
    QueryPrediction,
    load_travel_time_matrix,
    load_trip_market,
    load_strategy,
    run_query_file,
    simulate_many,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUERIES = PROJECT_ROOT / "data/processed/test_input.parquet"
DEFAULT_TRIPS = PROJECT_ROOT / "data/raw/yellow_tripdata_2023-02.parquet"
DEFAULT_STATISTICS = PROJECT_ROOT / "data/processed/zone_time_statistics.parquet"
DEFAULT_TRAVEL_TIMES = PROJECT_ROOT / "src/eval/travel_time_matrix1.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "tmp/evaluation.json"
SIMULATION_RUNS = 100
SIMULATION_BASE_SEED = 20230717


def evaluate_strategy(
    *,
    strategy_path: Path,
    queries_path: Path = DEFAULT_QUERIES,
    trips_path: Path = DEFAULT_TRIPS,
    statistics_path: Path = DEFAULT_STATISTICS,
    travel_times_path: Path = DEFAULT_TRAVEL_TIMES,
    output_path: Path = DEFAULT_OUTPUT,
    smoothing: float = 1.0,
) -> dict[str, object]:
    """Run a strategy once and return its two independent evaluation scores."""
    if smoothing <= 0:
        raise ValueError("smoothing must be positive")

    strategy = load_strategy(strategy_path)
    predictions = run_query_file(strategy, queries_path)

    formula_result = calculate_formula_score(
        predictions=predictions,
        statistics_path=statistics_path,
        travel_times_path=travel_times_path,
        smoothing=smoothing,
    )
    market = load_trip_market(trips_path)
    simulator_result = simulate_many(
        strategy=strategy,
        market=market,
        travel_times=load_travel_time_matrix(travel_times_path),
        runs=SIMULATION_RUNS,
        base_seed=SIMULATION_BASE_SEED,
    )

    result: dict[str, object] = {
        "formula_score": formula_result["score"],
        "simulator_score": simulator_result["score"],
        "formula_evaluation": formula_result,
        "simulator_evaluation": simulator_result,
        "strategy_file": str(strategy_path),
        "query_file": str(queries_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def calculate_formula_score(
    *,
    predictions: list[QueryPrediction],
    statistics_path: Path,
    travel_times_path: Path,
    smoothing: float,
) -> dict[str, object]:
    """Calculate NDCG@3 using the reference utility formula as relevance."""
    demand, mean_fare = _load_zone_statistics(statistics_path)
    travel_times = _load_travel_times(travel_times_path)
    ndcg_total = 0.0
    evaluated = 0
    zero_utility = 0

    for prediction in predictions:
        weekday = prediction.target_weekday
        target_slot = prediction.target_time_slot
        origin = prediction.current_location_id - 1
        utilities = []
        for destination in range(ZONE_COUNT):
            travel_time = travel_times[origin][destination]
            if math.isinf(travel_time):
                utilities.append(0.0)
                continue
            utility = (
                demand[weekday][target_slot][destination]
                * mean_fare[weekday][target_slot][destination]
                / (travel_time + smoothing)
            )
            utilities.append(utility)

        ideal_zones = sorted(
            range(1, ZONE_COUNT + 1),
            key=lambda location_id: (-utilities[location_id - 1], location_id),
        )[:3]
        ideal_relevance = [utilities[zone - 1] for zone in ideal_zones]
        ideal_dcg = _dcg(ideal_relevance)
        if ideal_dcg == 0.0:
            zero_utility += 1
            continue
        recommended_relevance = [
            utilities[zone - 1] for zone in prediction.top3
        ]
        ndcg_total += _dcg(recommended_relevance) / ideal_dcg
        evaluated += 1

    score = ndcg_total / evaluated if evaluated else 0.0
    return {
        "score": score,
        "metric": "formula_ndcg_at_3",
        "evaluated_queries": evaluated,
        "queries_without_positive_reference_utility": zero_utility,
        "smoothing": smoothing,
        "definition": (
            "NDCG@3 with Demand * Fare / (TravelTime + lambda) as relevance"
        ),
    }


def _load_zone_statistics(
    path: Path,
) -> tuple[list[list[list[float]]], list[list[list[float]]]]:
    demand = [[[0.0] * ZONE_COUNT for _ in range(48)] for _ in range(7)]
    mean_fare = [[[0.0] * ZONE_COUNT for _ in range(48)] for _ in range(7)]
    columns = [
        "pickup_location_id",
        "weekday",
        "time_slot",
        "pickup_count",
        "mean_fare_amount",
    ]
    for row in pq.read_table(path, columns=columns).to_pylist():
        location_id = int(row["pickup_location_id"])
        if not 1 <= location_id <= ZONE_COUNT:
            continue
        weekday = int(row["weekday"])
        time_slot = int(row["time_slot"])
        index = location_id - 1
        demand[weekday][time_slot][index] = float(row["pickup_count"])
        raw_fare = row["mean_fare_amount"]
        if raw_fare is not None and math.isfinite(float(raw_fare)):
            mean_fare[weekday][time_slot][index] = max(0.0, float(raw_fare))
    return demand, mean_fare


def _load_travel_times(path: Path) -> list[list[float]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if len(header) != ZONE_COUNT + 1:
            raise ValueError("travel-time matrix must have 263 destination columns")
        matrix = []
        for expected_origin, row in enumerate(reader, start=1):
            if len(row) != ZONE_COUNT + 1 or int(row[0]) != expected_origin:
                raise ValueError("invalid travel-time matrix row")
            matrix.append([float(value) for value in row[1:]])
    if len(matrix) != ZONE_COUNT:
        raise ValueError("travel-time matrix must have 263 origin rows")
    return matrix


def _dcg(relevance: Sequence[float]) -> float:
    return sum(
        value / math.log2(rank + 1)
        for rank, value in enumerate(relevance, start=1)
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate one strategy with formula and simulator scores."
    )
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--trips", type=Path, default=DEFAULT_TRIPS)
    parser.add_argument("--statistics", type=Path, default=DEFAULT_STATISTICS)
    parser.add_argument("--travel-times", type=Path, default=DEFAULT_TRAVEL_TIMES)
    parser.add_argument("--smoothing", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = evaluate_strategy(
        strategy_path=args.strategy,
        queries_path=args.queries,
        trips_path=args.trips,
        statistics_path=args.statistics,
        travel_times_path=args.travel_times,
        smoothing=args.smoothing,
        output_path=args.output,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
