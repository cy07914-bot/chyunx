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
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

VPS_URL  = os.environ.get("VPS_URL",  "https://YOUR_DOMAIN/phone-data")
API_KEY  = os.environ.get("API_KEY",  "xinxin-key")
INTERVAL = int(os.environ.get("INTERVAL", "60"))   # 默认60秒一次
SEND_CLIPBOARD = os.environ.get("SEND_CLIPBOARD", "false").lower() == "true"


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
        clipboard = run_json("termux-clipboard-get")  # 返回纯字符串
        if clipboard is not None:
            data["clipboard"] = str(clipboard)

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
