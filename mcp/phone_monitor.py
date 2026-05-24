#!/data/data/com.termux/files/usr/bin/python3
"""
手机端监控脚本 — 在 Android/Termux 中运行
定期采集手机状态，POST 到 VPS。

使用方法:
    VPS_URL=https://你的域名/phone-data API_KEY=你的密钥 python phone_monitor.py

后台运行（关闭 Termux 后继续运行）:
    nohup python phone_monitor.py > ~/monitor.log 2>&1 &

停止后台进程:
    kill $(cat ~/monitor.pid)
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser

VPS_URL        = os.environ.get("VPS_URL",  "https://YOUR_DOMAIN/phone-data")
API_KEY        = os.environ.get("API_KEY",  "xinxin-key")
INTERVAL       = int(os.environ.get("INTERVAL", "60"))
SEND_CLIPBOARD = os.environ.get("SEND_CLIPBOARD", "false").lower() == "true"
FETCH_URLS     = os.environ.get("FETCH_URLS", "true").lower() == "true"


_URL_RE = re.compile(r'^https?://\S+', re.IGNORECASE)
_last_fetched_url: str = ""


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.texts: list[str] = []
        self.title: list[str] = []
        self._skip = False
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        d = data.strip()
        if not d:
            return
        if self._in_title:
            self.title.append(d)
        elif not self._skip:
            self.texts.append(d)


def fetch_url_content(url: str) -> dict:
    global _last_fetched_url
    if url == _last_fetched_url:
        return {}                        # 没变化，跳过
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 16; Redmi Note 14 Pro) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            final_url = resp.url
            ct = resp.headers.get("Content-Type", "")
            # 尝试从 Content-Type 取编码，兜底 utf-8
            enc = "utf-8"
            if "charset=" in ct:
                enc = ct.split("charset=")[-1].split(";")[0].strip()
            raw = resp.read().decode(enc, errors="ignore")

        ex = _TextExtractor()
        ex.feed(raw)
        title   = "".join(ex.title).strip()
        content = re.sub(r"\s+", " ", " ".join(ex.texts)).strip()[:4000]
        _last_fetched_url = url
        return {"url": final_url, "title": title, "content": content, "status": "ok"}
    except Exception as e:
        return {"url": url, "title": "", "content": "", "status": "error", "error": str(e)}


def run_json(cmd: str):
    """执行 termux-api 命令，返回解析后的 JSON，失败返回 None。"""
    try:
        out = subprocess.check_output(
            cmd, shell=True, stderr=subprocess.DEVNULL, timeout=10
        ).decode().strip()
        return json.loads(out) if out else None
    except Exception:
        return None


def get_current_app() -> str:
    """尝试获取当前前台 App，失败返回 '未知'。"""
    cmds = [
        # Android 10+
        "dumpsys window windows 2>/dev/null | grep -oE 'mCurrentFocus=Window\\{[a-f0-9]+ [A-Za-z]+ [^}]+\\}' | grep -oE '[a-z][a-zA-Z0-9._]+/[a-zA-Z0-9._]+' | head -1",
        # 旧版 Android
        "dumpsys activity 2>/dev/null | grep 'mFocusedActivity' | grep -oE '[a-z][a-zA-Z0-9.]+/[a-zA-Z0-9.]+' | head -1",
        # 兜底方案
        "dumpsys window 2>/dev/null | grep 'mFocusedApp' | grep -oE '[a-z][a-zA-Z0-9.]+/[a-zA-Z0-9.]+' | head -1",
    ]
    for cmd in cmds:
        try:
            out = subprocess.check_output(
                cmd, shell=True, stderr=subprocess.DEVNULL, timeout=5
            ).decode().strip()
            if out:
                return out.split("\n")[0]
        except Exception:
            pass
    return "未知"


def get_screen_on() -> bool | None:
    """返回屏幕是否亮着，无法获取时返回 None。"""
    try:
        out = subprocess.check_output(
            "dumpsys power 2>/dev/null | grep 'Display Power'",
            shell=True, stderr=subprocess.DEVNULL, timeout=5
        ).decode()
        if "state=ON" in out:
            return True
        if "state=OFF" in out:
            return False
    except Exception:
        pass
    return None


def collect() -> dict:
    battery = run_json("termux-battery-status") or {}
    wifi    = run_json("termux-wifi-connectioninfo") or {}
    notifs  = run_json("termux-notification-list")
    if not isinstance(notifs, list):
        notifs = []
    volume  = run_json("termux-volume")
    if not isinstance(volume, list):
        volume = []

    data = {
        "battery":      battery,
        "wifi":         wifi,
        "notifications": notifs,
        "volume":       volume,
        "current_app":  get_current_app(),
        "screen_on":    get_screen_on(),
    }

    if SEND_CLIPBOARD:
        try:
            cb = subprocess.check_output(
                "termux-clipboard-get", shell=True, stderr=subprocess.DEVNULL, timeout=10
            ).decode().strip()
        except Exception:
            cb = ""
        if cb:
            data["clipboard"] = cb
            if FETCH_URLS and _URL_RE.match(cb):
                page = fetch_url_content(cb)
                if page:
                    data["fetched_page"] = page

    return data


def send(data: dict) -> bool:
    payload = json.dumps(data, ensure_ascii=False).encode()
    req = urllib.request.Request(
        VPS_URL,
        data=payload,
        headers={"Content-Type": "application/json", "X-Api-Key": API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"  发送失败: {e}", file=sys.stderr)
        return False


def main():
    # 保存 PID 方便外部停止
    with open(os.path.expanduser("~/monitor.pid"), "w") as f:
        f.write(str(os.getpid()))

    print(f"手机监控已启动")
    print(f"  目标: {VPS_URL}")
    print(f"  间隔: {INTERVAL}秒")
    print(f"  剪贴板: {'开启' if SEND_CLIPBOARD else '关闭'}")
    print(f"  PID:   {os.getpid()}  (~/monitor.pid)")
    print()

    while True:
        data = collect()
        ok   = send(data)
        pct  = data["battery"].get("percentage", "?")
        app  = data["current_app"]
        ts   = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {'✓' if ok else '✗'}  电量 {pct}%  |  {app}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
