# 推荐策略评测

`evaluate.py` 接收一个 Python 策略文件，输出参考效用分数和随机模拟收入。
测试集读取、策略调用、司机状态转移和收入统计都由 `src/eval` 完成。

## 策略接口

策略文件必须实现：

```python
def recommend(
    current_datetime: datetime,
    current_location_id: int,
) -> list[int]:
    """Return three LocationIDs in ranked order."""
```

返回值必须包含三个不重复的区域编号，每个编号都在 `1..263` 中。模拟器
将 Top-1 作为司机接单失败后的下一个目标区域。

## 两个评测分数

`evaluate.py` 对同一个策略输出：

- `formula_score`：根据
  $\widehat{Demand}\times\widehat{Fare}/(TravelTime+\lambda)$ 计算参考效用，
  再以该效用作为相关性计算 `NDCG@3`；
- `simulator_score`：进行 100 次完整月份模拟后，司机日均车费收入的平均值。

`formula_score` 用于检查单步 Top-3 与参考效用函数的一致性；
`simulator_score` 用于衡量策略连续运行时的长期收入。

## 模拟数据

模拟器使用：

```text
data/raw/yellow_tripdata_2023-02.parquet
src/eval/travel_time_matrix1.csv
```

二月订单按“日期 + 半小时 slot + 上车区域”分组。仅保留：

- 上下车时间都在模拟月份内；
- `PULocationID` 和 `DOLocationID` 都在 `1..263`；
- 载客时间大于 0 分钟且不超过 240 分钟。

订单的 `fare_amount` 作为司机收入。缺失、非有限或负数车费按 0 处理。

## 模拟初始状态

| 参数 | 固定值 |
| --- | --- |
| 开始时间 | `2023-02-01 00:00` |
| 结束时间 | `2023-03-01 00:00` |
| 模拟天数 | 28 |
| 司机初始区域 | 132 |
| slot 长度 | 30 分钟 |
| 模拟次数 | 100 |
| 基础随机种子 | `20230717` |

第 $r$ 次模拟使用随机种子 `20230717 + r`，因此同一份代码的评测结果
可以复现。

## 接单概率

设当前日期、slot 和区域中有 $n$ 条真实订单，司机的接单概率为：

$$
p(n)=1-e^{-n/20}
$$

该函数满足 $p(0)=0$，随需求增加而单调递增，是严格凹函数，并在需求增大时
渐近于 1。例如：

| $n$ | $p(n)$ |
| ---: | ---: |
| 0 | 0.000 |
| 1 | 0.049 |
| 20 | 0.632 |
| 100 | 0.993 |

## 时间换算

空驶时间来自 `travel_time_matrix1.csv`，载客时间来自随机选中订单的
上下车时间差。分钟数 $m$ 转换为 slot 数：

$$
\operatorname{Slots}(m)=
\max\left(1,\left\lfloor\frac{m}{30}+0.5\right\rfloor\right)
$$

因此无论司机前往自己所在区域还是其他区域，都至少消耗 1 个 slot。
例如 14 分钟、15 分钟和 30 分钟都记为 1 个 slot，45 分钟记为 2 个 slot。

## 单次模拟流程

每当司机在某个 slot 开始时处于空闲状态，执行以下步骤：

1. 查找“当前日期 + 当前 slot + 当前区域”中的二月真实订单。
2. 根据订单数 $n$ 计算 $p(n)$，进行一次 Bernoulli 抽样。
3. 如果接单成功，从该时空单元的订单中均匀随机选择一条：
   - 将订单 `fare_amount` 加入累计收入；
   - 将司机位置更新为订单 `DOLocationID`；
   - 根据载客时间向前推进对应 slot 数。
4. 如果接单失败：
   - 调用 `recommend(current_datetime, current_location_id)`；
   - 选择返回列表中的 Top-1 作为目标区域；
   - 从时间矩阵读取空驶分钟数，将司机移动到目标区域；
   - 将时间向前推进对应 slot 数，空驶不产生收入。
5. 重复上述流程，直到当前时间到达或超过 `2023-03-01 00:00`。

## 收入分数

单次模拟的日均收入为：

$$
\operatorname{DailyIncome}=
\frac{\sum \operatorname{fare\_amount}}{28}
$$

`evaluate.py` 使用 100 个固定随机种子独立运行模拟器，最终：

$$
\operatorname{simulator\_score}=
\frac{1}{100}\sum_{r=1}^{100}\operatorname{DailyIncome}_r
$$

输出还包含日均收入标准差、平均接单数、平均空驶次数以及第一次模拟的
详细结果。

## 运行

```bash
PYTHONPATH=src python3 -m eval.evaluate \
  --strategy src/2_recommendation_algorithm/baseline_1.py \
  --output tmp/baseline_1_evaluation.json
```

策略代码不得读取二月真实订单。该数据只能由评测器用于随机接单和收入计算。
