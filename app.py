# -*- coding: utf-8 -*-
import json,sys,os,time,re,threading,math,gzip,io
from datetime import datetime,timedelta
from http.server import HTTPServer,BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from concurrent.futures import ThreadPoolExecutor,as_completed
from urllib.parse import urlparse,parse_qs
try:
    import requests
except:
    print("pip install requests"); sys.exit(1)

def new_session():
    s=requests.Session()
    s.trust_env=False; s.proxies={}
    s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://finance.sina.com.cn"})
    return s

S=new_session()
CACHE={}; STOCK_CACHE_FILE=os.path.join(os.path.dirname(os.path.abspath(__file__)),"stocks_cache.json")
KLINES_CACHE_FILE=os.path.join(os.path.dirname(os.path.abspath(__file__)),"klines_cache.json")
KLINES_DAILY_CACHE=os.path.join(os.path.dirname(os.path.abspath(__file__)),"klines_daily.json")
PROGRESS={"total":0,"done":0,"msg":"","results":None,"status":"idle"}

# ---- Async Scan Infrastructure ----
import threading
SCAN_LOCK = threading.Lock()

def _scan_async(ms=35, topn=50, date_str=None):
    """Run scan in background, update PROGRESS with results."""
    global PROGRESS
    try:
        PROGRESS["status"] = "running"
        PROGRESS["results"] = None
        PROGRESS["msg"] = "Starting..."
        results = screen(ms=ms, topn=topn, date_str=date_str)
        PROGRESS["results"] = results
        PROGRESS["status"] = "done"
        PROGRESS["msg"] = "Completed: {} stocks found".format(len(results))
        return results
    except Exception as e:
        import traceback
        PROGRESS["status"] = "error"
        PROGRESS["msg"] = str(e)
        PROGRESS["error"] = traceback.format_exc()
        return []

def start_scan_async(ms=35, topn=50, date_str=None):
    """Start async scan if not already running."""
    if PROGRESS.get("status") == "running":
        return False
    PROGRESS["results"] = None
    PROGRESS["status"] = "running"
    PROGRESS["msg"] = "Starting scan..."
    PROGRESS["total"] = 0
    PROGRESS["done"] = 0
    t = threading.Thread(target=_scan_async, args=(ms, topn, date_str), daemon=True)
    t.start()
    return True

# ---- Email ----
def send_email(subject, body):
    """Send email via SMTP. Configure via env vars."""
    import smtplib
    from email.mime.text import MIMEText
    smtp_host = os.environ.get("SMTP_HOST", "smtp.qq.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "1101600259@qq.com")
    smtp_pass = os.environ.get("SMTP_PASS", "wbkxpxkyefsxjfdi")
    smtp_to = os.environ.get("SMTP_TO", "1101600259@qq.com")
    if not all([smtp_host, smtp_user, smtp_pass, smtp_to]):
        print("[Email] Not configured, skip")
        return False
    try:
        msg = MIMEText(body, "html", "utf-8")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = smtp_to
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [smtp_to], msg.as_string())
        print("[Email] Sent: {}".format(subject))
        return True
    except Exception as e:
        print("[Email] Failed: {}".format(e))
        return False

# ---- Scheduled 14:45 Auto-Scan ----
def is_trading_day():
    """Check if today is a trading day (Mon-Fri, not holiday)."""
    import datetime
    now = datetime.datetime.now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return True

def scheduled_scan_loop():
    """Background thread: check time, auto-scan at 14:45 daily."""
    import datetime, time as _time
    last_scan_date = None
    while True:
        try:
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            hm = now.hour * 100 + now.minute
            
            # Check if 14:45-14:35 and not scanned today
            if 1445 <= hm < 1450 and is_trading_day() and last_scan_date != today_str:
                print("[Scheduler] 14:45 auto-scan triggered")
                results = _scan_async(ms=30, topn=20, date_str=None)
                last_scan_date = today_str
                
                if results:
                    # Build email
                    rows = ""
                    for i, r in enumerate(results[:10]):
                        chg_str = "{:+.2f}%".format(r.get("change_pct", 0))
                        rows += "<tr><td>{}</td><td>{} {}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                            i+1, r["code"], r["name"], r["score"], chg_str, r.get("advice", ""))
                    body = """<h2>{} ??????</h2>
                    <p>???????14:45 | ???? {} ????</p>
                    <table border='1' cellpadding='6' style='border-collapse:collapse'>
                    <tr><th>#</th><th>??</th><th>??</th><th>??</th><th>??</th></tr>
                    {}
                    </table>
                    <p><small>-- ??????????</small></p>""".format(today_str, len(results), rows)
                    send_email("?????? - {}".format(today_str), body)
            
            _time.sleep(60)  # Check every 60 seconds
        except Exception as e:
            print("[Scheduler] Error: {}".format(e))
            _time.sleep(60)

# Start scheduler thread
_scheduler_thread = threading.Thread(target=scheduled_scan_loop, daemon=True)
_scheduler_thread.start()


def f(s):
    try: return float(s)
    except: return 0.0

def get_stocks():
    """并发获取A股全市场股票列表(剔除ST/退市/创业板/科创板)"""
    if "stocks" in CACHE: return CACHE["stocks"]
    
    # Try disk cache first
    try:
        with open(STOCK_CACHE_FILE,"r",encoding="utf-8") as fc:
            cached=json.load(fc)
            if cached and len(cached)>1000:
                CACHE["stocks"]=cached
                return cached
    except: pass
    
    def fetch_page(p):
        url=(f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             f"Market_Center.getHQNodeData?page={p}&num=100&sort=symbol&asc=1"
             f"&node=hs_a&symbol=&_s_r_a=auto")
        try:
            r=S.get(url,timeout=10); r.encoding="gb2312"
            data=json.loads(r.text)
            if not data: return []
            result=[]
            for x in data:
                c,n=x.get("code",""),x.get("name","")
                if c and n and all(k not in n.upper() for k in ["ST","\u9000"]) and not c.startswith(("300","301","688","689")):
                    result.append((c,n))
            return result
        except: return None
    
    all_stocks=[]
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs={ex.submit(fetch_page,p):p for p in range(1,80)}
        for f in as_completed(futs):
            data=f.result()
            if data is None: continue
            if not data: continue
            all_stocks.extend(data)
    all_stocks.sort(key=lambda x:x[0])
    CACHE["stocks"]=all_stocks
    try:
        with open(STOCK_CACHE_FILE,"w",encoding="utf-8") as fc:
            json.dump(all_stocks,fc,ensure_ascii=False)
    except: pass
    return all_stocks
# Pre-cache klines for most active stocks (background, runs at startup)
_precache_done = False
_daily_cache_loaded = False
_daily_cache_data = {}

def _load_daily_cache():
    global _daily_cache_loaded, _daily_cache_data
    if _daily_cache_loaded: return
    try:
        with open(KLINES_DAILY_CACHE, "r", encoding="utf-8") as f:
            _daily_cache_data = json.load(f)
        _daily_cache_loaded = True
        print(f"[Cache] Loaded {len(_daily_cache_data)-1} cached klines from disk")
    except:
        _daily_cache_loaded = True

def _save_daily_cache():
    global _daily_cache_data
    _daily_cache_data["_date"] = datetime.now().strftime("%Y-%m-%d")
    with open(KLINES_DAILY_CACHE, "w", encoding="utf-8") as f:
        json.dump(_daily_cache_data, f, ensure_ascii=False)

def precache_all_klines():
    global _precache_done, _daily_cache_loaded, _daily_cache_data
    stocks = get_stocks()
    today_str = datetime.now().strftime("%Y-%m-%d")
    _load_daily_cache()
    if _daily_cache_data.get("_date") == today_str and len(_daily_cache_data) > 500:
        _precache_done = True
        print(f"[Precache] Reusing {len(_daily_cache_data)-1} cached stocks from today")
        return
    
    # Only precache the first 800 stocks (most liquid, most likely candidates)
    # The rest will be cached incrementally during scans
    codes = [c for c,_ in stocks[:800]]
    print(f"[Precache] Fetching klines for top {len(codes)} stocks (background)...")
    t0 = time.time()
    def worker(code):
        sym = ("sh" if code.startswith("6") else "sz")+code
        kls = fetch_kline_sina(sym, 60)
        return code, kls
    done = 0
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(worker, c): c for c in codes}
        for fut in as_completed(futs):
            code, kls = fut.result()
            if kls: _daily_cache_data[code] = kls
            done += 1
            if done % 200 == 0:
                print(f"[Precache] {done}/{len(codes)} ({done*100//len(codes)}%)")
    _save_daily_cache()
    _precache_done = True
    print(f"[Precache] Done! {done} stocks cached in {time.time()-t0:.1f}s")

def get_cached_klines(code):
    """Get klines from daily cache if available"""
    global _daily_cache_loaded, _daily_cache_data
    _load_daily_cache()
    return _daily_cache_data.get(code, [])

def _add_kline_to_cache(code, kls):
    """Add kline data to daily cache incrementally"""
    global _daily_cache_loaded, _daily_cache_data
    _load_daily_cache()
    if code and kls and isinstance(kls, list) and len(kls) > 0:
        _daily_cache_data[code] = kls
        return True
    return False

def get_quotes(codes):
    if not codes: return {}
    syms=[("sh" if c.startswith("6") else "sz")+c for c in codes]
    res={}
    for i in range(0,len(syms),400):
        batch=syms[i:i+400]
        try:
            r=S.get("https://hq.sinajs.cn/list=" + ",".join(batch),timeout=20)
            r.encoding="gb2312"
            for line in r.text.strip().split("\n"):
                if "=" not in line: continue
                m=re.search(r"hq_str_s[hz](\d+)=\"(.+)\"",line)
                if not m: continue
                d=m.group(2).split(",")
                if len(d)>=32:
                    code=m.group(1)
                    res[code]={
                        "name":d[0],"open":f(d[1]),"yclose":f(d[2]),
                        "price":f(d[3]),"high":f(d[4]),"low":f(d[5]),
                        "vol":f(d[8]),"amt":f(d[9]),
                        "date":d[30] if len(d)>30 else "",
                        "turnover":f(d[38])/100 if len(d)>38 else 0,
                        "mktcap":f(d[45]) if len(d)>45 else 0,
                        "high52":f(d[41]) if len(d)>41 else 0,
                        "low52":f(d[42]) if len(d)>42 else 0,
                    }
        except: continue
    return res

