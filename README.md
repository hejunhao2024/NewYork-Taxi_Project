# New York Taxi 项目

本仓库当前整理了纽约 Yellow Taxi 项目已下载和已生成的数据。训练集与验证集**不由助教清洗**，保留原始订单字段，数据清洗由学生在项目中完成。

## 项目约定

对查询 `(current_location_id=i, query_time=t)`，司机在区域 `i` 完成当前订单。`t` 所在的半小时槽为当前槽；推荐目标是**下一个完整半小时槽**，而不是从精确下车时刻起滚动 30 分钟。

基础推荐效用为：

```text
Score(i, j, t) = Demand(j, t+1) × Fare(j, t+1) / (TravelTime(i, j, t) + λ)
```

其中 `Fare` 默认指 `fare_amount` 的区域平均值，即预计订单基础车费；`total_amount` 可用于扩展的综合收益分析，不表示司机净收入。评测中的“真实收益”指测试集上的区域级真实效用，而非某位司机实际到手的收入。

## 目录与用途

| 路径 | 用途 |
| --- | --- |
| `data/raw/yellow_tripdata_2023-01.parquet` | 2023 年 1 月 Yellow Taxi 原始订单；用于生成训练集和验证集。 |
| `data/raw/yellow_tripdata_2023-02.parquet` | 2023 年 2 月 Yellow Taxi 原始订单；用于生成不含答案的评测输入。 |
| `data/meta/taxi_zone_lookup.csv` | 区域编号、行政区和区域名称对照表。 |
| `data/meta/taxi_zones.zip` | Taxi Zone 地理边界 Shapefile 文件包，可用于地图可视化。 |
| `data/processed/train.parquet` | 以 `tpep_dropoff_datetime` 切分的 1 月 1 日至 24 日订单；未清洗。 |
| `data/processed/validation.parquet` | 以 `tpep_dropoff_datetime` 切分的 1 月 25 日至 31 日订单；未清洗。 |
| `data/processed/test_input.parquet` | 由 2 月有效下车事件抽样得到的评测查询输入；不含标签或未来订单信息。 |
| `data/processed/zone_time_statistics.parquet` | 仅由训练集统计的区域 × 星期 × 半小时需求示例。 |
| `data/processed/manifest.json` | 处理数据的时间窗口、抽样参数、行数和字段清单。 |

`data/raw/yellow_tripdata_2023-02.parquet` 是助教生成隐藏测试标签的私有来源。面向学生发布的数据包只能提供 `test_input.parquet`，不得包含该文件或测试标签。

## 表头示例

### 原始订单、训练集与验证集

`data/raw/yellow_tripdata_2023-01.parquet`、`data/processed/train.parquet` 和 `data/processed/validation.parquet` 的表头如下：

```text
VendorID, tpep_pickup_datetime, tpep_dropoff_datetime, passenger_count,
trip_distance, RatecodeID, store_and_fwd_flag, PULocationID, DOLocationID,
payment_type, fare_amount, extra, mta_tax, tip_amount, tolls_amount,
improvement_surcharge, total_amount, congestion_surcharge, airport_fee
```

`data/raw/yellow_tripdata_2023-02.parquet` 的其余字段相同；最后一列在源文件中写作 `Airport_fee`（首字母大写）。

- `PULocationID` / `DOLocationID`：上车 / 下车区域编号；可与区域对照表连接。
- `tpep_pickup_datetime` / `tpep_dropoff_datetime`：上车 / 下车时间。
- `fare_amount`、`tip_amount`、`total_amount`：订单收益相关字段。

### 区域编号对照表

`data/meta/taxi_zone_lookup.csv`：

```text
LocationID, Borough, Zone, service_zone
```

### 隐藏评测输入

`data/processed/test_input.parquet`：

```text
query_id, current_location_id, query_time, weekday, time_slot
```

- `current_location_id` 为司机完成当前订单后的所在区域。
- `weekday` 以周一为 `0`，`time_slot` 为当前半小时编号 `0`–`47`。
- 预测目标是下一个槽：`time_slot + 1`；当当前槽为 `47` 时，目标槽为次日的 `0`。

### 区域 × 时间段需求统计示例

`data/processed/zone_time_statistics.parquet`：

```text
pickup_location_id, weekday, time_slot, pickup_count, mean_fare_amount,
mean_total_amount
```

该表只使用训练集；`pickup_count` 是对应区域、星期和半小时内的上车订单数，后两列为该组订单的平均车费和平均总支付额。表中不存在的区域 × 星期 × 时间槽组合应视为需求 0。

## 数据来源

原始行程记录来自 [NYC Taxi & Limousine Commission Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page)。
