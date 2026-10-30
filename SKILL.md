---
name: workbuddy-cache-monitor
display_name: 缓存命中率监控
display_name_en: Cache Hit Monitor
description: 统计并展示 WorkBuddy 每次任务的上下文缓存命中率、命中/未命中 token、请求次数与时间分布。当用户询问缓存命中率、缓存复用、命中率低、token 消耗构成、某次任务花了多少、想看看板或用量报告时使用。
description_zh: 按任务维度统计 WorkBuddy 的缓存命中率与 token 消耗构成，输出文本报告与可视化看板。仅读取本机 WorkBuddy 日志，不联网、不上传数据。
description_en: Track per-task prompt-cache hit rate and token composition in WorkBuddy, with text reports and a self-contained HTML dashboard. Reads local WorkBuddy logs only — no network, no upload.
examples_zh:
  - "看看我最近 7 天的缓存命中率"
  - "刚才那次任务命中率为什么这么低"
  - "生成一份缓存命中率看板"
  - "哪个模型、哪个工作空间最费 token"
  - "让每轮回答末尾都显示当前命中率"
  - "命中率和 P50 为什么不一样"
examples_en:
  - "Show my cache hit rate for the last 7 days"
  - "Why was the hit rate so low on my last task"
  - "Generate a cache hit rate dashboard"
  - "Which model or workspace burns the most tokens"
  - "Show cache metrics at the end of every reply"
  - "Why do the hit rate and P50 differ"
version: 1.6.0
changelog: "v1.6.0: 移除失效的状态栏章节与相关死代码；新增 FAQ 与完整示例；实现细节移入 references/internals.md，正文精简约四成"
author: "ang985"
allowed-tools: Read,Write,Bash,Glob
---

# 缓存命中率监控

按**任务**维度统计 WorkBuddy 的上下文缓存复用情况，输出文本报告和单文件 HTML 看板。
数据全部来自本机日志，**不联网、不上传任何内容**。

## 前置条件

- 仅对 **WorkBuddy 桌面客户端**有效（数据来自客户端本地日志，其他环境无此目录）。
- 需要 **Python 3.8+**，只用标准库，无第三方依赖。
- 建议在 **Bash**（Git Bash / macOS / Linux shell）中执行。

## 何时使用

用户提到以下任意情形时加载本技能：缓存命中率 / 缓存复用 / cache hit rate；某次任务或某天消耗多少
token、命中多少；命中率为什么低、怎么提高；要一份用量报告或可视化看板。

## 快速开始

```bash
S="$HOME/.workbuddy/skills/workbuddy-cache-monitor/scripts"

python "$S/collect.py"              # 1. 增量同步（可反复执行，不重复计数）
python "$S/report.py" --range 7d    # 2. 文本报告
```

### 完整示例：从零到看板

```bash
# ① 首次同步全部历史日志
python "$S/collect.py"
# 输出：[sync] 文件 36 个 · 新行 14452 · 入库 4452 条 · 跳过 0 · 解析失败 0

# ② 看最近 7 天的汇总
python "$S/report.py" --range 7d

# ③ 只看某个工作空间，并导出 JSON 核对
python "$S/report.py" --range 30d --workspace <工作空间目录名> --json > r.json

# ④ 生成看板并用产物呈现能力打开
python "$S/report.py" --html --range all -o "$PWD/wb-cache-dashboard.html"
# 然后调用 present_files 打开该文件
```

### report.py 参数

| 参数 | 说明 |
|---|---|
| `--range 24h\|7d\|30d\|all\|Nd` | 时间范围，支持任意 `Nd`（如 `14d`） |
| `--workspace <目录名>` | 只统计某个工作空间 |
| `--json` | 输出结构化 JSON，便于二次处理 |
| `--html -o <路径>` | 生成单文件 HTML 看板 |

看板支持时间范围切换、工作空间筛选、点击表头排序，命中率低于 90% 的任务自动标红。

## 每轮回答末尾显示指标（可选）

在 `~/.workbuddy/settings.json` 注册 `UserPromptSubmit` 钩子：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"$HOME/.workbuddy/skills/workbuddy-cache-monitor/scripts/prompt_hook.py\"",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

配置后每轮自动采集并注入一行，模型在回答末尾原样输出：

