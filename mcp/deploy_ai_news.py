#!/usr/bin/env python3
"""部署 get_ai_news 工具到 VPS。在 Termux 里跑: python3 deploy_ai_news.py"""
import subprocess, sys

VPS = "root@66.245.217.76"
TARGET = "/opt/xinxin-monitor/vps_server.py"

PATCH1_OLD = '''    {
        "name": "get_weibo_trending",
        "description": "获取微博实时热搜榜，看看大家今天在聊什么。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数，默认20", "default": 20},
            },
            "required": [],
        },
    },'''

PATCH1_NEW = '''    {
        "name": "get_weibo_trending",
        "description": "获取微博实时热搜榜，看看大家今天在聊什么。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数，默认20", "default": 20},
            },
            "required": [],
        },
    },
    {
        "name": "get_ai_news",
        "description": "获取最新 AI 资讯，来自量子位和机器之心 RSS 订阅。看看 AI 圈今天有什么大事。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "每个来源最多返回几条，默认5", "default": 5},
            },
            "required": [],
        },
    },'''

PATCH2_OLD = 'ASYNC_TOOLS = {"twitter_post", "twitter_search", "twitter_get_mentions", "telegram_send", "get_weather", "get_weibo_trending"}'
PATCH2_NEW = 'ASYNC_TOOLS = {"twitter_post", "twitter_search", "twitter_get_mentions", "telegram_send", "get_weather", "get_weibo_trending", "get_ai_news"}'

PATCH3_OLD = 'async def get_weibo_trending_text(limit: int = 20) -> str:'
PATCH3_NEW = '''async def get_ai_news_text(limit: int = 5) -> str:
    import xml.etree.ElementTree as ET
    feeds = [
        ("量子位", "https://www.qbitai.com/feed"),
        ("机器之心", "https://www.jiqizhixin.com/rss.xml"),
    ]
    all_news = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for source, url in feeds:
            try:
                r = await client.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                root = ET.fromstring(r.content)
                channel = root.find("channel") or root
                items = channel.findall("item")
                for item in items[:limit]:
                    title = (item.findtext("title") or "").strip()
                    link  = (item.findtext("link") or "").strip()
                    pub   = (item.findtext("pubDate") or "").strip()
                    if title:
                        all_news.append({"source": source, "title": title, "link": link, "pubDate": pub})
            except Exception as e:
                all_news.append({"source": source, "error": str(e)})
    return json.dumps({"count": len(all_news), "news": all_news}, ensure_ascii=False, indent=2)


async def get_weibo_trending_text(limit: int = 20) -> str:'''

PATCH4_OLD = '            elif name == "get_weibo_trending":\n                text = await get_weibo_trending_text(int(args.get("limit", 20)))'
PATCH4_NEW = '''            elif name == "get_weibo_trending":
                text = await get_weibo_trending_text(int(args.get("limit", 20)))
            elif name == "get_ai_news":
                text = await get_ai_news_text(int(args.get("limit", 5)))'''


def ssh(cmd: str) -> str:
    r = subprocess.run(["ssh", VPS, cmd], capture_output=True, text=True)
    return r.stdout + r.stderr


def read_vps() -> str:
    r = subprocess.run(["ssh", VPS, f"cat {TARGET}"], capture_output=True, text=True)
    return r.stdout


def write_vps(content: str):
    r = subprocess.run(["ssh", VPS, f"cat > {TARGET}"], input=content, capture_output=True, text=True)
    return r.returncode == 0


patches = [
    ("add get_ai_news to TOOLS", PATCH1_OLD, PATCH1_NEW),
    ("add get_ai_news to ASYNC_TOOLS", PATCH2_OLD, PATCH2_NEW),
    ("add get_ai_news_text() function", PATCH3_OLD, PATCH3_NEW),
    ("add get_ai_news handler in handle_mcp", PATCH4_OLD, PATCH4_NEW),
]

print("读取 VPS 文件...")
content = read_vps()
if not content:
    print("❌ 读取失败，检查 SSH 连接")
    sys.exit(1)
print(f"读取成功，{len(content)} 字节")

changed = False
for name, old, new in patches:
    if old in content:
        content = content.replace(old, new, 1)
        print(f"✅ {name}")
        changed = True
    elif new.strip() in content or "get_ai_news" in content and "get_ai_news_text" in content:
        print(f"⏭  {name} 已存在，跳过")
    else:
        print(f"❌ {name} — 找不到目标字符串，跳过")

if not changed:
    print("没有变化，退出")
    sys.exit(0)

print("写入 VPS...")
if write_vps(content):
    print("✅ 写入成功")
else:
    print("❌ 写入失败")
    sys.exit(1)

print("重启服务...")
out = ssh("systemctl restart xinxin-monitor && sleep 2 && systemctl is-active xinxin-monitor")
print(out.strip())
print("完成！")
