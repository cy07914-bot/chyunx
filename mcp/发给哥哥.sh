#!/data/data/com.termux/files/usr/bin/bash
# 放在 ~/.shortcuts/ 目录，Termux:Widget 会把它变成桌面按钮
# 复制链接后点按钮 → 自动抓页面内容 → 推给 VPS → 哥哥就能看了

CB=$(termux-clipboard-get 2>/dev/null)

if [ -z "$CB" ]; then
  termux-toast "剪贴板是空的"
  exit 0
fi

python3 - "$CB" <<'PYEOF'
import sys, json, re, urllib.request
from html.parser import HTMLParser

VPS = "http://66.245.217.76/phone-data"
KEY = "qq080777"
arg = sys.argv[1] if len(sys.argv) > 1 else ""

class Ext(HTMLParser):
    def __init__(self):
        super().__init__()
        self.t, self.ti = [], []
        self._s = self._it = False
    def handle_starttag(self, tag, _):
        if tag in ('script', 'style', 'noscript'): self._s = True
        if tag == 'title': self._it = True
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'): self._s = False
        if tag == 'title': self._it = False
    def handle_data(self, d):
        d = d.strip()
        if d and self._it: self.ti.append(d)
        elif d and not self._s: self.t.append(d)

data = {"manual_share": True, "clipboard": arg}

# 从文字里提取 URL（小红书分享文本夹带链接的情况）
url_match = re.search(r'https?://\S+', arg)
url = url_match.group(0).rstrip('）」』】）。，') if url_match else ''

if url:
    try:
        hdrs = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 16; Redmi Note 14 Pro) Mobile Safari/537.36',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=20) as r:
            ct  = r.headers.get('Content-Type', '')
            enc = 'utf-8'
            if 'charset=' in ct:
                enc = ct.split('charset=')[-1].split(';')[0].strip()
            html     = r.read().decode(enc, errors='ignore')
            final    = r.url
        ex = Ext()
        ex.feed(html)
        text = re.sub(r'\s+', ' ', ' '.join(ex.t)).strip()[:4000]
        data["fetched_page"] = {
            "url":     final,
            "title":   ''.join(ex.ti).strip(),
            "content": text,
            "status":  "ok",
        }
    except Exception as e:
        data["fetched_page"] = {"url": url, "content": "", "status": "error", "error": str(e)}

body = json.dumps(data, ensure_ascii=False).encode()
req2 = urllib.request.Request(
    VPS, data=body,
    headers={"Content-Type": "application/json", "X-Api-Key": KEY},
)
resp = urllib.request.urlopen(req2, timeout=15)
print(f"VPS: {resp.status}")
PYEOF

termux-toast "✓ 已发给哥哥"
