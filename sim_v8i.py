# -*- coding: utf-8 -*-
import sys, os, math
os.environ["HTTP_PROXY"] = ""; os.environ["HTTPS_PROXY"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
exec(open("app.py", "r", encoding="utf-8").read().split("class ThreadingHTTPServer")[0])

test_kls = fetch_kline_sina("sh600000", 120)
trading_days = [k["day"] for k in test_kls if k["day"] >= "2025-06-01" and k["day"] <= "2026-06-12"]
print("Total trading days: {}".format(len(trading_days)))

idx_kls = fetch_kline_sina("sh000001", 120)
idx_map = {k["day"]: k for k in idx_kls}

def get_idx_ma(idx_map, day, n):
    days = sorted(idx_map.keys())
    if day not in days: return None
    i = days.index(day)
    if i < n - 1: return None
    closes = [idx_map[days[j]]["close"] for j in range(i - n + 1, i + 1)]
    return sum(closes) / n

def get_idx_slope(idx_map, day, n):
    days = sorted(idx_map.keys())
    if day not in days: return 0
    i = days.index(day)
    if i < n: return 0
    closes = [idx_map[days[j]]["close"] for j in range(i - n + 1, i + 1)]
    return (closes[-1] - closes[0]) / closes[0] * 100

# ---- BONUS INDICATORS ----
def calc_adx(highs, lows, closes, period=14):
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

def linear_reg_slope_r2(ys):
    n = len(ys)
    if n < 5: return 0, 0
    xs = list(range(n))
    sum_x = sum(xs); sum_y = sum(ys)
    sum_xy = sum(x*y for x,y in zip(xs,ys))
    sum_xx = sum(x*x for x in xs)
    denom = n * sum_xx - sum_x * sum_x
    if denom == 0: return 0, 0
    slope = (n * sum_xy - sum_x * sum_y) / denom
    mean_y = sum_y / n
    ss_tot = sum((y - mean_y)**2 for y in ys)
    intercept = (sum_y - slope * sum_x) / n
    ss_res = sum((y - (intercept + slope * x))**2 for x,y in zip(xs,ys))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    return round(slope, 4), round(r2, 3)

def calc_mfi(highs, lows, closes, vols, period=14):
    n = len(closes)
    if n < period + 1: return 50
    tp_list = []; mf_list = []
    for i in range(1, n):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        mf = tp * vols[i]
        tp_list.append(tp); mf_list.append(mf)
    pos_mf = 0; neg_mf = 0
    for i in range(len(mf_list) - period, len(mf_list)):
        if i > 0 and tp_list[i] > tp_list[i-1]:
            pos_mf += mf_list[i]
        elif i > 0:
            neg_mf += mf_list[i]
    if neg_mf == 0: return 100
    mfr = pos_mf / neg_mf if neg_mf > 0 else 1
    mfi = 100 - (100 / (1 + mfr))
    return round(mfi, 1)

def calc_macd_bonus(closes):
    n = len(closes)
    if n < 35: return 0, ""
    def ema(data, period):
        if len(data) < period: return [data[-1]]
        result = [sum(data[:period]) / period]
        alpha = 2.0 / (period + 1)
        for i in range(period, len(data)):
            result.append(data[i] * alpha + result[-1] * (1 - alpha))
        return result
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    min_len = min(len(ema12), len(ema26))
    macd_vals = [ema12[i] - ema26[i] for i in range(min_len)]
    sig_vals = ema(macd_vals, 9) if len(macd_vals) >= 9 else [macd_vals[-1]]
    macd_l = macd_vals[-1]; sig_l = sig_vals[-1]
    hist = macd_l - sig_l
    prev_hist = macd_vals[-2] - sig_vals[-2] if len(macd_vals) >= 2 and len(sig_vals) >= 2 else 0
    bonus = 0; detail = ""
    if macd_l > sig_l and macd_l > 0:
        bonus += 3; detail = "MACD+"
        if hist > prev_hist: bonus += 1
    elif macd_l > sig_l:
        bonus += 1; detail = "MACDX"
    return bonus, detail

def trend_bonus_v8(kls, ki):
    closes = [k["close"] for k in kls[max(0,ki-25):ki+1]]
    highs = [k["high"] for k in kls[max(0,ki-25):ki+1]]
    lows = [k["low"] for k in kls[max(0,ki-25):ki+1]]
    vols = [k["volume"] for k in kls[max(0,ki-25):ki+1]]
    if len(closes) < 20: return 0, "", 0
    
    bonus = 0; details = []; confidence = 0
    
    # ADX
    adx, pdi, ndi = calc_adx(highs, lows, closes)
    if adx > 30:
        bonus += 5; confidence += 2; details.append("ADX+" + str(int(adx)))
    elif adx > 25:
        bonus += 3; confidence += 1; details.append("ADX" + str(int(adx)))
    elif adx > 20:
        bonus += 1; details.append("ADX" + str(int(adx)))
    else:
        details.append("ADX" + str(int(adx)))
    if pdi > ndi and adx > 20: bonus += 2
    
    # Linear regression
    slope10, r2_10 = linear_reg_slope_r2(closes[-10:])
    if r2_10 > 0.6 and slope10 > 0: bonus += 3; confidence += 1; details.append("R2+")
    elif r2_10 > 0.4 and slope10 > 0: bonus += 1
    
    slope20, r2_20 = linear_reg_slope_r2(closes[-20:])
    if r2_20 > 0.5 and slope20 > 0: bonus += 2; confidence += 1; details.append("20T+")
    
    slope5, _ = linear_reg_slope_r2(closes[-5:])
    if slope5 > 0 and slope10 > 0 and slope20 > 0: bonus += 3; confidence += 1; details.append("ALIGN")
    elif slope5 > 0 and slope10 > 0: bonus += 1
    
    # Volume trend
    up_vol = 0; dn_vol = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            vi = i - max(0, len(closes) - len(vols))
            if 0 <= vi < len(vols): up_vol += vols[vi]
        else:
            vi = i - max(0, len(closes) - len(vols))
            if 0 <= vi < len(vols): dn_vol += vols[vi]
    if dn_vol == 0: dn_vol = 1
    if up_vol > dn_vol * 1.3: bonus += 2; confidence += 1; details.append("VUP")
    
    # MFI
    mfi = calc_mfi(highs, lows, closes, vols)
    if 55 <= mfi <= 75: bonus += 3; details.append("MFI" + str(int(mfi)))
    elif 45 <= mfi <= 80: bonus += 1
    
    # MACD bonus
    macd_b, macd_d = calc_macd_bonus(closes)
    bonus += macd_b
    if macd_d: details.append(macd_d)
    
    # Bollinger %B bonus (simple)
    if len(closes) >= 20:
        bb_ma = sum(closes[-20:]) / 20
        variance = sum((x - bb_ma)**2 for x in closes[-20:]) / 20
        sigma = math.sqrt(variance)
        bb_upper = bb_ma + 2 * sigma
        bb_lower = bb_ma - 2 * sigma
        bb_pct = (closes[-1] - bb_lower) / (bb_upper - bb_lower) if bb_upper > bb_lower else 0.5
        if 0.6 <= bb_pct <= 0.9: bonus += 2; details.append("BB" + str(int(bb_pct*100)))
    
    return bonus, ",".join(details), confidence

CAPITAL = 30000; TOP_N = 1  # 本金3万

stocks = get_stocks(); codes = [c for c, _ in stocks]; name_map = {c: n for c, n in stocks}
print("Fetching kline data for {} stocks...".format(len(codes)))
all_klines = get_historical_data(codes, "2025-05-15", datalen=120)  # 一年数据
print("Got {} stocks".format(len(all_klines)))

print("\n" + "=" * 70)
print("  V8i+ 一年回测 (2025.06 ~ 2026.06) 超跌反弹+移动止盈")
print("  本金: 30,000 | Top 1 | 自适应止盈止损 | 动态仓位")
print("=" * 70)

cash = CAPITAL; holdings = []; log = []; dlog = []; monthly = {}

for di, day in enumerate(trading_days):
    dd = {}
    for code, kls in all_klines.items():
        d = extract_date_data(kls, day)
        if d:
            d["code"] = code; d["name"] = name_map.get(code, "")
            dd[code] = d
    if not dd:
        cash += sum(h["cost"] for h in holdings)
        holdings = []
        dlog.append({"day": day, "total": cash, "pnl": cash - CAPITAL, "pct": (cash - CAPITAL) / CAPITAL * 100})
        continue
    
    # ---- SELL (V8 logic + trailing stop at 5%+) ----
    for h in holdings[:]:
        code = h["code"]
        if code not in dd: continue
        d = dd[code]; sp = None; reason = ""
        hold_days = h.get("hold_days", 1)
        max_hold = h.get("max_hold", 3)
        dyn_tp = h.get("dyn_tp", 5)
        dyn_sl = h.get("dyn_sl", -4)
        highest = h.get("highest", h["buy_price"])
        
        if d["high"] > highest: highest = d["high"]; h["highest"] = highest
        
        profit_from_high = (highest - h["buy_price"]) / h["buy_price"] * 100
        
        if d["high"] / h["buy_price"] - 1 >= dyn_tp / 100:
            sp = h["buy_price"] * (1 + dyn_tp / 100); reason = "止盈"
        elif d["low"] / h["buy_price"] - 1 <= dyn_sl / 100:
            sp = h["buy_price"] * (1 + dyn_sl / 100); reason = "止损"
        # Trailing stop only after 5%+ profit, protect 60% of gains
        elif profit_from_high >= 5:
            protected = h["buy_price"] + (highest - h["buy_price"]) * 0.4
            if d["low"] <= protected and protected > h["buy_price"]:
                sp = protected; reason = "移动止盈"
        
        if not sp:
            chg_from_buy = (d["close"] - h["buy_price"]) / h["buy_price"] * 100
            if hold_days >= max_hold:
                sp = d["close"]; reason = "到期平仓"
            elif hold_days >= 2 and chg_from_buy < -1:
                sp = d["close"]; reason = "走弱卖出"
            elif hold_days >= 2 and d["close"] < d.get("ma5", d["close"]):
                sp = d["close"]; reason = "跌破MA5"
        
        if sp:
            cash += h["shares"] * sp
            pnl = round(h["shares"] * sp - h["cost"], 2)
            log.append({"day": day, "act": "卖出", "code": code, "name": h["name"], "sp": round(sp, 2), "pnl": pnl, "pct": round((sp - h["buy_price"]) / h["buy_price"] * 100, 2), "r": reason, "held": hold_days})
            holdings.remove(h)
        else:
            h["hold_days"] = hold_days + 1
    
    # ---- OVERSOLD PRE-CHECK (before market) ----
    ma20_idx_early = get_idx_ma(idx_map, day, 20)
    ma60_idx_early = get_idx_ma(idx_map, day, 60)
    idx_close_early = idx_map[day]["close"] if day in idx_map else 0
    idx_slope5_early = get_idx_slope(idx_map, day, 5)
    
    is_bullish_early = ma20_idx_early and idx_close_early >= ma20_idx_early
    is_strong_early = is_bullish_early and idx_slope5_early > 0.3
    is_sideways_early = not is_bullish_early and ma60_idx_early and idx_close_early >= ma60_idx_early
    
    ml_early = ""
    if is_strong_early: ml_early = "BULLISH+"
    elif is_bullish_early: ml_early = "BULLISH"
    elif is_sideways_early: ml_early = "SIDEWAYS"
    elif ma60_idx_early and idx_close_early >= ma60_idx_early * 0.97 and idx_slope5_early > 0: ml_early = "WEAK+"
    else: ml_early = "BEAR"
    
    is_oversold_early = (ml_early in ("WEAK+", "SIDEWAYS") and idx_slope5_early < -1.0) or (ml_early == "BEAR" and ma60_idx_early and idx_close_early >= ma60_idx_early * 0.95 and idx_slope5_early < -1.5)
    
    if is_oversold_early and cash < CAPITAL * 0.15:
        # Sort holdings by profit (weakest first), sell enough to free ~15% capital
        sorted_holds = sorted(holdings[:], key=lambda h: (dd[h["code"]]["close"] - h["buy_price"]) / h["buy_price"] * 100 if h["code"] in dd else 999)
        for h in sorted_holds:
            if h["code"] not in dd: continue
            d = dd[h["code"]]
            pnl_pct = (d["close"] - h["buy_price"]) / h["buy_price"] * 100
            hold_days_os = h.get("hold_days", 1)
            sp = d["close"]
            cash += h["shares"] * sp
            pnl = round(h["shares"] * sp - h["cost"], 2)
            log.append({"day": day, "act": "卖出", "code": h["code"], "name": h["name"], "sp": round(sp, 2), "pnl": pnl, "pct": round(pnl_pct, 2), "r": "超跌调仓", "held": hold_days_os})
            holdings.remove(h)
            if cash >= CAPITAL * 0.15: break
    
    # ---- MARKET ----
    ma20_idx = get_idx_ma(idx_map, day, 20)
    ma60_idx = get_idx_ma(idx_map, day, 60)
    idx_close = idx_map[day]["close"] if day in idx_map else 0
    idx_slope5 = get_idx_slope(idx_map, day, 5)
    
    is_bullish = ma20_idx and idx_close >= ma20_idx
    is_strong = is_bullish and idx_slope5 > 0.3
    is_sideways = not is_bullish and ma60_idx and idx_close >= ma60_idx
    
    market_pos = 1.0; market_label = ""
    can_buy = (di < len(trading_days) - 1) and cash > 0
    
    if is_strong:
        market_pos = 1.0; market_label = "BULLISH+"
        log.append({"day": day, "act": "信息", "reason": "强势满仓"})
    elif is_bullish:
        market_pos = 1.0; market_label = "BULLISH"
    elif is_sideways:
        market_pos = 0.5; market_label = "SIDEWAYS"
        log.append({"day": day, "act": "信息", "reason": "横盘半仓"})
    elif ma60_idx and idx_close >= ma60_idx * 0.97 and idx_slope5 > 0:
        market_pos = 0.4; market_label = "WEAK+"
        log.append({"day": day, "act": "信息", "reason": "弱势40%仓"})
    else:
        can_buy = False; market_label = "BEAR"
    
    # Oversold market detection (for bounce plays)
    is_oversold = (market_label in ("WEAK+", "SIDEWAYS") and idx_slope5 < -1.0) or (market_label == "BEAR" and ma60_idx and idx_close >= ma60_idx * 0.95 and idx_slope5 < -1.5)
    if is_oversold and market_label == "BEAR":
        can_buy = True; market_pos = 0.25; market_label = "WEAK+"
        log.append({"day": day, "act": "信息", "reason": "超跌反弹25%仓"})
    elif is_oversold and market_label in ("WEAK+", "SIDEWAYS"):
        market_pos = min(market_pos, 0.25)
        log.append({"day": day, "act": "信息", "reason": "超跌反弹降仓25%"})
        log.append({"day": day, "act": "跳过", "reason": "大盘弱势"})
    
    if can_buy:
        candidates = []
        for code, d in dd.items():
            kls = all_klines.get(code, [])
            ki = None
            for j, k in enumerate(kls):
                if k["day"] == day: ki = j; break
            if ki is None or ki < 25: continue
            
            close = d["close"]; prev_close = d.get("prev_close", 0)
            if prev_close <= 0: continue
            chg = (close - prev_close) / prev_close * 100
            
            # ---- HARD FILTERS ----
            if is_oversold:
                if chg < -4.0 or chg > 5.5: continue
            else:
                if chg < 1.0 or chg > 5.5: continue
            amp = d.get("amp", 0)
            if is_oversold:
                if amp > 9.0: continue
            else:
                if amp > 6.0: continue
            cp = d.get("close_position", 1)
            br = d.get("body_ratio", 0)
            us = d.get("upper_shadow", 0)
            if is_oversold:
                if cp < 0.3: continue
            else:
                if cp < 0.55 or br < 0.25 or us > 0.45: continue
            if close < d.get("ma5", 0) and not is_oversold: continue
            if close < 4 or close > 80: continue
            vt = d.get("vol_trend", 0) or 0
            if vt < -5 and not is_oversold: continue
            
            closes_list = [k["close"] for k in kls[max(0,ki-25):ki+1]]
            highs_list = [k["high"] for k in kls[max(0,ki-25):ki+1]]
            vols_list = [k["volume"] for k in kls[max(0,ki-25):ki+1]]
            
            # RSI(6)
            gains = []; losses = []
            for i in range(1, min(7, len(closes_list))):
                diff = closes_list[-i] - closes_list[-i-1]
                if diff >= 0: gains.append(diff)
                else: losses.append(abs(diff))
            avg_gain = sum(gains)/6 if gains else 0.0001
            avg_loss = sum(losses)/6 if losses else 0.0001
            rsi6 = 100 - (100 / (1 + avg_gain/avg_loss)) if avg_loss > 0 else 100
            
            avg_vol5 = sum(vols_list[-6:-1]) / 5 if len(vols_list) >= 6 else vols_list[-1]
            vol_ratio = vols_list[-1] / avg_vol5 if avg_vol5 > 0 else 1
            
            green_days = 0
            for j in range(ki, max(-1, ki-3), -1):
                if j >= 0 and kls[j]["close"] >= kls[j]["open"]:
                    green_days += 1
                else: break
            if ki > 0 and kls[ki]["close"] < kls[ki]["open"]:
                green_days = 0
            
            high20 = max(highs_list[-20:]) if len(highs_list) >= 20 else max(highs_list)
            dist_high = (close - high20) / high20 * 100 if high20 > 0 else 0
            
            amt_val = vols_list[-1] * close / 100
            amt_yi = amt_val / 1e8
            
            # ---- IMPROVED FILTERS ----
            if is_oversold:
                # Oversold: relaxed filters for bounce candidates
                if rsi6 < 20 or rsi6 > 72: continue
                if vol_ratio < 0.3 or vol_ratio > 5.0: continue
                if rsi6 < 30 and dist_high > -15 and green_days > 0: continue
                if dist_high < -30: continue
                if amt_val < 20000000: continue
            else:
                if rsi6 < 45 or rsi6 > 72: continue
                if vol_ratio < 1.0 or vol_ratio > 4.0: continue
                if green_days < 1: continue
                if dist_high < -8: continue
                if amt_val < 50000000: continue
            
            # ---- TREND BONUS (V8 + new indicators) ----
            tbonus, tdetail, tconf = trend_bonus_v8(kls, ki)
            
            # ---- V8 SCORING (proven foundation) ----
            score = 0
            if is_oversold and chg < 0:
                # Oversold bounce: big bonus for reversal signals
                score += 20
                ls = d.get("lower_shadow", 0)
                if ls > 0.35: score += 10  # long lower shadow = strong support
                if cp > 0.5: score += 6
                if chg > -2: score += 5  # mild drop, not panic
                elif chg > -4: score += 3
                if d.get("vol_ratio", 0) > 1.2: score += 5  # volume = capitulation
                if close > d.get("ma5", 0): score += 4  # above MA5 is good
                # Bonus for oversold RSI
                if 25 <= rsi6 <= 40: score += 6  # oversold sweet spot
            
            # RSI sweet spot
            if is_oversold and chg < 0:
                if 25 <= rsi6 <= 45: score += 6  # oversold RSI is a feature
                elif 45 <= rsi6 <= 55: score += 4
                elif rsi6 < 25: score += 3  # extremely oversold
            else:
                if 55 <= rsi6 <= 65: score += 8
                elif 50 <= rsi6 <= 70: score += 5
                else: score += 2
            
            # Volume ratio
            if is_oversold and chg < 0:
                if 0.5 <= vol_ratio <= 1.5: score += 5  # stabilization volume
                elif vol_ratio > 1.5: score += 4  # elevated = attention
                elif vol_ratio < 0.5: score += 2  # very low volume
            else:
                if 1.3 <= vol_ratio <= 2.5: score += 7
                elif 1.0 <= vol_ratio <= 3.0: score += 4
                else: score += 1
            
            # Dist from high
            if is_oversold and chg < 0:
                if dist_high >= -5: score += 4
                elif dist_high >= -15: score += 3
                elif dist_high >= -25: score += 1  # deep drop = potential bounce
            else:
                if dist_high >= -1: score += 5
                elif dist_high >= -3: score += 3
                elif dist_high >= -5: score += 1
            
            # Above MA20
            if d.get("ma20") and close > d["ma20"]: score += 4
            elif is_oversold and close > d.get("ma20", 0) * 0.9: score += 2  # near MA20
            
            # Change quality
            if is_oversold and chg < 0:
                if chg > -1: score += 5  # nearly flat = stabilization
                elif chg > -3: score += 3
            else:
                if 2.5 <= chg <= 4.5: score += 5
                elif 1.5 <= chg <= 5: score += 3
            
            # Trend alignment
            ma5_ = d.get("ma5",0); ma10_ = d.get("ma10",0); ma20_ = d.get("ma20",0)
            if ma5_ > ma10_ > ma20_: score += 4
            elif ma5_ > ma10_: score += 2
            
            # Green days
            score += min(green_days, 3)
            
            # Amplitude
            if amp <= 3: score += 3
            elif amp <= 4: score += 1
            
            # Limit-up
            if d.get("recent_limit_up"): score += 3
            
            # Turnover
            if amt_yi >= 5: score += 3
            elif amt_yi >= 1: score += 1
            
            # Money flow
            if vt > 10 and green_days >= 2: score += 3
            
            # ---- V8 TREND BONUS ----
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
            
            # ---- ADVICE ----
            advice = "HOLD"
            if is_oversold and chg < 0 and tconf >= -1 and score >= 20:
                advice = "超跌反弹"
            elif tconf >= 2 and mom5 > 4 and market_label in ("BULLISH+", "BULLISH"):
                advice = "强烈买入"
            elif tconf >= 1 and mom5 > 1 and market_label in ("BULLISH+", "BULLISH", "SIDEWAYS"):
                advice = "买入"
            elif tconf >= 0 and mom5 > -1 and market_label in ("BULLISH+", "BULLISH", "SIDEWAYS", "WEAK+"):
                advice = "谨慎买入"
            elif is_oversold and tconf >= -2 and score >= 20:
                advice = "超跌反弹"
            elif market_label in ("BULLISH+", "BULLISH"):
                advice = "观望"
            else:
                continue
            
            # ---- DYNAMIC TP/SL ----
            atr_sum = 0
            for j in range(max(0, ki-13), ki+1):
                atr_sum += kls[j]["high"] - kls[j]["low"]
            atr14 = atr_sum / 14 if ki >= 13 else 2
            atr_pct = atr14 / close * 100
            
            if is_oversold and chg < 0:
                # Oversold: tighter SL (already down), modest TP
                dyn_tp = 4.0; dyn_sl = -3.5; max_hold = 2
            elif atr_pct > 5:
                dyn_tp = 7.0; dyn_sl = -6.0; max_hold = 2
            elif atr_pct > 3:
                dyn_tp = 6.0; dyn_sl = -5.0; max_hold = 3
            else:
                dyn_tp = 5.0; dyn_sl = -4.0; max_hold = 4
            
            if advice == "超跌反弹":
                # Oversold: keep tight TP/SL, short hold
                dyn_tp = min(4.5, dyn_tp)
                dyn_sl = max(-3.0, dyn_sl)
                max_hold = min(2, max_hold)
            elif advice == "强烈买入":
                dyn_tp = max(4.0, dyn_tp - 1)
                max_hold = min(6, max_hold + 2)
            elif advice == "买入":
                max_hold = min(5, max_hold + 1)
            elif advice == "谨慎买入":
                dyn_tp = min(7.0, dyn_tp + 1)
                dyn_sl = max(-3.0, dyn_sl + 1)
            
            # Market bully
            if market_label == "BULLISH+" and advice in ("强烈买入", "买入"):
                dyn_sl = dyn_sl - 0.5
                max_hold = min(7, max_hold + 1)
                score += 4
            
            if market_label == "BULLISH" and advice == "强烈买入":
                max_hold = min(6, max_hold + 1)
                score += 2
            
            candidates.append((code, d.get("name", ""), score, close, chg, advice, dyn_tp, dyn_sl, max_hold, atr_pct, green_days, round(rsi6,0), round(vol_ratio,1), tdetail, tconf, mom5))
        
        candidates.sort(key=lambda x: x[2], reverse=True)
        picks = candidates[:TOP_N]
        
        if picks and cash > 0:
            for code, name, score, close, chg, advice, dyn_tp, dyn_sl, max_hold, atr_pct, gd, rsi_v, vr_v, tdetail, tconf, mom5 in picks:
                if advice == "超跌反弹":
                    pos_mult = min(0.30, market_pos * 0.8)
                elif advice == "强烈买入":
                    pos_mult = min(1.0, market_pos * 1.0)
                elif advice == "买入":
                    pos_mult = min(1.0, market_pos * 0.9)
                elif advice == "谨慎买入":
                    pos_mult = min(0.6, market_pos * 0.5)
                else:
                    pos_mult = min(0.3, market_pos * 0.3)
                
                per = cash * pos_mult
                min_lot = close * 100
                if close > 0 and (per >= min_lot or (is_oversold and per >= min_lot * 0.5)):
                    shares = int(per / close / 100) * 100
                    if shares >= 100:
                        cost = shares * close
                        if cost <= cash:
                            cash -= cost
                            holdings.append({"code": code, "name": name, "buy_price": close, "shares": shares, "cost": cost, "hold_days": 1, "max_hold": max_hold, "dyn_tp": dyn_tp, "dyn_sl": dyn_sl, "highest": close, "advice": advice})
                            log.append({"day": day, "act": "买入", "code": code, "name": name, "price": round(close, 2), "shares": shares, "cost": round(cost, 2), "score": score, "rsi": rsi_v, "vr": vr_v, "advice": advice, "止盈": dyn_tp, "止损": dyn_sl, "hold": max_hold, "trend": tdetail, "market": market_label, "pos_pct": round(pos_mult*100)})

    hv = sum(dd[h["code"]]["close"] * h["shares"] if h["code"] in dd else h["cost"] for h in holdings)
    total = cash + hv; pnl = total - CAPITAL
    dlog.append({"day": day, "total": round(total, 2), "pnl": round(pnl, 2), "pct": round(pnl / CAPITAL * 100, 2)})
    month_key = day[:7]
    if month_key not in monthly or day >= monthly[month_key]["day"]:
        monthly[month_key] = {"day": day, "total": round(total, 2), "pnl": round(pnl, 2)}

# Print
for t in log:
    if t["act"] == "跳过":
        print("  {} 跳过 [{}]".format(t["day"], t["reason"]))
    elif t["act"] == "信息":
        print("  {} 信息 [{}]".format(t["day"], t["reason"]))
    elif t["act"] == "买入":
        tp = t.get("止盈", 5); sl = t.get("止损", -5); h = t.get("hold", 3)
        trend = t.get("trend", ""); pos = t.get("pos_pct", 100)
        print("  {} 买入 {} {:8s} @{:7.2f} x{:4d} sc={:.0f} [{}] {}% TP{:.0f}% SL{:+.0f}% H{} {}".format(t["day"], t["code"], t["name"], t["price"], t["shares"], t["score"], t["advice"], pos, tp, sl, h, trend))
    else:
        s = "+{:,.0f}".format(t["pnl"]) if t["pnl"] >= 0 else "{:,.0f}".format(t["pnl"])
        r = " [{}]".format(t.get("r", "")) if t.get("r") else ""
        print("  {} 卖出 {} {:8s} @{:7.2f} PnL={:>8} ({:+.1f}%){}".format(t["day"], t["code"], t["name"], t["sp"], s, t["pct"], r))

print("\n  {:<14} {:>10} {:>10} {:>8}".format("Day", "Value", "P&L", "Return"))
for d in dlog:
    s = "+{:,.0f}".format(d["pnl"]) if d["pnl"] >= 0 else "{:,.0f}".format(d["pnl"])
    print("  {:<14} {:>10,.0f} {:>10} {:>+7.1f}%".format(d["day"], d["total"], s, d["pct"]))

print("\n  Monthly:")
prev_val = CAPITAL
for mk in sorted(monthly.keys()):
    m = monthly[mk]; mpnl = m["total"] - prev_val; mpct = mpnl / prev_val * 100
    s = "+{:,.0f}".format(mpnl) if mpnl >= 0 else "{:,.0f}".format(mpnl)
    print("  {:<10} {:>10,.0f} {:>12} {:>+9.1f}%".format(mk, m["total"], s, mpct))
    prev_val = m["total"]

if dlog:
    final = dlog[-1]["total"]; fp = final - CAPITAL
    bn = sum(1 for t in log if t["act"] == "买入"); sn = sum(1 for t in log if t["act"] == "卖出")
    skips = sum(1 for t in log if t["act"] == "跳过")
    wn = sum(1 for t in log if t["act"] == "卖出" and t["pnl"] > 0)
    tp_cnt = sum(1 for t in log if t.get("r") == "止盈"); sl_cnt = sum(1 for t in log if t.get("r") == "止损")
    avg_win = sum(t["pnl"] for t in log if t["act"] == "卖出" and t["pnl"] > 0) / wn if wn > 0 else 0
    avg_loss = sum(t["pnl"] for t in log if t["act"] == "卖出" and t["pnl"] <= 0) / (sn - wn) if sn > wn else 0
    peak_p = CAPITAL; max_dd = 0
    for d in dlog:
        if d["total"] > peak_p: peak_p = d["total"]
        dd_pct = (d["total"] - peak_p) / peak_p * 100
        if dd_pct < max_dd: max_dd = dd_pct

    print("\n" + "=" * 70)
    print("  V8i FINAL: {:,.0f} -> {:,.0f} | P&L: {:+,.0f} ({:+.1f}%)".format(CAPITAL, final, fp, fp/CAPITAL*100))
    print("  MaxDD: {:+.1f}% | Trades: {}B/{}S | Skips: {} | Win: {:.0f}%({}/{}) | TP:{} SL:{}".format(max_dd, bn, sn, skips, wn/sn*100 if sn else 0, wn, sn, tp_cnt, sl_cnt))
    print("  AvgWin: {:+,.0f} | AvgLoss: {:+,.0f}".format(avg_win, avg_loss))
    print("=" * 70)
