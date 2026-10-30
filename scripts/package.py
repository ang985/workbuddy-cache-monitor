#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""package.py — 技能包发布前校验与打包

用法：
    python package.py                 # 校验 + 打包到 ./dist
    python package.py --check         # 只校验，不打包
    python package.py --out <目录>    # 指定输出目录

校验项对应开放平台《技能》规范与常见驳回原因：
  1. SKILL.md 位于包根目录一级
  2. frontmatter 必填字段齐全
  3. name 与目录名一致、符合小写短横线规范
  4. version 语义化
  5. description 不含泛化词（公约 2.2.3 明确禁止）
  6. examples 数量与格式
  7. @references/ 引用真实存在
  8. 无版本控制/缓存残留文件
  9. 无硬编码绝对路径、无密钥
 10. 包体积 ≤ 3MB
 11. Python 脚本语法可编译
"""
import os
import re
import sys
import zipfile
import py_compile
import tempfile
from pathlib import Path

MAX_SIZE = 3 * 1024 * 1024        # 平台上限 3MB
REQUIRED_FIELDS = ["name", "display_name", "display_name_en", "description",
                   "description_zh", "description_en", "version", "author"]
OPTIONAL_FIELDS = ["examples_zh", "examples_en", "allowed-tools"]
BANNED_WORDS = ["万能", "全面", "智能助手", "一站式解决", "all your tasks", "万能工具"]
JUNK = {".git", ".DS_Store", "__pycache__", "node_modules", ".idea", ".vscode"}
JUNK_SUFFIX = {".pyc", ".pyo", ".zip", ".log", ".swp"}

SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9]{16,}", "疑似 API Key"),
    (r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]", "疑似硬编码凭证"),
    (r"[A-Za-z]:[\\/](Users|用户)[\\/]", "硬编码 Windows 用户绝对路径"),
]

results = []


def ok(name, detail=""):
    results.append(("PASS", name, detail))


def bad(name, detail=""):
    results.append(("FAIL", name, detail))


def warn(name, detail=""):
    results.append(("WARN", name, detail))


def parse_frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None, text
    block, body = m.group(1), text[m.end():]
    data = {}
    key = None
    for raw in block.splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("- ") and key:
            data.setdefault(key, [])
            if isinstance(data[key], list):
                data[key].append(raw.lstrip()[2:].strip().strip('"').strip("'"))
            continue
        mm = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", raw)
        if mm:
            key = mm.group(1)
            val = mm.group(2).strip().strip('"').strip("'")
            data[key] = val if val else []
    return data, body


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in JUNK]
        for fn in filenames:
            yield Path(dirpath) / fn


def check(root):
    root = Path(root).resolve()
    skill_md = root / "SKILL.md"
    if not skill_md.exists():
        bad("SKILL.md 存在", "包根目录下未找到 SKILL.md")
        return False
    ok("SKILL.md 存在")

    text = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    if fm is None:
        bad("frontmatter 格式", "缺少 --- 包裹的 YAML 头")
        return False
    ok("frontmatter 格式")

    missing = [f for f in REQUIRED_FIELDS if not fm.get(f)]
    if missing:
        bad("必填字段完整", f"缺少 {missing}")
    else:
        ok("必填字段完整", f"{len(REQUIRED_FIELDS)} 项齐全")

    name = fm.get("name", "")
    if name != root.name:
        bad("name 与目录名一致", f"name={name} 目录={root.name}")
    elif not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        bad("name 命名规范", "需小写字母/数字/短横线")
    else:
        ok("name 与目录名一致", name)

    ver = fm.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
        bad("version 语义化", f"version={ver}，应为 1.0.0 形式")
    else:
        ok("version 语义化", ver)

    desc = fm.get("description", "")
    hits = [w for w in BANNED_WORDS if w.lower() in desc.lower()]
    if hits:
        bad("description 禁用泛化词", f"命中 {hits}")
    elif len(desc) < 20:
        warn("description 过短", f"{len(desc)} 字，建议写清触发场景")
    else:
        ok("description 无泛化词", f"{len(desc)} 字")

    for lang in ("examples_zh", "examples_en"):
        ex = fm.get(lang) or []
        if not isinstance(ex, list) or not ex:
            warn(f"{lang} 示例", "未提供，详情页「试试这样用」会为空")
        elif len(ex) < 3:
            warn(f"{lang} 示例", f"{len(ex)} 条，建议 3 条")
        else:
            ok(f"{lang} 示例", f"{len(ex)} 条")

    refs = set(re.findall(r"@(references/[\w./-]+)", body))
    for r in refs:
        if not (root / r).exists():
            bad("references 引用有效", f"@ {r} 不存在")
    if refs:
        ok("references 引用有效", f"{len(refs)} 处")

    junk = [str(p.relative_to(root)) for p in iter_files(root)
            if p.name in JUNK or p.suffix in JUNK_SUFFIX]
    leftover = [d for d in JUNK if (root / d).exists()]
    if junk or leftover:
        bad("无冗余文件", f"{junk + leftover}")
    else:
        ok("无冗余文件")

    problems = []
    for p in iter_files(root):
        if p.suffix.lower() in {".zip", ".png", ".jpg", ".ico", ".exe", ".dll", ".so"}:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat, label in SECRET_PATTERNS:
            if re.search(pat, content):
                problems.append(f"{p.relative_to(root)}: {label}")
    problems = [x for x in problems if "package.py" not in x]
    if problems:
        bad("无硬编码路径/密钥", "; ".join(problems))
    else:
        ok("无硬编码路径/密钥")

    size = sum(p.stat().st_size for p in iter_files(root))
    if size > MAX_SIZE:
        bad("包体积 ≤ 3MB", f"{size/1024/1024:.2f}MB")
    else:
        ok("包体积 ≤ 3MB", f"{size/1024:.0f}KB")

    syntax_err = []
    for p in iter_files(root):
        if p.suffix == ".py":
            try:
                py_compile.compile(str(p), cfile=os.path.join(
                    tempfile.gettempdir(), f"chk_{p.stem}.pyc"), doraise=True)
            except py_compile.PyCompileError as e:
                syntax_err.append(f"{p.name}: {e.msg}")
    if syntax_err:
        bad("Python 语法", "; ".join(syntax_err))
    else:
        ok("Python 语法")

    if fm.get("author") in ("本地自建", "author", "your-name", ""):
        warn("author 待确认", "需与开放平台入驻主体一致")
    return all(r[0] != "FAIL" for r in results)


def build(root, out_dir):
    root = Path(root).resolve()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = root.name
    ver = "0.0.0"
    fm, _ = parse_frontmatter((root / "SKILL.md").read_text(encoding="utf-8"))
    if fm:
        ver = fm.get("version", ver)
    target = out_dir / f"{name}-{ver}.zip"

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in iter_files(root):
            arc = f"{name}/{p.relative_to(root).as_posix()}"
            z.write(p, arc)
    return target


# ---------------------------------------------------------------- SkillHub
# SkillHub 与开放平台的打包要求相反：
#   开放平台 → zip 第一层必须是技能目录（skill-name/SKILL.md）
#   SkillHub  → SKILL.md 必须在 zip **根目录**（skill-name/SKILL.md 会校验失败）
# 且 SkillHub 的 frontmatter 走严格字段解析，_zh/_en 这类扩展字段有被判非法的风险，
# 所以打 SkillHub 包时把 frontmatter 收窄到白名单，正文原样保留。

SKILLHUB_FIELDS = ("name", "description", "version", "changelog", "author", "allowed-tools")
SKILLHUB_MAX_TOTAL = 10 * 1024 * 1024
SKILLHUB_MAX_FILE = 1 * 1024 * 1024
SKILLHUB_MAX_FILES = 100
ALLOWED_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".js", ".ts", ".py",
               ".sh", ".png", ".jpg", ".jpeg", ".svg", ".csv", ".toml"}


def _trim_frontmatter(text):
    """只保留 SkillHub 认识的字段，其余丢弃；正文一字不改。"""
    fm, body = parse_frontmatter(text)
    if fm is None:
        return text
    lines = ["---"]
    for key in SKILLHUB_FIELDS:
        val = fm.get(key)
        if not val:
            continue
        if isinstance(val, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {v}" for v in val)
        else:
            # 值里含冒号/引号时统一用双引号包裹并转义
            v = str(val).replace('"', '\\"')
            lines.append(f'{key}: "{v}"' if (":" in v or '"' in v) else f"{key}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def build_skillhub(root, out_dir):
    root = Path(root).resolve()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fm, _ = parse_frontmatter((root / "SKILL.md").read_text(encoding="utf-8"))
    name = (fm or {}).get("name", root.name)
    ver = (fm or {}).get("version", "0.0.0")
    target = out_dir / f"{name}-{ver}-skillhub.zip"

    problems = []
    files = list(iter_files(root))
    if len(files) > SKILLHUB_MAX_FILES:
        problems.append(f"文件数 {len(files)} 超过 {SKILLHUB_MAX_FILES}")
    total = 0
    for p in files:
        size = p.stat().st_size
        total += size
        if size > SKILLHUB_MAX_FILE:
            problems.append(f"{p.name} 单文件 {size/1024/1024:.2f}MB 超过 1MB")
        if p.suffix.lower() not in ALLOWED_EXT:
            problems.append(f"{p.name} 扩展名 {p.suffix} 不在白名单")
    if total > SKILLHUB_MAX_TOTAL:
        problems.append(f"总大小 {total/1024/1024:.2f}MB 超过 10MB")

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            rel = p.relative_to(root).as_posix()          # 注意：不带顶层目录
            if rel == "SKILL.md":
                data = _trim_frontmatter(p.read_text(encoding="utf-8")).encode("utf-8")
                z.writestr(rel, data)
            else:
                z.write(p, rel)

    with zipfile.ZipFile(target) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        root_level = [n for n in names if "/" not in n]
        if "SKILL.md" not in names:
            problems.append("SKILL.md 不在包根目录")
        elif "SKILL.md" not in root_level:
            problems.append("SKILL.md 被放在了子目录里")
    return target, problems, len(files)


def main():
    root = Path(__file__).resolve().parents[1]
    out_dir = root.parents[0] / "dist"
    if "--out" in sys.argv:
        out_dir = Path(sys.argv[sys.argv.index("--out") + 1])

    print(f"校验技能包：{root}\n")
    passed = check(root)

    print(f"{'结果':<6}{'检查项':<26}说明")
    print("-" * 74)
    for status, name, detail in results:
        print(f"{status:<6}{name:<26}{detail}")
    fails = sum(1 for r in results if r[0] == "FAIL")
    warns = sum(1 for r in results if r[0] == "WARN")
    print("-" * 74)
    print(f"PASS {len(results)-fails-warns} · WARN {warns} · FAIL {fails}")

    if fails:
        print("\n存在 FAIL 项，已停止打包。修正后重试。")
        return 1
    if "--check" in sys.argv:
        print("\n仅校验模式，未打包。")
        return 0

    if "--skillhub" in sys.argv:
        target, problems, nfiles = build_skillhub(root, out_dir)
        print(f"\n已打包（SkillHub 格式）：{target}")
        print(f"大小：{target.stat().st_size/1024:.0f} KB | 文件数：{nfiles}")
        if problems:
            print("\nSkillHub 校验未通过：")
            for p in problems:
                print(f"  ✗ {p}")
            return 1
        print("SkillHub 校验：SKILL.md 位于根目录 ✓ · 体积/文件数/扩展名 ✓")
        with zipfile.ZipFile(target) as z:
            print("包内结构：")
            for n in z.namelist():
                print(f"  {n}")
        return 0

    target = build(root, out_dir)
    print(f"\n已打包：{target}")
    print(f"大小：{target.stat().st_size/1024:.0f} KB")
    with zipfile.ZipFile(target) as z:
        tops = {n.split("/")[0] for n in z.namelist()}
        print(f"包内顶层目录：{tops}（应为技能目录本身，且只有一个）")
        print(f"文件数：{len(z.namelist())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
