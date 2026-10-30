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
examples_en:
  - "Show my cache hit rate for the last 7 days"
  - "Why was the hit rate so low on my last task"
  - "Generate a cache hit rate dashboard"
version: 1.5.2
changelog: "v1.5.2: 看板工作空间名改为通用短名（不依赖本机命名前缀）；package.py 兼容 git 仓库，SkillHub 包排除仓库元文件；移除已废弃的 statusline.sh"
author: "ang985"
allowed-tools: Read,Write,Bash,Glob
---

# 缓存命中率监控

按**任务**维度统计 WorkBuddy 的上下文缓存复用情况。数据全部来自本机日志，**不联网、不上传任何内容**。

## 前置条件

- 仅对 **WorkBuddy 桌面客户端**有效（数据来自客户端本地日志，其他环境无此目录）。
- 需要 **Python 3.8+**（无需第三方依赖，只用标准库）。
- 建议在 **Bash**（Git Bash / macOS / Linux shell）中执行下方命令。

## 何时使用

用户提到以下任意情形时加载本技能：

- 缓存命中率 / 缓存复用 / cache hit rate
- 某次任务或某天消耗了多少 token、命中多少
- 命中率为什么低、怎么提高
- 要一份用量报告或可视化看板

## 权限与隐私声明

| 项 | 说明 |
|---|---|
| 读取 | `~/.workbuddy/projects/**/*.jsonl`（只读，仅取 `usage` / `traceId` / `model` / `timestamp` 等元数据字段） |
| 写入 | `~/.workbuddy/cache-monitor/wbcc.db`（本地 SQLite）与用户指定的报告输出路径 |
| 明确不读 | 消息正文 `content`、提示词全文、任何文件内容 |
| 网络 | 完全不联网，无任何远端请求 |
| Hook（可选） | 需用户手动在 `~/.workbuddy/settings.json` 添加配置，技能本身不修改该文件 |

## 数据来源

| 来源 | 路径 | 用途 |
|---|---|---|
| 会话日志（主） | `~/.workbuddy/projects/<工作空间>/<session>.jsonl` | 每条 API 调用的 `usage`：`input_tokens` / `output_tokens` / `cache_read_input_tokens`，并带 `traceId` |
| 追踪日志（辅） | `~/.workbuddy/traces/<pid>/trace_*.jsonl` | span 级耗时与调用结构，覆盖率有限，仅作补充 |

**关键机制**：同一次任务内的所有 API 调用共享同一个 `traceId`。脚本按 `traceId` 分组，把「请求级」用量还原成「任务级」指标。无 `traceId` 时退化为 `session` 粒度。

## 使用步骤

先设置脚本目录（安装在项目级时，把 `$HOME/.workbuddy/skills` 换成 `<项目>/.codebuddy/skills`）：

```bash
S="$HOME/.workbuddy/skills/workbuddy-cache-monitor/scripts"
```

### 1. 先同步增量数据

```bash
python "$S/collect.py"
```

按字节偏移增量读取，重复执行不会重复计数。输出形如：
`[sync] 文件 36 个 · 新行 14452 · 入库 4452 条 · 跳过 0 · 解析失败 0`

### 2. 出文本报告

```bash
python "$S/report.py" --range 7d
```

| 参数 | 说明 |
|---|---|
| `--range 24h\|7d\|30d\|all\|Nd` | 时间范围，也支持任意 `Nd`（如 `14d`） |
| `--workspace <目录名>` | 只统计某个工作空间 |
| `--json` | 输出结构化 JSON，便于二次处理 |
| `--html -o <路径>` | 生成单文件 HTML 看板 |

### 3. 生成看板

```bash
python "$S/report.py" --html --range all -o "$PWD/wb-cache-dashboard.html"
```

看板为单文件、无外部依赖，可离线打开；支持时间范围切换、工作空间筛选、点击表头排序，命中率低于 90% 的任务自动标红。生成后用 `present_files` 展示给用户。

**明细表在窄容器（预览面板常见 300–400px）下的横向查看**——10 列必然溢出，
关键是不能让表格自然撑开，否则横向滚动条会落到第 200 多行之后、页面最底部，
用户只看到被裁掉的列，以为"滑不动"：

