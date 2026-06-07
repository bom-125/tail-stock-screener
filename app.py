# -*- coding: utf-8 -*-
import json,sys,os,time,re,threading,math
from datetime import datetime,timedelta
from http.server import HTTPServer,BaseHTTPRequestHandler
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
CACHE={}
PROGRESS={"total":0,"done":0,"msg":""}

def f(s):
    try: return float(s)
    except: return 0.0

def get_stocks():
    if "stocks" in CACHE: return CACHE["stocks"]
    all_stocks=[]
    for p in range(1,70):
        url=(f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             f"Market_Center.getHQNodeData?page={p}&num=100&sort=symbol&asc=1"
             f"&node=hs_a&symbol=&_s_r_a=auto")
        try:
            r=S.get(url,timeout=15); r.encoding="gb2312"
            data=json.loads(r.text)
            if not data: break
            for x in data:
                c,n=x.get("code",""),x.get("name","")
                if c and n and all(k not in n.upper() for k in ["ST","\u9000"]):
                    all_stocks.append((c,n))
        except: break
    CACHE["stocks"]=all_stocks
    return all_stocks

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

def get_historical_data(codes, target_date, datalen=30):
    results={}
    total=len(codes)
    PROGRESS["total"]=total; PROGRESS["done"]=0
    PROGRESS["msg"]=f"Getting {total} stocks..."
    def worker(code):
        sym=("sh" if code.startswith("6") else "sz")+code
        kls=fetch_kline_sina(sym, datalen)
        return code,kls
    with ThreadPoolExecutor(max_workers=30) as ex:
        futs={ex.submit(worker,c):c for c in codes}
        for i,fut in enumerate(as_completed(futs)):
            code,kls=fut.result()
            if kls: results[code]=kls
            PROGRESS["done"]=i+1
            if (i+1)%500==0:
                PROGRESS["msg"]=f"Got {i+1}/{total}..."
    PROGRESS["msg"]=f"Done {len(results)}/{total}"
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
    return {"open":today["open"],"close":today["close"],"high":today["high"],"low":today["low"],"volume":today["volume"],"prev_close":prev_close,"ma5":ma5,"ma10":ma10,"ma20":ma20,"ma60":ma60,"ma5_slope":calc_ma_slope(5),"ma10_slope":calc_ma_slope(10),"ma20_slope":calc_ma_slope(20),"amp":amp,"body_ratio":body_ratio,"upper_shadow":upper_shadow,"close_position":close_position,"recent_limit_up":recent_limit_up,"vol_trend":vol_trend,"date":today["day"]}

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

def screen(ms=50, topn=50, date_str=None):
    stocks=get_stocks()
    codes=[c for c,_ in stocks]
    if date_str:
        kline_data=get_historical_data(codes, date_str)
        results=[]
        for code,name in stocks:
            if code not in kline_data: continue
            kls=kline_data[code]
            dd=extract_date_data(kls, date_str)
            if dd is None: continue
            dd["name"]=name; dd["price"]=dd["close"]
            dd["turnover"]=0; dd["amt"]=dd.get("volume",0)*dd["close"]/100
            dd["mktcap"]=0
            sc,det=score_v5(dd)
            if sc>=ms:
                results.append({"code":code,"name":name,"score":sc,"price":dd["close"],"change_pct":((dd["close"]-dd["prev_close"])/dd["prev_close"]*100) if dd.get("prev_close") and dd["prev_close"]>0 else 0,"amount":dd.get("volume",0)*dd["close"]/100,"turnover":0,"trade_date":date_str,"advice":det.get("advice",""),"risk":det.get("risk",""),"details":det})
        results.sort(key=lambda x:x["score"],reverse=True)
        return results[:topn]
    else:
        quotes=get_quotes(codes)
        results=[]
        for code,name in stocks:
            if code not in quotes: continue
            d=quotes[code]
            sc,det=score_v5(d)
            if sc>=ms:
                results.append({"code":code,"name":name,"score":sc,"price":d["price"],"change_pct":det.get("chg",0),"amount":d["amt"],"turnover":d.get("turnover",0),"trade_date":d.get("date",""),"advice":det.get("advice",""),"risk":det.get("risk",""),"details":det})
        results.sort(key=lambda x:x["score"],reverse=True)
        return results[:topn]

