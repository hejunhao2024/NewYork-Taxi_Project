"""Month-long stochastic taxi simulator for one recommendation strategy."""

from __future__ import annotations

from array import array
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import csv
import importlib.util
import math
from pathlib import Path
import random
import statistics
import sys
from types import ModuleType
from typing import Callable, Sequence

import pyarrow.parquet as pq


ZONE_COUNT = 263
SLOT_MINUTES = 30
SIMULATION_START = datetime(2023, 2, 1, 0, 0)
SIMULATION_END = datetime(2023, 3, 1, 0, 0)
SIMULATION_DAYS = 28
START_LOCATION_ID = 132
DEMAND_SCALE = 20.0
Strategy = Callable[[datetime, int], Sequence[int] | tuple[Sequence[int], object]]


class MarketCell:
    """Compact collection of real trips available in one zone and slot."""

    __slots__ = ("dropoff_zones", "fares", "duration_slots")

    def __init__(self) -> None:
        self.dropoff_zones = array("H")
        self.fares = array("f")
        self.duration_slots = array("B")

    def append(self, dropoff_zone: int, fare: float, duration_slots: int) -> None:
        self.dropoff_zones.append(dropoff_zone)
        self.fares.append(fare)
        self.duration_slots.append(duration_slots)

    def __len__(self) -> int:
        return len(self.dropoff_zones)


@dataclass(frozen=True)
class SimulationResult:
    total_income: float
    average_daily_income: float
    served_trips: int
    relocation_count: int
    elapsed_slots: int


@dataclass(frozen=True)
class QueryPrediction:
    current_location_id: int
    target_weekday: int
    target_time_slot: int
    top3: tuple[int, int, int]


