"""
灏剧洏閫夎偂 Web 鏈嶅姟 - SSE瀹炴椂鎺ㄩ€佺増
绉诲姩绔搷搴斿紡鐣岄潰 + 灏剧洏鏃舵楂橀鍒锋柊
"""
from flask import Flask, render_template, jsonify, request, Response, stream_with_context
from engine import run_screen, is_market_open, ScreenerConfig
from datetime import datetime
import threading
import time
import json
import queue
import os

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# SSE 娑堟伅闃熷垪
sse_queues = []
sse_lock = threading.Lock()

# 缂撳瓨
cached_result = None
cache_lock = threading.Lock()
last_update = None

# 鍒锋柊闂撮殧锛氬熬鐩樻椂娈?0绉掞紝鍏朵粬鏃舵2鍒嗛挓
def get_refresh_interval():
    now = datetime.now()
    if now.weekday() >= 5:
        return 300  # 鍛ㄦ湯涓嶉绻佸埛鏂?    # 灏剧洏鏃舵 14:30-15:05 楂橀鍒锋柊
    tail_start = now.replace(hour=14, minute=30, second=0)
    tail_end = now.replace(hour=15, minute=5, second=0)
    if tail_start <= now <= tail_end:
        return 30  # 30绉?    # 鐩樹腑 60绉?    morning_start = now.replace(hour=9, minute=30, second=0)
    afternoon_end = now.replace(hour=15, minute=5, second=0)
    if morning_start <= now <= afternoon_end:
        return 60
    return 300


def broadcast_sse(data):
    """鍚戞墍鏈塖SE瀹㈡埛绔帹閫佹暟鎹?""
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
    """鍚庡彴瀹氭椂鍒锋柊閫夎偂缁撴灉"""
    global cached_result, last_update
    while True:
        interval = get_refresh_interval()
        
        if is_market_open() or datetime.now().weekday() < 5:
            try:
                result = run_screen()
                with cache_lock:
                    cached_result = result
                    last_update = datetime.now().strftime('%H:%M:%S')
                
                # 鎺ㄩ€佺粰鎵€鏈塖SE瀹㈡埛绔?                push_data = {
                    "type": "update",
                    "data": result,
                    "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "market_open": is_market_open()
                }
                broadcast_sse(json.dumps(push_data, ensure_ascii=False))
            except Exception as e:
                print(f"鍚庡彴鍒锋柊澶辫触: {e}")
        
        time.sleep(interval)


# ==================== 璺敱 ====================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/screen')
def api_screen():
    with cache_lock:
        result = cached_result
    if result is None:
        return jsonify({"success": False, "message": "鏆傛湭閫夎偂鏁版嵁", "stocks": []})
    return jsonify(result)


@app.route('/api/refresh')
def api_refresh():
    result = run_screen()
    global cached_result, last_update
    with cache_lock:
        cached_result = result
        last_update = datetime.now().strftime('%H:%M:%S')
    
    # 鎺ㄩ€佺粰SSE
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
    """SSE瀹炴椂鏁版嵁娴?""
    q = queue.Queue(maxsize=5)
    with sse_lock:
        sse_queues.append(q)
    
    def generate():
        try:
            # 鍏堝彂閫佸綋鍓嶇紦瀛樻暟鎹?            with cache_lock:
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
鈺斺晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晽
鈺?       馃敟 灏剧洏閫夎偂绯荤粺 v2.0              鈺?鈺?       SSE 瀹炴椂鎺ㄩ€?路 绉诲姩绔€傞厤         鈺?鈺犫晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨暎
鈺? 鏈満璁块棶:  http://127.0.0.1:5000        鈺?鈺? 鎵嬫満璁块棶:  http://{local_ip}:5000   鈺?鈺? 瀹炴椂鎺ㄩ€?  /api/stream (SSE)            鈺?鈺? 灏剧洏鏃舵:  30绉掕嚜鍔ㄥ埛鏂?                鈺?鈺? 鐩樹腑鏃舵:  60绉掕嚜鍔ㄥ埛鏂?                鈺?鈺氣晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨暆
    """)
    
    port = int(os.environ.get('PORT', 5000))
    print(f'  Port: {port}')
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
