# -*- coding: utf-8 -*-
"""灏剧洏閫夎偂寮曟搸 v3.1 - 浜旂淮缁煎悎璇勫垎锛堣祫閲戞祦鍚戝疄娴嬪彲鐢級"""
import os
for pv in ['HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','ALL_PROXY','all_proxy']:
    os.environ.pop(pv, None)
os.environ['NO_PROXY'] = '*'

import requests
_o = requests.Session.__init__
def _ni(s): _o(s); s.trust_env = False
requests.Session.__init__ = _ni

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
import re, time, warnings, io, sys
warnings.filterwarnings('ignore')

class ScreenerConfig:
    PCT_CHANGE_MIN = 2.0
    PCT_CHANGE_MAX = 6.5
    VOLUME_RATIO_MIN = 1.2
    TURNOVER_MIN = 3.0
    TURNOVER_MAX = 15.0
    MARKET_CAP_MIN = 30
    MARKET_CAP_MAX = 500
    PRICE_MIN = 5.0
    PRICE_MAX = 80.0
    EXCLUDE_ST = True
    EXCLUDE_CHINEXT = True
    EXCLUDE_STAR = True
    TOP_N = 30

_stock_cache = {'codes': None, 'time': 0}
_fund_flow_cache = {}

def safe_float(v):
    try: return float(v) if v and str(v).strip() else None
    except: return None

def get_stock_codes():
    """Get stock codes from local stocks.json (fast, Railway-safe) with akshare fallback"""
    import json as _json
    now = time.time()
    if _stock_cache["codes"] is not None and now - _stock_cache["time"] < 3600:
        return _stock_cache["codes"]
    
    stocks_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stocks.json")
    if os.path.exists(stocks_json):
        try:
            with open(stocks_json, "r", encoding="utf-8") as f:
                data = _json.load(f)
            _stock_cache["codes"] = data["codes"]
            _stock_cache["time"] = now
            print(f"[engine] Loaded {len(data['codes'])} stocks from stocks.json")
            return data["codes"]
        except Exception as e:
            print(f"[engine] stocks.json error: {e}")
    
    print("[engine] Falling back to akshare...")
    old_out = sys.stdout; sys.stdout = io.StringIO()
    try:
        df = ak.stock_info_a_code_name()
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["sid"] = df["code"].apply(lambda x: "sh"+x if x.startswith(("6","9")) else "sz"+x)
        codes = [{"code": r["code"], "name": r["name"], "sid": r["sid"],
                   "st": "ST" in str(r["name"]),
                   "chinext": r["code"].startswith(("300","301")),
                   "star": r["code"].startswith(("688","689"))}
                 for _, r in df.iterrows()]
        _stock_cache["codes"] = codes; _stock_cache["time"] = now
        return codes
    finally: sys.stdout = old_out

def fetch_real_time_data():
    codes = get_stock_codes()
    if not codes: return None
    s = requests.Session(); s.trust_env = False
    s.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'})
    rows = []
    for i in range(0, len(codes), 200):
        batch = codes[i:i+200]
        url = 'https://hq.sinajs.cn/list=' + ','.join([c['sid'] for c in batch])
        try:
            r = s.get(url, timeout=30); r.encoding = 'gbk'
            for line in r.text.strip().split('\n'):
                m = re.search(r'var hq_str_(\w+)="(.+)"', line)
                if not m: continue
                sid, vals = m.group(1), m.group(2).split(',')
                if len(vals) < 32: continue
                row = {
                    'code': sid[2:], 'name': vals[0],
                    'open': safe_float(vals[1]), 'prev_close': safe_float(vals[2]),
                    'price': safe_float(vals[3]), 'high': safe_float(vals[4]),
                    'low': safe_float(vals[5]), 'volume': safe_float(vals[8]),
                    'amount': safe_float(vals[9]),
                }
                if row['price'] and row['price']>0 and row['prev_close'] and row['prev_close']>0:
                    row['pct_change'] = round((row['price']-row['prev_close'])/row['prev_close']*100,2)
                    row['amplitude'] = round((row['high']-row['low'])/row['prev_close']*100,2) if row['high'] and row['low'] else 0
                    row['volume_ratio'] = 1.0
                    rows.append(row)
        except: pass
        time.sleep(0.08)
    return pd.DataFrame(rows) if rows else None

