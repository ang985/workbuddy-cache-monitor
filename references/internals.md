# 实现细节与平台实测

本文件记录「为什么这么实现」，供排查异常或适配新版本时参考。日常使用不需要读。

## 一、桌面端事件支持实测（WorkBuddy 0.8.2）

| 能力 | 是否生效 | 判定依据 |
|---|---|---|
| `UserPromptSubmit` 钩子 | ✅ 触发 | `prompt-hook.log` 每轮新增一行 |
| `Stop` 钩子 | ❌ 不触发 | `hook-calls.log` 始终为空 |
| `statusLine` 渲染 | ❌ 不渲染 | 字段能被读取，但界面底部无任何内容（该能力属 CLI 终端） |
| 聊天区渲染 Markdown 链接 | ❌ 不渲染 | `[文本](file://...)` 会原样显示源码 |

**结论**：桌面端唯一可用的注入点是 `UserPromptSubmit` 的 `additionalContext`。
采集必须在同一事件里完成，不能依赖 `Stop` 事后写数据。
因此 `statusline-*.txt` 那套文件落盘机制已从代码中移除——它依赖 `Stop`，
在桌面端会「配置成功但永远不更新」，属于会误导用户的死路径。

## 二、两个必须遵守的实现约束

| 约束 | 原因 | 违反后果 |
|---|---|---|
| `json.dumps(..., ensure_ascii=True)` | 宿主按系统 ANSI（cp936）解码管道；直出 UTF-8 中文会被撕成乱码 | 注入静默失效，模型收到一堆乱码 |
| 不依赖 stdin payload | 桌面版调用 hook 时 **stdin 为 0 字节**，与文档描述的 JSON 输入不符 | 若遇空 payload 就 return，等于每轮把自己关掉 |

`ensure_ascii=True` 把中文与 `—·` 等符号全部转义成 `\uXXXX`，stdout 成为
**纯 ASCII 字节流**——宿主无论按 UTF-8 还是 cp936 解码，字节完全一致。
验证方法：输出应满足 `all(b < 128 for b in stdout)`。

空 stdin 时改用数据库里**最近 30 分钟内仍活跃的会话**兜底：

```sql
SELECT session_id, MAX(ts) FROM requests WHERE session_id<>''
GROUP BY session_id ORDER BY MAX(ts) DESC LIMIT 1
```

## 三、底部那行为什么不做成可点击链接

**实测做不到，不要再尝试。** 两个原因叠加：

1. 桌面版聊天区不渲染 `[文本](file://...)`，会原样显示 Markdown 源码，比纯文本更难看。
2. 宿主能点的链接由它自己生成（形如
   `http://127.0.0.1:<随机端口>/static-html/<一次性哈希>/dashboard.html`），
   端口与哈希每次都变，**不存在可固化进文字的稳定 URL**。

正确形态：底部保持纯文本；看板走「打开一次，之后自动重载」——由助手用产物
呈现能力打开，钩子在后台覆盖同一个文件，预览面板自行刷新。

## 四、三个数字口径不同（最常被当成 bug）

| 数字 | 来源 | 含义 |
|---|---|---|
| 本会话 x% | 当前 `session_id` 聚合 | 这次对话的命中/输入比值 |
| 全局 y% | 全库 `SUM(cache_read)/SUM(input)` | 全部历史，等同看板「整体命中率」 |
| P50 | 各任务命中率的中位数 | 不是均值，也不是整体比值 |

差异通常在 0.2–0.5 个百分点，属正常。看板同时显示三者，回答用户必须区分。

## 五、看板刷新机制

- 路径固定为 `<公共目录>/wb-cache/dashboard.html`（**纯 ASCII**，`file://` 才可用）。
- 钩子用 `Popen` 异步重生成，不 `wait`：生成一次约 460ms，若同步执行会叠加到
  500ms 采集开销上，首字延迟接近 1 秒。
- 设 120 秒 TTL，连续多轮不会重复生成。
- 明细表的 `wrap` 需固定高度+`overflow:auto`，横向滚动条才不会落到第 200 多行之后。

## 六、排查用日志

| 文件 | 内容 |
|---|---|
| `~/.workbuddy/cache-monitor/prompt-hook.log` | 每次 UserPromptSubmit 的 `stdin_len` 与算出的指标 |
| `~/.workbuddy/cache-monitor/hook-calls.log` | 每次 Stop 调用（桌面版为空即证明该事件未触发） |

判断链路：日志有 `called stdin_len=0` 说明钩子被执行；其后有 `metrics ...` 说明算出了数；
两者都有但界面没显示，才去查注入编码。
