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

import pandas as pd
import numpy as np
from datetime import datetime
import re, time, warnings, io, sys
warnings.filterwarnings('ignore')

class ScreenerConfig:
    # SEPA / 9-Step Filter v4.0
    PCT_CHANGE_MIN = 3.0
    PCT_CHANGE_MAX = 5.0
    VOLUME_RATIO_MIN = 1.0
    TURNOVER_MIN = 5.0
    TURNOVER_MAX = 10.0
    MARKET_CAP_MIN = 50
    MARKET_CAP_MAX = 200
    AMPLITUDE_MAX = 8.0
    PRICE_MIN = 5.0
    PRICE_MAX = 80.0
    EXCLUDE_ST = True
    EXCLUDE_CHINEXT = True
    EXCLUDE_STAR = True
    MA_SHORT = 50
    MA_LONG = 150
    LIMIT_UP_DAYS = 20
    VOLUME_EXPAND_RATIO = 1.5
    TOP_N = 30
    # Quality filter: minimum total score for recommendation
    MIN_TOTAL_SCORE = 40  # Show all, scoring provides tier
    # Recommendation levels that qualify as "strong pick"
    MIN_RECO_LEVEL = "watch"  # "strong_buy" / "buy" / "watch" / "avoid" 

_stock_cache = {'codes': None, 'time': 0}
_fund_flow_cache = {}
_hist_cache = {}

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
        import akshare as ak
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
    s = requests.Session(); s.trust_env = False
    
    # === Source 1: Eastmoney batch API (works best on Railway) ===
    try:
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"})
        url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=6000&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f100,f115,f152,f184"
        r = s.get(url, timeout=20)
        if r.status_code == 200:
            data = r.json()
            stocks = data.get("data", {}).get("diff", [])
            if stocks and len(stocks) > 100:
                print(f"[fetch] Eastmoney: {len(stocks)} stocks")
                all_rows = []
                for st in stocks:
                    code = str(st.get("f12", "")).zfill(6)
                    if not code or len(code) != 6: continue
                    price = safe_float(st.get("f2"))
                    prev = safe_float(st.get("f18"))
                    if not price or price <= 0 or not prev or prev <= 0: continue
                    pct = safe_float(st.get("f3")); amp = safe_float(st.get("f7"))
                    turnover = safe_float(st.get("f8")); vol_ratio = safe_float(st.get("f10"))
                    all_rows.append({
                        "code": code, "name": str(st.get("f14", "")),
                        "open": safe_float(st.get("f17")), "prev_close": prev,
                        "price": price, "high": safe_float(st.get("f15")),
                        "low": safe_float(st.get("f16")), "volume": safe_float(st.get("f5")),
                        "amount": safe_float(st.get("f6")),
                        "pct_change": pct/100 if pct and abs(pct)>50 else pct,
                        "amplitude": amp/100 if amp and abs(amp)>50 else amp,
                        "turnover_rate": turnover/100 if turnover and abs(turnover)>50 else turnover,
                        "volume_ratio": vol_ratio/100 if vol_ratio and abs(vol_ratio)>50 else vol_ratio,
                        "total_mcap": safe_float(st.get("f20")),
                    })
                return pd.DataFrame(all_rows)
    except Exception as e:
        print(f"[fetch] Eastmoney failed: {e}")
    
    # === Source 2: Sina fallback ===
    try:
        print("[fetch] Trying Sina fallback...")
        codes = get_stock_codes()
        if codes:
            s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://finance.sina.com.cn/"})
            rows = []
            for i in range(0, len(codes), 200):
                batch = codes[i:i+200]
                url = "https://hq.sinajs.cn/list=" + ",".join([c["sid"] for c in batch])
                try:
                    r2 = s.get(url, timeout=15); r2.encoding = "gbk"
                    for line in r2.text.strip().split("\n"):
                        m = re.search(r'var hq_str_(\w+)="(.+)"', line)
                        if not m: continue
                        sid, vals = m.group(1), m.group(2).split(",")
                        if len(vals) < 32: continue
                        row = {
                            "code": sid[2:], "name": vals[0],
                            "open": safe_float(vals[1]), "prev_close": safe_float(vals[2]),
                            "price": safe_float(vals[3]), "high": safe_float(vals[4]),
                            "low": safe_float(vals[5]), "volume": safe_float(vals[8]),
                            "amount": safe_float(vals[9]),
                        }
                        if row["price"] and row["price"]>0 and row["prev_close"] and row["prev_close"]>0:
                            row["pct_change"] = round((row["price"]-row["prev_close"])/row["prev_close"]*100,2)
                            row["amplitude"] = round((row["high"]-row["low"])/row["prev_close"]*100,2) if row["high"] and row["low"] else 0
                            row["volume_ratio"] = 1.0
                            rows.append(row)
                except: pass
                time.sleep(0.05)
            if rows:
                print(f"[fetch] Sina: {len(rows)} stocks")
                return pd.DataFrame(rows)
    except Exception as e:
        print(f"[fetch] Sina fallback failed: {e}")
    
    print("[fetch] All sources failed")
    return None