def load_strategy(strategy_path: Path) -> Strategy:
    """Import a Python strategy file and return its recommend function."""
    if not strategy_path.is_file():
        raise FileNotFoundError(strategy_path)
    spec = importlib.util.spec_from_file_location("taxi_strategy", strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load strategy: {strategy_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return _find_recommend_function(module)


def _find_recommend_function(module: ModuleType) -> Strategy:
    recommend = getattr(module, "recommend", None)
    if not callable(recommend):
        raise AttributeError(
            "strategy must define recommend(current_datetime, current_location_id)"
        )
    return recommend


def pickup_probability(demand: int) -> float:
    """Map non-negative demand to [0, 1) with a concave saturation curve."""
    if demand < 0:
        raise ValueError("demand cannot be negative")
    return 1.0 - math.exp(-demand / DEMAND_SCALE)


def minutes_to_slots(minutes: float) -> int:
    """Round half up to 30-minute slots and enforce at least one slot."""
    if not math.isfinite(minutes) or minutes < 0:
        raise ValueError("travel time must be a finite non-negative number")
    return max(1, int(math.floor(minutes / SLOT_MINUTES + 0.5)))


def load_trip_market(trips_path: Path) -> dict[int, MarketCell]:
    """Group valid February trips by day, half-hour slot, and pickup zone."""
    market: dict[int, MarketCell] = {}
    parquet = pq.ParquetFile(trips_path)
    columns = [
        "tpep_pickup_datetime",
        "tpep_dropoff_datetime",
        "PULocationID",
        "DOLocationID",
        "fare_amount",
    ]
    for batch in parquet.iter_batches(columns=columns, batch_size=65_536):
        values = [batch.column(column).to_pylist() for column in columns]
        for pickup_time, dropoff_time, raw_pickup, raw_dropoff, raw_fare in zip(
            *values
        ):
            if not isinstance(pickup_time, datetime) or not isinstance(
                dropoff_time, datetime
            ):
                continue
            if not (
                SIMULATION_START <= pickup_time < SIMULATION_END
                and pickup_time < dropoff_time <= SIMULATION_END
            ):
                continue
            if raw_pickup is None or raw_dropoff is None:
                continue
            pickup_zone = int(raw_pickup)
            dropoff_zone = int(raw_dropoff)
            if not (
                1 <= pickup_zone <= ZONE_COUNT
                and 1 <= dropoff_zone <= ZONE_COUNT
            ):
                continue
            duration_minutes = (dropoff_time - pickup_time).total_seconds() / 60.0
            if not 0.0 < duration_minutes <= 240.0:
                continue
            fare = 0.0 if raw_fare is None else float(raw_fare)
            if not math.isfinite(fare):
                fare = 0.0
            fare = max(0.0, fare)

            day_index = (pickup_time.date() - SIMULATION_START.date()).days
            time_slot = (pickup_time.hour * 60 + pickup_time.minute) // SLOT_MINUTES
            key = _market_key(day_index, time_slot, pickup_zone)
            cell = market.get(key)
            if cell is None:
                cell = MarketCell()
                market[key] = cell
            cell.append(
                dropoff_zone,
                fare,
                minutes_to_slots(duration_minutes),
            )
    return market


def load_travel_time_matrix(path: Path) -> list[list[float]]:
    """Load the evaluator's directed 263 x 263 travel-time matrix."""
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


def run_query_file(strategy: Strategy, queries_path: Path) -> list[QueryPrediction]:
    """Call a strategy for formula-based evaluation queries."""
    columns = ["current_location_id", "query_time", "weekday", "time_slot"]
    predictions = []
    for row in pq.read_table(queries_path, columns=columns).to_pylist():
        query_time = row["query_time"]
        if not isinstance(query_time, datetime):
            raise TypeError("query_time must be a datetime")
        current_location_id = int(row["current_location_id"])
        if query_time.weekday() != int(row["weekday"]):
            raise ValueError("query weekday does not match query_time")
        current_slot = (query_time.hour * 60 + query_time.minute) // SLOT_MINUTES
        if current_slot != int(row["time_slot"]):
            raise ValueError("query time_slot does not match query_time")
        top3 = call_strategy(strategy, query_time, current_location_id)
        target_time = _next_slot_time(query_time)
        predictions.append(
            QueryPrediction(
                current_location_id=current_location_id,
                target_weekday=target_time.weekday(),
                target_time_slot=(
                    target_time.hour * 60 + target_time.minute
                )
                // SLOT_MINUTES,
                top3=top3,
            )
        )
    return predictions


def call_strategy(
    strategy: Strategy,
    current_time: datetime,
    current_location_id: int,
) -> tuple[int, int, int]:
    """Call recommend and normalize either Top-3 or legacy (Top-3, scores)."""
    raw_result = strategy(current_time, current_location_id)
    if (
        isinstance(raw_result, tuple)
        and len(raw_result) == 2
        and isinstance(raw_result[0], Sequence)
    ):
        raw_top3 = raw_result[0]
    else:
        raw_top3 = raw_result
    top3 = tuple(int(value) for value in raw_top3)
    if len(top3) != 3 or len(set(top3)) != 3:
        raise ValueError("recommend must return three distinct LocationIDs")
    if not all(1 <= location_id <= ZONE_COUNT for location_id in top3):
        raise ValueError("recommended LocationIDs must be in 1..263")
    return top3  # type: ignore[return-value]


def simulate_once(
    *,
    strategy: Strategy,
    market: dict[int, MarketCell],
    travel_times: list[list[float]],
    seed: int,
) -> SimulationResult:
    """Simulate one driver through all of February 2023."""
    rng = random.Random(seed)
    current_time = SIMULATION_START
    current_location_id = START_LOCATION_ID
    total_income = 0.0
    served_trips = 0
    relocation_count = 0

    while current_time < SIMULATION_END:
        day_index = (current_time.date() - SIMULATION_START.date()).days
        time_slot = (current_time.hour * 60 + current_time.minute) // SLOT_MINUTES
        cell = market.get(_market_key(day_index, time_slot, current_location_id))
        demand = 0 if cell is None else len(cell)

        if cell is not None and rng.random() < pickup_probability(demand):
            trip_index = rng.randrange(demand)
            total_income += float(cell.fares[trip_index])
            current_location_id = int(cell.dropoff_zones[trip_index])
            occupied_slots = int(cell.duration_slots[trip_index])
            current_time += timedelta(minutes=occupied_slots * SLOT_MINUTES)
            served_trips += 1
            continue

        top3 = call_strategy(strategy, current_time, current_location_id)
        destination = top3[0]
        travel_minutes = travel_times[current_location_id - 1][destination - 1]
        travel_slots = minutes_to_slots(travel_minutes)
        current_location_id = destination
        current_time += timedelta(minutes=travel_slots * SLOT_MINUTES)
        relocation_count += 1

    elapsed_slots = int(
        (current_time - SIMULATION_START).total_seconds() // (SLOT_MINUTES * 60)
    )
    return SimulationResult(
        total_income=total_income,
        average_daily_income=total_income / SIMULATION_DAYS,
        served_trips=served_trips,
        relocation_count=relocation_count,
        elapsed_slots=elapsed_slots,
    )


def simulate_many(
    *,
    strategy: Strategy,
    market: dict[int, MarketCell],
    travel_times: list[list[float]],
    runs: int = 100,
    base_seed: int = 20230717,
) -> dict[str, object]:
    """Run independent simulations and average daily strategy income."""
    if runs <= 0:
        raise ValueError("runs must be positive")
    results = [
        simulate_once(
            strategy=strategy,
            market=market,
            travel_times=travel_times,
            seed=base_seed + run_index,
        )
        for run_index in range(runs)
    ]
    daily_incomes = [result.average_daily_income for result in results]
    return {
        "score": statistics.fmean(daily_incomes),
        "metric": "average_daily_fare_income",
        "runs": runs,
        "days_per_run": SIMULATION_DAYS,
        "income_stddev": statistics.pstdev(daily_incomes),
        "average_served_trips": statistics.fmean(
            result.served_trips for result in results
        ),
        "average_relocations": statistics.fmean(
            result.relocation_count for result in results
        ),
        "pickup_probability": "1 - exp(-demand / 20)",
        "start_location_id": START_LOCATION_ID,
        "base_seed": base_seed,
        "first_run": asdict(results[0]),
    }


def _market_key(day_index: int, time_slot: int, location_id: int) -> int:
    return (day_index * 48 + time_slot) * ZONE_COUNT + location_id - 1


def _next_slot_time(value: datetime) -> datetime:
    slot_start = value.replace(
        minute=(value.minute // SLOT_MINUTES) * SLOT_MINUTES,
        second=0,
        microsecond=0,
    )
    return slot_start + timedelta(minutes=SLOT_MINUTES)
