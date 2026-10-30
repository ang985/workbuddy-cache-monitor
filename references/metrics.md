# 指标口径与异常处理

## 一、基础量

| 量 | 字段 | 说明 |
|---|---|---|
| 输入 token | `input_tokens` / `inputTokens` | 包含缓存命中的部分 |
| 缓存命中 token | `cache_read_input_tokens` / `cached_tokens` | 命中前缀缓存、按折扣计费的部分 |
| 缓存写入 token | `cache_creation_input_tokens` | 当前实测恒为 0，保留兼容 |
| 输出 token | `output_tokens` / `outputTokens` | 模型生成部分 |
| 未命中 token | `input - cache_read` | 真正参与计算的输入，成本主项 |

## 二、派生指标

| 指标 | 公式 | 用途 |
|---|---|---|
| 缓存命中率 | `cache_read / input` | 核心指标；越高说明前缀复用越好 |
| 任务命中率 | 该 trace 内 `Σcache_read / Σinput` | 回答「这次任务省了多少」 |
| 命中率分布 | P50 / P90 | 避免被极端值误导；P50 比均值更稳 |
| 单次任务成本权重 | `Σinput / Σ调用次数` | 识别上下文膨胀 |
| 低命中任务数 | `count(hit_rate < 90%)` | 异常巡检的抓手 |

**为什么用 P50 而不是均值**：命中率分布是长尾偏斜的（大量 99% + 少数刚起步的 20%），均值会被小任务拉低，P50 更能代表典型水平。

## 三、任务切分

一次任务 = 一个 `traceId` 下的全部 API 调用。

- p50 请求数 10 次，最长 196 次 → 一个 trace 可能持续很久，属于正常的长任务。
- 无 `traceId` 时退化为 `sess:<sessionId>` 粒度，并在指标中标记。
- 同一 `traceId` 可能跨多轮用户输入（多轮追问算同一次任务），这是按"任务"而非"轮次"统计的有意选择。

## 四、异常分类与处理

| 异常 | 判定 | 处理 |
|---|---|---|
| `parse_error` | 行 JSON 解析失败 | 跳过该行，记入 `anomalies`，不中断扫描 |
| `cache_gt_input` | `cache_read > input` | 截断为 `input`，保证命中率 ≤ 100%，并记 flag |
| `cache_without_input` | `input = 0` 但有命中 | 视为无效记录，不计入分母 |
| `no_trace_id` | 缺 `traceId` | 退化到 session 粒度，任务表标记 |
| `no_model` | 缺模型名 | 归入 `unknown`，仍计入总量 |
| `bad_timestamp` | 时间戳 ≤ 0 或超前 1 天以上 | 保留数值，但排除在时间筛选外 |
| 半行写入 | 文件末尾无换行 | 只消费到最后一个 `\n`，余量留待下次 |
| 文件截断/轮转 | 当前 size < 上次记录 | offset 归零重扫，靠指纹主键去重保证幂等 |
| 重复计数 | 同一 usage 在行内嵌套出现多次 | 同结构指纹去重 |

## 五、已知偏差

1. **首轮必然低**。任务第一次调用没有可复用前缀，命中率天然为 0；短任务（1~2 次调用）的单任务命中率会显著拖低分布尾部。
2. **压缩会重置缓存**。上下文压缩后前缀改变，命中率出现台阶式下跌，随后爬升。
3. **换模型即失效**。不同模型的缓存不共享，切换档位后需重新积累。
4. **日志可能有延迟**。会话进行中最后几条记录可能尚未落盘，实时数值略低于最终值；`Stop` hook 触发时通常已完整。
5. **`traces/` 覆盖率低**。`modelInfo` 仅约 17% 的 trace 具备，不能作为主口径，只用于交叉验证。

## 六、自检方法

若对某个数字存疑，按以下顺序核对：

```bash
# 1. 看结构化明细，拿到 traceId
python scripts/report.py --json --range 7d > /tmp/r.json
# 2. 回原始日志中定位该 traceId 的所有 usage 行
grep -c "\"traceId\":\"<trace_id>\"" ~/.workbuddy/projects/**/*.jsonl
```

口径一致的前提下，脚本聚合值应等于原始行手工求和。
