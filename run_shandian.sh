#!/bin/bash
# 闪电观察发帖 - nohup方式运行，不被Claude会话kill
# 用法: bash run_shandian.sh [文件名] [--no-image]

cd /Users/bytedance/claude/media-assistant

FILE=${1:-posts/shandian.txt}
shift || true

LOG="logs/shandian_$(date +%Y%m%d_%H%M%S).log"
mkdir -p logs

nohup python3 shandian_post.py --file "$FILE" "$@" > "$LOG" 2>&1 &
PID=$!
echo "✅ 进程已启动 (PID: $PID)"
echo "📋 日志: $LOG"
echo "👀 查看进度: tail -f $LOG"
echo "🛑 停止: kill $PID"
