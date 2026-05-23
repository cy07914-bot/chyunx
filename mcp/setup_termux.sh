#!/data/data/com.termux/files/usr/bin/bash
# ─── Termux 手机端配置脚本 ────────────────────────────────────────────────────
# 在 Android/Termux 中运行
#
# 前置条件:
#   1. 从 F-Droid 安装 Termux（不要用 Google Play 版本）
#   2. 从 F-Droid 安装 Termux:API
#   3. 运行此脚本

set -e

echo "=== [1/3] 安装 Termux 依赖 ==="
pkg update -y
pkg install -y termux-api python curl

echo ""
echo "=== [2/3] 手机权限配置（需要手动完成）==="
echo ""
echo "  请在手机【设置】中完成以下授权："
echo ""
echo "  a) 通知读取权限（用于获取通知内容）"
echo "     设置 → 应用 → 特殊权限 → 通知读取 → 开启 Termux:API"
echo ""
echo "  b) 悬浮窗/使用情况访问（用于获取当前 App）"
echo "     设置 → 应用 → 特殊权限 → 有权查看使用情况的应用 → 开启 Termux:API"
echo ""
echo "  完成后按回车继续..."
read -r

echo "=== [3/3] 测试 termux-api 是否正常工作 ==="
echo -n "  电池状态: "
termux-battery-status 2>/dev/null | python -c "import sys,json; d=json.load(sys.stdin); print(f\"{d.get('percentage')}% {d.get('status')}\")" || echo "需要 Termux:API 应用"

echo ""
echo "=========================================="
echo "配置完成！"
echo ""
echo "启动监控（替换为你的实际域名和密钥）："
echo ""
echo "  VPS_URL=https://你的域名/phone-data \\"
echo "  API_KEY=你的密钥 \\"
echo "  python ~/phone_monitor.py"
echo ""
echo "后台运行（Termux 关闭后继续）："
echo ""
echo "  VPS_URL=https://你的域名/phone-data \\"
echo "  API_KEY=你的密钥 \\"
echo "  nohup python ~/phone_monitor.py > ~/monitor.log 2>&1 &"
echo ""
echo "查看日志:"
echo "  tail -f ~/monitor.log"
echo ""
echo "停止运行:"
echo "  kill \$(cat ~/monitor.pid)"
echo "=========================================="
