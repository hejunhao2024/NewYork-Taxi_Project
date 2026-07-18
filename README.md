# New York Taxi Zone Recommendation

## 1. 项目背景

假设一名黄色出租车司机刚在区域 $i$ 结束一笔订单，当前时间为 $t$。系统需要
根据历史订单、当前位置和时间，推荐未来半小时最值得前往的三个接客区域。

这是一个时空推荐与决策优化问题，不是“用户—商品”协同过滤。算法需要考虑：

- 候选区域下一时段的需求；
- 从当前区域前往候选区域的时间成本；
- 候选区域的历史平均收入；
- 星期和半小时时段对需求的影响。

数据集没有稳定的司机身份、车辆身份和空载轨迹，因此本项目定义为“基于区域级
历史订单的接客区域推荐”，不是个性化司机推荐或真实平台派单系统。

## 2. 出租车区域与时间

纽约共有 263 个有效 Taxi Zone，对应 `LocationID=1..263`。区域 264 和 265
不纳入本项目。

![NYC Taxi Zones by LocationID](docs/images/taxi_zones_map_labeled.png)

一天划分为 48 个半小时时段：

```text
00:00-00:29 -> time_slot = 0
00:30-00:59 -> time_slot = 1
...
23:30-23:59 -> time_slot = 47
```

`weekday` 使用 Python 约定：周一为 0，周日为 6。

## 3. 仓库结构

```text
data/
├── raw/                              # 2023 年 1、2 月 TLC 原始订单
├── meta/                             # 区域名称与地图边界
└── processed/                        # 课程提供或脚本生成的数据

docs/images/                        # README 图片

src/
├── 1_data_clean/                    # Part 1：学生数据清洗代码
├── 2_recommendation_algorithm/       # Part 2：Baseline 填空与矩阵生成
├── 3_extension_task/                 # Part 3：学生扩展任务代码
└── eval/                             # 策略加载、参考公式评分和收入模拟

tmp/                               # 本地临时输出，不作为提交内容
```

### 任务与文件

| 部分 | 学生工作目录 | 主要参考文件 | 主要产物 |
| --- | --- | --- | --- |
| Part 1 数据清洗 | `src/1_data_clean/` | `data/README.md` | 清洗代码、清洗规则与数据统计 |
| Part 2 基础推荐 | `src/2_recommendation_algorithm/` | `baseline_1.py`、`baseline_2_1.py`、`baseline_2_2.py` | 可被评测器调用的 `recommend` |
| Part 2 评测 | `src/eval/` | [`src/eval/README.md`](src/eval/README.md) | `formula_score` 和 `simulator_score` |
| Part 3 扩展任务 | `src/3_extension_task/` | 本 README 的 Part 3 | 扩展算法、评测代码与实验结果 |

`src/1_data_clean/` 和 `src/3_extension_task/` **不提供起始模板文件**。这两个空目录
是学生代码的统一放置位置，学生按自己的方案创建 Python 文件即可。

## 4. 环境配置

```bash
conda create -n nyc-taxi python=3.11 -y
conda activate nyc-taxi
pip install -r requirements.txt
```

从仓库根目录运行 Python 模块时，在命令前加：

```bash
PYTHONPATH=src
```

## 5. 数据说明

项目使用 NYC TLC Yellow Taxi Trip Records。数据来源、字段和表头详见
[`data/README.md`](data/README.md)。

### 原始数据

```text
data/raw/yellow_tripdata_2023-01.parquet
data/raw/yellow_tripdata_2023-02.parquet
```

### 已准备数据

| 文件 | 内容 |
| --- | --- |
| `data/processed/train.parquet` | 1 月 1-24 日订单，2,346,531 行，未清洗 |
| `data/processed/validation.parquet` | 1 月 25-31 日订单，719,593 行，未清洗 |
| `data/processed/test_input.parquet` | 2 月评测查询，10,080 行，不含答案 |
| `data/processed/zone_time_statistics.parquet` | 仅由训练集统计的区域需求与平均车费 |
| `data/processed/travel_time_matrix_dijkstra.csv` | 仅由训练集生成的 263×263 有向最短时间矩阵 |
| `data/processed/manifest.json` | 时间窗口、行数、字段和产物信息 |

训练、验证和测试必须按日期顺序划分，不能随机拆分订单。策略代码不得读取
`yellow_tripdata_2023-02.parquet`；二月真实订单只能由评测器使用。

---

## Part 1：数据清洗

### 代码放置

在 `src/1_data_clean/` 中自行创建清洗代码。本部分没有指定函数名、文件名或命令行接口，
但代码必须能够重新运行并复现报告中的清洗结果。

### 基本要求

至少处理：

- `PULocationID` 或 `DOLocationID` 不在 `1..263`；
- 上下车时间缺失或下车不晚于上车；
- 行程时长、行程距离或费用明显异常；
- 必要字段缺失与重复记录。

并根据上车时间构造日期、`weekday`、`time_slot` 和是否为工作日等特征。

清洗阈值由学生自行决定，但需要在报告中说明：

- 清洗前后的订单数量；
- 删除了哪些异常数据；
- 清洗阈值及其理由；
- 训练集和验证集的日期范围。

---

## Part 2：基础推荐算法

### 统一策略接口