| 设计 | 作用 |
|---|---|
| `.wrap{max-height:min(46vh,420px);overflow:auto}` | 表格在**固定高度的框内**滚动，横条永远贴在框底，不被行数顶走 |
| `#tbl{min-width:980px}` | 强制溢出，否则列被压缩到容器宽度、根本不产生滚动 |
| `.wrap{cursor:grab}` + pointer 事件 | **按住表格直接拖动**，不依赖滚动条是否可见（最可靠的一路） |
| `.sec{position:sticky;top:0}` + `◀ ▶` 按钮 | 滚到表格时工具栏吸在面板顶部，按钮始终可点，每次移动 0.7 个可视宽 |
| `thead th{position:sticky;top:0}`、`td:first-child{position:sticky;left:0}` | 表头和首列双吸附，横竖滚动都不丢参照 |
| `#tbl{overflow:visible}` | 若留 `overflow:hidden`，`thead` 的 sticky 会失效（祖先成了自己的滚动容器） |

验证方法：`wrap.scrollWidth - wrap.clientWidth > 0` 且点击 ▶ 后 `wrap.scrollLeft` 变化。

### 4. 自动采集（可选）

> **桌面版 0.8.2 实测：`Stop` 事件不触发**（对话结束后 `statusline-latest.txt` 的 mtime 不变）。
> 桌面端请直接跳到第 6 节；本节配置适用于 CLI 或未来支持 `Stop` 的版本。
> `hook.py` 每次被调用都会往 `~/.workbuddy/cache-monitor/hook-calls.log` 追加一行，
> 可用它确认当前版本是否真的触发了该事件。

在 `~/.workbuddy/settings.json` 注册 Stop hook，每轮回答结束后自动增量采集：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"$HOME/.workbuddy/skills/workbuddy-cache-monitor/scripts/hook.py\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

`hook.py` 恒返回 `exit 0`，采集失败也不会阻塞对话；命中率正常时完全静默。

> Windows 下 hook 由 Git Bash 执行，路径**必须用正斜杠 + 双引号**，否则反斜杠会被当作转义符导致静默失败。

### 5. 状态栏（可选，**仅 CLI 生效**）

> **重要：`statusLine` 是 CodeBuddy Code（终端）的能力，WorkBuddy 桌面客户端不渲染。**
> 已在 0.8.2 实测确认：字段能被读取，但界面底部不会出现任何内容。桌面端请直接用第 6 节方案。

把摘要常驻在终端界面底部一行，例如：

```
7轮 · 88次调用 | 1012万 tok · 缓存命中 96.5%
```

`hook.py` 每轮结束会把结果预写进 `~/.workbuddy/cache-monitor/statusline-latest.txt`，状态栏只需 `cat` 该文件——**这一步很重要**：statusLine 最短 300ms 刷新一次，若每次重新扫描日志会拖慢界面。

在 `~/.workbuddy/settings.json` 添加：

```json
{
  "statusLine": {
    "type": "command",
    "command": "cat \"$HOME/.workbuddy/cache-monitor/statusline-latest.txt\" 2>/dev/null || printf '%s' '缓存统计中…'",
    "padding": 0
  }
}
```

注意三点：
1. **不要写 `bash script.sh`**。Windows 上裸 `bash` 可能被解析到 WSL，`$HOME` 会指向 Linux 家目录导致读不到文件；宿主本身就在 bash 中执行 command，无需二次启动。
2. 状态栏**只支持纯文本**，不能点击、不能弹出详情面板。要看明细请用文本报告或 HTML 看板。
3. 若中文显示为乱码，把内容降级为纯 ASCII（如 `7r 88c | 10.1M tok | cache 96.5%`）。

### 6. 桌面端方案：每轮回答末尾显示（v1.3.0）

桌面端无法注入 UI，但 `UserPromptSubmit` 钩子的 `hookSpecificOutput.additionalContext`
会真正拼进上下文。用它每轮注入指标，模型即可在回答末尾输出一行：

```
— cache 97.2% · 129 calls —
```

注册（与 Stop 并列）。**注意：桌面版不触发 `Stop`，采集必须由本事件完成**：

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

`prompt_hook.py` 一次完成三件事：

1. **采集**：调用 `collect.sync()` 增量入库。实测桌面版 0.8.2 的 `Stop` 事件不触发，
   `statusline-latest.txt` 的 mtime 在对话结束后保持不变，故不能依赖 Stop 写数据。
