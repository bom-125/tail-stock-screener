#!/usr/bin/env python3
"""
尾盘选股器 v2 - Tail Stock Screener
数据源: 新浪财经 (hq.sinajs.cn)
选股策略: 尾盘放量突破 + 趋势位置 + 换手活跃度+ K线形态
"""
import json, sys, os, time, math, re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.sina.com.cn"
    })
except ImportError:
    print("pip install requests"); sys.exit(1)

CACHE = {}

# ====== 数据层 ======

def get_stock_list():
    if "stock_list" in CACHE:
        return CACHE["stock_list"]
    all_stocks = []
    for page in range(1, 11):
        url = (f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"Market_Center.getHQNodeData?page={page}&num=600&sort=symbol&asc=1"
               f"&node=hs_a&symbol=&_s_r_a=auto")
        try:
            resp = session.get(url, timeout=15)
            resp.encoding = 'gb2312'
            data = json.loads(resp.text)
            for item in data:
                code = item.get("code", "")
                name = item.get("name", "")
                if code and name and "ST" not in name.upper():
                    all_stocks.append((code, name))
        except:
            break
    CACHE["stock_list"] = all_stocks
    return all_stocks

def fetch_quotes(codes):
    if not codes:
        return {}
    symbols = []
    for code in codes:
        prefix = "sh" if code.startswith("6") else "sz"
        symbols.append(f"{prefix}{code}")
    
    results = {}
    for i in range(0, len(symbols), 400):
        batch = symbols[i:i+400]
        url = f"https://hq.sinajs.cn/list={','.join(batch)}"
        try:
            resp = session.get(url, timeout=20)
            resp.encoding = 'gb2312'
            for line in resp.text.strip().split("\n"):
                if not line or "=" not in line:
                    continue
                m = re.search(r'hq_str_s[hz](\d+)="(.+)"', line)
                if m:
                    code = m.group(1)
                    data = m.group(2).split(",")
                    if len(data) >= 32:
                        results[code] = {
                            "name": data[0], "open": sf(data[1]),
                            "close_yest": sf(data[2]), "price": sf(data[3]),
                            "high": sf(data[4]), "low": sf(data[5]),
                            "volume": sf(data[8]), "amount": sf(data[9]),
                            "date": data[30] if len(data) > 30 else "",
                        }
        except:
            continue
    return results

def sf(s):
    try: return float(s)
    except: return 0.0

# ====== 选股策略 ======

def score_stock(data):
    """
    尾盘选股评分体系（满分100）
    策略1: 尾盘拉升强度 (0-25) - 收盘位置 + 涨幅
    策略2: 成交量评分 (0-15) - 成交额越大越好
    策略3: 趋势位置 (0-15) - 涨幅2%-6%最佳
    策略4: 换手活跃度 (0-15) - 成交额代理
    策略5: K线形态 (0-15) - 阳线实体+上影线
    策略6: 突破信号 (0-15) - 涨幅加速
    """
    p, o, h, l, v, amt, yc = data["price"], data["open"], data["high"], data["low"], data["volume"], data["amount"], data["close_yest"]
    
    if p <= 0 or yc <= 0:
        return 0, {}
    
    chg = (p - yc) / yc * 100
    rng = h - l
    
    # 过滤
    if chg < 0 or chg >= 9.5 or p < 3:
        return 0, {}
    
    sc, dt = 0, {"涨幅%": round(chg, 2)}
    
    # 策略1: 尾盘拉升
    rp = (p - l) / rng if rng > 0 else 0.5
    s1 = min(25, rp * 20 + (min(chg/6*5, 5) if chg <= 6 else rp*15))
    sc += s1; dt["收盘位置"] = f"{rp*100:.0f}%"; dt["尾盘拉升"] = round(s1,1)
    
    # 策略2: 量能
    amt_yi = amt / 1e8
    if amt_yi > 10: s2 = 15
    elif amt_yi > 5: s2 = 12
    elif amt_yi > 2: s2 = 9
    elif amt_yi > 1: s2 = 6
    elif amt_yi > 0.5: s2 = 3
    else: s2 = 1
    sc += s2; dt["成交额(亿)"] = f"{amt_yi:.1f}"; dt["量能分"] = round(s2,1)
    
    # 策略3: 趋势
    if 2 <= chg <= 6: s3 = 15
    elif 0.5 <= chg < 2: s3 = 8 + (chg-0.5)/1.5*7
    elif chg > 6: s3 = max(5, 15-(chg-6)*1.5)
    else: s3 = chg/0.5*8
    s3 = max(0, min(15, s3))
    sc += s3; dt["趋势分"] = round(s3,1)
    
    # 策略4: 换手（金额代理）
    if 2 <= amt_yi <= 5: s4 = 10
    elif 5 < amt_yi <= 15: s4 = 15
    elif 15 < amt_yi <= 30: s4 = 12
    elif amt_yi > 30: s4 = 8
    else: s4 = 5
    sc += s4; dt["换手分"] = round(s4,1)
    
    # 策略5: K线
    if rng > 0:
        body = abs(p-o); br = body/rng
        if p > o:
            us = (h-p)/rng
            s5 = max(2, min(15, 8 + br*5 + (5 if us<0.1 and br>0.5 else 0)))
        else: s5 = br*3
        s5 = max(1, min(15, s5))
    else: s5 = 8
    sc += s5; dt["K线分"] = round(s5,1)
    
    # 策略6: 突破
    if chg >= 3: s6 = 12
    elif chg >= 2: s6 = 9
    elif chg >= 1: s6 = 6
    else: s6 = 3
    sc += s6; dt["突破分"] = round(s6,1)
    
    return min(100, round(sc, 1)), dt