def screen_stocks(df):
    if df is None or df.empty: return pd.DataFrame()
    cfg = ScreenerConfig()
    r = df.copy()
    if cfg.EXCLUDE_ST:
        r = r[~r['name'].str.contains(r'ST|閫€|\*ST', na=False, regex=True)].copy()
    if cfg.EXCLUDE_CHINEXT:
        r = r[~r['code'].str.startswith(('300','301'))].copy()
    if cfg.EXCLUDE_STAR:
        r = r[~r['code'].str.startswith(('688','689'))].copy()
    r = r[(r['pct_change']>=cfg.PCT_CHANGE_MIN)&(r['pct_change']<=cfg.PCT_CHANGE_MAX)].copy()
    r = r[(r['price']>=cfg.PRICE_MIN)&(r['price']<=cfg.PRICE_MAX)].copy()
    if 'amplitude' in r.columns:
        r = r[r['amplitude'] <= cfg.AMPLITUDE_MAX].copy()
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

def fetch_kline_batch(codes, days=160):
    s = requests.Session(); s.trust_env = False
    s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"})
    results = {}
    for code in codes:
        try:
            market = 0 if code.startswith(("0","3","2")) else 1
            url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={market}.{code}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=0&end=20500101&lmt={days}"
            r = s.get(url, timeout=10)
            if r.status_code == 200:
                klines = r.json().get("data", {}).get("klines", [])
                if klines:
                    parsed = []
                    for k in klines:
                        parts = k.split(",")
                        parsed.append({"date": parts[0], "open": float(parts[1]), "close": float(parts[2]), "high": float(parts[3]), "low": float(parts[4]), "volume": float(parts[5]), "amount": float(parts[6]) if len(parts) > 6 else 0})
                    results[code] = parsed
        except:
            pass
    return results


def calc_sepa_metrics(code, kline_data, current_price):
    cfg = ScreenerConfig()
    result = {"above_ma50": False, "above_ma150": False, "ma50": None, "ma150": None, "has_limit_up": False, "limit_up_count": 0, "volume_expanding": False, "vol_ratio_50d": 0, "near_20d_high": False, "has_long_upper_shadow": False}
    if not kline_data or len(kline_data) < 50:
        return result
    closes = [k["close"] for k in kline_data]
    volumes = [k["volume"] for k in kline_data]
    highs = [k["high"] for k in kline_data]
    if len(closes) >= 50:
        ma50 = sum(closes[-50:]) / 50
        result["ma50"] = round(ma50, 2)
        result["above_ma50"] = current_price > ma50
    if len(closes) >= 150:
        ma150 = sum(closes[-150:]) / 150
        result["ma150"] = round(ma150, 2)
        result["above_ma150"] = current_price > ma150
    recent = kline_data[-cfg.LIMIT_UP_DAYS:]
    for i in range(1, len(recent)):
        if recent[i-1]["close"] > 0:
            chg = (recent[i]["close"] - recent[i-1]["close"]) / recent[i-1]["close"] * 100
            if chg >= 9.5:
                result["limit_up_count"] += 1
    result["has_limit_up"] = result["limit_up_count"] > 0
    if len(volumes) >= 50 and kline_data[-1]["volume"] > 0:
        avg_vol = sum(volumes[-51:-1]) / 50
        if avg_vol > 0:
            result["vol_ratio_50d"] = round(kline_data[-1]["volume"] / avg_vol, 2)
            result["volume_expanding"] = result["vol_ratio_50d"] >= cfg.VOLUME_EXPAND_RATIO
    if len(highs) >= 20:
        high_20d = max(highs[-21:-1])
        result["near_20d_high"] = current_price >= high_20d * 0.97
        last_k = kline_data[-1]
        body = abs(last_k["close"] - last_k["open"])
        upper_shadow = last_k["high"] - max(last_k["close"], last_k["open"])
        if body > 0 and upper_shadow > body * 2:
            result["has_long_upper_shadow"] = True
    return result


