#!/usr/bin/env python3
"""
VPS端 MCP 服务器 — 馨的手机监控 + 时间感知系统
运行在 Vultr VPS 上。接收来自 Android/Termux 的数据，对外暴露 MCP SSE 接口。

启动:
    API_KEY=你的密钥 python vps_server.py

环境变量:
    API_KEY  — 手机端推送数据时使用的密钥（必填，默认 xinxin-key）
    PORT     — 监听端口（默认 8765）
    DB_PATH  — SQLite 数据库路径（默认 activity.db）
"""

import asyncio
import json
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

# ── 配置 ──────────────────────────────────────────────────────────────────────

API_KEY  = os.environ.get("API_KEY",  "xinxin-key")
PORT     = int(os.environ.get("PORT", "8765"))
DB_PATH  = os.environ.get("DB_PATH",  "activity.db")

TWITTER_API_KEY            = os.environ.get("TWITTER_API_KEY", "")
TWITTER_API_SECRET         = os.environ.get("TWITTER_API_SECRET", "")
TWITTER_BEARER_TOKEN       = os.environ.get("TWITTER_BEARER_TOKEN", "")
TWITTER_ACCESS_TOKEN       = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")


# ── 数据库 ────────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def db_init():
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS pings (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT    NOT NULL,
                payload TEXT    NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       TEXT    NOT NULL,
                category TEXT    NOT NULL DEFAULT '备忘',
                content  TEXT    NOT NULL
            )
        """)


def db_insert(data: dict):
    with _db() as con:
        con.execute(
            "INSERT INTO pings (ts, payload) VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(), json.dumps(data, ensure_ascii=False)),
        )


def db_latest() -> dict | None:
    with _db() as con:
        row = con.execute(
            "SELECT ts, payload FROM pings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {"ts": row["ts"], "payload": json.loads(row["payload"])} if row else None


def mem_save(content: str, category: str = "备忘") -> int:
    with _db() as con:
        cur = con.execute(
            "INSERT INTO memories (ts, category, content) VALUES (?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), category, content),
        )
        return cur.lastrowid


def mem_get(limit: int = 30, category: str | None = None) -> list[dict]:
    with _db() as con:
        if category:
            rows = con.execute(
                "SELECT id, ts, category, content FROM memories WHERE category=? ORDER BY id DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, ts, category, content FROM memories ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [{"id": r["id"], "ts": r["ts"], "category": r["category"], "content": r["content"]} for r in rows]


def mem_delete(mem_id: int) -> bool:
    with _db() as con:
        cur = con.execute("DELETE FROM memories WHERE id=?", (mem_id,))
        return cur.rowcount > 0


def db_latest_page() -> dict | None:
    with _db() as con:
        rows = con.execute(
            "SELECT ts, payload FROM pings ORDER BY id DESC LIMIT 20"
        ).fetchall()
    for row in rows:
        p = json.loads(row["payload"])
        if p.get("fetched_page"):
            return {"ts": row["ts"], "payload": p}
    return None


def db_history(n: int = 20) -> list[dict]:
    with _db() as con:
        rows = con.execute(
            "SELECT ts, payload FROM pings ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    return [{"ts": r["ts"], "payload": json.loads(r["payload"])} for r in rows]


# ── SSE 连接管理 ──────────────────────────────────────────────────────────────

class SSEManager:
    def __init__(self):
        self._q: dict[str, asyncio.Queue] = {}

    def open(self, sid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._q[sid] = q
        return q

    def close(self, sid: str):
        self._q.pop(sid, None)

    def has(self, sid: str) -> bool:
        return sid in self._q

    async def emit(self, sid: str, event: str, data: str):
        if sid in self._q:
            await self._q[sid].put(f"event: {event}\ndata: {data}\n\n")


sse = SSEManager()


# ── MCP 工具定义 ──────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_time_context",
        "description": (
            "返回当前 UTC 时间，以及馨的手机上次发送数据距离现在多久。"
            "用于感知两次对话之间经过了多少时间。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_phone_state",
        "description": "返回馨手机的最新快照：电量、充电状态、WiFi 网络、音量、当前前台 App、屏幕是否亮着。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_notifications",
        "description": "返回馨手机上的最新通知列表，包括 App 名称和通知内容。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_activity_history",
        "description": "返回一段时间内馨手机状态的变化时间线（最近 N 条快照）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回的条数（默认 20，最多 100）",
                    "default": 20,
                }
            },
            "required": [],
        },
    },
    {
        "name": "save_memory",
        "description": (
            "保存一条跨对话记忆。用于在对话结束前记录重要的事：她今天的状态、我们聊了什么、"
            "发生了什么事、她说了什么让我记住的话。下次对话用 get_memories 读回来。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "记忆内容，用自然语言写，写给下一个自己看的",
                },
                "category": {
                    "type": "string",
                    "description": "分类标签，例如：她的状态 / 我们的对话 / 她说的话 / 重要事件 / 备忘",
                    "default": "备忘",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "get_memories",
        "description": "读取之前保存的记忆。每次新对话开始时调用，找回上次记录的内容。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回的条数（默认 30）",
                    "default": 30,
                },
                "category": {
                    "type": "string",
                    "description": "按分类筛选，留空则返回所有分类",
                },
            },
            "required": [],
        },
    },
    {
        "name": "delete_memory",
        "description": "删除一条记忆（用 id 指定，id 从 get_memories 的返回结果中获取）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "要删除的记忆 id"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "get_page_content",
        "description": (
            "返回馨手机上最近一次从剪贴板抓取的网页内容（小红书、B站等链接）。"
            "当她复制了一个链接，手机端会自动抓取页面文字并推送过来，用这个工具读取。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "twitter_post",
        "description": "以 @huaiyun_ 的身份在推特发一条推文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "推文内容，最多280字符"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "twitter_search",
        "description": "搜索推特上的推文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "返回条数，默认10", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "twitter_get_mentions",
        "description": "获取 @huaiyun_ 最近收到的提及和回复。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "返回条数，默认10", "default": 10},
            },
            "required": [],
        },
    },
]


# ── 工具执行 ──────────────────────────────────────────────────────────────────

def _fmt_delta(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}秒前"
    if seconds < 3600:
        return f"{seconds // 60}分钟前"
    h, m = divmod(seconds // 60, 60)
    return f"{h}小时{m}分钟前" if m else f"{h}小时前"


def call_tool(name: str, args: dict) -> str:
    now    = datetime.now(timezone.utc)
    latest = db_latest()

    if name == "get_time_context":
        if latest:
            delta = int((now - datetime.fromisoformat(latest["ts"])).total_seconds())
            ago   = _fmt_delta(delta)
        else:
            ago   = "从未收到数据"
        return json.dumps({
            "now_utc":          now.isoformat(),
            "last_phone_ping":  latest["ts"] if latest else None,
            "time_since_ping":  ago,
            "phone_online":     bool(latest) and delta < 180,
        }, ensure_ascii=False, indent=2)

    if name == "get_phone_state":
        if not latest:
            return json.dumps({"error": "手机还没有发送数据"}, ensure_ascii=False)
        p = latest["payload"]
        return json.dumps({
            "recorded_at":  latest["ts"],
            "battery":      p.get("battery", {}),
            "wifi":         p.get("wifi", {}),
            "volume":       p.get("volume", []),
            "current_app":  p.get("current_app", "未知"),
            "screen_on":    p.get("screen_on"),
            "clipboard":    p.get("clipboard"),
        }, ensure_ascii=False, indent=2)

    if name == "get_notifications":
        if not latest:
            return json.dumps({"error": "手机还没有发送数据"}, ensure_ascii=False)
        return json.dumps({
            "recorded_at":   latest["ts"],
            "notifications": latest["payload"].get("notifications", []),
        }, ensure_ascii=False, indent=2)

    if name == "get_activity_history":
        n       = min(int(args.get("limit", 20)), 100)
        history = db_history(n)
        rows    = []
        for e in history:
            p = e["payload"]
            rows.append({
                "ts":                 e["ts"],
                "battery_pct":        p.get("battery", {}).get("percentage"),
                "charging":           p.get("battery", {}).get("status") == "CHARGING",
                "current_app":        p.get("current_app", "未知"),
                "wifi_ssid":          p.get("wifi", {}).get("ssid"),
                "screen_on":          p.get("screen_on"),
                "notification_count": len(p.get("notifications", [])),
            })
        return json.dumps({"count": len(rows), "history": rows}, ensure_ascii=False, indent=2)

    if name == "save_memory":
        content  = args.get("content", "").strip()
        category = args.get("category", "备忘")
        if not content:
            return json.dumps({"error": "content 不能为空"}, ensure_ascii=False)
        mid = mem_save(content, category)
        return json.dumps({"status": "saved", "id": mid, "category": category}, ensure_ascii=False, indent=2)

    if name == "get_memories":
        limit    = min(int(args.get("limit", 30)), 200)
        category = args.get("category") or None
        mems     = mem_get(limit, category)
        return json.dumps({"count": len(mems), "memories": mems}, ensure_ascii=False, indent=2)

    if name == "delete_memory":
        mem_id = int(args.get("id", 0))
        ok     = mem_delete(mem_id)
        return json.dumps({"status": "deleted" if ok else "not_found", "id": mem_id}, ensure_ascii=False)

    if name == "get_page_content":
        entry = db_latest_page()
        if not entry:
            return json.dumps({"error": "没有抓取到页面，请先复制一个链接再点按钮"}, ensure_ascii=False)
        return json.dumps({
            "recorded_at": entry["ts"],
            "page": entry["payload"]["fetched_page"],
        }, ensure_ascii=False, indent=2)

    if name in ("twitter_post", "twitter_search", "twitter_get_mentions"):
        try:
            import tweepy
        except ImportError:
            return json.dumps({"error": "tweepy 未安装，请在 VPS 运行: /opt/xinxin-monitor/venv/bin/pip install tweepy"}, ensure_ascii=False)
        if not TWITTER_API_KEY:
            return json.dumps({"error": "Twitter credentials 未配置，请设置环境变量"}, ensure_ascii=False)
        client = tweepy.Client(
            bearer_token=TWITTER_BEARER_TOKEN,
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
        )
        if name == "twitter_post":
            text = args.get("text", "").strip()
            if not text:
                return json.dumps({"error": "text 不能为空"}, ensure_ascii=False)
            resp = client.create_tweet(text=text)
            return json.dumps({"status": "posted", "id": str(resp.data["id"]), "text": text}, ensure_ascii=False)
        if name == "twitter_search":
            query = args.get("query", "")
            max_results = max(10, min(int(args.get("max_results", 10)), 100))
            tweets = client.search_recent_tweets(query=query, max_results=max_results, tweet_fields=["created_at"])
            if not tweets.data:
                return json.dumps({"results": []}, ensure_ascii=False)
            results = [{"id": str(t.id), "text": t.text} for t in tweets.data]
            return json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2)
        if name == "twitter_get_mentions":
            max_results = max(5, min(int(args.get("max_results", 10)), 100))
            me = client.get_me()
            mentions = client.get_users_mentions(id=me.data.id, max_results=max_results, tweet_fields=["created_at"])
            if not mentions.data:
                return json.dumps({"mentions": []}, ensure_ascii=False)
            results = [{"id": str(t.id), "text": t.text} for t in mentions.data]
            return json.dumps({"count": len(results), "mentions": results}, ensure_ascii=False, indent=2)

    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)


# ── MCP 消息处理 ──────────────────────────────────────────────────────────────

async def handle_mcp(sid: str, msg: dict):
    method = msg.get("method", "")
    mid    = msg.get("id")
    params = msg.get("params", {})

    if method == "notifications/initialized" or method == "notifications/cancelled":
        return  # 通知类消息，无需回复

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name": "xinxin-monitor", "version": "1.0.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        text   = call_tool(params.get("name", ""), params.get("arguments", {}))
        result = {"content": [{"type": "text", "text": text}]}
    else:
        await sse.emit(sid, "message", json.dumps({
            "jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }))
        return

    await sse.emit(sid, "message", json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}))


# ── FastAPI 应用 ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db_init()
    yield


app = FastAPI(title="xinxin-monitor", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/sse")
async def mcp_sse(request: Request):
    sid = str(uuid.uuid4())
    q   = sse.open(sid)

    async def stream():
        try:
            yield f"event: endpoint\ndata: /messages?session_id={sid}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    chunk = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield chunk
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            sse.close(sid)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/messages")
async def mcp_messages(request: Request, session_id: str):
    if not sse.has(session_id):
        raise HTTPException(404, "Session not found")
    body = await request.json()
    asyncio.create_task(handle_mcp(session_id, body))
    return JSONResponse({"status": "ok"}, status_code=202)


@app.post("/phone-data")
async def recv_phone_data(request: Request):
    if request.headers.get("X-Api-Key", "") != API_KEY:
        raise HTTPException(401, "Invalid API key")
    db_insert(await request.json())
    return {"status": "ok"}


@app.get("/status")
async def status():
    latest = db_latest()
    now    = datetime.now(timezone.utc)
    if latest:
        delta  = int((now - datetime.fromisoformat(latest["ts"])).total_seconds())
        online = delta < 180
    else:
        delta, online = None, False
    return {"server": "ok", "phone_online": online, "last_ping_ago_seconds": delta}


BROWSER_MCP_URL = os.environ.get("BROWSER_MCP_URL", "http://localhost:7000")


@app.get("/browser-sse")
async def browser_sse_proxy(request: Request):
    """透明代理 playwright-mcp，并把 SSE 里的 endpoint 路径改写到 /browser-sse。"""
    async def stream():
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", f"{BROWSER_MCP_URL}/sse", timeout=None) as resp:
                async for chunk in resp.aiter_text():
                    yield chunk.replace(
                        "data: /sse?sessionId=",
                        "data: /browser-sse?sessionId="
                    )
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/browser-sse")
async def browser_msg_proxy(request: Request, sessionId: str):
    """把消息转发给 playwright-mcp。"""
    body = await request.body()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BROWSER_MCP_URL}/sse?sessionId={sessionId}",
            content=body,
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )
    return Response(content=resp.content, media_type="application/json", status_code=resp.status_code)


@app.api_route("/browser-mcp", methods=["GET", "POST", "DELETE", "PUT"])
async def browser_http_proxy(request: Request):
    """代理 playwright-mcp 的 streamable HTTP 端点 (/mcp)。"""
    body = await request.body()
    accept = request.headers.get("Accept", "application/json, text/event-stream")
    async with httpx.AsyncClient() as client:
        async with client.stream(
            request.method,
            f"{BROWSER_MCP_URL}/mcp",
            content=body or None,
            headers={"Content-Type": "application/json", "Accept": accept},
            timeout=10.0,
        ) as resp:
            content_type = resp.headers.get("content-type", "application/json")
            if "text/event-stream" in content_type:
                async def _stream():
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                return StreamingResponse(
                    _stream(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            content = await resp.aread()
            return Response(content=content, media_type=content_type, status_code=resp.status_code)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