策略文件不需要读取 `test_input.parquet`，也不需要生成预测 CSV。学生只需实现：

```python
def recommend(
    current_datetime: datetime,
    current_location_id: int,
) -> list[int]:
    """Return three distinct LocationIDs in ranked order."""
```

评测器会动态加载策略文件并调用该函数。

### Baseline 1：热门区域

学生在 `src/2_recommendation_algorithm/baseline_1.py` 中完成 `recommend` 的 TODO。
历史上车数量直接作为推荐分数：

$$
\operatorname{Score}_1(j,s)=
\operatorname{PickupCount}(j,s+1)
$$

对 263 个区域的分数从高到低排序，返回 Top-3。分数相同时，`LocationID`
较小的区域优先。该方法不使用当前位置、平均车费或移动时间。

### Baseline 2：需求、收入和移动成本

#### 第一步：生成最短时间矩阵

`src/2_recommendation_algorithm/baseline_2_1.py` 从训练集建立 263 节点的带权有向图。
如果训练订单中出现过 $A\rightarrow B$，就建立边 $A\rightarrow B$，边权为该 OD
组合的平均行程时间。然后从每个节点运行 Dijkstra，不可达位置保留为 `inf`。

矩阵已放在 `data/processed/travel_time_matrix_dijkstra.csv`。如需重新生成：

```bash
PYTHONPATH=src python3 -m 2_recommendation_algorithm.baseline_2_1 \
  --train data/processed/train.parquet \
  --output data/processed/travel_time_matrix_dijkstra.csv
```

#### 第二步：计算推荐

学生在 `src/2_recommendation_algorithm/baseline_2_2.py` 中完成 `recommend` 的 TODO：

$$
\operatorname{Score}_2(i,j,s)=
\frac{
\widehat{\operatorname{Demand}}(j,s+1)\times
\widehat{\operatorname{Fare}}(j,s+1)
}{
\operatorname{TravelTime}(i,j)+\lambda
}
$$

需要为 263 个候选区域计算效用，将不可达区域的效用设为 0，再返回 Top-3。

### 评测

评测代码位于 `src/eval/`。评测器的完整输入、随机模拟状态转移、概率函数、
时间换算和收入定义见 [`src/eval/README.md`](src/eval/README.md)。

评测器同时输出：

- `formula_score`：以参考效用作为相关性的 `NDCG@3`；
- `simulator_score`：100 次完整二月模拟的平均日收入。

评测 Baseline 1：

```bash
PYTHONPATH=src python3 -m eval.evaluate \
  --strategy src/2_recommendation_algorithm/baseline_1.py \
  --output tmp/baseline_1_evaluation.json
```

评测 Baseline 2：

```bash
PYTHONPATH=src python3 -m eval.evaluate \
  --strategy src/2_recommendation_algorithm/baseline_2_2.py \
  --output tmp/baseline_2_evaluation.json
```

---

## Part 3：扩展任务

### 代码放置

所有扩展任务代码统一放在 `src/3_extension_task/`。本部分没有起始模板文件，学生
根据所选方向自行设计文件结构、函数接口和评测代码。

每组只需选择一个扩展方向，并完成：

1. 一个清晰可复现的基础方法；
2. 一个有明确动机的改进方法；
3. 一套与问题定义一致的评价流程；
4. 基础方法与改进方法的实验对比。

### 方向一：多步区域规划

连续规划未来 $T=3\sim6$ 个半小时时段，比较单步贪心和多步规划。可以使用
动态规划、Beam Search、有限深度搜索、时空图搜索或启发式搜索。至少报告
累计收入、空驶时间、接单次数和算法运行时间。

### 方向二：多辆出租车统一调度

同时为多辆出租车分配目标区域，减少车辆过度集中。可以使用带容量限制的贪心分配、
二分图匹配、最小费用最大流、区域配额或局部搜索。需要明确订单匹配、区域容量、
空驶与等待规则，并报告车队总收入、平均收入、空驶成本和需求覆盖率。

### 方向三：动态推荐分数与参数优化

比较固定权重和根据星期、高峰期、区域类型或近期收益动态调整的权重。可以使用
网格搜索、随机搜索、坐标搜索或局部搜索。所有参数只能根据训练集和验证集确定，
不得根据测试结果反复调整。

### 方向四：交互式出租车推荐系统

将已完成的推荐算法接入交互式地图。系统至少支持选择时间和当前区域、高亮
Top-3、展示预计需求/收入/移动成本，并支持修改输入后重新计算。只绘制静态地图
或预先生成结果，不视为完成本方向。

### 扩展任务报告

报告至少说明：

1. 问题输入、输出和优化目标；
2. 基础方法与改进方法；
3. 数据结构、核心算法与复杂度；
4. 评价规则、实验设置和对比结果；
5. 方法局限性。

---

## 6. 完成检查

提交前确认：

- Part 1 的清洗代码位于 `src/1_data_clean/`；
- Baseline 1 和 Baseline 2 都能通过统一 `recommend` 接口返回三个不重复区域；
- 策略代码没有读取二月真实订单；
- `eval.evaluate` 能输出 `formula_score` 和 `simulator_score`；
- Part 3 代码位于 `src/3_extension_task/`；
- 报告中说明了数据清洗、算法设计、复杂度、评测设置和实验结果。