def screen_stocks(df):
    if df is None or df.empty: return pd.DataFrame()
    cfg = ScreenerConfig()
    r = df.copy()
    if cfg.EXCLUDE_ST:
        r = r[~r['name'].str.contains(r'ST|閫€|\*ST', na=False, regex=True)].copy()
    r = r[(r['pct_change']>=cfg.PCT_CHANGE_MIN)&(r['pct_change']<=cfg.PCT_CHANGE_MAX)].copy()
    r = r[(r['price']>=cfg.PRICE_MIN)&(r['price']<=cfg.PRICE_MAX)].copy()
    if 'high' in r.columns and 'low' in r.columns:
        r = r[~((r['high']==r['low'])&(r['pct_change'].abs()>9.5))].copy()
    return r

def fetch_fund_flow_batch(codes):
    """Get today fund flow for selected stocks via Eastmoney"""
    s = requests.Session(); s.trust_env = False
    s.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/'})
    results = {}
    for code in codes:
        if code in _fund_flow_cache:
            results[code] = _fund_flow_cache[code]
            continue
        try:
            market = 1 if code.startswith(('6','9')) else 0
            url = f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=1&klt=1&secid={market}.{code}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64'
            r = s.get(url, timeout=10)
            if r.status_code==200:
                data = json.loads(r.text)
                klines = data.get('data',{}).get('klines',[])
                if klines:
                    parts = klines[-1].split(',')
                    results[code] = {
                        'main_net_inflow': safe_float(parts[1]) if len(parts)>1 else None,
                        'super_large_net': safe_float(parts[2]) if len(parts)>2 else None,
                        'large_net': safe_float(parts[3]) if len(parts)>3 else None,
                        'main_net_ratio': safe_float(parts[6]) if len(parts)>6 else None,
                    }
                    _fund_flow_cache[code] = results[code]
        except: pass
        time.sleep(0.08)
    return results

def deep_score(row, fund_flow, rank_idx):
    """Five-dimension scoring: Trend(25) + Capital(25) + Sentiment(15) + Technical(20) + Value(15) = 100"""
    code = row['code']
    f = fund_flow.get(code, {})
    pct = row.get('pct_change', 0) or 0
    price = row.get('price', 0) or 0
    high = row.get('high', 0) or 0
    low = row.get('low', 0) or 0
    open_p = row.get('open', 0) or 0
    amp = row.get('amplitude', 0) or 0
    vol = row.get('volume', 0) or 0
    amount = row.get('amount', 0) or 0
    
    # ---- 1. TREND (25) 瓒嬪娍寮哄害 ----
    trend = 0
    if 3.0 <= pct <= 5.5: trend += 15
    elif 2.0 <= pct < 3.0: trend += 11
    elif 5.5 < pct <= 6.5: trend += 9
    else: trend += 6
    if vol > 2e7: trend += 10
    elif vol > 8e6: trend += 8
    elif vol > 3e6: trend += 5
    else: trend += 3
    
    # ---- 2. CAPITAL (25) 璧勯噾璁ゅ彲 ----
    capital = 0
    main_net = f.get('main_net_inflow', 0) or 0
    main_ratio = f.get('main_net_ratio', 0) or 0
    
    if main_net > 1e8: capital += 10
    elif main_net > 5e7: capital += 8
    elif main_net > 1e7: capital += 6
    elif main_net > 0: capital += 4
    else: capital += 1
    
    if main_ratio > 8: capital += 8
    elif main_ratio > 4: capital += 6
    elif main_ratio > 0: capital += 4
    else: capital += 1
    
    # Volume/amount ratio as liquidity proxy
    if amount > 5e8: capital += 7
    elif amount > 1e8: capital += 5
    elif amount > 5e7: capital += 3
    else: capital += 1
    
    # ---- 3. SENTIMENT (15) 甯傚満鎯呯华 ----
    sentiment = 0
    if 5 <= amp <= 9: sentiment += 8
    elif 3 <= amp <= 11: sentiment += 6
    elif amp > 0: sentiment += 3
    else: sentiment += 1
    
    # Price relative to day range (strength signal)
    if high > low > 0:
        pos = (price - low) / (high - low)
        if pos >= 0.8: sentiment += 7
        elif pos >= 0.5: sentiment += 4
        else: sentiment += 2
    else: sentiment += 2
    
    # ---- 4. TECHNICAL (20) 鎶€鏈舰鎬?----
    technical = 0
    # Tail-market momentum (close vs open)
    if open_p > 0:
        tail_pct = (price - open_p) / open_p * 100
        if tail_pct > 3: technical += 10
        elif tail_pct > 1.5: technical += 7
        elif tail_pct > 0: technical += 5
        else: technical += 2
    else: technical += 2
    
    # Amplitude quality
    if 4 <= amp <= 7: technical += 10
    elif 2 <= amp <= 10: technical += 6
    else: technical += 3
    
    # ---- 5. VALUE (15) 浼板€奸€傞厤 ----
    value = 0
    # Price range scoring (small-mid cap proxy)
    if 8 <= price <= 30: value += 7
    elif 5 <= price <= 60: value += 5
    else: value += 3
    
    # Rank bonus (top picks get slight edge from being selected first)
    if rank_idx < 3: value += 5
    elif rank_idx < 10: value += 4
    elif rank_idx < 20: value += 3
    else: value += 2
    
    # Amplitude-to-volume efficiency
    if vol > 0 and amp > 0:
        eff = (amp * amount / vol) if vol > 0 else 0
        if eff > 50: value += 3
        elif eff > 20: value += 2
        else: value += 1
    else: value += 1
    
    total = trend + capital + sentiment + technical + value
    
    return {
        'total': round(total, 1),
        'trend': round(trend, 1),
        'capital': round(capital, 1),
        'sentiment': round(sentiment, 1),
        'technical': round(technical, 1),
        'valuation': round(value, 1),
        'fund_flow': {
            'main_net_inflow': main_net,
            'main_net_ratio': main_ratio,
        }
    }