def enrich_stock_details(codes):
    """A-share comprehensive data enrichment via Eastmoney"""
    s = requests.Session(); s.trust_env = False
    s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"})
    results = {}
    for code in codes:
        try:
            market = 0 if code.startswith(("0","3","2")) else 1
            fields = "f43,f50,f100,f115,f116,f117,f152,f162,f167,f168,f169,f173,f174,f184"
            url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields={fields}"
            r = s.get(url, timeout=8)
            if r.status_code == 200:
                d = r.json().get("data", {})
                if d:
                    pe_ttm = d.get("f115")  # PE(TTM) in raw format
                    pe_dyn = d.get("f162")  # PE(动态)
                    results[code] = {
                        "turnover_rate": round(d.get("f167", 0) / 100, 2) if d.get("f167") != "-" else None,
                        "volume_ratio": round(d.get("f50", 0) / 100, 2),
                        "total_mcap": round(d.get("f116", 0) / 1e8, 2) if d.get("f116") else None,
                        "float_mcap": round(d.get("f117", 0) / 1e8, 2) if d.get("f117") else None,
                        "pe_ttm": round(pe_ttm / 100, 2) if isinstance(pe_ttm, (int,float)) and pe_ttm > 0 else (round(pe_dyn / 100, 2) if isinstance(pe_dyn, (int,float)) and pe_dyn > 0 else None),
                        "pe_dyn": round(pe_dyn / 100, 2) if isinstance(pe_dyn, (int,float)) and pe_dyn > 0 else None,
                        "momentum": round(d.get("f152", 0) / 100, 2),          # 涨速(%)
                        "bid_ask_ratio": round(d.get("f184", 0) / 100, 2),     # 委比(%)
                        "chg_5d": round(d.get("f173", 0) / 100, 2),            # 5日涨跌幅
                        "chg_ytd": round(d.get("f174", 0) / 100, 2),           # 今年涨跌幅
                        "sector": d.get("f100", ""),                           # 行业
                    }
        except:
            pass
    return results