2. **计算**：按 `session_id` 聚合当前会话（请求数 < 3 时退回 24h 口径）。
3. **注入**：输出 `additionalContext`，并在 `statusline-latest.txt` 落一行纯 ASCII 摘要。

#### 两个必须遵守的实现约束

| 约束 | 原因 | 后果 |
|---|---|---|
| **`json.dumps(..., ensure_ascii=True)`** | 宿主按系统 ANSI（cp936）解码管道；直出 UTF-8 会被解码成乱码，甚至导致 JSON 解析失败、整块丢弃 | 注入静默失效，或模型收到一堆 `7�� ��` 无法理解 |
| **不依赖 stdin payload** | 桌面版 0.8.2 调用 hook 时 **stdin 为 0 字节**（与文档描述的 JSON 输入不符） | 若遇空 payload 就 return，等于每轮把自己关掉 |

`ensure_ascii=True` 会把中文和 `—·` 等符号全部转义成 `\uXXXX`，使 stdout 成为
**纯 ASCII 字节流**——宿主无论按 UTF-8 还是 cp936 解码，字节完全一致，还原后也正确。
验证方法：输出应满足 `all(b < 128 for b in stdout)`。

空 stdin 时不能早退，改用数据库里**最近 30 分钟内仍活跃的会话**兜底：

```sql
SELECT session_id, MAX(ts) FROM requests WHERE session_id<>''
GROUP BY session_id ORDER BY MAX(ts) DESC LIMIT 1
```

#### 底部那行为什么不做成链接

**实测结论：做不到，不要再尝试。** 两个原因叠加：

1. 桌面版聊天区不渲染 `[文本](file://...)`，会原样显示 Markdown 源码，比纯文本更难看。
2. 宿主能点的链接是它自己生成的临时地址（形如
   `http://127.0.0.1:<随机端口>/static-html/<一次性哈希>/dashboard.html`），
   端口与哈希每次都变，**不存在可固化进文字的稳定 URL**。

正确形态：底部保持纯文本，看板走「**打开一次，之后自动重载**」——
由助手调用产物呈现能力打开，hook 在后台覆盖同一个文件，预览面板自己刷新。

#### 底部行必须标注两个口径

底部数字是**本次会话**，看板「整体命中率」是**全部历史**，两者天然不同（常见差 0.2–0.5 个点）。
不标注就会被当成数据不一致：

```
— 本会话 97.9% · 172 次 | 全局 98.1% —
```

注意看板里还有第三个数字「任务命中率 P50」，那是**中位数**而非整体比值，
三者口径互不相同，回答用户时必须区分。看板位置：

```
<公共目录>/wb-cache/dashboard.html      # 纯 ASCII，file:// 才可用
```

看板由 hook 后台异步刷新（`Popen` 不 `wait`），并设 120 秒 TTL。
若同步生成，460ms 会叠加到 500ms 采集上，首字延迟接近 1 秒。

**这仍不是真正的 UI**：该行是模型输出的文本，附着在当前回答末尾、会随对话滚动，
不是固定底栏。

#### 排查用日志

| 文件 | 内容 |
|---|---|
| `~/.workbuddy/cache-monitor/prompt-hook.log` | 每次 UserPromptSubmit 的 `stdin_len` 与算出的指标 |
| `~/.workbuddy/cache-monitor/hook-calls.log` | 每次 Stop 调用（桌面版为空即证明该事件未触发） |

## 口径

- 命中率 = `cache_read` / `input`
- `input` 已包含 `cache_read`，因此「未命中」= `input - cache_read`
- 总量 = `input + output + cache_write`（命中部分不重复累加）
- 详细口径与异常处理见 @references/metrics.md

## 注意事项

1. **不要读消息正文**。只解析 `usage` / `traceId` / `model` 等元数据字段，避免把对话内容带入上下文或写入数据库。
2. **低命中率不一定是问题**。任务首轮、上下文压缩后、更换模型时，前缀缓存必然失效，单任务命中率短暂走低属正常。判断要看趋势，而不是单点。
3. **报告数字要与原始日志可核对**。用户质疑时，用 `--json` 输出并指出对应的 `traceId`，不要只给结论。
4. `traces/` 目录的 `modelInfo` 覆盖率仅约 17%，不要用它作为主口径。
5. 日期口径依赖客户端日志格式；若客户端升级导致字段变化，先运行 `python "$S/selftest.py"` 与一次 `collect.py` 确认解析是否仍正常。