def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5: return False
    return ((now.hour==9 and now.minute>=30) or (now.hour==10) or (now.hour==11 and now.minute<=30) or
            (now.hour==13) or (now.hour==14) or (now.hour==15 and now.minute==0))

def run_screen():
    t0 = time.time()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Screen v3.1...")
    df = fetch_real_time_data()
    if df is None or df.empty:
        return {"error":"fetch failed","stocks":[],"timestamp":datetime.now().isoformat(),"market_open":is_market_open()}
    total = len(df)
    passed = screen_stocks(df)
    if passed.empty:
        return {"success":True,"total_screened":int(total),"matched":0,"stocks":[],"timestamp":datetime.now().strftime('%Y-%m-%d %H:%M:%S'),"market_open":is_market_open()}
    
    top_codes = passed.head(ScreenerConfig.TOP_N)['code'].tolist()
    print(f"Pass 1: {len(passed)}/{total}, fetching fund flow for {len(top_codes)} stocks...")
    fund_flow = fetch_fund_flow_batch(top_codes)
    
    stocks = []
    for idx, (_, row) in enumerate(passed.head(ScreenerConfig.TOP_N).iterrows()):
        s = {
            'code': row['code'], 'name': row['name'],
            'price': round(float(row['price']),2) if pd.notna(row['price']) else None,
            'pct_change': round(float(row['pct_change']),2) if pd.notna(row['pct_change']) else None,
            'amplitude': round(float(row['amplitude']),2) if pd.notna(row.get('amplitude',0)) else None,
            'volume': safe_float(str(row.get('volume',''))),
            'amount': safe_float(str(row.get('amount',''))),
        }
        scoring = deep_score(row, fund_flow, idx)
        s.update(scoring)
        stocks.append(s)
    
    stocks.sort(key=lambda x: x['total'], reverse=True)
    
    elapsed = time.time()-t0
    print(f"Done {elapsed:.1f}s: {len(stocks)} ranked (top score: {stocks[0]['total'] if stocks else 0})")
    return {"success":True,"total_screened":int(total),"matched":len(stocks),"stocks":stocks,"timestamp":datetime.now().strftime('%Y-%m-%d %H:%M:%S'),"market_open":is_market_open()}

if __name__=='__main__':
    r = run_screen()
    print(f"Matched: {r['matched']}/{r['total_screened']}")
    for s in r['stocks'][:10]:
        print(f"  {s['code']} {s['name']} {s['pct_change']}% | {s['total']}鍒?(T:{s['trend']} C:{s['capital']} S:{s['sentiment']} T:{s['technical']} V:{s['valuation']})")