def fetch_kline_sina(symbol, datalen=30):
    try:
        r=S.get(f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={datalen}",timeout=10)
        r.encoding="gb2312"
        data=json.loads(r.text)
        if isinstance(data,list):
            return [{"day":d.get("day",""),"open":f(d.get("open",0)),"high":f(d.get("high",0)),"low":f(d.get("low",0)),"close":f(d.get("close",0)),"volume":f(d.get("volume",0))} for d in data]
        return []
    except: return []

def get_historical_data(codes, target_date, datalen=80):
    """获取历史K线数据（带磁盘缓存）"""
    results={}
    
    # Try disk cache
    cache_key = target_date + "_" + str(datalen)
    try:
        with open(KLINES_CACHE_FILE,"r",encoding="utf-8") as fc:
            all_cached=json.load(fc)
            cached=all_cached.get(cache_key)
            if cached and len(cached)>500:
                PROGRESS["total"]=len(codes); PROGRESS["done"]=len(codes)
                PROGRESS["msg"]=f"Loaded {len(cached)} from cache"
                return cached
    except: pass
    total=len(codes)
    PROGRESS["total"]=total; PROGRESS["done"]=0
    PROGRESS["msg"]=f"Getting {total} stocks..."
    def worker(code):
        sym=("sh" if code.startswith("6") else "sz")+code
        kls=fetch_kline_sina(sym, datalen)
        return code,kls
    with ThreadPoolExecutor(max_workers=15) as ex:
        futs={ex.submit(worker,c):c for c in codes}
        for i,fut in enumerate(as_completed(futs)):
            code,kls=fut.result()
            if kls: results[code]=kls
            PROGRESS["done"]=i+1
            if (i+1)%500==0:
                PROGRESS["msg"]=f"Got {i+1}/{total}..."
    PROGRESS["msg"]=f"Done {len(results)}/{total}"
    # Save to disk cache
    try:
        all_cached={}
        try:
            with open(KLINES_CACHE_FILE,"r",encoding="utf-8") as fc:
                all_cached=json.load(fc)
        except: pass
        all_cached[cache_key]=results
        with open(KLINES_CACHE_FILE,"w",encoding="utf-8") as fc:
            json.dump(all_cached,fc,ensure_ascii=False)
    except: pass
    return results

def extract_date_data(kline_data, target_date):
    if not kline_data: return None
    target=target_date.replace("-","")
    idx=None
    for i,d in enumerate(kline_data):
        if d["day"]==target_date or d["day"]==target:
            idx=i; break
    if idx is None:
        for i,d in enumerate(kline_data):
            if d["day"]>=target: idx=i; break
        if idx is None and kline_data: idx=len(kline_data)-1
    if idx is None: return None
    today=kline_data[idx]
    def calc_ma(period):
        start=max(0,idx-period+1)
        window=kline_data[start:idx+1]
        if len(window)<period: return None
        closes=[d["close"] for d in window]
        return sum(closes)/len(closes)
    def calc_ma_slope(period):
        if idx<period: return 0
        ma_today=calc_ma(period)
        if ma_today and idx>=period+1:
            start2=max(0,idx-period)
            window2=kline_data[start2:idx]
            if len(window2)>=period:
                closes2=[d["close"] for d in window2]
                ma_yest=sum(closes2)/len(closes2)
                if ma_yest and ma_yest>0: return (ma_today-ma_yest)/ma_yest*100
        return 0
    ma5=calc_ma(5); ma10=calc_ma(10); ma20=calc_ma(20); ma60=calc_ma(60) if idx>=60 else None
    prev_close=None
    if idx>0: prev_close=kline_data[idx-1]["close"]
    amp=(today["high"]-today["low"])/prev_close*100 if prev_close and prev_close>0 else 0
    rng=today["high"]-today["low"]
    if today["close"]>=today["open"]: body=today["close"]-today["open"]
    else: body=today["open"]-today["close"]
    body_ratio=body/rng if rng>0 else 0
    if today["close"]>=today["open"]: upper_shadow=(today["high"]-today["close"])/rng if rng>0 else 0
    else: upper_shadow=(today["high"]-today["open"])/rng if rng>0 else 0
    close_position=(today["close"]-today["low"])/rng if rng>0 else 1
    recent_limit_up=False
    for j in range(max(0,idx-20),idx):
        if j>0:
            prev=kline_data[j-1]["close"]
            cur=kline_data[j]["close"]
            if prev>0 and (cur-prev)/prev>=0.095: recent_limit_up=True; break
    vol_trend=0
    if idx>=4:
        recent_vols=[kline_data[j]["volume"] for j in range(max(0,idx-4),idx+1)]
        if len(recent_vols)>=3:
            half=len(recent_vols)//2
            first_half=sum(recent_vols[:half])/half if half>0 else 0
            second_half=sum(recent_vols[half:])/(len(recent_vols)-half) if len(recent_vols)-half>0 else 0
            if first_half>0: vol_trend=(second_half-first_half)/first_half*100
    return {"_idx": idx, "open":today["open"],"close":today["close"],"high":today["high"],"low":today["low"],"volume":today["volume"],"prev_close":prev_close,"ma5":ma5,"ma10":ma10,"ma20":ma20,"ma60":ma60,"ma5_slope":calc_ma_slope(5),"ma10_slope":calc_ma_slope(10),"ma20_slope":calc_ma_slope(20),"amp":amp,"body_ratio":body_ratio,"upper_shadow":upper_shadow,"close_position":close_position,"recent_limit_up":recent_limit_up,"vol_trend":vol_trend,"date":today["day"]}

# ============ 7-Dimension Scoring ============
def score_v5(data):
    p=data.get("price") or data.get("close",0)
    o=data.get("open",0); h=data.get("high",0); l=data.get("low",0)
    yc=data.get("yclose") or data.get("prev_close",0)
    amt=data.get("amt",0); turnover=data.get("turnover",0)
    mktcap=data.get("mktcap",0)
    if p<=0 or (yc and yc<=0): return 0,{"filter":"数据异常"}
    chg=(p-yc)/yc*100 if yc>0 else 0
    rng=h-l if h and l else 0
    amp=(h-l)/yc*100 if yc>0 and h and l else 0
    if chg<0: return 0,{"filter":"下跌股","chg":round(chg,2)}
    if chg>=9.5: return 0,{"filter":"涨停板","chg":round(chg,2)}
    if p<3: return 0,{"filter":"低价股<3元"}
    sc=0; dt={"chg":round(chg,2),"amp":round(amp,2),"price":round(p,2)}

    # Dim1: Price form (25pts)
    if 3<=chg<=5: ps=25; dt["price_eval"]="温和拉升(3-5%)"
    elif 2<=chg<3: ps=18+(chg-2)*7; dt["price_eval"]="可接受(2-3%)"
    elif 5<chg<=7: ps=25-(chg-5)*6; dt["price_eval"]="追高风险(5-7%)"
    elif 0.5<=chg<2: ps=8+chg*5; dt["price_eval"]="偏弱(<2%)"
    else: ps=max(2,25-(chg-7)*5); dt["price_eval"]="涨幅过大"
    if amp<=3: ap=0; dt["amp_eval"]="稳定"
    elif amp<=5: ap=-2; dt["amp_eval"]="正常"
    elif amp<=7: ap=-5; dt["amp_eval"]="偏大"
    else: ap=-10; dt["amp_eval"]="过大"
    dt["price_score"]=round(ps+ap,1); sc+=ps+ap

    # Dim2: Volume (20pts)
    amt_yi=amt/1e8 if amt else 0
    if 5<=turnover<=10: ts=10; dt["to_eval"]="活跃适中(5-10%)"
    elif 3<=turnover<5: ts=3+turnover*1.4; dt["to_eval"]="偏冷(3-5%)"
    elif 2<=turnover<3: ts=turnover*2; dt["to_eval"]="冷门(2-3%)"
    elif 10<turnover<=15: ts=10-(turnover-10)*0.5; dt["to_eval"]="偏热(10-15%)"
    elif turnover>15: ts=max(2,7-(turnover-15)*0.3); dt["to_eval"]="过热(>15%)"
    else: ts=max(1,turnover*1.5); dt["to_eval"]="极冷(<2%)"
    ts=max(1,min(10,ts))
    if amt_yi>10: ats=10; dt["amt_eval"]="大资金关注"
    elif amt_yi>5: ats=8; dt["amt_eval"]="活跃"
    elif amt_yi>2: ats=6; dt["amt_eval"]="一般"
    elif amt_yi>1: ats=4; dt["amt_eval"]="偏弱"
    else: ats=2; dt["amt_eval"]="流动性差"
    vt=data.get("vol_trend",0) or 0
    if vt>20: vb=3; dt["vol_trend_eval"]="阶梯放量"
    elif vt>10: vb=2; dt["vol_trend_eval"]="温和放量"
    elif vt>0: vb=1; dt["vol_trend_eval"]="微放"
    else: vb=0; dt["vol_trend_eval"]="持平/缩量"
    dt["vol_score"]=round(ts+ats+vb,1); sc+=ts+ats+vb

    # Dim3: Trend MAs (20pts)
    ts2=0; ma5=data.get("ma5"); ma10=data.get("ma10"); ma20=data.get("ma20")
    if ma5 and ma10 and ma20 and all(v>0 for v in [ma5,ma10,ma20]):
        if ma5>ma10>ma20: ts2+=8; dt["ma_arrange"]="多头排列"
        elif ma5>ma10: ts2+=4; dt["ma_arrange"]="偏多"
        elif ma5>ma20: ts2+=2; dt["ma_arrange"]="中性"
        else: dt["ma_arrange"]="空头排列"
    else: dt["ma_arrange"]="数据不足"
    if p>ma5 if ma5 else False: ts2+=5; dt["above_ma5"]="是"
    elif ma10 and p>ma10: ts2+=3; dt["above_ma5"]="站上10日线"
    else: dt["above_ma5"]="否"
    ms5=data.get("ma5_slope",0) or 0
    ms10=data.get("ma10_slope",0) or 0
    ms20=data.get("ma20_slope",0) or 0
    if ms5>0: ts2+=2
    if ms10>0: ts2+=1
    if ms20>0: ts2+=1
    dirs=[]
    dirs.append("MA5"+("UP" if ms5>0 else "DN"))
    dirs.append("MA10"+("UP" if ms10>0 else "DN"))
    dirs.append("MA20"+("UP" if ms20>0 else "DN"))
    dt["ma_dir"]=",".join(dirs)
    if ma5 and ma20 and ma20>0:
        spread=(ma5-ma20)/ma20*100
        if 2<=spread<=8: ts2+=2; dt["ma_spread"]="健康发散"
        elif spread>8: dt["ma_spread"]="过度发散"
        else: dt["ma_spread"]="均线粘合"
    dt["trend_score"]=round(ts2,1); sc+=ts2

    # Dim4: K-line form (15pts)
    ks=0; br=data.get("body_ratio",0); us=data.get("upper_shadow",0)
    cp=data.get("close_position",1)
    if br>=0.6: ks+=6; dt["body_eval"]="实体饱满"
    elif br>=0.4: ks+=4; dt["body_eval"]="实体适中"
    elif br>=0.2: ks+=2; dt["body_eval"]="实体偏小"
    else: dt["body_eval"]="十字星"
    if us<=0.1: ks+=5; dt["shadow_eval"]="极短(强势)"
    elif us<=0.2: ks+=3; dt["shadow_eval"]="较短"
    elif us<=0.3: ks+=1; dt["shadow_eval"]="一般"
    else: ks-=2; dt["shadow_eval"]="过长!警惕"
    if cp>=0.95: ks+=4; dt["close_eval"]="光头(强势)"
    elif cp>=0.85: ks+=3; dt["close_eval"]="高位"
    elif cp>=0.7: ks+=1; dt["close_eval"]="中位"
    else: ks-=1; dt["close_eval"]="低位"
    dt["kline_score"]=round(ks,1); sc+=ks

    # Dim5: Market cap (10pts)
    mcy=mktcap/1e8 if mktcap else 0
    if 50<=mcy<=200: mks=10; dt["mkt_eval"]="黄金区间(50-200亿)"
    elif 30<=mcy<50: mks=7; dt["mkt_eval"]="偏小(30-50亿)"
    elif 200<mcy<=300: mks=8; dt["mkt_eval"]="偏大(200-300亿)"
    elif 20<=mcy<30: mks=4; dt["mkt_eval"]="小盘(20-30亿)"
    elif mcy>300: mks=5; dt["mkt_eval"]="大盘蓝筹"
    else: mks=2; dt["mkt_eval"]="微型盘"
    dt["mkt_cap_yi"]=f"{mcy:.0f}" if mcy>0 else "--"
    dt["mkt_score"]=mks; sc+=mks

    # Dim6: Technical bonus (10pts)
    tbs=0
    if data.get("recent_limit_up"): tbs+=5; dt["limit_gene"]="有(股性活跃)"
    else: dt["limit_gene"]="无"
    h52=data.get("high52",0); l52=data.get("low52",0)
    if h52>0 and l52>0:
        pos52=(p-l52)/(h52-l52)*100 if h52!=l52 else 50
        dt["pos_52w"]=f"{pos52:.0f}%"
        if pos52<=30: tbs+=3; dt["low_start"]="是(低位)"
        elif pos52<=50: tbs+=1; dt["low_start"]="中位"
        else: dt["low_start"]="高位"
    else: dt["pos_52w"]="--"
    if chg>2 and (data.get("vol_trend",0) or 0)>10: tbs+=2; dt["vol_price"]="放量上涨"
    else: dt["vol_price"]="一般"
    dt["tech_score"]=tbs; sc+=tbs

    # Dim7: Risk filters (veto)
    risks=[]
    if us>0.35: risks.append("长上影出货")
    if chg>7: risks.append("追高风险")
    if turnover>20: risks.append("换手率过高")
    if chg>3 and amt_yi<0.5: risks.append("无量空涨")
    if h52>0 and l52>0:
        pos=(p-l52)/(h52-l52)*100
        if pos>80 and (data.get("vol_trend",0) or 0)>15: risks.append("高位放量")
    if p<5 and turnover<1: risks.append("冷门低价")
    if amp>8: risks.append("振幅过大")
    if risks:
        dt["risk"]="|".join(risks)
        severe=["Long shadow","No volume","高位放量"]
        if any(any(s in r for s in severe) for r in risks):
            return 0,{"filter":risks[0],"chg":round(chg,2)}
    else: dt["risk"]="无明显风险"

    # Total & Advice
    total=min(100,max(0,round(sc,1)))
    dt["total"]=total
    if total>=80: dt["advice"]="强烈推荐"; dt["advice_detail"]="形态完美，多项指标共振，次日高开概率大。建议开盘确认后择机入场，止损设-3%。"; dt["grade"]="A"
    elif total>=70: dt["advice"]="推荐关注"; dt["advice_detail"]="信号明确，综合表现优秀。建议加入自选，次日开盘观察确认后适量介入。"; dt["grade"]="B"
    elif total>=60: dt["advice"]="适当关注"; dt["advice_detail"]="条件基本符合，存在部分瑕疵。可轻仓试探，严格设置止损。"; dt["grade"]="C"
    elif total>=50: dt["advice"]="一般关注"; dt["advice_detail"]="信号偏弱，多个维度不理想。建议观望不入场，等待更好时机。"; dt["grade"]="D"
    elif total>=40: dt["advice"]="观望"; dt["advice_detail"]="多项指标不达标，不具备操作价值，不推荐介入。"; dt["grade"]="E"
    else: dt["advice"]="回避"; dt["advice_detail"]="综合评分过低，风险大于机会，坚决回避。"; dt["grade"]="F"
    return total,dt


# ---- V8i PROFESSIONAL LIVE SCORING ----
def calc_adx_live(highs, lows, closes, period=14):
    n = len(closes)
    if n < period + 1: return 0, 0, 0
    tr_list = []; plus_dm = []; minus_dm = []
    for i in range(1, n):
        h, l, c = highs[i], lows[i], closes[i]
        ph, pl = highs[i-1], lows[i-1]
        tr = max(h-l, abs(h-ph), abs(l-pl))
        tr_list.append(tr)
        updm = h - ph if (h - ph) > (pl - l) and (h - ph) > 0 else 0
        dndm = pl - l if (pl - l) > (h - ph) and (pl - l) > 0 else 0
        plus_dm.append(updm); minus_dm.append(dndm)
    atr = sum(tr_list[:period]) / period
    atr_pdm = sum(plus_dm[:period]) / period
    atr_ndm = sum(minus_dm[:period]) / period
    alpha = 1.0 / period
    for i in range(period, len(tr_list)):
        atr = atr * (1-alpha) + tr_list[i] * alpha
        atr_pdm = atr_pdm * (1-alpha) + plus_dm[i] * alpha
        atr_ndm = atr_ndm * (1-alpha) + minus_dm[i] * alpha
    pdi = 100 * atr_pdm / atr if atr > 0 else 0
    ndi = 100 * atr_ndm / atr if atr > 0 else 0
    dx_list = []
    atr2 = sum(tr_list[:period]) / period; atr_pdm2 = sum(plus_dm[:period]) / period; atr_ndm2 = sum(minus_dm[:period]) / period
    for i in range(period, len(tr_list)):
        atr2 = atr2 * (1-alpha) + tr_list[i] * alpha
        atr_pdm2 = atr_pdm2 * (1-alpha) + plus_dm[i] * alpha
        atr_ndm2 = atr_ndm2 * (1-alpha) + minus_dm[i] * alpha
        pdi2 = 100 * atr_pdm2 / atr2 if atr2 > 0 else 0
        ndi2 = 100 * atr_ndm2 / atr2 if atr2 > 0 else 0
        dx2 = 100 * abs(pdi2 - ndi2) / (pdi2 + ndi2) if (pdi2 + ndi2) > 0 else 0
        dx_list.append(dx2)
    if dx_list:
        adx_val = sum(dx_list[:period]) / period
        for i in range(period, len(dx_list)):
            adx_val = adx_val * (1-alpha) + dx_list[i] * alpha
        return round(adx_val, 1), round(pdi, 1), round(ndi, 1)
    return 0, round(pdi, 1), round(ndi, 1)

def score_v8i(quote, kls, market_idx_kls=None, ki=None):
    """Canonical V8i scoring - exact match with sim_v8i.py proven logic (+27.5%).
    quote: realtime quote dict OR None (uses kls data instead)
    kls: full kline data list
    market_idx_kls: index kline data for market state
    Returns: (score, details_dict, advice)"""
    
    vol_skip = False; vol_reduce = False; idx_vol5 = 0.0
    # Guard: empty klines
    if not kls or len(kls) == 0:
        return 0, {"filter": "No kline data"}, "??"
    # Extract data from kls (last entry is current day)
    if ki is None: ki = len(kls) - 1
    close = kls[ki]["close"]
    prev_close = kls[ki-1]["close"] if ki > 0 else close
    high = kls[ki]["high"]; low = kls[ki]["low"]
    
    chg = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0
    amp = (high - low) / prev_close * 100 if prev_close > 0 else 0
    cp = (close - low) / (high - low) if high > low else 1
    p = close; yc = prev_close; amt = kls[ki]["volume"] * close

    if p <= 0 or yc <= 0: return 0, {"filter": "Invalid data"}, "??"
    if vol_skip: return 0, {"filter": "????%.1f%%??" % idx_vol5}, "??"
    
    # ---- Detect market state EARLY (before chg filter, for oversold bounce) ----
    market_label = "SIDEWAYS"
    if market_idx_kls and len(market_idx_kls) >= 20:
        idx_closes = [k["close"] for k in market_idx_kls[-20:]]
        idx_ma20 = sum(idx_closes)/20
        idx_close = market_idx_kls[-1]["close"]
        idx_s5 = (idx_closes[-1] - idx_closes[-5])/idx_closes[-5]*100 if len(idx_closes) >= 5 else 0
        is_bull = idx_close >= idx_ma20
        is_strong = is_bull and idx_s5 > 0.3
        idx_ma60 = sum(k["close"] for k in market_idx_kls[-60:])/60 if len(market_idx_kls) >= 60 else idx_ma20
        is_side = not is_bull and idx_close >= idx_ma60 if len(market_idx_kls) >= 60 else True
        if is_strong: market_label = "BULLISH+"
        elif is_bull: market_label = "BULLISH"
        elif is_side: market_label = "SIDEWAYS"
        else: market_label = "WEAK+"
    # ---- Market volatility filter ----
    idx_vol5 = 0
    if market_idx_kls and len(market_idx_kls) >= 5:
        idx_amps = []
        for j in range(max(0, len(market_idx_kls)-5), len(market_idx_kls)):
            ik = market_idx_kls[j]
            if ik["high"] > 0 and ik["low"] > 0:
                idx_amps.append((ik["high"]-ik["low"])/ik["close"]*100)
        if idx_amps:
            idx_vol5 = sum(idx_amps)/len(idx_amps)
    # High volatility: reduce position or skip
    vol_skip = False
    vol_reduce = False
    if idx_vol5 > 5.0:
        vol_skip = True  # Extreme volatility, skip trading
    elif idx_vol5 > 3.0:
        vol_reduce = True  # Elevated volatility, reduce position
    # Check if index itself dropped significantly (oversold signal)
    idx_drop5 = (idx_closes[-1] - idx_closes[-5])/idx_closes[-5]*100 if len(idx_closes) >= 5 else 0
    is_oversold_market = (market_label in ("WEAK+","SIDEWAYS") and idx_drop5 < -1.0)
    
    # ---- Hard filters (sim_v8i.py exact) ----
    # Oversold bounce: allow chg -4% to +5.5% in weak markets
    if is_oversold_market:
        if chg < -4.0 or chg > 5.5: return 0, {"filter": "?%.1f%%??" % chg, "chg": round(chg,2)}, "??"
    else:
        if chg < 1.0 or chg > 5.5: return 0, {"filter": "?%.1f%%??" % chg, "chg": round(chg,2)}, "??"
    if is_oversold_market:
        if amp > 9.0: return 0, {"filter": "?%.1f%%??" % amp, "amp": round(amp,2)}, "??"
    else:
        if amp > 7.0: return 0, {"filter": "?%.1f%%??" % amp, "amp": round(amp,2)}, "??"
    if p < 4 or p > 80: return 0, {"filter": "??"}, "??"
    if amt < 30000000: return 0, {"filter": "??"}, "??"
    
    if not kls or len(kls) < 25:
        return 35, {"chg": round(chg,2), "mode": "simplified"}, "??"
    
    # Compute indicators from kline data (sim_v8i.py exact)
    closes_list = [k["close"] for k in kls[max(0,ki-25):ki+1]]
    highs_list = [k["high"] for k in kls[max(0,ki-25):ki+1]]
    vols_list = [k["volume"] for k in kls[max(0,ki-25):ki+1]]
    
    # Compute MAs
    ma5 = sum(closes_list[-5:]) / 5 if len(closes_list) >= 5 else p
    ma10 = sum(closes_list[-10:]) / 10 if len(closes_list) >= 10 else p
    ma20 = sum(closes_list[-20:]) / 20 if len(closes_list) >= 20 else p
    
    # V8i shape filters
    body_ratio = abs(close - kls[ki]["open"]) / (high - low) if high > low else 0
    upper_shadow = (high - max(close, kls[ki]["open"])) / (high - low) if high > low else 0
    lower_shadow = (min(close, kls[ki]["open"]) - low) / (high - low) if high > low else 0
    if is_oversold_market:
        # Oversold: very lenient, just need some reversal signal
        pass  # Let all oversold stocks through the K-line check
    else:
        if cp < 0.55 or body_ratio < 0.25 or upper_shadow > 0.45:
            return 0, {"filter": "K?????"}, "??"
    if close < ma5 and not is_oversold_market:
        return 0, {"filter": "??MA5"}, "??"
    
    # Volume trend (match extract_date_data formula exactly)
    vt = 0
    if ki >= 4:
        recent_vols = [kls[j]["volume"] for j in range(max(0, ki-4), ki+1)]
        if len(recent_vols) >= 3:
            half = len(recent_vols) // 2
            first_half = sum(recent_vols[:half]) / half if half > 0 else 0
            second_half = sum(recent_vols[half:]) / (len(recent_vols) - half) if len(recent_vols) - half > 0 else 0
            if first_half > 0: vt = (second_half - first_half) / first_half * 100
    if vt < -5 and not is_oversold_market: return 0, {"filter": "??"}, "??"
    
    # RSI(6)
    gains = []; losses = []
    for i in range(1, min(7, len(closes_list))):
        diff = closes_list[-i] - closes_list[-i-1]
        if diff >= 0: gains.append(diff)
        else: losses.append(abs(diff))
    avg_gain = sum(gains)/6 if gains else 0.0001
    avg_loss = sum(losses)/6 if losses else 0.0001
    rsi6 = 100 - (100 / (1 + avg_gain/avg_loss)) if avg_loss > 0 else 100
    
    # Volume ratio
    avg_vol5 = sum(vols_list[-6:-1]) / 5 if len(vols_list) >= 6 else vols_list[-1]
    vol_ratio = vols_list[-1] / avg_vol5 if avg_vol5 > 0 else 1
    
    # Green days
    green_days = 0
    for j in range(ki, max(-1, ki-3), -1):
        if j >= 0 and kls[j]["close"] >= kls[j]["open"]:
            green_days += 1
        else: break
    if ki > 0 and kls[ki]["close"] < kls[ki]["open"]:
        green_days = 0
    
    # Distance from 20d high
    high20 = max(highs_list[-20:]) if len(highs_list) >= 20 else max(highs_list)
    dist_high = (close - high20) / high20 * 100 if high20 > 0 else 0
    
    amt_val = vols_list[-1] * close / 100  # sim_v8i.py exact formula
    amt_yi = amt_val / 1e8
    
    # ---- IMPROVED FILTERS (sim_v8i.py exact) ----
    if is_oversold_market:
        # Oversold: relaxed filters - stocks that fell need lower bars
        if rsi6 < 20 or rsi6 > 72: return 0, {"filter": "RSI%d??" % int(rsi6)}, "??"
        if vol_ratio < 0.3 or vol_ratio > 5.0: return 0, {"filter": "??%.1f??" % vol_ratio}, "??"
        if rsi6 < 30 and dist_high > -15 and green_days > 0: return 0, {"filter": "????/??"}, "??"
        if dist_high < -30: return 0, {"filter": "???%.1f%%??" % dist_high}, "??"
        if amt_val < 20000000: return 0, {"filter": "???%.0f???" % (amt_val/1e4)}, "??"
    else:
        if rsi6 < 45 or rsi6 > 72: return 0, {"filter": "RSI%d??" % int(rsi6)}, "??"
        if vol_ratio < 1.0 or vol_ratio > 4.0: return 0, {"filter": "??%.1f??" % vol_ratio}, "??"
        if green_days < 1: return 0, {"filter": "???"}, "??"
        if dist_high < -8: return 0, {"filter": "???%.1f%%??" % dist_high}, "??"
        if amt_val < 50000000: return 0, {"filter": "???%.0f???" % (amt_val/1e4)}, "??"
    
    # ---- TREND BONUS ----
    tbonus, tdetail, tconf = trend_bonus_v8(kls, ki)
    
    # ---- Market state (already detected above) ----
    
    # ---- V8 SCORING (sim_v8i.py exact weights) ----
    score = 0
    
    # Oversold bounce scoring: bonus for reversal signals
    if is_oversold_market and chg < 0:
        score += 20  # Base score for oversold candidates
        if lower_shadow > 0.35: score += 10  # Long lower shadow = strong support
        if cp > 0.5: score += 6
        if chg > -2: score += 5  # Mild drop, not panic
        elif chg > -4: score += 3
        if vol_ratio > 1.2: score += 5  # Volume = capitulation
        if close > ma5: score += 4  # Above MA5 is good
        # Bonus for oversold RSI
        if 25 <= rsi6 <= 40: score += 6  # Oversold sweet spot
    
    # RSI sweet spot
    if is_oversold_market and chg < 0:
        if 25 <= rsi6 <= 45: score += 6  # Oversold RSI is a feature
        elif 45 <= rsi6 <= 55: score += 4
        elif rsi6 < 25: score += 3  # Extremely oversold
    else:
        if 55 <= rsi6 <= 65: score += 8
        elif 50 <= rsi6 <= 70: score += 5
        else: score += 2
    
    # Volume ratio
    if is_oversold_market and chg < 0:
        if 0.5 <= vol_ratio <= 1.5: score += 5  # Stabilization volume
        elif vol_ratio > 1.5: score += 4  # Elevated = attention
        elif vol_ratio < 0.5: score += 2  # Very low volume
    else:
        if 1.3 <= vol_ratio <= 2.5: score += 7
        elif 1.0 <= vol_ratio <= 3.0: score += 4
        else: score += 1
    
    # Dist from high
    if is_oversold_market and chg < 0:
        if dist_high >= -5: score += 4
        elif dist_high >= -15: score += 3
        elif dist_high >= -25: score += 1  # Deep drop = potential bounce
    else:
        if dist_high >= -1: score += 5
        elif dist_high >= -3: score += 3
        elif dist_high >= -5: score += 1
    
    # Above MA20
    if close > ma20: score += 4
    elif is_oversold_market and close > ma20 * 0.9: score += 2  # Near MA20
    
    # Change quality
    if is_oversold_market and chg < 0:
        if chg > -1: score += 5  # Nearly flat = stabilization
        elif chg > -3: score += 3
    else:
        if 2.5 <= chg <= 4.5: score += 5
        elif 1.5 <= chg <= 5: score += 3
    
    if ma5 > ma10 > ma20: score += 4
    elif ma5 > ma10: score += 2
    
    score += min(green_days, 3)
    
    if amp <= 3: score += 3
    elif amp <= 4: score += 1
    
    if amt_yi >= 5: score += 3
    elif amt_yi >= 1: score += 1
    
    if vt > 10 and green_days >= 2: score += 3
    
    score += tbonus
    
    # Momentum
    mom5 = 0
    if ki >= 5:
        mom5 = (close - kls[ki-4]["close"]) / kls[ki-4]["close"] * 100
        if mom5 > 8: score += 5
        elif mom5 > 4: score += 3
        elif mom5 > 0: score += 1
    
    # Vol+price rising
    if ki >= 2:
        vol_rising = kls[ki]["volume"] > kls[ki-1]["volume"] and kls[ki-1]["volume"] > kls[ki-2]["volume"]
        price_rising = kls[ki]["close"] > kls[ki-1]["close"] and kls[ki-1]["close"] > kls[ki-2]["close"]
        if vol_rising and price_rising: score += 3
        elif price_rising: score += 1
    
    # ---- ADVICE (with oversold bounce) ----
    advice = "HOLD"
    if is_oversold_market and chg < 0 and tconf >= -1 and score >= 20:
        advice = "超跌反弹"
    elif tconf >= 2 and mom5 > 4 and market_label in ("BULLISH+", "BULLISH"):
        advice = "强烈买入"
    elif tconf >= 1 and mom5 > 1 and market_label in ("BULLISH+", "BULLISH", "SIDEWAYS"):
        advice = "买入"
    elif tconf >= 0 and mom5 > -1 and market_label in ("BULLISH+", "BULLISH", "SIDEWAYS", "WEAK+"):
        advice = "谨慎买入"
    elif is_oversold_market and tconf >= -2 and score >= 20:
        advice = "超跌反弹"
    elif market_label in ("BULLISH+", "BULLISH"):
        advice = "观望"
    else:
        return 0, {"filter": "市场/趋势不符"}, "观望"
    # ---- Market bully bonus ----
    if market_label == "BULLISH+" and advice in ("??", "??"):
        score += 4
    if market_label == "BULLISH" and advice == "??":
        score += 2
    
    # ---- DYNAMIC TP/SL (sim_v8i.py exact) ----
    atr_sum = 0
    for j in range(max(0, ki-13), ki+1):
        atr_sum += kls[j]["high"] - kls[j]["low"]
    atr14 = atr_sum / 14 if ki >= 13 else 2
    atr_pct = atr14 / close * 100
    
    if atr_pct > 5: dtp, dsl, mhold = 7.0, -6.0, 2
    elif atr_pct > 3: dtp, dsl, mhold = 6.0, -5.0, 3
    else: dtp, dsl, mhold = 5.0, -4.0, 4
    
    if advice == "??": dtp = max(4.0, dtp-1); mhold = min(6, mhold+2)
    elif advice == "??": mhold = min(5, mhold+1)
    elif advice == "??": dtp = min(7.0, dtp+1); dsl = max(-3.0, dsl+1)
    elif advice == "??": dtp = min(4.0, dtp-2); dsl = max(-2.5, dsl+2); mhold = min(2, mhold-1)
    
    if market_label == "BULLISH+" and advice in ("??", "??"):
        dsl -= 0.5; mhold = min(7, mhold+1)
    if market_label == "BULLISH" and advice == "??":
        mhold = min(6, mhold+1)
    
    # Position sizing
    if advice == "??": pos_pct = 100
    elif advice == "??": pos_pct = 90
    elif advice == "??": pos_pct = 60
    elif advice == "??": pos_pct = 25
    elif advice == "??": pos_pct = 30
    else: pos_pct = 30
    if vol_reduce: pos_pct = min(pos_pct, 25)
    
    tp_price = round(close * (1 + dtp/100), 2)
    sl_price = round(close * (1 + dsl/100), 2)
    final_score = min(100, max(0, round(score)))
    
    details = {
        "chg": round(chg, 2), "amp": round(amp, 2),
        "rsi": round(rsi6, 0), "vol_ratio": round(vol_ratio, 1),
        "dist_high": round(dist_high, 1),
        "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
        "ma_arrange": "??" if ma5 > ma10 > ma20 else ("??" if ma5 > ma10 else "??"),
        "vol_trend": round(vt, 1),
        "trend": tdetail, "trend_conf": tconf,
        "mom5": round(mom5, 1),
        "market": market_label, "market_label": market_label,
        "tp_price": tp_price, "sl_price": sl_price,
        "tp_pct": dtp, "sl_pct": dsl,
        "max_hold": mhold, "position_pct": pos_pct,
        "atr_pct": round(atr_pct, 1),
        "total": final_score
    }
    
    return final_score, details, advice


_idx_kls_cache = None
_idx_kls_cache_time = 0

def score_v8i_live(quote, kls, idx_kls=None):
    """Compatibility wrapper - delegates to score_v8i. Use cached idx_kls when provided."""
    global _idx_kls_cache, _idx_kls_cache_time
    if idx_kls is None:
        # Use cached index klines (refresh every 60 seconds)
        now = time.time()
        if _idx_kls_cache is None or now - _idx_kls_cache_time > 60:
            _idx_kls_cache = fetch_kline_sina("sh000001", 60)
            _idx_kls_cache_time = now
        idx_kls = _idx_kls_cache
    return score_v8i(quote, kls, idx_kls)


# ============ V8i Helper Functions ============

def calc_adx_live(highs, lows, closes, period=14):
    n = len(closes)
    if n < period + 1: return 0, 0, 0
    tr_list = []; plus_dm = []; minus_dm = []
    for i in range(1, n):
        h_v, l_v, c = highs[i], lows[i], closes[i]
        ph, pl = highs[i-1], lows[i-1]
        tr = max(h_v-l_v, abs(h_v-ph), abs(l_v-pl))
        tr_list.append(tr)
        updm = h_v-ph if (h_v-ph) > (pl-l_v) and (h_v-ph) > 0 else 0
        dndm = pl-l_v if (pl-l_v) > (h_v-ph) and (pl-l_v) > 0 else 0
        plus_dm.append(updm); minus_dm.append(dndm)
    atr = sum(tr_list[:period])/period
    atr_pdm = sum(plus_dm[:period])/period
    atr_ndm = sum(minus_dm[:period])/period
    alpha = 1.0/period
    for i in range(period, len(tr_list)):
        atr = atr*(1-alpha) + tr_list[i]*alpha
        atr_pdm = atr_pdm*(1-alpha) + plus_dm[i]*alpha
        atr_ndm = atr_ndm*(1-alpha) + minus_dm[i]*alpha
    pdi = 100*atr_pdm/atr if atr > 0 else 0
    ndi = 100*atr_ndm/atr if atr > 0 else 0
    dx = 100*abs(pdi-ndi)/(pdi+ndi) if (pdi+ndi) > 0 else 0
    return round(dx, 1), round(pdi, 1), round(ndi, 1)


def linear_reg_slope_r2(ys):
    n = len(ys)
    if n < 3: return 0, 0
    xs = list(range(n))
    mx = sum(xs)/n; my = sum(ys)/n
    ss_xy = sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
    ss_xx = sum((x-mx)**2 for x in xs)
    if ss_xx == 0: return 0, 0
    slope = ss_xy/ss_xx
    ss_res = sum((ys[i] - (my + slope*(xs[i]-mx)))**2 for i in range(n))
    ss_tot = sum((y-my)**2 for y in ys)
    r2 = 1 - ss_res/ss_tot if ss_tot > 0 else 0
    return slope/my*100 if my != 0 else 0, r2


def calc_mfi(highs, lows, closes, vols, period=14):
    n = len(closes)
    if n < period + 1: return 50
    tp_list = []; mf_list = []
    for i in range(1, n):
        tp = (highs[i] + lows[i] + closes[i])/3
        mf = tp * vols[i]
        tp_list.append(tp); mf_list.append(mf)
    pos_mf = 0; neg_mf = 0
    for i in range(len(mf_list)-period, len(mf_list)):
        if i > 0 and tp_list[i] > tp_list[i-1]: pos_mf += mf_list[i]
        elif i > 0: neg_mf += mf_list[i]
    if neg_mf == 0: return 100
    mfr = pos_mf/neg_mf if neg_mf > 0 else 1
    return round(100 - (100/(1+mfr)), 1)


def calc_macd_bonus(closes):
    n = len(closes)
    if n < 35: return 0, ""
    def ema(data, period):
        if len(data) < period: return [data[-1]]
        result = [sum(data[:period])/period]
        alpha = 2.0/(period+1)
        for i in range(period, len(data)):
            result.append(data[i]*alpha + result[-1]*(1-alpha))
        return result
    ema12 = ema(closes, 12); ema26 = ema(closes, 26)
    min_len = min(len(ema12), len(ema26))
    macd_vals = [ema12[i]-ema26[i] for i in range(min_len)]
    sig_vals = ema(macd_vals, 9) if len(macd_vals) >= 9 else [macd_vals[-1]]
    macd_l = macd_vals[-1]; sig_l = sig_vals[-1]
    hist = macd_l - sig_l
    prev_hist = macd_vals[-2]-sig_vals[-2] if len(macd_vals)>=2 and len(sig_vals)>=2 else 0
    bonus = 0; detail = ""
    if macd_l > sig_l and macd_l > 0:
        bonus += 3; detail = "MACD+"
        if hist > prev_hist: bonus += 1
    elif macd_l > sig_l:
        bonus += 1; detail = "MACDX"
    return bonus, detail


def trend_bonus_v8(kls, ki):
    closes = [k["close"] for k in kls[max(0, ki-25):ki+1]]
    highs = [k["high"] for k in kls[max(0, ki-25):ki+1]]
    lows = [k["low"] for k in kls[max(0, ki-25):ki+1]]
    vols = [k["volume"] for k in kls[max(0, ki-25):ki+1]]
    if len(closes) < 20: return 0, "", 0
    
    bonus = 0; details = []; confidence = 0
    
    adx, pdi, ndi = calc_adx_live(highs, lows, closes)
    if adx > 30: bonus += 5; confidence += 2; details.append("ADX"+str(int(adx))+"+")
    elif adx > 25: bonus += 3; confidence += 1; details.append("ADX"+str(int(adx)))
    elif adx > 20: bonus += 1; details.append("ADX"+str(int(adx)))
    else: details.append("ADX"+str(int(adx)))
    if pdi > ndi and adx > 20: bonus += 2
    
    slope10, r2_10 = linear_reg_slope_r2(closes[-10:])
    if r2_10 > 0.6 and slope10 > 0: bonus += 3; confidence += 1; details.append("R2+")
    elif r2_10 > 0.4 and slope10 > 0: bonus += 1
    
    slope20, r2_20 = linear_reg_slope_r2(closes[-20:])
    if r2_20 > 0.5 and slope20 > 0: bonus += 2; confidence += 1; details.append("20T+")
    
    slope5, _ = linear_reg_slope_r2(closes[-5:])
    if slope5 > 0 and slope10 > 0 and slope20 > 0: bonus += 3; confidence += 1; details.append("ALIGN")
    elif slope5 > 0 and slope10 > 0: bonus += 1
    
    up_vol = 0; dn_vol = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            vi = i - max(0, len(closes)-len(vols))
            if 0 <= vi < len(vols): up_vol += vols[vi]
        else:
            vi = i - max(0, len(closes)-len(vols))
            if 0 <= vi < len(vols): dn_vol += vols[vi]
    if dn_vol == 0: dn_vol = 1
    if up_vol > dn_vol*1.3: bonus += 2; confidence += 1; details.append("VUP")
    
    mfi = calc_mfi(highs, lows, closes, vols)
    if 55 <= mfi <= 75: bonus += 3; details.append("MFI"+str(int(mfi)))
    elif 45 <= mfi <= 80: bonus += 1
    
    macd_b, macd_d = calc_macd_bonus(closes)
    bonus += macd_b
    if macd_d: details.append(macd_d)
    
    if len(closes) >= 20:
        bb_ma = sum(closes[-20:])/20
        variance = sum((x-bb_ma)**2 for x in closes[-20:])/20
        sigma = math.sqrt(variance)
        bb_upper = bb_ma + 2*sigma; bb_lower = bb_ma - 2*sigma
        bb_pct = (closes[-1]-bb_lower)/(bb_upper-bb_lower) if bb_upper > bb_lower else 0.5
        if 0.6 <= bb_pct <= 0.9: bonus += 2; details.append("BB"+str(int(bb_pct*100)))
    
    return bonus, ",".join(details), confidence


# Keep old score_v8i_live reference point for compatibility
# score_v8i_live is now defined above as wrapper to score_v8i



HOLDINGS_FILE=os.path.join(os.path.dirname(os.path.abspath(__file__)),"holdings.json")

def load_holdings():
    try:
        with open(HOLDINGS_FILE,"r",encoding="utf-8") as f:
            return json.load(f)
    except: return []

def save_holdings(data):
    with open(HOLDINGS_FILE,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False)

def analyze_holding(code, name, buy_price, shares, buy_date):
    sym=("sh" if code.startswith("6") else "sz")+code
    kls=fetch_kline_sina(sym, 80)
    # Estimate hold days from buy_date
    from datetime import datetime
    hold_days_est = 1
    if buy_date:
        try:
            buy_dt = datetime.strptime(buy_date.replace("-",""), "%Y%m%d")
            today = datetime.now()
            # Count trading days (approximate: weekdays only)
            hold_days_est = max(1, len([d for d in kls if d["day"] >= buy_date]))
        except:
            pass
    if not kls:
        return {"code":code,"name":name,"buy_price":buy_price,"shares":shares,"cur_price":0,"pnl_pct":0,"pnl_amt":0,"market_value":0,"advice":"数据不足","reason":"无法获取K线数据"}
    latest=kls[-1]; cur_price=latest["close"]; cur_day=latest["day"]
    pnl=(cur_price-buy_price)/buy_price*100; pnl_amt=(cur_price-buy_price)*shares; market_value=cur_price*shares
    closes=[k["close"] for k in kls]; highs=[k["high"] for k in kls]; lows=[k["low"] for k in kls]; vols=[k["volume"] for k in kls]
    if len(closes)<20:
        return {"code":code,"name":name,"buy_price":buy_price,"shares":shares,"cur_price":round(cur_price,2),"pnl_pct":round(pnl,2),"pnl_amt":round(pnl_amt,2),"market_value":round(market_value,2),"advice":"持有","reason":"数据不足无法分析"}
    gains=[]; losses_r=[]
    for i in range(1,min(7,len(closes))):
        diff=closes[-i]-closes[-i-1]
        if diff>=0: gains.append(diff)
        else: losses_r.append(abs(diff))
    avg_g=sum(gains)/6 if gains else 0.0001; avg_l=sum(losses_r)/6 if losses_r else 0.0001
    rsi=100-(100/(1+avg_g/avg_l)) if avg_l>0 else 100
    ma5=sum(closes[-5:])/5; ma10=sum(closes[-10:])/10; ma20=sum(closes[-20:])/20
    tr_list=[]; plus_dm=[]; minus_dm=[]
    for i in range(1,len(closes)):
        h,l,c=highs[i],lows[i],closes[i]; ph,pl=highs[i-1],lows[i-1]
        tr=max(h-l,abs(h-ph),abs(l-pl)); tr_list.append(tr)
        updm=h-ph if (h-ph)>(pl-l) and (h-ph)>0 else 0
        dndm=pl-l if (pl-l)>(h-ph) and (pl-l)>0 else 0
        plus_dm.append(updm); minus_dm.append(dndm)
    period=14; adx=0
    if len(tr_list)>=period:
        atr_v=sum(tr_list[:period])/period; atr_p=sum(plus_dm[:period])/period; atr_n=sum(minus_dm[:period])/period
        alpha=1.0/period
        for i in range(period,len(tr_list)):
            atr_v=atr_v*(1-alpha)+tr_list[i]*alpha; atr_p=atr_p*(1-alpha)+plus_dm[i]*alpha; atr_n=atr_n*(1-alpha)+minus_dm[i]*alpha
        pdi=100*atr_p/atr_v if atr_v>0 else 0; ndi=100*atr_n/atr_v if atr_v>0 else 0
        dx=100*abs(pdi-ndi)/(pdi+ndi) if (pdi+ndi)>0 else 0; adx=dx
    avg_vol5=sum(vols[-6:-1])/5 if len(vols)>=6 else vols[-1]; vol_ratio=vols[-1]/avg_vol5 if avg_vol5>0 else 1
    trend_up=cur_price>ma5>ma10>ma20; trend_down=cur_price<ma20
    rsi_oh=rsi>75; rsi_healthy=50<=rsi<=70; vol_exp=vol_ratio>1.3
    advice="持有"; reason=""
    if pnl>=10:
        if rsi_oh or (pnl>=20 and not vol_exp): advice="止盈卖出"; reason="盈利%.1f%%且过热,RSI%d高位"%(pnl,int(rsi))
        else: advice="持有"; reason="盈利%.1f%%且趋势健康"%pnl
    elif pnl>=5:
        if trend_down: advice="减仓"; reason="盈利%.1f%%但跌破20日均线"%pnl
        elif rsi_oh: advice="减仓"; reason="盈利%.1f%%但RSI%d过热"%(pnl,int(rsi))
        else: advice="持有"; reason="盈利%.1f%%趋势良好"%pnl
    elif pnl>=-3:
        if trend_up and rsi_healthy and vol_exp: advice="加仓"; reason="多头趋势+放量+RSI健康"
        elif trend_up: advice="持有"; reason="多头趋势良好"
        elif cur_price>ma5: advice="持有"; reason="站稳5日均线"
        else: advice="持有"; reason="短期震荡观望"
    elif pnl>=-5:
        if trend_down: advice="止损卖出"; reason="亏损%.1f%%且趋势走弱"%pnl
        else: advice="减仓"; reason="亏损%.1f%%注意风险"%pnl
    else: advice="止损卖出"; reason="亏损%.1f%%建议止损"%pnl
    
    # ---- TP/SL/Position calculation ----
    atr_sum_h = 0
    for j in range(max(0, len(highs)-14), len(highs)):
        atr_sum_h += highs[j] - lows[j]
    atr14_h = atr_sum_h / 14 if len(highs) >= 14 else 2
    atr_pct_h = atr14_h / cur_price * 100 if cur_price > 0 else 3
    
    if atr_pct_h > 5: h_tp, h_sl, h_hold = 7.0, -6.0, 2
    elif atr_pct_h > 3: h_tp, h_sl, h_hold = 6.0, -5.0, 3
    else: h_tp, h_sl, h_hold = 5.0, -4.0, 4
    
    # Adjust based on trend
    if trend_up: h_tp = max(4.0, h_tp - 1); h_hold = min(6, h_hold + 2)
    elif trend_down: h_tp = min(7.0, h_tp + 1); h_sl = max(-3.0, h_sl + 1)
    
    # Position advice
    if pnl >= 5 and trend_up: pos_advice = 80
    elif pnl >= 0 and trend_up: pos_advice = 60
    elif pnl >= -3: pos_advice = 40
    else: pos_advice = 20
    
    tp_price_h = round(cur_price * (1 + h_tp / 100), 2)
    sl_price_h = round(cur_price * (1 + h_sl / 100), 2)
    
    # ---- SELL SIGNAL MONITORING ----
    highest_since_buy = max([h["high"] for h in kls[-hold_days_est:]]) if kls else cur_price
    profit_from_high = (highest_since_buy - buy_price) / buy_price * 100
    
    sell_signals = []
    
    # 1. Take profit
    if cur_price >= buy_price * (1 + h_tp / 100):
        sell_signals.append("止盈触发: {:.2f} > 目标{:.2f}".format(cur_price, buy_price * (1 + h_tp / 100)))
    elif cur_price >= buy_price * 1.03:
        gap = round((buy_price * (1 + h_tp / 100) / cur_price - 1) * 100, 1)
        sell_signals.append("接近止盈: 距{:.2f}还差{:.1f}%".format(buy_price * (1 + h_tp / 100), gap))
    
    # 2. Stop loss
    if cur_price <= buy_price * (1 + h_sl / 100):
        sell_signals.append("止损触发: {:.2f} < 止损{:.2f}".format(cur_price, buy_price * (1 + h_sl / 100)))
    
    # 3. Trailing stop
    if profit_from_high >= 5:
        protected = buy_price + (highest_since_buy - buy_price) * 0.4
        if cur_price <= protected:
            sell_signals.append("移动止盈触发: 最高{:.2f} 保护{:.2f}".format(highest_since_buy, protected))
        else:
            sell_signals.append("移动止盈监控: 最高{:.2f} 保护{:.2f}".format(highest_since_buy, protected))
    
    # 4. Weakness
    if hold_days_est >= 2 and pnl < -1:
        sell_signals.append("走弱: 持有{}天亏损{:.1f}%".format(hold_days_est, pnl))
    
    # 5. Break MA5
    if hold_days_est >= 2 and cur_price < ma5:
        sell_signals.append("跌破MA5: {:.2f} < {:.2f}".format(cur_price, ma5))
    
    # 6. Expiry
    if hold_days_est >= h_hold - 1:
        sell_signals.append("临近到期: 已持{}/{}天".format(hold_days_est, h_hold))
    
    if not sell_signals:
        sell_signals.append("无卖出信号, 继续持有")
    
    return {"code":code,"name":name,"buy_price":buy_price,"shares":shares,"buy_date":buy_date,"cur_price":round(cur_price,2),"cur_day":cur_day,"pnl_pct":round(pnl,2),"pnl_amt":round(pnl_amt,2),"market_value":round(market_value,2),"cost":round(buy_price*shares,2),"rsi":round(rsi,0),"adx":round(adx,0),"vol_ratio":round(vol_ratio,1),"ma5":round(ma5,2),"ma10":round(ma10,2),"ma20":round(ma20,2),"advice":advice,"reason":reason,"tp_price":tp_price_h,"sl_price":sl_price_h,"tp_pct":round(h_tp,1),"sl_pct":round(h_sl,1),"max_hold":h_hold,"position_pct":pos_advice,"atr_pct":round(atr_pct_h,1),"sell_signals":sell_signals,"highest":round(highest_since_buy,2),"hold_days":hold_days_est}

def analyze_all_holdings():
    holdings=load_holdings(); results=[]
    for h in holdings:
        r=analyze_holding(h["code"],h["name"],h["buy_price"],h["shares"],h.get("buy_date",""))
        results.append(r)
    return results


def market_state_ok():
    """Quick market state check for live mode"""
    try:
        idx_kl = fetch_kline_sina("sh000001", 30)
        if not idx_kl or len(idx_kl) < 20: return True
        closes = [k["close"] for k in idx_kl]
        ma20 = sum(closes[-20:]) / 20
        return closes[-1] >= ma20
    except: return True

def screen(ms=35, topn=50, date_str=None):
    stocks=get_stocks()
    codes=[c for c,_ in stocks]
    if date_str:
        kline_data=get_historical_data(codes, date_str)
        # Pre-fetch index klines for market state (filtered to target date)
        idx_kls_raw = fetch_kline_sina("sh000001", 200)
        idx_kls_hist = [k for k in (idx_kls_raw or []) if k["day"] <= date_str]
        
        # Detect oversold market for pre-filter
        _idx_oversold = False
        if idx_kls_hist and len(idx_kls_hist) >= 5:
            _ic = [k["close"] for k in idx_kls_hist[-20:]]
            _drop5 = (_ic[-1] - _ic[-5])/_ic[-5]*100 if len(_ic) >= 5 else 0
            _idx_oversold = _drop5 < -1.0
        
        results=[]
        for code,name in stocks:
            if code not in kline_data: continue
            kls=kline_data[code]
            dd=extract_date_data(kls, date_str)
            if dd is None: continue
            p = dd["close"]; yc = dd.get("prev_close", 0)
            if yc <= 0: continue
            chg = (p - yc) / yc * 100
            # ---- FAST pre-filter (looser for oversold markets) ----
            if _idx_oversold:
                if chg < -4.0 or chg > 5.5: continue
            else:
                if chg < 1.0 or chg > 5.5: continue
            amp_v = dd.get("amp", 0)
            if amp_v > 7.0: continue
            if dd.get("close_position", 0) < 0.4: continue
            if p < 4 or p > 80: continue
            amt_v = dd.get("volume", 0) * p
            if amt_v < 30000000: continue
            # ---- V8i CANONICAL SCORING (score_v8i) ----
            kls = kline_data[code]
            
            # Build quote dict for score_v8i
            quote_v8 = {
                "price": str(dd["close"]),
                "open": str(dd["open"]),
                "high": str(dd["high"]),
                "low": str(dd["low"]),
                "yclose": str(dd.get("prev_close", 0)),
                "amt": str(dd.get("volume", 0) * dd["close"]),
            }
            
            score, details, advice = score_v8i(quote_v8, kls, idx_kls_hist, ki=dd["_idx"])
            if score <= 0: continue
            
            close = dd["close"]
            chg = (close - dd.get("prev_close", 1)) / dd.get("prev_close", 1) * 100 if dd.get("prev_close", 0) > 0 else 0
            amt_val = dd.get("volume", 0) * close
            
            # Use TP/SL from score_v8i
            tp_price = details.get("tp_price", round(close*1.05,2))
            sl_price = details.get("sl_price", round(close*0.96,2))
            dtp = details.get("tp_pct", 5.0)
            dsl = details.get("sl_pct", -4.0)
            mh = details.get("max_hold", 3)
            pos_pct = details.get("position_pct", 50)
            market_label = details.get("market_label", "SIDEWAYS")
            
            # Buy timing
            buy_timing = "??14:45???"
            if advice == "??" and market_label == "BULLISH+":
                buy_timing = "???????????"
            elif advice == "??":
                buy_timing = "???????????"
            elif advice == "??" and market_label == "BULLISH+":
                buy_timing = "?????????????"
            elif advice == "??":
                buy_timing = "????14:45???"
            elif advice == "??":
                buy_timing = "????15??????"
            elif advice == "??":
                buy_timing = "???????????????????"
            elif advice == "??":
                buy_timing = "??????????"
            
            # Augment details with frontend fields
            details["market"] = market_label
            details["market_label"] = market_label
            details["tp_price"] = tp_price
            details["sl_price"] = sl_price
            details["tp_pct"] = dtp
            details["sl_pct"] = dsl
            details["max_hold"] = mh
            details["position_pct"] = pos_pct
            details["buy_timing"] = buy_timing
            details["total"] = score
            
            if score >= ms:
                results.append({"code":code,"name":name,"score":min(100,max(0,round(score))),"price":close,"change_pct":round(chg,2),"amount":amt_val,"turnover":dd.get("turnover",0),"trade_date":date_str,"advice":advice,"risk":"","details":details})
        results.sort(key=lambda x:x["score"],reverse=True)
        return results[:topn]
    else:
        # Limit to top stocks for live scanning (symbol-sorted ~= importance)
        # Scan limit: use env var SCAN_LIMIT for Railway (default 1000), or full pool locally
        scan_limit = int(os.environ.get("SCAN_LIMIT", "99999"))
        scan_limit = min(scan_limit, len(stocks))  # full pool by default
        scan_stocks = stocks[:scan_limit]
        scan_codes = [c for c,_ in scan_stocks]
        PROGRESS["total"]=len(scan_stocks); PROGRESS["done"]=0; PROGRESS["msg"]="Fetching real-time quotes..."
        quotes=get_quotes(scan_codes)
        PROGRESS["msg"]=f"Got {len(quotes)} quotes, fetching kline data..."
        
        results=[]
        # Step 1: fast filter using quotes
        candidates=[]
        for code,name in scan_stocks:
            if code not in quotes: continue
            d=quotes[code]
            p=f(d.get("price",0)); yc=f(d.get("yclose",0))
            if p<=0 or yc<=0: continue
            chg=(p-yc)/yc*100
            if chg<1.0 or chg>5.5: continue
            h_v=f(d.get("high",0)); l_v=f(d.get("low",0))
            if yc>0 and h_v and l_v:
                amp=(h_v-l_v)/yc*100
                if amp>6.0: continue
            candidates.append((code,name,d))
        
        PROGRESS["msg"]=f"Fast-filtered {len(candidates)} candidates, deep scoring..."
        
        # Step 2: deep score with kline data for candidates
        # Use daily cache when available (massive speedup for mobile)
        kline_map={}
        cached_count = 0
        to_fetch = []
        for code,name,d in candidates[:300]:
            cached = get_cached_klines(code)
            if cached:
                kline_map[code] = cached
                cached_count += 1
            else:
                to_fetch.append(code)
        
        if to_fetch:
            PROGRESS["msg"]=f"Cache hit {cached_count}/{len(candidates[:300])}, fetching {len(to_fetch)}..."
            def fetch_one(code):
                sym=("sh" if code.startswith("6") else "sz")+code
                return code, fetch_kline_sina(sym, 60)
            
            with ThreadPoolExecutor(max_workers=25) as ex:
                futs={ex.submit(fetch_one,c):c for c in to_fetch}
                for i,fut in enumerate(as_completed(futs)):
                    code,kls=fut.result()
                    if kls: kline_map[code]=kls; _add_kline_to_cache(code, kls)
                    if (i+1)%100==0:
                        PROGRESS["done"]=cached_count+i+1
                        PROGRESS["total"]=len(candidates[:300])
                        PROGRESS["msg"]=f"K-line {cached_count+i+1}/{len(candidates[:300])}..."
        else:
            PROGRESS["msg"]=f"All {len(candidates[:300])} klines from cache!"
        
        PROGRESS["total"]=len(candidates)
        PROGRESS["done"]=0
        PROGRESS["msg"]=f"Scoring {len(candidates)} stocks..."
        
        # Fetch index klines once for all candidates
        idx_kls_cached = fetch_kline_sina("sh000001", 60)
        for i,(code,name,d) in enumerate(candidates):
            kls=kline_map.get(code, [])
            if not kls: continue
            sc,det,timing=score_v8i_live(d, kls, idx_kls_cached)
            if sc>=ms:
                results.append({
                    "code":code,"name":name,"score":sc,
                    "price":round(f(d.get("price",0)),2),
                    "change_pct":round(det.get("chg",0),2),
                    "amount":f(d.get("amt",0)),
                    "turnover":f(d.get("turnover",0)),
                    "trade_date":d.get("date",""),
                    "advice":timing,
                    "timing_reason":det.get("timing_reason",""),
                    "risk":det.get("filter","") if det.get("filter") else "",
                    "details":det
                })
            if (i+1) % 50 == 0:
                PROGRESS["done"]=i+1
                PROGRESS["msg"]=f"Scored {i+1}/{len(candidates)}, found {len(results)}"
        
        results.sort(key=lambda x:x["score"],reverse=True)
        PROGRESS["msg"]=f"Done! {len(results[:topn])} results"
        # Save fetched klines to daily cache for faster subsequent scans
        for code, kls in kline_map.items():
            _add_kline_to_cache(code, kls)
        _save_daily_cache()
        return results[:topn]


# ---- Holdings Monitor & Sell Alerts ----
MONITOR_ALERTS = []  # Store recent alerts: [{time, code, name, type, price, msg}]
MONITOR_STATUS = {"running": False, "last_check": "", "next_check": ""}

def is_market_open():
    """Check if market is currently open (9:30-11:30, 13:00-15:00 Mon-Fri)"""
    import datetime
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t < 1130) or (1300 <= t < 1500)

