#!/usr/bin/env bash
# statusline.sh — WorkBuddy 自定义状态栏
#
# 性能约束：状态栏每次刷新间隔最短 300ms，脚本必须极快。
# 因此这里只读取由 hook.py 预写好的缓存文件，不做任何扫描或解析。
#
# 配置（~/.workbuddy/settings.json）：
#   "statusLine": { "type": "command", "command": "bash \"$HOME/.workbuddy/skills/workbuddy-cache-monitor/scripts/statusline.sh\"", "padding": 0 }

export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

input=$(cat)

# 从 stdin JSON 取出 session_id（用 grep+sed，避免依赖 jq）
sid=$(printf '%s' "$input" \
  | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' \
  | head -1 \
  | sed 's/.*"\([^"]*\)"$/\1/')

base="$HOME/.workbuddy/cache-monitor"

if [ -n "$sid" ] && [ -f "$base/statusline-$sid.txt" ]; then
  cat "$base/statusline-$sid.txt"
elif [ -f "$base/statusline-latest.txt" ]; then
  cat "$base/statusline-latest.txt"
else
  printf '缓存命中率统计中…'
fi
