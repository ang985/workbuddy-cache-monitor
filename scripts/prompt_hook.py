#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UserPromptSubmit 钩子：采集 + 计算 + 把当前会话指标注入本轮上下文。

为什么采集也放在这里
--------------------
实测（WorkBuddy 桌面版 0.8.2）：`UserPromptSubmit` 会触发，`Stop` 不触发
（`statusline-latest.txt` 的 mtime 在整轮对话结束后始终不变）。因此不能把
「写最新数据」的职责交给 Stop，必须由本钩子自行完成：每轮用户提交消息时，
上一轮的全部用量已经落进日志，此刻同步即可拿到完整数据。

编码约束（重要）
----------------
宿主的 stdin/stdout 管道按系统 ANSI（cp936）解码，UTF-8 的中文会被撕成乱码。
因此 **输出到 stdout 的内容一律只用 ASCII**，绝不放中文。

设计约束
--------
* 永不阻塞：任何异常都输出 `{}` 并 exit 0。
* 只读元数据字段，不读对话正文。
* 采集固定开销约 500ms（stat 全部日志文件），属可接受范围。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

STATE = Path.home() / ".workbuddy" / "cache-monitor"
LATEST = STATE / "statusline-latest.txt"
MIN_CALLS = 3           # 会话请求数不足时退回全局口径，避免首轮只有 1 条

# 看板必须放在**纯 ASCII 路径**下：file:// 链接里出现中文或空格时，
# 多数宿主会截断或拒绝打开。优先用系统公共目录（不含用户名，天然 ASCII），
# 取不到再退回用户目录下的英文子目录。
def _public_dir() -> Path:
    env = os.environ.get("PUBLIC")
    if env and Path(env).is_dir():
        return Path(env)
    return Path.home().parent / "Public"


DASH = _public_dir() / "wb-cache" / "dashboard.html"
DASH_TTL = 120          # 秒。超过这个间隔才重新生成，避免每轮都付 460ms
DASH_URL = DASH.as_uri()


def refresh_dashboard_async() -> None:
    """后台异步重生成看板，不阻塞注入。

    直接 Popen 不 wait：生成一次约 460ms，若同步执行会叠加到 500ms 的采集
    开销上，让提交后的首字延迟接近 1 秒。TTL 控制避免连续多轮重复触发。
    """
    try:
        if DASH.exists() and (time.time() - DASH.stat().st_mtime) < DASH_TTL:
            return
        DASH.parent.mkdir(parents=True, exist_ok=True)
        flags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
        subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "report.py"),
             "--html", "--range", "all", "-o", str(DASH)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception:
        pass


def emit(text: str = "") -> None:
    """写出 additionalContext。

    `ensure_ascii=True` 是这里的关键：它把中文与所有非 ASCII 字符转义成
    `\\uXXXX`，使 stdout 成为**纯 ASCII 字节流**。宿主按 cp936 还是 UTF-8 解码，
    拿到的字节都完全一致，JSON 解析不会失败，还原后中文也正确。
    （此前用 ensure_ascii=False 直出 UTF-8，被 cp936 解码后整块丢弃。）
    """
    payload: dict = {}
    if text:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": text,
            }
        }
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    sys.stdout.flush()


def trace(msg: str) -> None:
    """记录每次调用的结果，用于判断宿主是否真的触发了本钩子。"""
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(STATE / "prompt-hook.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{msg}\n")
    except OSError:
        pass


def human(n: int) -> str:
    """紧凑数字：1.4e7 -> 14.0M。注意 M 的基数是 1e6，不是 1e4。"""
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return str(n)


def latest_session(db):
    """找出最近仍活跃的会话 id。

    桌面版实测会给 hook 传入**空 stdin**（而非文档所述的 JSON），因此不能依赖
    payload 里的 session_id。退而求其次：取库里最近有记录、且 30 分钟内仍在
    写入的会话，等价于「当前这个对话」。
    """
    row = db.execute(
        "SELECT session_id, MAX(ts) FROM requests WHERE session_id<>'' "
        "GROUP BY session_id ORDER BY MAX(ts) DESC LIMIT 1"
    ).fetchone()
    if row and row[0] and (time.time() * 1000 - (row[1] or 0)) < 30 * 60 * 1000:
        return row[0]
    return ""


