#!/bin/bash
# ─── VPS 一键配置脚本 ─────────────────────────────────────────────────────────
# 在 Vultr VPS 上执行（以 root 运行）
#
# 使用:
#   1. 把 vps_server.py 和 requirements.txt 上传到 VPS
#   2. 修改下方 DOMAIN 和 API_KEY
#   3. bash setup_vps.sh

set -e

DOMAIN="mcp.chyunx.com"        # 子域名，需要在 DNS 加 A 记录指向 66.245.217.76
API_KEY="xinxin-key"          # 改成你自己定的密钥（手机端也要一致）
INSTALL_DIR="/opt/xinxin-monitor"
PORT=8765

echo "=== [1/5] 安装系统依赖 ==="
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx curl

echo "=== [2/5] 配置 Python 虚拟环境 ==="
mkdir -p "$INSTALL_DIR"
cp vps_server.py requirements.txt "$INSTALL_DIR/"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

echo "=== [3/5] 创建 systemd 服务 ==="
cat > /etc/systemd/system/xinxin-monitor.service << EOF
[Unit]
Description=馨的手机监控 MCP 服务器
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
Environment=API_KEY=$API_KEY
Environment=PORT=$PORT
Environment=DB_PATH=$INSTALL_DIR/activity.db
ExecStart=$INSTALL_DIR/venv/bin/python vps_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xinxin-monitor
systemctl start xinxin-monitor

echo "=== [4/5] 配置 Nginx 反代 ==="
cat > /etc/nginx/sites-available/xinxin-monitor << EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass         http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade \$http_upgrade;
        proxy_set_header   Connection '';
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;

        # SSE 关键配置
        proxy_buffering         off;
        proxy_cache             off;
        proxy_read_timeout      3600s;
        proxy_send_timeout      3600s;
        chunked_transfer_encoding on;
    }
}
EOF

ln -sf /etc/nginx/sites-available/xinxin-monitor /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

echo "=== [5/5] 申请 SSL 证书 ==="
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@"$DOMAIN" || \
    echo "SSL 申请失败（DNS 可能还没生效，等几分钟再手动运行: certbot --nginx -d $DOMAIN）"

echo ""
echo "=========================================="
echo "完成！"
echo "  MCP SSE 地址:    https://$DOMAIN/sse"
echo "  手机推送地址:    https://$DOMAIN/phone-data"
echo "  状态检查:        https://$DOMAIN/status"
echo "  API_KEY:         $API_KEY"
echo ""
echo "在 Claude Code 设置中添加 MCP 服务器:"
echo '  claude mcp add xinxin-monitor --transport sse https://'"$DOMAIN"'/sse'
echo "=========================================="
