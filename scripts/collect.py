#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect.py — WorkBuddy 用量日志采集与增量入库

设计要点
--------
1. 增量读取：按字节偏移(offset)记录每个日志文件的消费位置，只处理新增内容。
2. 完整性保护：只消费到最后一个换行符为止，避免读到写了一半的行。
3. 幂等去重：以内容指纹作主键，重复扫描不会造成重复计数。
4. 异常隔离：解析失败/字段缺失/数值越界不中断流程，记入 anomalies 表。

日志格式（实测）
----------------
projects/<workspace>/<session>.jsonl 中的 function_call / message 行带 usage 对象：
    input_tokens / output_tokens / cache_read_input_tokens
同一行通常还带 traceId —— 一次任务(trace)的所有 API 调用共享同一个 traceId，
这是把「请求级」数据还原成「任务级」指标的关键。
"""
import json
import os
import hashlib
import glob
import sqlite3
import time
from pathlib import Path

HOME = Path.home()
WB_DIR = HOME / ".workbuddy"
PROJECTS_DIR = WB_DIR / "projects"
DB_DIR = WB_DIR / "cache-monitor"
DB_PATH = DB_DIR / "wbcc.db"

INPUT_ALIASES = ("input_tokens", "inputTokens", "prompt_tokens")
OUTPUT_ALIASES = ("output_tokens", "outputTokens", "completion_tokens")
CACHE_READ_ALIASES = ("cache_read_input_tokens", "cacheReadInputTokens", "cached_tokens",
                      "prompt_cache_hit_tokens")
CACHE_WRITE_ALIASES = ("cache_creation_input_tokens", "cacheCreationInputTokens", "cacheWrite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT,
    session_id  TEXT,
    workspace   TEXT,
    model       TEXT,
    ts          INTEGER,
    input       INTEGER,
    output      INTEGER,
    cache_read  INTEGER,
    cache_write INTEGER,
    flags       TEXT,
    src         TEXT
);
CREATE INDEX IF NOT EXISTS idx_req_trace ON requests(trace_id);
CREATE INDEX IF NOT EXISTS idx_req_ts    ON requests(ts);
CREATE INDEX IF NOT EXISTS idx_req_ws    ON requests(workspace);

CREATE TABLE IF NOT EXISTS files (
    path       TEXT PRIMARY KEY,
    offset     INTEGER,
    size       INTEGER,
    mtime      REAL,
    scanned_at INTEGER
);

CREATE TABLE IF NOT EXISTS anomalies (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    kind   TEXT,
    path   TEXT,
    detail TEXT,
    ts     INTEGER
);
"""


def connect():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    return db


def _find(obj, key, depth=0):
    """在嵌套结构里找第一个字符串字段（限制深度，避免大对象递归过深）"""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        v = obj.get(key)
        if isinstance(v, str):
            return v
        for v in obj.values():
            if isinstance(v, (dict, list)):
                r = _find(v, key, depth + 1)
                if r:
                    return r
    elif isinstance(obj, list):
        for i in obj:
            r = _find(i, key, depth + 1)
            if r:
                return r
    return None


def _num(usage, aliases):
    """按别名取数值；兼容 OpenAI 的 xxx_details.cached_tokens 嵌套"""
    for a in aliases:
        v = usage.get(a)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.isdigit():
            return int(v)
    for v in usage.values():
        if isinstance(v, dict):
            got = _num(v, aliases)
            if got:
                return got
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    got = _num(item, aliases)
                    if got:
                        return got
    return 0


def _usages(obj):
    """收集一条日志行中所有 usage 对象"""
    out = []
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() in ("usage", "tokenusage", "usageinfo") and isinstance(v, dict):
                    out.append(v)
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return out


def _looks_newer_than_last_line(buf):
    return buf


