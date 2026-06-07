"""
尾盘选股 Web 服务 - SSE实时推送版
移动端响应式界面 + 尾盘时段高频刷新
"""
import sys, os
from flask import Flask, render_template, jsonify, request, Response, stream_with_context
from engine import run_screen, is_market_open, ScreenerConfig, run_historical_screen
from datetime import datetime
import threading
import time
import json
import queue
import sys, os

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# SSE 消息队列
sse_queues = []
sse_lock = threading.Lock()

# 缓存
cached_result = None
cache_lock = threading.Lock()
last_update = None

# 刷新间隔：尾盘时段30秒，其他时段2分钟
def get_refresh_interval():
    now = datetime.now()
    if now.weekday() >= 5:
        return 300  # 周末不频繁刷新
    # 尾盘时段 14:30-15:05 高频刷新
    tail_start = now.replace(hour=14, minute=30, second=0)
    tail_end = now.replace(hour=15, minute=5, second=0)
    if tail_start <= now <= tail_end:
        return 30  # 30秒
    # 盘中 60秒
    morning_start = now.replace(hour=9, minute=30, second=0)
    afternoon_end = now.replace(hour=15, minute=5, second=0)
    if morning_start <= now <= afternoon_end:
        return 60
    return 300


def broadcast_sse(data):
    """向所有SSE客户端推送数据"""
    with sse_lock:
        dead = []
        for q in sse_queues:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            sse_queues.remove(q)


def background_screen():
    """后台定时刷新选股结果"""
    global cached_result, last_update
    # Run initial screen with retries (Railway cold start may need warmup)
    for attempt in range(5):
        try:
            print(f"[startup] Attempt {attempt+1}/5...")
            result = run_screen(enable_sepa=False)
            if result and result.get('success') and result.get('stocks') is not None:
                with cache_lock:
                    cached_result = result
                    last_update = datetime.now().strftime('%H:%M:%S')
                print(f"[startup] Screen done: {result.get('matched',0)} stocks")
                break
            elif result and result.get('error'):
                print(f"[startup] API error: {result['error']}, retrying in 5s...")
                time.sleep(5)
            else:
                print(f"[startup] No data, retrying in 3s...")
                time.sleep(3)
        except Exception as e:
            print(f"[startup] Attempt {attempt+1} failed: {e}")
            time.sleep(3)
    else:
        print("[startup] All attempts failed, will retry on next cycle")
    
    while True:
        interval = get_refresh_interval()
        
        if is_market_open() or datetime.now().weekday() < 5:
            try:
                result = run_screen(enable_sepa=True)
                with cache_lock:
                    cached_result = result
                    last_update = datetime.now().strftime('%H:%M:%S')
                
                # 推送给所有SSE客户端
                push_data = {
                    "type": "update",
                    "data": result,
                    "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "market_open": is_market_open()
                }
                broadcast_sse(json.dumps(push_data, ensure_ascii=False))
            except Exception as e:
                print(f"后台刷新失败: {e}")
        
        time.sleep(interval)


# ==================== 路由 ====================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/screen')
def api_screen():
    with cache_lock:
        result = cached_result
    if result is None:
        return jsonify({"success": False, "message": "暂未选股数据", "stocks": []})
    return jsonify(result)


@app.route('/api/history')
def api_history():
    date = request.args.get('date', '')
    if not date:
        return jsonify({"success": False, "message": "请提供日期参数 ?date=YYYY-MM-DD"})
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except:
        return jsonify({"success": False, "message": "日期格式错误，请使用 YYYY-MM-DD"})
    
    result = run_historical_screen(date)
    return jsonify(result)


@app.route('/api/refresh')
def api_refresh():
    result = run_screen()
    global cached_result, last_update
    with cache_lock:
        cached_result = result
        last_update = datetime.now().strftime('%H:%M:%S')
    
    # 推送给SSE
    push_data = {
        "type": "update",
        "data": result,
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "market_open": is_market_open()
    }
    broadcast_sse(json.dumps(push_data, ensure_ascii=False))
    return jsonify(result)


@app.route('/api/status')
def api_status():
    with cache_lock:
        count = len(cached_result['stocks']) if cached_result and 'stocks' in cached_result else 0
    return jsonify({
        'market_open': is_market_open(),
        'last_update': last_update,
        'stocks_count': count,
        'refresh_interval': get_refresh_interval()
    })


@app.route('/api/stream')
def api_stream():
    """SSE实时数据流"""
    q = queue.Queue(maxsize=5)
    with sse_lock:
        sse_queues.append(q)
    
    def generate():
        try:
            # 先发送当前缓存数据
            with cache_lock:
                if cached_result:
                    init_data = json.dumps({
                        "type": "update",
                        "data": cached_result,
                        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "market_open": is_market_open()
                    }, ensure_ascii=False)
                    yield f"data: {init_data}\n\n"
            
            while True:
                try:
                    data = q.get(timeout=30)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now().isoformat()})}\n\n"
        except GeneratorExit:
            with sse_lock:
                if q in sse_queues:
                    sse_queues.remove(q)
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )


if __name__ == '__main__':
    bg_thread = threading.Thread(target=background_screen, daemon=True)
    bg_thread.start()
    
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print(f"""
╔══════════════════════════════════════════╗
║        🔥 尾盘选股系统 v2.0              ║
║        SSE 实时推送 · 移动端适配         ║
╠══════════════════════════════════════════╣
║  本机访问:  http://127.0.0.1:5000        ║
║  手机访问:  http://{local_ip}:5000   ║
║  实时推送:  /api/stream (SSE)            ║
║  尾盘时段:  30秒自动刷新                 ║
║  盘中时段:  60秒自动刷新                 ║
╚══════════════════════════════════════════╝
    """)
    
    port = int(os.environ.get('PORT', 5000))
    print(f'  Port: {port}')
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
