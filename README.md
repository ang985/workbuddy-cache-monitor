# workbuddy-cache-monitor

**WorkBuddy 缓存命中率监控 · Cache Hit Rate Monitor for WorkBuddy**

[中文](#中文) · [English](#english)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) · ![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg) · ![Dependencies](https://img.shields.io/badge/dependencies-none-lightgrey.svg)

按**任务**维度统计 WorkBuddy 的上下文缓存（prompt cache）命中率与 token 消耗构成，输出文本报告和一份可离线打开的单文件 HTML 看板。

Track per-task prompt-cache hit rate and token usage in WorkBuddy, with text reports and a self-contained offline HTML dashboard.

> 数据全部来自本机日志，**完全不联网**，不读取也不上传任何对话正文。
> All data comes from local logs — **no network, no upload**, and conversation content is never read.

---

## 中文

### 它能回答什么

- 我最近 7 天 / 30 天的缓存命中率是多少？
- 刚才那次任务命中率为什么只有 12%？是哪一轮没命中？
- 哪个模型、哪个工作空间最费 token？
- 输入 token 里有多少走了缓存、多少是现算的？命中率的 P50 / P90 分布如何？

### 安装

```bash
git clone https://github.com/ang985/workbuddy-cache-monitor.git \
  ~/.workbuddy/skills/workbuddy-cache-monitor
```

需要 **Python 3.8+**，只用标准库，无第三方依赖。

### 快速开始

```bash
S="$HOME/.workbuddy/skills/workbuddy-cache-monitor/scripts"

python "$S/collect.py"              # 1. 增量同步本机日志到本地 SQLite（可反复执行，不重复计数）
python "$S/report.py" --range 7d    # 2. 出文本报告
```

生成可视化看板：

```bash
python "$S/report.py" --html --range all -o "$PWD/dashboard.html"
```

### report.py 参数

| 参数 | 说明 |
|---|---|
| `--range 24h｜7d｜30d｜all｜Nd` | 时间范围，支持任意 `Nd`（如 `14d`） |
| `--workspace <目录名>` | 只统计某个工作空间 |
| `--json` | 输出结构化 JSON，便于二次处理 |
| `--html -o <路径>` | 生成单文件 HTML 看板 |

看板支持切换时间范围、按工作空间筛选、点击表头排序，命中率低于 90% 的任务自动标红。单文件、零外部依赖，可离线打开或直接分享。

### 每轮回答末尾自动显示（可选）

在 `~/.workbuddy/settings.json` 注册 `UserPromptSubmit` 钩子，即可在每轮对话中自动注入本会话指标：

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

Windows 下 hook 由 Git Bash 执行，路径**必须用正斜杠 + 双引号**，否则反斜杠会被当作转义符导致静默失败。

> 桌面客户端（如 0.8.2）实测不触发 `Stop` 事件，也不渲染 `statusLine`。因此本技能在桌面端只依赖上面的 `UserPromptSubmit` 钩子。

### 指标口径（三个数不一样是正常的）

| 口径 | 含义 |
|---|---|
| **本会话命中率** | 当前这次对话的命中/输入比值 |
| **全局命中率** | 全部历史任务的汇总比值（看板上的「整体命中率」即此项，会随筛选条件变化） |
| **P50** | 任务命中率的**中位数**，不是平均值；与整体命中率是两个不同的统计量 |

命中率 = `cache_read_input_tokens / input_tokens`。输出 token 不参与缓存计算，单独统计。

### 数据来源

| 来源 | 路径 | 用途 |
|---|---|---|
| 会话日志（主） | `~/.workbuddy/projects/<工作空间>/<session>.jsonl` | 每条 API 调用的 `usage` 与 `traceId` |
| 追踪日志（辅） | `~/.workbuddy/traces/<pid>/trace_*.jsonl` | span 级耗时，覆盖率有限，仅作补充 |

同一次任务内的所有 API 调用共享同一个 `traceId`；脚本按 `traceId` 分组，把请求级用量还原成任务级指标。无 `traceId` 时退化为 session 粒度。采集按字节偏移增量读取，重复执行不会重复计数。

更完整的指标定义、异常分类与已知偏差见 [references/metrics.md](references/metrics.md)。

### 隐私与权限

| 项 | 说明 |
|---|---|
| 读取 | `~/.workbuddy/projects/**/*.jsonl`，仅取 `usage` / `traceId` / `model` / `timestamp` 等元数据 |
| 写入 | `~/.workbuddy/cache-monitor/wbcc.db`（本地 SQLite）与用户指定的报告输出路径 |
| 明确不读 | 消息正文 `content`、提示词全文、任何文件内容 |
| 网络 | 完全不联网，无任何远端请求 |
| 配置 | hook 需用户手动在 `~/.workbuddy/settings.json` 中添加，技能本身不会修改该文件 |

### 目录结构

```
workbuddy-cache-monitor/
├── SKILL.md               技能定义与工作流（AI 读取的入口）
├── README.md
├── LICENSE
├── references/
│   ├── metrics.md         指标定义与口径说明
│   └── internals.md       实现细节与平台实测（排查用）
└── scripts/
    ├── collect.py         增量同步日志 → SQLite
    ├── report.py          文本报告 / JSON / HTML 看板
    ├── prompt_hook.py     UserPromptSubmit 钩子（每轮注入指标）
    ├── hook.py            Stop 钩子（可选，桌面端不触发该事件）
    ├── selftest.py        自检脚本
    └── package.py         打包校验（--skillhub 生成上架用 zip）
```

自检与打包：

```bash
python "$S/selftest.py"            # 自检
python "$S/package.py" --skillhub  # 生成 SKILL.md 位于根目录的 zip
```

### License

[MIT](LICENSE) © 2026 ang985

---

## English

### What it answers

- What is my cache hit rate over the last 7 / 30 days?
- Why was the last task only at 12%? Which call missed the cache?
- Which model or workspace burns the most tokens?
- How much of the input came from cache versus being re-computed? What is the P50 / P90 distribution?

### Install

```bash
git clone https://github.com/ang985/workbuddy-cache-monitor.git \
  ~/.workbuddy/skills/workbuddy-cache-monitor
```

Requires **Python 3.8+**. Standard library only, zero third-party dependencies.

### Quick start

```bash
S="$HOME/.workbuddy/skills/workbuddy-cache-monitor/scripts"

python "$S/collect.py"              # 1. Incrementally sync local logs into SQLite (idempotent)
python "$S/report.py" --range 7d    # 2. Print the text report
```

Generate the dashboard:

```bash
python "$S/report.py" --html --range all -o "$PWD/dashboard.html"
```

### report.py options

| Option | Description |
|---|---|
| `--range 24h｜7d｜30d｜all｜Nd` | Time range; any `Nd` works (e.g. `14d`) |
| `--workspace <dir>` | Restrict to one workspace |
| `--json` | Emit structured JSON for further processing |
| `--html -o <path>` | Write a single-file HTML dashboard |

The dashboard supports range switching, workspace filtering, click-to-sort headers, and highlights tasks below 90% hit rate in red. It is a single file with no external dependencies — open it offline or share it as-is.

### Optional: show metrics at the end of every reply

Register a `UserPromptSubmit` hook in `~/.workbuddy/settings.json` to inject the current session's metrics into each turn:

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

On Windows, hooks run through Git Bash — paths **must use forward slashes inside double quotes**, otherwise backslashes are treated as escapes and the hook fails silently.

> The desktop client (e.g. 0.8.2) does not fire `Stop` events and does not render `statusLine`. On desktop, this skill relies solely on the `UserPromptSubmit` hook above.

### Three numbers, three meanings (they differ on purpose)

| Metric | Meaning |
|---|---|
| **Session hit rate** | Cache-hit / input ratio for the current conversation only |
| **Global hit rate** | Aggregate ratio across all historical tasks (this is the dashboard's "overall" figure; it shifts with filters) |
| **P50** | The **median** of per-task hit rates, not the mean — a different statistic from the global hit rate |

Hit rate = `cache_read_input_tokens / input_tokens`. Output tokens are excluded from cache accounting and reported separately.

### Data sources

| Source | Path | Use |
|---|---|---|
| Session logs (primary) | `~/.workbuddy/projects/<workspace>/<session>.jsonl` | Per-call `usage` plus `traceId` |
| Trace logs (secondary) | `~/.workbuddy/traces/<pid>/trace_*.jsonl` | Span-level timing; limited coverage, cross-check only |

Every API call within one task shares the same `traceId`. The scripts group by `traceId` to roll request-level usage up into task-level metrics, and fall back to session granularity when `traceId` is absent. Collection reads by byte offset, so repeated runs never double-count.

See [references/metrics.md](references/metrics.md) for full metric definitions, anomaly classification, and known biases.

### Privacy & permissions

| Item | Detail |
|---|---|
| Reads | `~/.workbuddy/projects/**/*.jsonl` — only metadata such as `usage`, `traceId`, `model`, `timestamp` |
| Writes | `~/.workbuddy/cache-monitor/wbcc.db` (local SQLite) and the report path you specify |
| Never reads | Message bodies (`content`), full prompts, or any file contents |
| Network | None at all — no remote requests |
| Config | Hooks must be added manually to `~/.workbuddy/settings.json`; this skill never edits that file |

### Layout

```
workbuddy-cache-monitor/
├── SKILL.md               Skill definition and workflow (entry point for the AI)
├── README.md
├── LICENSE
├── references/
│   ├── metrics.md         Metric definitions and methodology
│   └── internals.md       Implementation notes and platform findings
└── scripts/
    ├── collect.py         Incremental log sync → SQLite
    ├── report.py          Text report / JSON / HTML dashboard
    ├── prompt_hook.py     UserPromptSubmit hook (per-turn metrics injection)
    ├── hook.py            Stop hook (optional; desktop client never fires it)
    ├── selftest.py        Self-test
    └── package.py         Packaging checks (--skillhub builds the upload zip)
```

Self-test and packaging:

```bash
python "$S/selftest.py"            # Self-test
python "$S/package.py" --skillhub  # Build a zip with SKILL.md at the root
```

### License

[MIT](LICENSE) © 2026 ang985
