# -*- coding: utf-8 -*-
"""尾盘选股器 v3 - AI策略增强版"""
import json, sys, os, time, re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import requests
    S = requests.Session()
    S.trust_env = False; S.proxies = {}; S.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://finance.sina.com.cn"})
except: print("pip install requests"); sys.exit(1)

def get_stocks():
    all_stocks = []
    for page in range(1, 60):
        url = f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=600&sort=symbol&asc=1&node=hs_a&symbol=&_s_r_a=auto"
        try:
            r = S.get(url, timeout=15); r.encoding = 'gb2312'
            for x in json.loads(r.text):
                c, n = x.get("code",""), x.get("name","")
                if c and n and "ST" not in n.upper(): all_stocks.append((c, n))
        except: break
    return all_stocks

def get_quotes(codes):
    if not codes: return {}
    syms = [("sh" if c.startswith("6") else "sz") + c for c in codes]
    res = {}
    for i in range(0, len(syms), 400):
        batch = syms[i:i+400]
        try:
            r = S.get(f"https://hq.sinajs.cn/list={','.join(batch)}", timeout=20)
            r.encoding = 'gb2312'
            for line in r.text.strip().split("\n"):
                if "=" not in line: continue
                m = re.search(r'hq_str_s[hz](\d+)="(.+)"', line)
                if not m: continue
                d = m.group(2).split(",")
                if len(d) >= 32:
                    res[m.group(1)] = {
                        "name": d[0], "open": f(d[1]), "yclose": f(d[2]),
                        "price": f(d[3]), "high": f(d[4]), "low": f(d[5]),
                        "vol": f(d[8]), "amt": f(d[9]), "date": d[30] if len(d)>30 else ""
                    }
        except: continue
    return res

def f(s):
    try: return float(s)
    except: return 0.0

def score(data):
    p, o, h, l, amt, yc = data["price"], data["open"], data["high"], data["low"], data["amt"], data["yclose"]
    if p <= 0 or yc <= 0: return 0, {}
    chg = (p - yc) / yc * 100
    rng = h - l
    if chg < 0 or chg >= 9.5 or p < 3: return 0, {}
    
    sc, dt = 0, {"涨幅%": round(chg,2)}
    amt_yi = amt / 1e8
    
    # === 六维度评分 ===
    # 1. 尾盘强度 (0-25): 收盘位置+涨幅
    rp = (p - l) / rng if rng > 0 else 0.5
    s1 = min(25, rp * 20 + (min(chg/6*5, 5) if chg <= 6 else 0))
    sc += s1; dt["尾盘分"] = round(s1,1); dt["收盘位"] = f"{rp*100:.0f}%"
    
    # 2. 量能 (0-15): 成交额
    s2 = 15 if amt_yi>10 else 12 if amt_yi>5 else 9 if amt_yi>2 else 6 if amt_yi>1 else 3 if amt_yi>0.5 else 1
    sc += s2; dt["量能分"] = round(s2,1); dt["成交额"] = f"{amt_yi:.1f}亿"
    
    # 3. 趋势 (0-15): 涨幅2-6%最优
    s3 = 15 if 2<=chg<=6 else (8+(chg-0.5)/1.5*7 if 0.5<=chg<2 else max(5,15-(chg-6)*1.5) if chg>6 else chg/0.5*8)
    s3 = max(0, min(15, s3)); sc += s3; dt["趋势分"] = round(s3,1)
    
    # 4. 活跃度 (0-15): 成交额代理换手
    s4 = 15 if 5<amt_yi<=15 else 12 if 15<amt_yi<=30 else 10 if 2<=amt_yi<=5 else 8 if amt_yi>30 else 5
    sc += s4; dt["活跃分"] = round(s4,1)
    
    # 5. K线形态 (0-15)
    if rng > 0:
        body = abs(p-o); br = body/rng
        s5 = max(2,min(15, 8+br*5+(5 if p>o and (h-p)/rng<0.1 and br>0.5 else 0))) if p>o else max(1,min(15,br*3))
    else: s5 = 8
    sc += s5; dt["K线分"] = round(s5,1)
    
    # 6. 突破力度 (0-15)
    s6 = 15 if chg>=5 else 12 if chg>=3 else 9 if chg>=2 else 6 if chg>=1 else 3
    sc += s6; dt["突破分"] = round(s6,1)
    
    # === AI操作建议 ===
    advice = ""
    if sc >= 80:
        advice = "强烈推荐 - 多项指标共振，次日高开概率大，可集合竞价介入"
    elif sc >= 65:
        advice = "推荐关注 - 尾盘信号明确，建议次日开盘观察5分钟后决策"
    elif sc >= 50:
        advice = "可关注 - 信号一般，可加入自选观察，等回调5日线入场"
    else:
        advice = "观望 - 等更明确信号"
    
    # 风控提示
    risk = ""
    if chg > 7: risk += "涨幅已大注意追高风险; "
    if amt_yi < 1: risk += "成交清淡注意流动性; "
    if rng > 0 and (h-p)/rng > 0.3: risk += "上影线较长有抛压; "
    if not risk: risk = "形态健康"
    
    dt["建议"] = advice
    dt["风控"] = risk
    
    return min(100, round(sc,1)), dt