def monitor_holdings():
    """Check all holdings against real-time quotes for sell signals"""
    global MONITOR_ALERTS, MONITOR_STATUS
    if not is_market_open():
        MONITOR_STATUS["running"] = False
        return []
    
    holdings = load_holdings()
    if not holdings:
        MONITOR_STATUS["running"] = False
        return []
    
    codes = []
    for h in holdings:
        sym = ("sh" if h["code"].startswith("6") else "sz") + h["code"]
        codes.append(sym)
    
    try:
        quotes = get_quotes(codes)
    except:
        return []
    
    alerts = []
    now = datetime.now()
    
    for h in holdings:
        sym = ("sh" if h["code"].startswith("6") else "sz") + h["code"]
        if sym not in quotes:
            continue
        
        q = quotes[sym]
        cur_price = f(q.get("price", 0))
        high = f(q.get("high", 0))
        low = f(q.get("low", 0))
        yclose = f(q.get("yclose", 0))
        
        if cur_price <= 0 or h.get("buy_price", 0) <= 0:
            continue
        
        bp = h["buy_price"]
        pnl_pct = (cur_price - bp) / bp * 100
        shares = h.get("shares", 0)
        pnl_amt = (cur_price - bp) * shares
        
        # Calculate dynamic TP/SL using stored kline data
        sym_k = ("sh" if h["code"].startswith("6") else "sz") + h["code"]
        kls = fetch_kline_sina(sym_k, 60) or []
        
        # Default TP/SL
        tp_pct = 5.0; sl_pct = -4.0; max_hold = 4
        
        if kls and len(kls) >= 14:
            atr_sum = sum(k["high"] - k["low"] for k in kls[-14:])
            atr14 = atr_sum / 14
            atr_pct = atr14 / cur_price * 100 if cur_price > 0 else 3
            if atr_pct > 5: tp_pct, sl_pct, max_hold = 7.0, -6.0, 2
            elif atr_pct > 3: tp_pct, sl_pct, max_hold = 6.0, -5.0, 3
            else: tp_pct, sl_pct, max_hold = 5.0, -4.0, 4
        
        # Estimate hold days
        buy_date = h.get("buy_date", "")
        hold_days = 1
        if buy_date and kls:
            hold_days = max(1, len([k for k in kls if k["day"] >= buy_date.replace("-", "")]))
        
        triggered = False
        alert_type = ""
        alert_msg = ""
        
        # Check TP
        tp_price = bp * (1 + tp_pct / 100)
        if high >= tp_price:
            triggered = True
            alert_type = "????"
            alert_msg = "{} ????? {:.2f} (??{:.2f}, ??{:.1f}%)".format(h["name"], tp_price, cur_price, pnl_pct)
        
        # Check SL
        sl_price = bp * (1 + sl_pct / 100)
        if not triggered and low <= sl_price:
            triggered = True
            alert_type = "????"
            alert_msg = "{} ????? {:.2f} (??{:.2f}, ??{:.1f}%)".format(h["name"], sl_price, cur_price, pnl_pct)
        
        # Check trailing stop
        if not triggered and kls:
            recent_highs = [k["high"] for k in kls[-hold_days:]]
            highest_since = max(recent_highs) if recent_highs else cur_price
            profit_from_high = (highest_since - bp) / bp * 100
            if profit_from_high >= 5:
                protected = bp + (highest_since - bp) * 0.4
                if low <= protected:
                    triggered = True
                    alert_type = "????"
                    alert_msg = "{} ?????? {:.2f} (??{:.2f}, ??{:.2f})".format(h["name"], protected, highest_since, cur_price)
        
        # Check weak hold
        if not triggered and hold_days >= 2 and pnl_pct < -1:
            triggered = True
            alert_type = "????"
            alert_msg = "{} ??{}???{:.1f}%, ????".format(h["name"], hold_days, pnl_pct)
        
        if triggered:
            alert = {
                "time": now.strftime("%H:%M:%S"),
                "code": h["code"],
                "name": h["name"],
                "type": alert_type,
                "price": round(cur_price, 2),
                "pnl_pct": round(pnl_pct, 2),
                "pnl_amt": round(pnl_amt, 2),
                "msg": alert_msg
            }
            alerts.append(alert)
            MONITOR_ALERTS.insert(0, alert)
            # Keep only last 50 alerts
            if len(MONITOR_ALERTS) > 50:
                MONITOR_ALERTS = MONITOR_ALERTS[:50]
            
            # Send email alert
            body = """<h2>{} ????!</h2>
            <p><b>{}</b> ({})</p>
            <p>????: {:.2f} | ??: {:+.1f}% ({:+.0f}?)</p>
            <p><b>{}</b></p>
            <p><small>-- ??????????</small></p>""".format(
                alert_type, h["code"], h["name"], cur_price, pnl_pct, pnl_amt, alert_msg
            )
            send_email("????: {} {} - {}".format(h["code"], h["name"], alert_type), body)
    
    MONITOR_STATUS["running"] = True
    MONITOR_STATUS["last_check"] = now.strftime("%H:%M:%S")
    MONITOR_STATUS["holdings_count"] = len(holdings)
    MONITOR_STATUS["alerts_count"] = len(alerts)
    return alerts