def global_stats(db):
    """全库口径（与看板「整体命中率」完全一致）。"""
    row = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(input),0), COALESCE(SUM(cache_read),0), "
        "COUNT(DISTINCT trace_id) FROM requests"
    ).fetchone()
    if not row or not row[1]:
        return 0, 0, 0.0
    return row[0], row[3], row[2] / row[1] * 100


def session_stats(session_id: str):
    """返回 (calls, input, cache_read, hit_rate, scope)。优先当前会话。"""
    from collect import connect        # 延迟导入，异常时不拖慢退出

    db = connect()
    try:
        sid = session_id or latest_session(db)
        if sid:
            row = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(input),0), COALESCE(SUM(cache_read),0) "
                "FROM requests WHERE session_id=?",
                (sid,),
            ).fetchone()
            if row and row[0] >= MIN_CALLS:
                calls, inp, cr = row[0], row[1], row[2]
                scope = "session" if session_id else "recent session"
                return calls, inp, cr, (cr / inp * 100 if inp else 0.0), scope

        row = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(input),0), COALESCE(SUM(cache_read),0) "
            "FROM requests WHERE ts >= ?",
            (int((time.time() - 86400) * 1000),),
        ).fetchone()
        calls, inp, cr = (row[0], row[1], row[2]) if row else (0, 0, 0)
        return calls, inp, cr, (cr / inp * 100 if inp else 0.0), "24h"
    finally:
        db.close()


def main() -> None:
    raw = ""
    payload: dict = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = parsed
    except Exception:
        payload = {}

    sid = payload.get("session_id") or ""
    # 记录 stdin 实际长度：桌面版实测为 0，与文档描述的 JSON 输入不符，
    # 这条日志是判断「宿主有没有传参」的唯一依据。
    trace(f"called stdin_len={len(raw)} sid={sid[:12] if sid else '-'}")

    # 1. 采集（失败则沿用上次结果，不阻断）
    synced = False
    try:
        from collect import sync
        sync(verbose=False)
        synced = True
    except Exception:
        pass

    # 2. 计算
    try:
        calls, inp, cr, hit, scope = session_stats(sid)
    except Exception:
        emit()
        return
    if not calls:
        trace("no data -> skip")
        emit()
        return

    trace(f"metrics calls={calls} hit={hit:.1f}% scope={scope}")

    # 3. 全局口径（与看板「整体命中率」一致），用于消除两个数字的歧义
    g_hit, g_tasks = 0.0, 0
    try:
        from collect import connect
        _db = connect()
        _, g_tasks, g_hit = global_stats(_db)
        _db.close()
    except Exception:
        pass

    # 4. 落盘（供 HTML 看板与人工查看；保持与旧版同一路径）
    line = f"cache {hit:.1f}% | {calls} calls | {human(inp)} tok"
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        LATEST.write_text(line, encoding="utf-8")
    except OSError:
        pass

    # 5. 后台刷新看板（不阻塞）
    refresh_dashboard_async()

    # 6. 注入。整行保持纯文本。
    #    桌面版聊天区不渲染 `[文本](file://...)`——会原样显示 Markdown 源码。
    #    同时标注两个口径：本会话数与看板全局数常被误认为矛盾。
    label = f"— 本会话 {hit:.1f}% · {calls} 次 | 全局 {g_hit:.1f}% —"
    stale = "" if synced else " (collect failed, may be stale)"
    ctx = (
        f"[cache-monitor] This {scope}: {calls} calls, {hit:.1f}% cache hit, "
        f"{human(inp)} input tokens. Global: {g_hit:.1f}% over {g_tasks} tasks{stale}. "
        f"Dashboard: {DASH}\n"
        f"End your reply with exactly this line, verbatim: {label}\n"
        f"Note: if asked why chat and dashboard numbers differ, the chat line is "
        f"this conversation only while the dashboard is all history."
    )
    emit(ctx)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
    sys.exit(0)