```
— 本会话 98.0% · 423 次 | 全局 98.1% —
```

三条要点：

1. **Windows 下路径必须用正斜杠 + 双引号**，反斜杠会被当作转义符导致静默失败。
2. 桌面端**没有状态栏**能力，`statusLine` 字段不渲染；指标只能通过本钩子注入的文本行与看板查看。
3. 钩子必须输出纯 ASCII（`ensure_ascii=True`）且不能依赖 stdin，否则注入会静默失效——细节见
   @references/internals.md。

## 常见问题 FAQ

**Q1 命中率怎么算？输出 token 算不算？**
命中率 = `cache_read / input`，输出 token 不参与缓存计算，单独统计。

**Q2 底部显示 98.0%，看板写 98.11%，哪个对？**
都对，口径不同：底部是本次会话，看板「整体命中率」是全部历史。差 0.2–0.5 个点属正常。

**Q3 P50 为什么和整体命中率差很远？**
P50 是各任务命中率的**中位数**，整体命中率是总量比值。大量长任务 99% + 少数短任务 20% 时两者必然分叉。
判断趋势看 P50，判断成本看整体。

**Q4 单个任务只有 12%，是不是出问题了？**
多数不是。首轮没有可复用前缀、上下文被压缩、切换模型档位，都会让命中率台阶式下跌。

**Q5 每轮末尾没有出现那行字？**
按顺序查：① `settings.json` 是否注册了 `UserPromptSubmit`；② 路径是否用了反斜杠（Windows 常见）；
③ 看 `~/.workbuddy/cache-monitor/prompt-hook.log` 有没有新行——有 `called` 说明钩子跑了，没有说明宿主
没调用；④ 会话请求数少于 3 时会退回 24 小时口径，属正常。

**Q6 看板一直不更新？**
钩子刷新有 120 秒 TTL，避免每轮都付生成开销。等两分钟或重新打开文件即可。

**Q7 底部那行能点开看板吗？**
不能。桌面端不渲染 Markdown 链接，宿主的可点地址每次带随机端口和哈希，无法固化。看板需单独打开一次，
之后钩子在后台覆盖同一文件，预览面板会自动重载。

**Q8 会重复计数吗？**
不会。采集按字节偏移增量读取，并按记录指纹去重；重复执行 `collect.py` 结果不变。

**Q9 数据会被上传吗？**
不会。全程只读本机 `~/.workbuddy/projects/**/*.jsonl`，无任何网络请求。

**Q10 换机器或重装后历史数据没了？**
数据在本机 SQLite（`~/.workbuddy/cache-monitor/wbcc.db`）。换机器需重新 `collect.py`，但只能采到新机器上
仍存在的日志。

## 口径

- 命中率 = `cache_read` / `input`
- `input` 已包含 `cache_read`，故「未命中」= `input - cache_read`
- 总量 = `input + output + cache_write`（命中部分不重复累加）
- 完整定义、异常分类与已知偏差见 @references/metrics.md

## 权限与隐私

| 项 | 说明 |
|---|---|
| 读取 | `~/.workbuddy/projects/**/*.jsonl`，仅取 `usage` / `traceId` / `model` / `timestamp` 等元数据 |
| 写入 | `~/.workbuddy/cache-monitor/wbcc.db` 与用户指定的报告输出路径 |
| 明确不读 | 消息正文 `content`、提示词全文、任何文件内容 |
| 网络 | 完全不联网 |
| Hook | 需用户手动在 `~/.workbuddy/settings.json` 添加，技能不修改该文件 |

## 注意事项

1. **不要读消息正文**。只解析 `usage` / `traceId` / `model` 等元数据，避免把对话内容带入上下文或写库。
2. **低命中率不一定是问题**。首轮、上下文压缩后、更换模型时前缀缓存必然失效，要看趋势而非单点。
3. **报告数字要与原始日志可核对**。用户质疑时用 `--json` 输出并指出对应 `traceId`，不要只给结论。
4. `traces/` 的 `modelInfo` 覆盖率仅约 17%，不能作为主口径。
5. 客户端升级若改了日志字段，先跑 `python "$S/selftest.py"` 与一次 `collect.py` 确认解析仍正常。