def screen(ms=40, topn=50):
    stocks = get_stocks()
    quotes = get_quotes([c for c,_ in stocks])
    results = []
    for code, name in stocks:
        if code not in quotes: continue
        d = quotes[code]
        sc, det = score(d)
        if sc >= ms:
            results.append({
                "code":code,"name":name,"score":sc,"price":d["price"],
                "change_pct":det.get("涨幅%",0),"amount":d["amt"],
                "trade_date":d.get("date",""),
                "advice":det.get("建议",""),"risk":det.get("风控",""),
                "details":det
            })
    results.sort(key=lambda x:x["score"], reverse=True)
    return results[:topn]

HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>尾盘选股器 v3</title>
<style>
:root{--bg:#0a0e17;--card:#111827;--border:#1e293b;--text:#e2e8f0;--muted:#64748b;--accent:#3b82f6;--green:#22c55e;--red:#ef4444;--orange:#f59e0b}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:32px 16px;display:flex;justify-content:center}
.container{max-width:960px;width:100%}
.header{text-align:center;margin-bottom:24px}
.header h1{font-size:26px;font-weight:700;letter-spacing:-.5px}
.header p{color:var(--muted);font-size:13px;margin-top:4px}
.controls{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px 24px;margin-bottom:16px;display:flex;gap:12px;align-items:end;flex-wrap:wrap}
.field{display:flex;flex-direction:column;gap:4px}
.field label{font-size:11px;color:var(--muted);font-weight:500}
.field select{background:#0a0e17;border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:8px;font-size:13px;font-family:inherit;outline:none;width:120px}
.btn{background:var(--accent);color:#fff;border:none;padding:8px 20px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn:hover{opacity:.85}.btn:disabled{opacity:.5;cursor:not-allowed}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
.badge-buy{background:#1a3a1a;color:var(--green)}
.badge-watch{background:#2a2a1a;color:var(--orange)}
.badge-hold{background:#1a1a2a;color:var(--muted)}
.status{text-align:center;padding:12px;color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:16px}
.card-header{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;font-size:13px}
.card-header span{color:var(--muted)}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:8px 12px;font-size:11px;color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:10px 12px;font-size:13px;border-bottom:1px solid var(--border);font-variant-numeric:tabular-nums}
tr:hover{background:#1a2030}
.score{font-weight:700;font-size:14px}
.score-gold{color:#f0c000}.score-green{color:var(--green)}.score-blue{color:var(--accent)}.score-gray{color:var(--muted)}
.advice-cell{font-size:12px;max-width:200px;line-height:1.4}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="container">
<div class="header"><h1>尾盘选股器 v3</h1><p>六维度AI策略评分 + 操作建议 | 数据源: 新浪财经</p></div>
<div class="controls">
<div class="field"><label>评分门槛</label><select id="ms"><option value="30">30-宽松</option><option value="40" selected>40-适中</option><option value="50">50-严格</option><option value="60">60-精选</option></select></div>
<button class="btn" id="btn" onclick="go()">开始扫描</button>
<span style="font-size:12px;color:var(--muted)" id="dl"></span>
</div>
<div class="status" id="status">点击扫描获取今日尾盘选股推荐</div>
<div class="card" id="card" style="display:none">
<div class="card-header"><b id="rd">结果</b><span id="rc"></span></div>
<div style="overflow-x:auto"><table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>综合评分</th><th>现价</th><th>涨幅</th><th>成交额</th><th>尾盘</th><th>趋势</th><th>量能</th><th>K线</th><th>操作建议</th></tr></thead><tbody id="tb"></tbody></table></div>
</div>
</div>
<script>
async function go(){
var ms=document.getElementById("ms").value,b=document.getElementById("btn"),s=document.getElementById("status"),c=document.getElementById("card");
b.disabled=true;b.textContent="扫描中...";s.innerHTML='<span class="spinner"></span>扫描全市场A股...';c.style.display="none";
try{
var r=await fetch("/api/scan?min_score="+ms),d=await r.json();
if(d.error){s.textContent=d.error}else if(d.count===0){s.innerHTML="无结果,降低门槛试试"}else{s.textContent="";render(d)}
}catch(e){s.textContent="连接失败"}
b.disabled=false;b.textContent="开始扫描";
}
function render(d){
document.getElementById("card").style.display="block";
document.getElementById("rd").textContent="扫描结果 - "+(d.results[0]?d.results[0].trade_date||d.date:d.date);
document.getElementById("rc").textContent=d.count+"只 | "+d.elapsed+"s";
document.getElementById("dl").textContent="交易日期: "+(d.results[0]?d.results[0].trade_date||d.date:d.date);
var h="";
d.results.forEach(function(x,i){
var dt=x.details||{},sc=x.score;
var scCls=sc>=70?"score-gold":sc>=55?"score-green":sc>=40?"score-blue":"score-gray";
var advCls=sc>=70?"badge-buy":sc>=55?"badge-watch":"badge-hold";
var advLabel=sc>=70?"买入":sc>=55?"关注":"观望";
h+="<tr><td>"+(i+1)+"</td><td style='font-weight:600'>"+x.code+"</td><td>"+x.name+"</td>";
h+="<td><span class='score "+scCls+"'>"+x.score+"</span></td>";
h+="<td>"+x.price.toFixed(2)+"</td><td>"+(x.change_pct>0?"+":"")+x.change_pct.toFixed(2)+"%</td>";
h+="<td>"+(x.amount/1e8).toFixed(1)+"亿</td>";
h+="<td>"+(dt["尾盘分"]||"--")+"</td><td>"+(dt["趋势分"]||"--")+"</td><td>"+(dt["量能分"]||"--")+"</td><td>"+(dt["K线分"]||"--")+"</td>";
h+="<td class='advice-cell'><span class='badge "+advCls+"'>"+advLabel+"</span><br><span style='color:var(--muted)'>"+(dt["建议"]||"")+"</span></td></tr>";
});
document.getElementById("tb").innerHTML=h;
}
</script>
</body>
</html>'''

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _s(self,b,ct="text/html; charset=utf-8",code=200):
        b = b.encode("utf-8") if isinstance(b,str) else b
        self.send_response(code); self.send_header("Content-Type",ct)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path in ("/","/index.html"): self._s(HTML)
        elif self.path.startswith("/api/scan"):
            from urllib.parse import urlparse,parse_qs
            ms = int(parse_qs(urlparse(self.path).query).get("min_score",[40])[0])
            t0=time.time()
            try:
                r=screen(ms=ms); td=datetime.now().strftime("%Y-%m-%d")
                data={"date":td,"count":len(r),"elapsed":round(time.time()-t0,1),"results":r}
            except Exception as e: data={"error":str(e)}
            self._s(json.dumps(data,ensure_ascii=False),"application/json; charset=utf-8")
        else: self._s("404",code=404)

if __name__=="__main__":
    import argparse; os.environ["HTTP_PROXY"]=""; os.environ["HTTPS_PROXY"]=""
    p=argparse.ArgumentParser()
    p.add_argument("--serve",action="store_true"); p.add_argument("--port",type=int,default=5000)
    p.add_argument("--min-score",type=int,default=40); a=p.parse_args()
    if a.serve:
        port=int(os.environ.get("PORT",a.port))
        HTTPServer(("0.0.0.0",port),H).serve_forever()
    else:
        t0=time.time()
        print(f"\n{'='*50}\n  尾盘选股器 v3\n{'='*50}\n")
        r=screen(ms=a.min_score)
        for i,x in enumerate(r,1):
            print(f"{i}. {x['code']} {x['name']} {x['score']}分 | {x['advice']}")
        print(f"\n耗时{time.time()-t0:.1f}s")