def screen(min_score=40, topn=50):
    stocks = get_stock_list()
    codes = [c for c, _ in stocks]
    quotes = fetch_quotes(codes)
    
    results = []
    for code, name in stocks:
        if code not in quotes:
            continue
        data = quotes[code]
        sc, det = score_stock(data)
        if sc >= min_score:
            results.append({
                "code": code, "name": name, "score": sc,
                "price": data["price"], "change_pct": det.get("涨幅%", 0),
                "amount": data["amount"], "details": det
            })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:topn]

# ====== HTML ======

INDEX_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>尾盘选股器</title>
<style>
:root{--bg:#0a0e17;--card:#111827;--border:#1e293b;--text:#e2e8f0;--muted:#64748b;--accent:#3b82f6;--green:#22c55e;--red:#ef4444;--yellow:#eab308}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;justify-content:center;padding:32px 16px}
.container{max-width:900px;width:100%}
.header{text-align:center;margin-bottom:32px}
.header h1{font-size:28px;font-weight:700;letter-spacing:-.5px}
.header p{color:var(--muted);font-size:14px;margin-top:4px}
.controls{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;margin-bottom:20px;display:flex;gap:12px;align-items:end;flex-wrap:wrap}
.field{display:flex;flex-direction:column;gap:4px}
.field label{font-size:12px;color:var(--muted);font-weight:500}
.field select{background:#0a0e17;border:1px solid var(--border);color:var(--text);padding:10px 14px;border-radius:8px;font-size:14px;font-family:inherit;outline:none;width:120px}
.btn{background:var(--accent);color:#fff;border:none;padding:10px 24px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{opacity:.85}.btn:disabled{opacity:.5;cursor:not-allowed}
.status{text-align:center;padding:16px;color:var(--muted);font-size:13px}
.results-card{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden}
.results-header{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;font-size:13px}
.results-count{color:var(--muted)}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:10px 16px;font-size:12px;color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--border)}
td{padding:12px 16px;font-size:14px;border-bottom:1px solid var(--border);font-variant-numeric:tabular-nums}
tr:hover{background:#1a2030}
.score-badge{display:inline-block;padding:2px 10px;border-radius:20px;font-weight:700;font-size:13px}
.score-high{background:#1a3a1a;color:var(--green)}.score-mid{background:#2a2a1a;color:var(--yellow)}.score-low{background:#1a1a2a;color:var(--muted)}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;vertical-align:middle;margin-right:8px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="container">
<div class="header"><h1>📈 尾盘选股器</h1><p>基于尾盘放量突破、趋势位置、换手活跃度、K线形态筛选次日潜力股</p></div>
<div class="controls">
<div class="field"><label>最低评分</label><select id="scoreInput"><option value="30">30 - 宽松</option><option value="40" selected>40 - 适中</option><option value="50">50 - 严格</option><option value="60">60 - 精选</option></select></div>
<div class="field"><label>实时行情</label><span style="color:var(--muted);font-size:13px;padding:10px 0" id="dateLabel">--</span></div>
<button class="btn" id="scanBtn" onclick="scan()">开始扫描</button>
</div>
<div class="status" id="status">点击"开始扫描"获取今日尾盘选股推荐</div>
<div class="results-card" id="resultsCard" style="display:none">
<div class="results-header"><span>📊 扫描结果 — <span id="resultDate"></span></span><span class="results-count" id="resultCount"></span></div>
<div style="overflow-x:auto"><table><thead><tr><th>排名</th><th>代码</th><th>名称</th><th>评分</th><th>现价</th><th>涨幅</th><th>成交额</th><th>尾盘</th><th>K线</th></tr></thead><tbody id="tableBody"></tbody></table></div>
</div>
</div>
<script>
document.getElementById("dateLabel").textContent = new Date().toISOString().split("T")[0];
async function scan(){
var ms=document.getElementById("scoreInput").value,btn=document.getElementById("scanBtn"),st=document.getElementById("status"),card=document.getElementById("resultsCard");
btn.disabled=true;btn.textContent="扫描中...";st.innerHTML='<span class="spinner"></span> 扫描全市场A股，约5-10秒...';card.style.display="none";
try{
var r=await fetch("/api/scan?min_score="+ms),d=await r.json();
if(d.error){st.textContent=d.error}else if(d.count===0){st.innerHTML="⚠️ 无结果，降低评分试试"}else{st.textContent="";render(d)}
}catch(e){st.textContent="连接失败"}
btn.disabled=false;btn.textContent="开始扫描";
}
function render(d){
document.getElementById("resultsCard").style.display="block";
document.getElementById("resultDate").textContent=d.date;
document.getElementById("resultCount").textContent=d.count+" 只 | "+d.elapsed+"s";
var h="";
d.results.forEach(function(x,i){
var c=x.score>=60?"score-high":x.score>=45?"score-mid":"score-low",dt=x.details||{};
h+="<tr><td>"+(i+1)+"</td><td style='font-weight:600'>"+x.code+"</td><td>"+x.name+"</td><td><span class='score-badge "+c+"'>"+x.score+"</span></td><td>"+x.price.toFixed(2)+"</td><td>"+(x.change_pct>0?"+":"")+x.change_pct.toFixed(2)+"%</td><td>"+(x.amount/1e8).toFixed(1)+"亿</td><td>"+(dt["尾盘拉升"]||"--")+"</td><td>"+(dt["K线分"]||"--")+"</td></tr>";
});
document.getElementById("tableBody").innerHTML=h;
}
</script>
</body>
</html>'''

# ====== HTTP Server ======

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    
    def _send(self, body, ct="text/html; charset=utf-8", code=200):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send(INDEX_HTML)
        elif self.path.startswith("/api/scan"):
            try:
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                ms = int(q.get("min_score", [40])[0])
            except:
                ms = 40
            
            t0 = time.time()
            try:
                results = screen(min_score=ms)
                today = datetime.now().strftime("%Y-%m-%d")
                data = {"date": today, "count": len(results), "elapsed": round(time.time()-t0,1), "results": results}
            except Exception as e:
                data = {"error": str(e)}
            self._send(json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")
        else:
            self._send("Not Found", code=404)

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--min-score", type=int, default=40)
    a = p.parse_args()
    
    if a.serve:
        port = int(os.environ.get("PORT", a.port))
        server = HTTPServer(("0.0.0.0", port), Handler)
        print(f"尾盘选股器 http://0.0.0.0:{port}")
        try: server.serve_forever()
        except KeyboardInterrupt: server.shutdown()
    else:
        t0 = time.time()
        print(f"\n{'='*55}\n  尾盘选股器 v2\n  策略: 尾盘放量突破 + 趋势 + 换手 + K线 + 突破\n{'='*55}\n")
        r = screen(min_score=a.min_score)
        if r:
            print(f"\n选出 {len(r)} 只:\n")
            print(f"{'#':<4} {'代码':<8} {'名称':<8} {'评分':<5} {'现价':<8} {'涨幅':<7} {'成交额':<10}")
            print("-"*58)
            for i,x in enumerate(r,1):
                print(f"{i:<4} {x['code']:<8} {x['name']:<8} {x['score']:<5} {x['price']:<8.2f} {x['change_pct']:>+5.2f}%  {x['amount']/1e8:>8.1f}亿")
        else:
            print("\n无结果")
        print(f"\n耗时: {time.time()-t0:.1f}s")
