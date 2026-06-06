# -*- coding: utf-8 -*-
"""尾盘选股 Web 服务 - 本地/云端通用"""
import os, sys

if os.name == "nt":
    for pv in ["HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"]:
        os.environ.pop(pv, None)
    os.environ["NO_PROXY"] = "*"
    import requests as _r
    _o = _r.Session.__init__
    def _ni(s): _o(s); s.trust_env = False
    _r.Session.__init__ = _ni

from flask import Flask, render_template, jsonify, Response, stream_with_context
from engine import run_screen, is_market_open
from datetime import datetime
import threading, time, json, queue, socket

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

sse_queues, sse_lock = [], threading.Lock()
cached_result, cache_lock, last_update = None, threading.Lock(), None

def get_refresh_interval():
    now = datetime.now()
    if now.weekday() >= 5: return 300
    tail_s = now.replace(hour=14, minute=30, second=0)
    tail_e = now.replace(hour=15, minute=5, second=0)
    if tail_s <= now <= tail_e: return 30
    morning_s = now.replace(hour=9, minute=30, second=0)
    afternoon_e = now.replace(hour=15, minute=5, second=0)
    if morning_s <= now <= afternoon_e: return 60
    return 300

def broadcast_sse(data):
    with sse_lock:
        dead = [q for q in sse_queues if not (lambda q: q.put_nowait(data) or True)(q) if False]
        for q in dead: sse_queues.remove(q)
    # Simpler broadcast
    with sse_lock:
        for q in sse_queues[:]:
            try: q.put_nowait(data)
            except: pass

def background_screen():
    global cached_result, last_update
    while True:
        interval = get_refresh_interval()
        if is_market_open() or datetime.now().weekday() < 5:
            try:
                result = run_screen()
                with cache_lock:
                    cached_result = result
                    last_update = datetime.now().strftime("%H:%M:%S")
                push = json.dumps({"type":"update","data":result,"timestamp":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"market_open":is_market_open()}, ensure_ascii=False)
                broadcast_sse(push)
            except Exception as e:
                print(f"BG error: {e}")
        time.sleep(interval)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/screen")
def api_screen():
    with cache_lock: r = cached_result
    if r is None: return jsonify({"success":False,"message":"waiting","stocks":[]})
    return jsonify(r)

@app.route("/api/refresh")
def api_refresh():
    result = run_screen()
    global cached_result, last_update
    with cache_lock:
        cached_result = result
        last_update = datetime.now().strftime("%H:%M:%S")
    push = json.dumps({"type":"update","data":result,"timestamp":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"market_open":is_market_open()}, ensure_ascii=False)
    broadcast_sse(push)
    return jsonify(result)

@app.route("/api/status")
def api_status():
    with cache_lock: count = len(cached_result["stocks"]) if cached_result and "stocks" in cached_result else 0
    return jsonify({"market_open":is_market_open(),"last_update":last_update,"stocks_count":count,"refresh_interval":get_refresh_interval()})

@app.route("/api/stream")
def api_stream():
    q = queue.Queue(maxsize=5)
    with sse_lock: sse_queues.append(q)
    def gen():
        try:
            with cache_lock:
                if cached_result:
                    yield "data: " + json.dumps({"type":"update","data":cached_result,"timestamp":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"market_open":is_market_open()}, ensure_ascii=False) + "\n\n"
            while True:
                try:
                    data = q.get(timeout=30)
                    yield "data: " + data + "\n\n"
                except queue.Empty:
                    yield "data: " + json.dumps({"type":"heartbeat","timestamp":datetime.now().isoformat()}) + "\n\n"
        except GeneratorExit:
            with sse_lock:
                if q in sse_queues: sse_queues.remove(q)
    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})

if __name__ == "__main__":
    threading.Thread(target=background_screen, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    is_cloud = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RENDER"))
    print(f"Starting on 0.0.0.0:{port} [{'CLOUD' if is_cloud else 'LOCAL'}]")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)