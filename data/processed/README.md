# Processed course artifacts

These artifacts are generated from the official raw Yellow Taxi files by
`src/nyc_taxi_prep/prepare_data.py`.

- `train.parquet` contains raw January rows with drop-off time from January 1
  through January 24 inclusive.
- `validation.parquet` contains raw January rows with drop-off time from
  January 25 through January 31 inclusive.
- Neither raw partition is cleaned, filtered, or schema-normalized. Students
  are responsible for data cleaning.
- `zone_time_statistics.parquet` is a training-only helper table. It excludes
  only zones 264 and 265 and groups pickup demand by Monday-based weekday and
  a 30-minute slot numbered 0 through 47.
- `test_input.parquet` contains sampled February drop-off queries only; it has
  no targets or future trip information.

Run:

```sh
PYTHONPATH=src python3 -m nyc_taxi_prep.prepare_data \
  --january data/raw/yellow_tripdata_2023-01.parquet \
  --february data/raw/yellow_tripdata_2023-02.parquet \
  --output data/processed
```
