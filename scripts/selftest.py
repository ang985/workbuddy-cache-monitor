#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 采集模块的边界行为自检

覆盖：正常入库、越界截断、半行丢弃、增量幂等、文件截断后重扫不重复计数。
全部在临时目录里跑，不触碰真实库与真实日志。
"""
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import collect  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {extra}")


def line(inp, outp, cr, tid="t1", ts=None):
    return json.dumps({
        "timestamp": ts or int(time.time() * 1000),
        "sessionId": "s1",
        "traceId": tid,
        "model": "test-model",
        "providerData": {"usage": {
            "input_tokens": inp, "output_tokens": outp, "cache_read_input_tokens": cr}},
    }, ensure_ascii=False)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="wbcc-test-"))
    ws = tmp / "projects" / "ws-test"
    ws.mkdir(parents=True)
    collect.PROJECTS_DIR = tmp / "projects"
    collect.DB_DIR = tmp / "db"
    collect.DB_PATH = tmp / "db" / "t.db"

    log = ws / "s1.jsonl"

    print("[1] 正常入库 + 越界截断")
    log.write_text("\n".join([
        line(1000, 100, 800),          # 正常 80%
        line(500, 50, 900),            # cache_read > input，应被截断到 500
        json.dumps({"timestamp": 0}),  # 无 usage，应被忽略
    ]) + "\n", encoding="utf-8")
    collect.sync(verbose=False)
    db = sqlite3.connect(collect.DB_PATH)
    rows = db.execute("SELECT input, cache_read, flags FROM requests ORDER BY input DESC").fetchall()
    check("写入 2 条有效记录", len(rows) == 2, f"实际 {len(rows)}")
    check("正常记录命中值不变", rows[0][1] == 800, f"cache_read={rows[0][1]}")
    check("越界命中被截断为 input", rows[1][1] == 500, f"cache_read={rows[1][1]}")
    check("越界记录带 cache_gt_input 标记", "cache_gt_input" in (rows[1][2] or ""), rows[1][2])

    print("[2] 半行不消费")
    with open(log, "a", encoding="utf-8") as f:
        f.write(line(2000, 200, 1900)[:40])      # 故意截断的半行，无换行
    collect.sync(verbose=False)
    n = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    check("半行不入库", n == 2, f"实际 {n}")

    print("[3] 补齐后能正确续读")
    with open(log, "a", encoding="utf-8") as f:
        f.write(line(2000, 200, 1900)[40:] + "\n")
    collect.sync(verbose=False)
    n = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    check("补齐后新增 1 条", n == 3, f"实际 {n}")

    print("[4] 重复同步幂等")
    collect.sync(verbose=False)
    collect.sync(verbose=False)
    n = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    check("重复同步不重复计数", n == 3, f"实际 {n}")

    print("[5] 文件被截断后重扫不重复")
    log.write_text(line(3000, 300, 2900, tid="t2") + "\n", encoding="utf-8")
    collect.sync(verbose=False)
    n = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    check("截断重扫后共 4 条（去重生效）", n == 4, f"实际 {n}")
    dup = db.execute(
        "SELECT COUNT(*) FROM (SELECT id, COUNT(*) c FROM requests GROUP BY id HAVING c>1)"
    ).fetchone()[0]
    check("无重复主键", dup == 0, f"重复组 {dup}")

    print("[6] 聚合口径")
    db.close()
    import report  # noqa: E402
    report.connect = collect.connect
    db2 = collect.connect()
    tasks = report.load_tasks(db2)
    s = report.summarize(tasks)
    check("任务数按 traceId 聚合为 2", s["tasks"] == 2, f"实际 {s['tasks']}")
    check("整体命中率 ≤ 100%", s["hit_rate"] <= 100, f"{s['hit_rate']}")
    check("未命中合计为正", s["uncached"] > 0, f"{s['uncached']}")
    db2.close()

    print()
    print(f"结果：PASS {PASS} · FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
