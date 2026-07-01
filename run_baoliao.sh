#!/bin/bash
# 爆料活动发帖 - nohup方式运行，不被Claude会话kill
# 用法: bash run_baoliao.sh [文件名]

cd /Users/bytedance/claude/media-assistant

FILE=${1:-posts/baoliao.txt}
shift || true

LOG="logs/baoliao_$(date +%Y%m%d_%H%M%S).log"
mkdir -p logs

nohup python3 paste_post.py --file "$FILE" "$@" > "$LOG" 2>&1 &
PID=$!
echo "✅ 进程已启动 (PID: $PID)"
echo "📋 日志: $LOG"
echo "👀 查看进度: tail -f $LOG"
echo "🛑 停止: kill $PID"