HTML=r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>尾盘选股器 v5 全策略版</title>
<style>
:root{
  --bg:#060912;--card:#0d1117;--card2:#131820;
  --border:#1a2332;--text:#c9d1d9;--muted:#6e7681;
  --accent:#58a6ff;--green:#3fb950;--red:#f85149;
  --orange:#d29922;--gold:#e3b341;--purple:#a371f7;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;justify-content:center;padding:24px 16px;background-image:radial-gradient(ellipse at 50% 0%,#0d1525 0%,var(--bg) 70%)}
.container{max-width:1200px;width:100%}
.header{text-align:center;margin-bottom:8px}
.header h1{font-size:26px;font-weight:800;letter-spacing:1px;background:linear-gradient(135deg,#58a6ff,#a371f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header .sub{color:var(--muted);font-size:12px;margin-top:4px}
.banner{background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:12px;font-size:12px;color:var(--muted);line-height:1.7;display:grid;grid-template-columns:1fr 1fr;gap:8px 24px}
.banner b{color:var(--text)}
.banner .hl{color:var(--gold)}
.banner .dg{color:var(--red)}
.controls{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px 20px;margin-bottom:10px;display:flex;gap:12px;align-items:end;flex-wrap:wrap}
.field{display:flex;flex-direction:column;gap:3px}
.field label{font-size:11px;color:var(--muted);font-weight:500}
.field input,.field select{background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:7px;font-size:13px;font-family:inherit;outline:none;transition:border-color .2s}
.field input:focus,.field select:focus{border-color:var(--accent)}
.field input[type="date"]{color-scheme:dark}
.btn{background:linear-gradient(135deg,#1a6ff5,#5b3fd9);color:#fff;border:none;padding:9px 24px;border-radius:7px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;transition:opacity .2s,transform .1s;letter-spacing:.5px}
.btn:hover{opacity:.9;transform:translateY(-1px)}
.btn:disabled{opacity:.4;transform:none;cursor:not-allowed}
.btn-outline{background:transparent;border:1px solid var(--border);color:var(--text);padding:9px 18px;border-radius:7px;font-size:13px;cursor:pointer;font-family:inherit;transition:.2s}
.btn-outline:hover{border-color:var(--accent);color:var(--accent)}
.status{text-align:center;padding:12px;color:var(--muted);font-size:12px}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.progress-bar{width:100%;height:3px;background:var(--border);border-radius:2px;margin-top:6px;overflow:hidden}
.progress-bar div{height:100%;background:var(--accent);transition:width .3s}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:12px}
.card-h{padding:10px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;font-size:13px;font-weight:600}
.card-h span{color:var(--muted);font-weight:400}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:8px 10px;font-size:10px;color:var(--muted);font-weight:500;text-transform:uppercase;border-bottom:1px solid var(--border);white-space:nowrap;letter-spacing:.3px}
td{padding:9px 10px;font-size:12px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover{background:#151c28}
.score{font-weight:700;font-size:14px}
.s-A{color:#ff6b6b}.s-B{color:#ff922b}.s-C{color:var(--gold)}
.s-D{color:var(--accent)}.s-E{color:var(--muted)}.s-F{color:#444}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;margin-right:4px;letter-spacing:.3px}
.bg-A{background:#2d1515;color:#ff6b6b}.bg-B{background:#2d2010;color:#ff922b}
.bg-C{background:#2d2410;color:var(--gold)}.bg-D{background:#10202d;color:var(--accent)}
.bg-E{background:#1a1a1a;color:var(--muted)}
.detail-btn{background:none;border:1px solid var(--border);color:var(--accent);padding:2px 8px;border-radius:5px;font-size:10px;cursor:pointer;font-family:inherit;transition:.2s}
.detail-btn:hover{background:var(--accent);color:#fff}
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);z-index:100;justify-content:center;align-items:center;padding:20px}
.modal-overlay.active{display:flex}
.modal{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px 24px;max-width:500px;width:100%;max-height:80vh;overflow-y:auto}
.modal h3{font-size:16px;margin-bottom:12px;color:var(--accent)}
.modal .row{display:flex;justify-content:space-between;padding:6px 0;font-size:12px;border-bottom:1px solid #ffffff08}
.modal .row .label{color:var(--muted)}
.modal .row .val{font-weight:600}
.modal .close{float:right;background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer;line-height:1}
.modal .advice-box{background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:12px;margin-top:12px;font-size:12px;line-height:1.6}
.modal .advice-box b{color:var(--gold)}
.risk-tag{background:#2d1515;color:var(--red);padding:1px 6px;border-radius:4px;font-size:10px;margin-left:4px}
.no-risk{color:var(--green);font-size:10px}
@media(max-width:768px){.banner{grid-template-columns:1fr}.controls{flex-direction:column;align-items:stretch}.field input,.field select{width:100%}th{font-size:9px}td{font-size:11px}}
</style>
</head>
<body>
<div class="container">
<div class="header"><h1>尾盘选股器 v5</h1><p class="sub">7维AI评分 | 14:30-15:00尾盘T+1短线套利 | A股全市场</p></div>
<div class="banner">
<div><b>评分维度：</b> <span class="hl">Price</span> | <span class="hl">Volume</span> | <span class="hl">Trend MA</span> | <span class="hl">K-Line</span></div>
<div><b>优选条件：</b> Gain 3-5% | Amp <=5% | Bull MA | TO 5-10% | MCap 50-200B</div>
<div><b>风控排除：</b> <span class="dg">ST/退市</span> | <span class="dg">长上影出货</span> | <span class="dg">无量空涨</span> | <span class="dg">高位放量</span></div>
<div><b>加分项：</b> 涨停基因 | 低位启动 | 放量上涨 | 独立抗跌</div>
</div>
<div class="controls">
<div class="field"><label>交易日期</label><input type="date" id="td" style="width:150px"></div>
<div class="field"><label>评分门槛</label><select id="ms" style="width:130px"><option value="40">40 - 宽松</option><option value="50" selected>50 - 标准</option><option value="55">55 - 严格</option><option value="60">60 - 优质</option><option value="65">65 - 极品</option></select></div>
<button class="btn" id="btn" onclick="scan()">开始扫描</button>
<button class="btn-outline" onclick="setToday()">今日</button>
<span style="font-size:11px;color:var(--muted);margin-left:8px" id="tip"></span>
</div>
<div class="status" id="status"><span style="font-size:13px">点击「开始扫描」或「今日」| 实时约30秒 | 历史约1-2分钟</span></div>
<div class="card" id="card" style="display:none">
<div class="card-h"><b id="rd"></b><span id="rc"></span></div>
<div class="table-wrap"><table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>综合分</th><th>现价</th><th>涨幅</th><th>振幅</th><th>成交额</th><th>换手</th><th>市值</th><th>趋势</th><th>操作建议</th><th>详情</th></tr></thead><tbody id="tb"></tbody></table></div>
</div>
</div>

<div class="modal-overlay" id="modal-overlay" onclick="closeModal(event)">
<div class="modal" id="modal-content"></div>
</div>

<script>
function getLatestTradeDay(d){var day=d.getDay();if(day===0){d.setDate(d.getDate()-2)}else if(day===6){d.setDate(d.getDate()-1)}return d.toISOString().split("T")[0]}
document.getElementById("td").value=getLatestTradeDay(new Date());
function setToday(){var d=new Date();document.getElementById("td").value=getLatestTradeDay(d);scan()}

async function scan(){
var ms=document.getElementById("ms").value,td=document.getElementById("td").value;
var isToday=(td===getLatestTradeDay(new Date()));
var b=document.getElementById("btn"),s=document.getElementById("status"),c=document.getElementById("card"),tip=document.getElementById("tip");
b.disabled=true;b.textContent="扫描中...";s.innerHTML="<span class=\"spinner\"></span>Scanning A-Share market...<div class=\"progress-bar\"><div id=\"pb\" style=\"width:5%\"></div></div>";c.style.display="none";tip.textContent="";
var url="/api/scan?min_score="+ms;if(!isToday){url+="&date="+td}
try{
var resp=await fetch(url,{signal:AbortSignal.timeout(180000)}),d=await resp.json();
if(d.error){s.textContent="错误: "+d.error}
else if(d.count===0){s.innerHTML="<span style=\"color:var(--orange)\"\>No results. Try lowering min score.</span>"}
else{s.innerHTML="<span style=\"color:var(--green)\"\>Scan complete</span>";render(d)}
tip.textContent="耗时 "+(d.elapsed||0)+"秒"}catch(e){s.textContent="连接失败: "+e.message;tip.textContent=""}
b.disabled=false;b.textContent="开始扫描"}

function render(d){
var card=document.getElementById("card");card.style.display="block";
document.getElementById("rd").textContent="扫描结果 | "+d.date;
document.getElementById("rc").textContent=d.count+" 只 | 耗时"+d.elapsed+"秒";
var h="";
d.results.forEach(function(x,i){
var dt=x.details||{},sc=x.score;
var scClass,label,labelClass;
if(sc>=80){scClass="s-A";label="强烈推荐";labelClass="bg-A"}
else if(sc>=70){scClass="s-B";label="推荐关注";labelClass="bg-B"}
else if(sc>=60){scClass="s-C";label="适当关注";labelClass="bg-C"}
else if(sc>=50){scClass="s-D";label="一般关注";labelClass="bg-D"}
else{scClass="s-E";label="观望";labelClass="bg-E"}
var chg=x.change_pct||0;
var chgStr=(chg>=0?"+":"")+chg.toFixed(2)+"%";
var chgColor=chg>=3?"var(--green)":chg>=0?"var(--accent)":"var(--red)";
var amtStr=(x.amount/1e8).toFixed(1)+"亿";
var toStr=(x.turnover||0).toFixed(1)+"%";
var mktStr=dt["mkt_cap_yi"]||"--";
var trendStr=dt["ma_arrange"]||"--";
var riskStr=dt["risk"]||"";var hasRisk=riskStr&&riskStr!=="Clean";
window["_d"+i]=x;
h+="<tr><td>"+(i+1)+"</td>";
h+="<td style="font-weight:600;color:var(--accent)">"+x.code+"</td>";
h+="<td>"+x.name+"</td>";
h+="<td><span class="score "+scClass+"">"+x.score+"</span></td>";
h+="<td>"+(x.price||0).toFixed(2)+"</td>";
h+="<td style="color:"+chgColor+";font-weight:600">"+chgStr+"</td>";
h+="<td>"+(dt["amp"]||0).toFixed(1)+"%</td>";
h+="<td>"+amtStr+"</td><td>"+toStr+"</td><td>"+mktStr+"</td>";
h+="<td style="font-size:11px">"+trendStr+"</td>";
h+="<td style="max-width:160px;font-size:11px"><span class="badge "+labelClass+"">"+label+"</span>";
if(hasRisk){h+="<span class="risk-tag">!</span>"}h+="</td>";
h+="<td><button class="detail-btn" onclick="showDetail("+i+")">+</button></td></tr>"});
document.getElementById("tb").innerHTML=h}

function showDetail(i){
var x=window["_d"+i],dt=x.details||{};
var overlay=document.getElementById("modal-overlay"),content=document.getElementById("modal-content");
var sc=x.score;
var grade=sc>=80?"A级 - 强烈推荐":sc>=70?"B级 - 推荐关注":sc>=60?"C级 - 适当关注":sc>=50?"D级 - 一般关注":"E级 - 观望";
var rows=[
["代码",x.code],["名称",x.name],["综合评分",x.score+" ("+grade+")"],
["现价",(x.price||0).toFixed(2)+"元"],["涨幅",(x.change_pct||0).toFixed(2)+"%"],
["振幅",(dt["amp"]||0).toFixed(1)+"%"],
["价格评价",dt["price_eval"]||"--"],["振幅评价",dt["amp_eval"]||"--"],
["换手评价",dt["to_eval"]||"--"],["量能评价",dt["amt_eval"]||"--"],
["量能趋势",dt["vol_trend_eval"]||"--"],
["K线实体",dt["body_eval"]||"--"],["上影线",dt["shadow_eval"]||"--"],
["收盘位置",dt["close_eval"]||"--"],
["均线排列",dt["ma_arrange"]||"--"],["均线方向",dt["ma_dir"]||"--"],
["均线发散",dt["ma_spread"]||"--"],
["流通市值",dt["mkt_cap_yi"]||"--"],["市值评价",dt["mkt_eval"]||"--"],
["涨停基因",dt["limit_gene"]||"--"],["52周位置",dt["pos_52w"]||"--"],
["低位启动",dt["low_start"]||"--"],["价量配合",dt["vol_price"]||"--"],
["风险提示",dt["risk"]||"--"]
];
var rowHtml=rows.map(function(r){return "<div class=\"row\"><span class=\"label\">"+r[0]+"</span><span class=\"val\">"+r[1]+"</span></div>"}).join("");
content.innerHTML="<button class=\"close\" onclick=\"document.getElementById("modal-overlay").classList.remove("active")\">&times;</button><h3>"+x.code+" "+x.name+"</h3>"+rowHtml+"<div class=\"advice-box\"><b>操作建议：</b><br>"+dt["advice_detail"]+"</div>";
overlay.classList.add("active")}

function closeModal(e){if(e.target===document.getElementById("modal-overlay")){document.getElementById("modal-overlay").classList.remove("active")}}
document.addEventListener("keydown",function(e){if(e.key==="Escape"){document.getElementById("modal-overlay").classList.remove("active")}})
</script>
</body>
</html>
'''

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _s(self,b,ct="text/html; charset=utf-8",code=200):
        b=b.encode("utf-8") if isinstance(b,str) else b
        self.send_response(code)
        self.send_header("Content-Type",ct)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Content-Length",str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if self.path in ("/","/index.html"): self._s(HTML)
        elif self.path.startswith("/api/scan"):
            qs=parse_qs(urlparse(self.path).query)
            ms=int(qs.get("min_score",[50])[0])
            date_str=qs.get("date",[None])[0]
            t0=time.time()
            try:
                r=screen(ms=ms, date_str=date_str)
                td=date_str or datetime.now().strftime("%Y-%m-%d")
                data={"date":td,"count":len(r),"elapsed":round(time.time()-t0,1),"results":r,"mode":"Historical" if date_str else "Live"}
            except Exception as e:
                import traceback
                data={"error":str(e),"trace":traceback.format_exc()}
            self._s(json.dumps(data,ensure_ascii=False),"application/json; charset=utf-8")
        elif self.path.startswith("/api/progress"):
            self._s(json.dumps(PROGRESS,ensure_ascii=False),"application/json; charset=utf-8")
        else: self._s("404",code=404)

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--serve",action="store_true")
    p.add_argument("--port",type=int,default=5000)
    p.add_argument("--min-score",type=int,default=50)
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
        HTTPServer(("0.0.0.0",port),H).serve_forever()
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