def monitor_loop():
    """Background thread: monitor holdings during market hours"""
    import time as _time
    while True:
        try:
            if is_market_open():
                monitor_holdings()
                _time.sleep(120)  # Check every 120s during market (reduced for mobile/Railway)
            else:
                MONITOR_STATUS["running"] = False
                _time.sleep(120)  # Check every 2 minutes outside market
        except Exception as e:
            print("[Monitor] Error: {}".format(e))
            _time.sleep(60)

# Start monitor thread
_monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
_monitor_thread.start()

def _load_html():
    p=os.path.join(os.path.dirname(os.path.abspath(__file__)),"static","index.html")
    with open(p,"r",encoding="utf-8") as f:
        return f.read()
HTML_CACHE=None

def get_html():
    global HTML_CACHE
    if HTML_CACHE is None:
        HTML_CACHE=_load_html()
    return HTML_CACHE
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _s(self,b,ct="text/html; charset=utf-8",code=200):
        b=b.encode("utf-8") if isinstance(b,str) else b
        # Gzip compression for responses > 1KB
        accept_enc = self.headers.get("Accept-Encoding","")
        use_gzip = "gzip" in accept_enc.lower() and len(b) > 1024
        if use_gzip:
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=4) as gf:
                gf.write(b)
            b = buf.getvalue()
        self.send_response(code)
        self.send_header("Content-Type",ct)
        self.send_header("Access-Control-Allow-Origin","*")
        if use_gzip:
            self.send_header("Content-Encoding","gzip")
        self.send_header("Content-Length",str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        self.do_GET()
    def do_DELETE(self):
        self.do_GET()

    def do_GET(self):
        if self.path in ("/","/index.html"):
            b=get_html().encode("utf-8") if isinstance(get_html(),str) else get_html()
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin","*")
            self.send_header("Cache-Control","no-cache, no-store, must-revalidate")
            self.send_header("Pragma","no-cache")
            self.send_header("Expires","0")
            self.send_header("Content-Length",str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path.startswith("/api/scan"):
            qs=parse_qs(urlparse(self.path).query)
            ms=int(qs.get("min_score",[35])[0])
            date_str=qs.get("date",[None])[0]
            async_mode = qs.get("async",["1"])[0] != "0"
            
            if async_mode:
                # Non-blocking: start background scan, return immediately
                started = start_scan_async(ms=ms, date_str=date_str)
                data = {
                    "status": "started" if started else "already_running",
                    "mode": "Historical" if date_str else "Live",
                    "date": date_str or datetime.now().strftime("%Y-%m-%d")
                }
                self._s(json.dumps(data,ensure_ascii=False),"application/json; charset=utf-8")
            else:
                # Blocking mode (for local testing / compatibility)
                t0=time.time()
                try:
                    r=screen(ms=ms, date_str=date_str)
                    td=date_str or datetime.now().strftime("%Y-%m-%d")
                    data={"date":td,"count":len(r),"elapsed":round(time.time()-t0,1),"results":r,"mode":"Historical" if date_str else "Live"}
                except Exception as e:
                    import traceback
                    data={"error":str(e),"trace":traceback.format_exc()}
                self._s(json.dumps(data,ensure_ascii=False),"application/json; charset=utf-8")
        
        elif self.path.startswith("/api/holdings/analyze"):
            results=analyze_all_holdings()
            self._s(json.dumps({"holdings":results,"total_pnl":sum(r["pnl_amt"] for r in results),"total_value":sum(r["market_value"] for r in results)},ensure_ascii=False),"application/json; charset=utf-8")
        elif self.path.startswith("/api/holdings"):
            if self.command=="POST":
                try:
                    cl=int(self.headers.get("Content-Length",0))
                    body=json.loads(self.rfile.read(cl))
                    # Convert numeric fields
                    for k in ("buy_price","shares"):
                        if k in body:
                            try: body[k]=float(body[k])
                            except: pass
                    if "shares" in body: body["shares"]=int(body["shares"])
                    holdings=load_holdings()
                    found=False
                    for h in holdings:
                        if h["code"]==body.get("code"):
                            h.update(body); found=True; break
                    if not found: holdings.append(body)
                    save_holdings(holdings)
                    self._s(json.dumps({"ok":True,"count":len(holdings)},ensure_ascii=False),"application/json; charset=utf-8")
                except Exception as e:
                    self._s(json.dumps({"error":str(e)},ensure_ascii=False),"application/json; charset=utf-8",code=400)
            elif self.command=="DELETE":
                qs=parse_qs(urlparse(self.path).query)
                code=qs.get("code",[""])[0]
                holdings=[h for h in load_holdings() if h["code"]!=code]
                save_holdings(holdings)
                self._s(json.dumps({"ok":True,"count":len(holdings)},ensure_ascii=False),"application/json; charset=utf-8")
            else:
                self._s(json.dumps(load_holdings(),ensure_ascii=False),"application/json; charset=utf-8")

        elif self.path.startswith("/api/quotes"):
            qs=parse_qs(urlparse(self.path).query)
            code=qs.get("code",[""])[0]
            if code:
                try:
                    quotes=get_quotes([code])
                    q=quotes.get(code,{})
                    self._s(json.dumps({"code":code,"name":q.get("name",""),"price":q.get("price","0")},ensure_ascii=False),"application/json; charset=utf-8")
                except:
                    self._s(json.dumps({"error":"fetch failed"},ensure_ascii=False),"application/json; charset=utf-8")
            else:
                self._s(json.dumps({"error":"no code"},ensure_ascii=False),"application/json; charset=utf-8")
        
        elif self.path.startswith("/api/alerts"):
            self._s(json.dumps(MONITOR_ALERTS,ensure_ascii=False),"application/json; charset=utf-8")
        
        elif self.path.startswith("/api/monitor/check"):
            alerts = monitor_holdings()
            self._s(json.dumps({"alerts": alerts, "status": MONITOR_STATUS}, ensure_ascii=False),"application/json; charset=utf-8")
        
        elif self.path.startswith("/api/monitor"):
            self._s(json.dumps(MONITOR_STATUS, ensure_ascii=False),"application/json; charset=utf-8")
        
        elif self.path.startswith("/api/progress"):
            resp = dict(PROGRESS)
            if PROGRESS.get("status") == "done" and PROGRESS.get("results"):
                resp["results"] = PROGRESS["results"]
            self._s(json.dumps(resp,ensure_ascii=False),"application/json; charset=utf-8")
        else: self._s("404",code=404)

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--serve",action="store_true")
    p.add_argument("--port",type=int,default=5000)
    p.add_argument("--min-score",type=int,default=35)
    p.add_argument("--date",type=str,default=None)
    a=p.parse_args()
    os.environ["HTTP_PROXY"]=""
    os.environ["HTTPS_PROXY"]=""
    if a.serve:
        port=int(os.environ.get("PORT",a.port))
        print("\n" + "="*55)
        print(f"  Tail-Stock Screener v5")
        print(f"  7-Dim AI Scoring | http://localhost:{port}")
        print("="*55 + "\n")
        ThreadingHTTPServer(("0.0.0.0",port),H).serve_forever()
    else:
        t0=time.time()
        print("\n" + "="*55)
        print(f"  Tail-Stock Screener v5")
        print("  Date: " + (a.date or "Live"))
        print("="*55 + "\n")
        r=screen(ms=a.min_score, date_str=a.date)
        for i,x in enumerate(r,1):
            dt=x.get("details",{})
            print(f"{i:2}. {x['code']} {x['name']:8s} {x['score']:5.1f} | {x['advice']}")
            if dt.get("risk") and dt["risk"]!="Clean":
                print(f"     Risk: {dt['risk']}")
        print(f"\nTotal {len(r)} | Elapsed {time.time()-t0:.1f}s")