def deep_score(row, fund_flow, rank_idx, sepa=None):
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
    
    # ---- SEPA SCORING (15 bonus points) ----
    sepa_score = 0
    sepa_signals = []
    
    if sepa:
        # MA alignment (price above both MA50 and MA150)
        if sepa.get("above_ma50") and sepa.get("above_ma150"):
            sepa_score += 5
            sepa_signals.append("股价站上MA50/MA150多头排列")
        elif sepa.get("above_ma50"):
            sepa_score += 3
            sepa_signals.append("股价在MA50之上")
        
        # Limit-up history (涨停基因)
        if sepa.get("has_limit_up"):
            lu_count = sepa.get("limit_up_count", 0)
            if lu_count >= 2:
                sepa_score += 5
                sepa_signals.append(f"近20日{lu_count}次涨停，主力高度活跃")
            else:
                sepa_score += 3
                sepa_signals.append("近20日有涨停，主力活跃")
        
        # Volume expansion (量能放大)
        if sepa.get("volume_expanding"):
            sepa_score += 3
            sepa_signals.append(f"量能放大{sepa.get('vol_ratio_50d',0):.1f}倍，资金加速流入")
        
        # Near 20-day high (突破形态)
        if sepa.get("near_20d_high"):
            sepa_score += 2
            sepa_signals.append("接近20日新高，突破在即")
        
        # No long upper shadow (无长上影线)
        if not sepa.get("has_long_upper_shadow"):
            sepa_score += 1
        else:
            sepa_signals.append("长上影线，短期抛压存在")
    
    # Add SEPA to total
    total = trend + capital + sentiment + technical + value + sepa_score
    # Merge SEPA signals into prediction signals
    predict_signals.extend(sepa_signals)
    
    # ---- 6. RECOMMENDATION based on comprehensive analysis ----
    recommendation = ""
    signals = []
    
    if total >= 85:
        recommendation = "strong_buy"
        signals.append("综合评分优秀")
    elif total >= 70:
        recommendation = "buy"
        signals.append("综合评分良好")
    elif total >= 55:
        recommendation = "watch"
        signals.append("综合评分一般")
    else:
        recommendation = "avoid"
        signals.append("综合评分偏低")
    
    # Fund flow adjustment (upgrade by 1 level for strong inflow)
    if main_net >= 5e7:
        signals.append(f"主力净流入{main_net/1e4:.0f}万")
        if recommendation == "buy": recommendation = "strong_buy"
        elif recommendation == "watch": recommendation = "buy"
        elif recommendation == "avoid": recommendation = "watch"
    elif main_net >= 1e7:
        signals.append(f"主力小幅流入{main_net/1e4:.0f}万")
    elif main_net < -5e7:
        signals.append("主力大幅流出，注意风险")
        if recommendation == "strong_buy": recommendation = "buy"
        elif recommendation == "buy": recommendation = "watch"
    
    # Amplitude risk warning
    if amp > 10:
        signals.append("振幅过大，波动风险高")
        if recommendation in ("strong_buy", "buy"): recommendation = "watch"
    elif 4 <= amp <= 7:
        signals.append("振幅适中")
    
    # Tail-market price position signal
    if high > low > 0 and price > 0:
        pos = (price - low) / (high - low)
        if pos >= 0.85:
            signals.append("尾盘强势(高位收盘)")
        elif pos <= 0.3:
            signals.append("尾盘走弱(低位收盘)")
            if recommendation == "strong_buy": recommendation = "buy"
            elif recommendation == "buy": recommendation = "watch" 
    
    reco_map = {"strong_buy": "强力买入", "buy": "建议买入", "watch": "观望关注", "avoid": "谨慎回避"}
    
    # ---- 7. NEXT-DAY PREDICTION (明日分时预判) ----
    next_day_score = 0
    predict_signals = []
    
    # A. Tail-market momentum analysis (尾盘动量分析)
    if open_p > 0 and price > 0:
        tail_pct = (price - open_p) / open_p * 100
        if tail_pct > 2 and vol > 0:
            next_day_score += 15
            predict_signals.append("尾盘强势拉升，资金介入明显")
        elif tail_pct > 1:
            next_day_score += 8
            predict_signals.append("尾盘小幅拉升")
        elif tail_pct > 0:
            next_day_score += 4
        elif tail_pct < -1:
            next_day_score -= 10
            predict_signals.append("尾盘回落，次日承压")
    
    # B. Price position analysis (收盘位置分析)
    if high > low > 0 and price > 0:
        close_pos = (price - low) / (high - low)
        if close_pos >= 0.9:
            next_day_score += 10
            predict_signals.append("收盘于全天高点附近，强势特征")
        elif close_pos >= 0.7:
            next_day_score += 4
        elif close_pos <= 0.3:
            next_day_score -= 8
            predict_signals.append("收盘于全天低位，弱势特征")
    
    # C. Fund flow impact on next day (资金面预判)
    if main_net >= 1e8:
        next_day_score += 16
        predict_signals.append(f"主力大幅净流入{main_net/1e4:.0f}万，次日大概率高开")
    elif main_net >= 5e7:
        next_day_score += 10
        predict_signals.append(f"主力净流入{main_net/1e4:.0f}万，有资金支撑")
    elif main_net >= 1e7:
        next_day_score += 4
    elif main_net < -5e7:
        next_day_score -= 12
        predict_signals.append("主力大幅流出，次日低开风险")
    elif main_net < -1e7:
        next_day_score -= 6
    
    # D. Volume-price relationship (量价关系)
    if vol > 0:
        pct = safe_float(str(row.get('pct_change', 0))) or 0
        if pct > 5 and amp > 8:
            next_day_score -= 5
            predict_signals.append("放量冲高回落风险，谨慎追涨")
        elif pct > 3 and amp < 6 and vol > 1e7:
            next_day_score += 6
            predict_signals.append("温和放量上涨，量价配合良好")
    
    # E. Amplitude risk (振幅风险)
    if amp > 9:
        next_day_score -= 6
        predict_signals.append("振幅过大，次日震荡概率高")
    elif amp < 2:
        next_day_score -= 3
        predict_signals.append("振幅过小，动能不足")
    
    # F. Price range and market cap proxy (市值活跃度)
    if 8 <= price <= 50 and amount > 5e7:
        next_day_score += 3
        predict_signals.append("中小盘活跃标的，次日延续性较好")
    
    # ---- 8. PREDICTION OUTPUT ----
    if next_day_score >= 25:
        direction = "上涨"
        confidence = min(90, 55 + next_day_score)
    elif next_day_score >= 10:
        direction = "偏多震荡"
        confidence = min(80, 40 + next_day_score)
    elif next_day_score >= -5:
        direction = "横盘震荡"
        confidence = 35 + next_day_score
    elif next_day_score >= -15:
        direction = "偏空震荡"
        confidence = min(70, 50 - abs(next_day_score))
    else:
        direction = "下跌"
        confidence = min(85, 55 + abs(next_day_score))
    
    # ---- 9. SELL ADVICE (卖出建议) ----
    sell_advice = ""
    sell_detail = []
    
    if recommendation in ("strong_buy", "buy") and next_day_score >= 20:
        sell_advice = "次日尾盘或后天早盘择机卖出"
        sell_detail.append("主力资金积极，预计次日延续强势")
        sell_detail.append("建议14:30后观察分时是否放量滞涨再决定")
    elif recommendation in ("strong_buy", "buy") and next_day_score >= 5:
        sell_advice = "次日冲高时卖出"
        sell_detail.append("次日开盘大概率冲高，建议09:45-10:15择机卖出")
        sell_detail.append("若开盘后量能不济，应在10:00前卖出")
    elif recommendation == "watch":
        if next_day_score >= 0:
            sell_advice = "次日开盘15分钟内卖出"
            sell_detail.append("标的评分一般，不建议持仓过久")
            sell_detail.append("利用开盘流动性较好时快速出清")
        else:
            sell_advice = "竞价阶段或开盘即卖出"
            sell_detail.append("技术面偏弱，次日大概率低开")
            sell_detail.append("建议09:25集合竞价时挂低价卖出")
    else:
        sell_advice = "竞价阶段立即卖出"
        sell_detail.append("评分偏低，不宜持有")
        sell_detail.append("建议09:20前挂跌停价参与竞价卖出")
    
    # If close near high with strong volume, add target price estimate
    target_high = None
    target_low = None
    if price and pct:
        target_high = round(price * (1 + max(0.01, pct/100 * 0.5)), 2)
        target_low = round(price * (1 - min(0.03, abs(pct)/100 * 0.3)), 2)
    
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
        },
        'recommendation': reco_map[recommendation],
        'reco_level': recommendation,
        'signals': signals,
        'prediction': {
            'direction': direction,
            'confidence': round(confidence, 1),
            'signals': predict_signals,
        },
        'sepa_score': round(sepa_score, 1),
        'max_score': 115,
        'sell_advice': sell_advice,
        'sell_detail': sell_detail,
        'target_high': target_high,
        'target_low': target_low,
    }