def sync(verbose=True):
    """扫描所有项目日志，增量写入数据库。返回统计信息。"""
    db = connect()
    stats = {"files": 0, "new_lines": 0, "inserted": 0, "skipped": 0, "errors": 0}
    now = int(time.time() * 1000)

    files = sorted(glob.glob(str(PROJECTS_DIR / "**" / "*.jsonl"), recursive=True))
    for path in files:
        try:
            st = os.stat(path)
        except OSError:
            continue
        row = db.execute("SELECT offset, size FROM files WHERE path=?", (path,)).fetchone()
        start = 0
        if row:
            prev_off, prev_size = row[0], row[1]
            # 文件变小说明被截断/重写 → 从头重扫（指纹去重保证幂等）
            start = prev_off if st.st_size >= prev_size else 0

        with open(path, "rb") as f:
            f.seek(start)
            data = f.read()
        if not data:
            db.execute(
                "INSERT INTO files(path,offset,size,mtime,scanned_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
                "scanned_at=excluded.scanned_at",
                (path, start, st.st_size, st.st_mtime, now))
            continue

        cut = data.rfind(b"\n")
        if cut == -1:            # 没有完整行，留待下次
            continue
        chunk = data[:cut + 1]
        new_offset = start + cut + 1

        workspace = os.path.basename(os.path.dirname(path))
        rows = []
        for raw in chunk.split(b"\n"):
            if not raw.strip():
                continue
            stats["new_lines"] += 1
            if b"usage" not in raw and b"input_tokens" not in raw and b"inputTokens" not in raw:
                continue
            try:
                o = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                stats["errors"] += 1
                db.execute("INSERT INTO anomalies(kind,path,detail,ts) VALUES(?,?,?,?)",
                           ("parse_error", path, raw[:120].decode("utf-8", "replace"), now))
                continue

            us = _usages(o)
            if not us:
                continue
            trace_id = _find(o, "traceId") or ""
            session_id = o.get("sessionId") or _find(o, "sessionId") or ""
            model = _find(o, "model") or "unknown"
            ts = o.get("timestamp") or 0
            seen = set()
            for u in us:
                inp = _num(u, INPUT_ALIASES)
                outp = _num(u, OUTPUT_ALIASES)
                cr = _num(u, CACHE_READ_ALIASES)
                cw = _num(u, CACHE_WRITE_ALIASES)
                shape = (inp, outp, cr, cw)
                if shape in seen:      # 同一行内嵌套重复的 usage
                    continue
                seen.add(shape)
                if inp <= 0 and outp <= 0:
                    stats["skipped"] += 1
                    continue

                flags = []
                if cr > inp > 0:
                    flags.append("cache_gt_input")
                    cr = inp             # 越界值截断，避免命中率 >100%
                if cr and not inp:
                    flags.append("cache_without_input")
                if not trace_id:
                    flags.append("no_trace_id")
                if model == "unknown":
                    flags.append("no_model")
                if ts <= 0 or ts > now + 86400000:
                    flags.append("bad_timestamp")

                fp = hashlib.sha1(
                    f"{trace_id}|{ts}|{model}|{inp}|{outp}|{cr}|{cw}|{session_id}".encode()
                ).hexdigest()
                rows.append((fp, trace_id, session_id, workspace, model, ts, inp, outp, cr, cw,
                             ",".join(flags), "projects"))

        before = db.total_changes
        db.executemany(
            "INSERT OR IGNORE INTO requests(id,trace_id,session_id,workspace,model,ts,"
            "input,output,cache_read,cache_write,flags,src) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
        stats["inserted"] += db.total_changes - before
        db.execute(
            "INSERT INTO files(path,offset,size,mtime,scanned_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET offset=excluded.offset, size=excluded.size, "
            "mtime=excluded.mtime, scanned_at=excluded.scanned_at",
            (path, new_offset, st.st_size, st.st_mtime, now))
        stats["files"] += 1

    db.commit()
    db.close()
    if verbose:
        print(f"[sync] 文件 {stats['files']} 个 · 新行 {stats['new_lines']} · "
              f"入库 {stats['inserted']} 条 · 跳过 {stats['skipped']} · 解析失败 {stats['errors']}")
    return stats


def reset():
    """清空数据库并可选择保留结构（用于口径变更后重建）"""
    db = connect()
    db.executescript("DELETE FROM requests; DELETE FROM files; DELETE FROM anomalies;")
    db.commit()
    db.close()
    print("[reset] 已清空缓存数据库")


if __name__ == "__main__":
    import sys
    if "--reset" in sys.argv:
        reset()
    sync()
