#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.py — 挂在 Stop 事件上的入口

在 settings.json 中注册：
    "Stop": [{ "hooks": [{ "type": "command",
        "command": "python \"$HOME/.workbuddy/skills/workbuddy-cache-monitor/scripts/hook.py\"",
        "timeout": 30 }] }]

行为
----
1. 静默增量采集（不打断对话）。
2. 取「最近一次任务」的命中率。
3. 仅在命中率低于阈值时通过 systemMessage 提示；否则完全静默。
4. 任何异常都以 exit 0 退出，绝不阻塞主流程。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from collect import sync, connect  # noqa: E402
from report import load_tasks, summarize, LOW_HIT_THRESHOLD  # noqa: E402

STATUS_DIR = Path.home() / ".workbuddy" / "cache-monitor"


def _human(n):
    if n >= 1e8:
        return f"{n/1e8:.2f}亿"
    if n >= 1e4:
        return f"{n/1e4:.0f}万"
    return str(n)


def write_statusline(sid):
    """生成状态栏文本并落盘。

    statusLine 最短 300ms 刷新一次，脚本不能做扫描；这里把结果预写进文件，
    statusline.sh 只负责 cat，单次开销可压到 50ms 以内。
    """
    if not sid:
        return
    db = connect()
    row = db.execute(
        "SELECT COUNT(DISTINCT COALESCE(NULLIF(trace_id,''), session_id)), COUNT(*), "
        "SUM(input), SUM(output), SUM(cache_read) FROM requests WHERE session_id=?",
        (sid,)).fetchone()
    db.close()
    traces, calls, inp, outp, cr = row
    if not calls:
        return
    inp, outp, cr = inp or 0, outp or 0, cr or 0
    rate = (cr / inp * 100) if inp else 0.0
    text = (f"{traces}轮 · {calls}次调用 "
            f"| {_human(inp + outp)} tok · 缓存命中 {rate:.1f}%")
    try:
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        (STATUS_DIR / f"statusline-{sid}.txt").write_text(text, encoding="utf-8")
        (STATUS_DIR / "statusline-latest.txt").write_text(text, encoding="utf-8")
    except OSError:
        pass


def read_payload():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def log_call(event, session_id):
    """记录每次被调用，用于确认宿主是否真的触发了该事件。

    桌面端各事件的支持情况不一致（实测 UserPromptSubmit 会触发、Stop 存疑），
    没有这条日志就无法区分「hook 没被执行」和「hook 执行了但没算对」。
    """
    try:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{event}\t{(session_id or '')[:12]}\n"
        with open(STATUS_DIR / "hook-calls.log", "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def main():
    payload = read_payload()
    event = payload.get("hook_event_name", "Stop")
    log_call(event, payload.get("session_id"))

    try:
        sync(verbose=False)
        write_statusline(payload.get("session_id"))
    except Exception as exc:                       # 采集失败不阻塞
        print(json.dumps({"continue": True,
                          "systemMessage": f"[cache-monitor] 采集失败：{exc}"},
                         ensure_ascii=False))
        return

    try:
        db = connect()
        tasks = load_tasks(db, days=None)
        db.close()
        if not tasks:
            print(json.dumps({"continue": True}))
            return

        latest = tasks[0]
        s = summarize(tasks)
        warn = latest["hit_rate"] < LOW_HIT_THRESHOLD

        if warn:
            msg = (f"本轮任务缓存命中率 {latest['hit_rate']:.1f}%"
                   f"（低于 {LOW_HIT_THRESHOLD:.0f}%）：{latest['calls']} 次请求，"
                   f"输入 {latest['input']:,} · 命中 {latest['cache_read']:,}。"
                   f"通常出现在任务刚开始或上下文被压缩后，属正常现象；"
                   f"若持续偏低，检查是否频繁开启新会话或模型档位频繁切换。")
        else:
            msg = (f"本轮任务缓存命中率 {latest['hit_rate']:.1f}%（{latest['calls']} 次请求）。"
                   f"全局 {s['hit_rate']:.1f}%。")

        print(json.dumps({"continue": True, "systemMessage": msg}, ensure_ascii=False))
    except Exception:
        print(json.dumps({"continue": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"continue": True}))
    sys.exit(0)