def run_historical_screen(target_date):
    """Historical screening using Eastmoney kline API (works on Railway)"""
    import concurrent.futures
    t0 = time.time()
    
    # Cache check
    global _hist_cache
    if target_date in _hist_cache:
        print(f"[history] Cache hit: {target_date}")
        return _hist_cache[target_date]
    
    print(f"[history] Fetching {target_date} via Eastmoney...")
    
    codes = get_stock_codes()
    if not codes:
        return {"error": "no_stock_list", "stocks": []}
    
    # 200 diverse stocks
    step = max(1, len(codes) // 200)
    sample = codes[::step][:250]
    
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/"})
    
    # Calculate start date (3 trading days before target to ensure prev day)
    from datetime import datetime as dt, timedelta
    try:
        target_dt = dt.strptime(target_date, "%Y-%m-%d")
        start_dt = target_dt - timedelta(days=7)  # 7 calendar days = ~5 trading days
        start_str = start_dt.strftime("%Y%m%d")
        end_str = target_dt.strftime("%Y%m%d")
    except:
        start_str = target_date.replace("-", "")
        end_str = start_str
    
    def fetch_kline(c):
        try:
            market = 0 if c["code"].startswith(("0","3","2")) else 1
            # Single API call: get 7 calendar days (~5 trading days) including target
            url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={market}.{c['code']}&klt=101&fqt=0&beg={start_str}&end={end_str}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&lmt=10"
            r = sess.get(url, timeout=8)
            if r.status_code != 200:
                return None
            data = r.json()
            klines = data.get("data", {}).get("klines", [])
            if not klines or len(klines) < 2:
                return None
            
            # Find target date and previous trading day
            target_kl = None
            prev_kl = None
            target_str = target_date  # "2026-06-01"
            
            for i, k in enumerate(klines):
                parts = k.split(",")
                if parts[0] == target_str:
                    target_kl = parts
                    if i > 0:
                        prev_parts = klines[i-1].split(",")
                        prev_kl = prev_parts
                    break
            
            if not target_kl or not prev_kl:
                return None
            
            open_p = float(target_kl[1])
            close_p = float(target_kl[2])
            high = float(target_kl[3])
            low = float(target_kl[4])
            vol = float(target_kl[5])
            amt = float(target_kl[6]) if len(target_kl) > 6 else 0
            turnover_rate = float(target_kl[7]) if len(target_kl) > 7 and target_kl[7] else None
            amp_pct = float(target_kl[8]) if len(target_kl) > 8 and target_kl[8] else None
            pct_change_pct = float(target_kl[9]) if len(target_kl) > 9 and target_kl[9] else None
            
            prev_close = float(prev_kl[2])
            if close_p <= 0 or prev_close <= 0:
                return None
            
            if prev_close <= 0:
                return None
            
            pct = (close_p - prev_close) / prev_close * 100
            amp = (high - low) / prev_close * 100
            
            return {
                "code": c["code"], "name": c["name"],
                "open": open_p, "prev_close": prev_close,
                "price": close_p, "high": high, "low": low,
                "volume": vol, "amount": amt,
                "pct_change": round(pct, 2), "amplitude": round(amp, 2),
                "volume_ratio": 1.0,
                "turnover_rate": turnover_rate,
            }
        except:
            return None
    
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = {executor.submit(fetch_kline, c): c for c in sample}
        done = 0
        for future in concurrent.futures.as_completed(futures, timeout=30):
            done += 1
            try:
                result = future.result(timeout=3)
                if result:
                    rows.append(result)
            except:
                pass
    
    print(f"[history] Got {len(rows)} records from {done} stocks in {time.time()-t0:.1f}s")
    
    if len(rows) < 5:
        result = {"success": True, "total_screened": len(rows), "matched": 0,
                   "stocks": [], "has_recommendation": False,
                   "timestamp": target_date, "market_open": True, "is_historical": True}
        if len(rows) > 0:
            _hist_cache[target_date] = result
        return result
    
    df = pd.DataFrame(rows)
    passed = screen_stocks(df)
    
    if passed.empty:
        result = {"success": True, "total_screened": int(len(rows)), "matched": 0,
                   "stocks": [], "has_recommendation": False,
                   "timestamp": target_date, "market_open": True, "is_historical": True}
        _hist_cache[target_date] = result
        return result
    
    passed = passed.head(min(50, len(passed)))
    
    stocks = []
    for idx, (_, row) in enumerate(passed.iterrows()):
        code = row["code"]
        pct = row["pct_change"]
        amp = row.get("amplitude", 5)
        price = row["price"]
        
        # Simplified but meaningful scoring
        trend = min(25, max(5, pct * 4))
        capital = 8 if row.get("volume", 0) > 1e7 else 5
        sentiment = max(5, min(15, (10 - amp) * 1.5))
        technical = min(20, max(5, pct * 2.5 + (10 - amp) * 0.5))
        valuation = max(5, min(15, 15 - abs(price - 20) / 4))
        total = round(trend + capital + sentiment + technical + valuation, 1)
        
        if total >= 80: tier = "strong"; tier_label = "强烈推荐"
        elif total >= 60: tier = "good"; tier_label = "可以关注"
        elif total >= 40: tier = "watch"; tier_label = "一般关注"
        else: tier = "weak"; tier_label = "风险提示"
        
        stocks.append({
            "code": code, "name": row["name"],
            "price": round(float(price), 2), "pct_change": round(float(pct), 2),
            "amplitude": round(float(amp), 2),
            "volume": safe_float(str(row.get("volume", ""))),
            "amount": safe_float(str(row.get("amount", ""))),
            "enhanced": {"turnover": row.get("turnover_rate"), "volume_ratio": row.get("volume_ratio"),
                          "mktcap_yi": None, "total_mcap_yi": None, "pe": None,
                          "pb": None, "momentum": None, "bid_ask": None,
                          "chg_5d": None, "chg_ytd": None, "sector": None},
            "total": total, "trend": round(trend,1), "capital": round(capital,1),
            "sentiment": round(sentiment,1), "technical": round(technical,1),
            "valuation": round(valuation,1),
            "fund_flow": {"main_net_inflow": 0, "main_net_ratio": 0},
            "recommendation": tier_label, "reco_level": tier,
            "tier": tier, "tier_label": tier_label,
            "signals": [f"涨幅{pct:.1f}%" if pct > 0 else f"跌幅{abs(pct):.1f}%"],
            "prediction": {"direction": "", "confidence": 0, "signals": []},
            "sell_advice": "", "sell_detail": [], "target_high": None, "target_low": None,
            "sepa": {}, "sepa_score": 0,
        })
    
    stocks.sort(key=lambda x: x["total"], reverse=True)
    
    strong_count = sum(1 for s in stocks if s["tier"] == "strong")
    good_count = sum(1 for s in stocks if s["tier"] == "good")
    
    result = {
        "success": True, "total_screened": int(len(rows)), "matched": len(stocks),
        "tiers": {"strong": strong_count, "good": good_count,
                   "watch": sum(1 for s in stocks if s["tier"]=="watch"),
                   "weak": sum(1 for s in stocks if s["tier"]=="weak")},
        "has_recommendation": strong_count > 0 or good_count > 0,
        "stocks": stocks, "timestamp": target_date, "market_open": True,
        "is_historical": True,
    }
    
    # Cache
    _hist_cache[target_date] = result
    if len(_hist_cache) > 30:
        oldest = min(_hist_cache.keys())
        del _hist_cache[oldest]
    
    print(f"[history] Done: {len(stocks)} stocks ({strong_count} strong) in {time.time()-t0:.1f}s")
    return result


def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5: return False
    return ((now.hour==9 and now.minute>=30) or (now.hour==10) or (now.hour==11 and now.minute<=30) or
            (now.hour==13) or (now.hour==14) or (now.hour==15 and now.minute==0))

def run_screen(enable_sepa=True):
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
    
    # SEPA: fetch kline for MA + limit-up analysis
    top_codes_list = passed.head(min(ScreenerConfig.TOP_N, len(passed)))["code"].tolist()
    kline_data = fetch_kline_batch(top_codes_list, days=60)
    # Enrichment now done via batch API (no separate calls needed)
    enriched = {}
    
    stocks = []
    for idx, (_, row) in enumerate(passed.head(ScreenerConfig.TOP_N).iterrows()):
        code = row["code"]
        em = enriched.get(code, {})
        s = {
            'code': code, 'name': row['name'],
            'price': round(float(row['price']),2) if pd.notna(row['price']) else None,
            'pct_change': round(float(row['pct_change']),2) if pd.notna(row['pct_change']) else None,
            'amplitude': round(float(row['amplitude']),2) if pd.notna(row.get('amplitude',0)) else None,
            'volume': safe_float(str(row.get('volume',''))),
            'amount': safe_float(str(row.get('amount',''))),
            'enhanced': {
                'turnover': em.get('turnover_rate'),             # 换手率(%)
                'volume_ratio': em.get('volume_ratio'),          # 量比
                'mktcap_yi': em.get('float_mcap'),               # 流通市值(亿)
                'total_mcap_yi': em.get('total_mcap'),           # 总市值(亿)
                'pe': em.get('pe_ttm') or em.get('pe_dyn'),      # 市盈率
                'pb': None,                                      # 市净率(暂缺)
                'momentum': em.get('momentum'),                  # 涨速
                'bid_ask': em.get('bid_ask_ratio'),              # 委比
                'chg_5d': em.get('chg_5d'),                      # 5日涨跌
                'chg_ytd': em.get('chg_ytd'),                    # 今年涨跌
                'sector': em.get('sector'),                      # 行业板块
            }
        }
        kd = kline_data.get(code, [])
        sepa = calc_sepa_metrics(code, kd, float(row['price']))
        scoring = deep_score(row, fund_flow, idx, sepa)
        s.update(scoring)
        s['sepa'] = sepa
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
