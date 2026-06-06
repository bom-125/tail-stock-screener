# -*- coding: utf-8 -*-
"""尾盘选股引擎 - 新浪实时数据 + AKShare股票列表"""
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

# Global cache for stock list
_stock_cache = {'codes': None, 'time': 0}

def get_stock_codes():
    """Get all A-share stock codes (cached for 30 min)"""
    now = time.time()
    if _stock_cache['codes'] is not None and now - _stock_cache['time'] < 1800:
        return _stock_cache['codes']
    
    # Suppress akshare output
    old_out = sys.stdout
    sys.stdout = io.StringIO()
    try:
        df = ak.stock_info_a_code_name()
        df['code'] = df['code'].astype(str).str.zfill(6)
        df['sid'] = df['code'].apply(lambda x: 'sh'+x if x.startswith(('6','9')) else 'sz'+x)
        codes = df[['code','name','sid']].to_dict('records')
        _stock_cache['codes'] = codes
        _stock_cache['time'] = now
        return codes
    finally:
        sys.stdout = old_out

def fetch_real_time_data():
    """Batch query Sina real-time quotes"""
    codes = get_stock_codes()
    if not codes:
        return None
    
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://finance.sina.com.cn/'
    })
    
    rows = []
    batch_size = 200
    
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        symbols = ','.join([c['sid'] for c in batch])
        url = f'https://hq.sinajs.cn/list={symbols}'
        
        try:
            r = s.get(url, timeout=30)
            r.encoding = 'gbk'
            for line in r.text.strip().split('\n'):
                m = re.search(r'var hq_str_(\w+)="(.+)"', line)
                if not m:
                    continue
                sid = m.group(1)
                vals = m.group(2).split(',')
                if len(vals) < 32:
                    continue
                
                row = {
                    'code': sid[2:],
                    'name': vals[0],
                    'open': safe_float(vals[1]),
                    'prev_close': safe_float(vals[2]),
                    'price': safe_float(vals[3]),
                    'high': safe_float(vals[4]),
                    'low': safe_float(vals[5]),
                    'volume': safe_float(vals[8]),
                    'amount': safe_float(vals[9]),
                }
                # Filter invalid
                if row['price'] and row['price'] > 0 and row['prev_close'] and row['prev_close'] > 0:
                    row['pct_change'] = round((row['price']-row['prev_close'])/row['prev_close']*100, 2)
                    row['change_amount'] = round(row['price']-row['prev_close'], 2)
                    if row['high'] and row['low']:
                        row['amplitude'] = round((row['high']-row['low'])/row['prev_close']*100, 2)
                    else:
                        row['amplitude'] = 0
                    rows.append(row)
        except Exception as e:
            print(f"Batch {i} error: {e}")
        
        time.sleep(0.1)  # Rate limit
    
    if not rows:
        return None
    
    df = pd.DataFrame(rows)
    df['volume'] = df['volume'].fillna(0)
    df['amount'] = df['amount'].fillna(0)
    df['volume_ratio'] = 1.0
    df['turnover_rate'] = 0.0
    df['float_market_cap_yi'] = 0.0
    
    return df

def safe_float(v):
    try: return float(v) if v and v.strip() else None
    except: return None

def screen_stocks(df):
    if df is None or df.empty:
        return pd.DataFrame()
    cfg = ScreenerConfig()
    r = df.copy()
    
    if cfg.EXCLUDE_ST:
        r = r[~r['name'].str.contains(r'ST|退|\*ST', na=False, regex=True)].copy()
    
    r = r[(r['pct_change']>=cfg.PCT_CHANGE_MIN)&(r['pct_change']<=cfg.PCT_CHANGE_MAX)].copy()
    r = r[(r['price']>=cfg.PRICE_MIN)&(r['price']<=cfg.PRICE_MAX)].copy()
    
    if 'high' in r.columns and 'low' in r.columns:
        r = r[~((r['high']==r['low'])&(r['pct_change'].abs()>9.5))].copy()
    
    if not r.empty:
        r['score'] = 0.0
        r['score'] += 30 - abs(r['pct_change']-4.5)*4
        if 'amplitude' in r.columns:
            r['score'] += np.clip(r['amplitude'].fillna(0)*2, 0, 15)
        r = r.sort_values('score', ascending=False)
    
    return r

def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5: return False
    return ((now.hour==9 and now.minute>=30) or (now.hour==10) or (now.hour==11 and now.minute<=30) or
            (now.hour==13) or (now.hour==14) or (now.hour==15 and now.minute==0))

def run_screen():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Screen start...")
    t0 = time.time()
    df = fetch_real_time_data()
    if df is None or df.empty:
        return {"error":"fetch failed","stocks":[],"timestamp":datetime.now().isoformat(),"market_open":is_market_open()}
    total = len(df)
    result = screen_stocks(df)
    
    cols = ['code','name','price','pct_change','volume_ratio','turnover_rate','amplitude','amount','float_market_cap_yi','score']
    avail = [c for c in cols if c in result.columns]
    stocks = []
    if not result.empty:
        for _,row in result.head(30).iterrows():
            s = {}
            for c in avail:
                v = row[c]
                if isinstance(v,(np.integer,)): v=int(v)
                elif isinstance(v,(np.floating,)): v=round(float(v),2) if not pd.isna(v) else None
                elif pd.isna(v): v=None
                s[c]=v
            stocks.append(s)
    
    elapsed = time.time()-t0
    print(f"Done in {elapsed:.1f}s: {len(stocks)}/{total}")
    return {"success":True,"total_screened":int(total),"matched":len(stocks),"stocks":stocks,
            "timestamp":datetime.now().strftime('%Y-%m-%d %H:%M:%S'),"market_open":is_market_open()}

if __name__=='__main__':
    r = run_screen()
    print(f"Matched: {r['matched']}/{r['total_screened']}  Market open: {r['market_open']}")
    for s in r['stocks'][:10]:
        print(f"  {s['code']} {s['name']} pct:{s.get('pct_change')}% score:{s.get('score')}")