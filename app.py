from flask import Flask, jsonify, redirect, request, Response, stream_with_context
import asyncio
import aiohttp
import re
import time
from urllib.parse import unquote

app = Flask(__name__)

API_URL = "https://www.alraimedia.com/livestream/"

cache = {
    "hls_url": None,
    "timestamp": 0
}

async def fetch_fresh_hls():
    """Asenkron olarak dış API'den en taze HLS linkini çeker."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, timeout=4) as response:
                if response.status == 200:
                    data = await response.text()
                    match = re.search(r'(https://live.kwikmotion.com/alraimedialive/alraitv.smil/.*?\.m3u8\?hdnts=[^"]+)', data)
                    return match.group(1) if match else None
    except Exception as e:
        print(f"API İstek Hatası: {e}")
    return None

async def get_latest_hls_url():
    current_time = time.time()
    if not cache["hls_url"] or (current_time - cache["timestamp"]) >= 1.0:
        new_url = await fetch_fresh_hls()
        if new_url:
            cache["hls_url"] = new_url
            cache["timestamp"] = current_time
    return cache["hls_url"]

def extract_token_from_url(url):
    match = re.search(r'(hdntl=[^/&"\']+)', url)
    return match.group(1) if match else None

@app.route('/api/latest', methods=['GET'])
def api_latest():
    """JSON API Endpoint'i"""
    hls_url = asyncio.run(get_latest_hls_url())
    if not hls_url:
        return jsonify({"error": "Yayın kaynağına ulaşılamadı"}), 500
    
    server_host = f"{request.scheme}://{request.host}"
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)

    return jsonify({
        "status": "success",
        "server_host": server_host,
        "client_ip": client_ip,
        "hls_url": hls_url,
        "token": extract_token_from_url(hls_url),
        "timestamp": cache["timestamp"]
    })

@app.route('/live/alraitv.m3u8', methods=['GET'])
def dynamic_m3u8_redirect():
    """Ana m3u8 yönlendirme endpoint'i (HTTP 302)"""
    hls_url = asyncio.run(get_latest_hls_url())
    if not hls_url:
        return "Yayın kaynağına ulaşılamadı!", 500

    latest_token = extract_token_from_url(hls_url)
    target_url = hls_url
    if latest_token:
        old_token = extract_token_from_url(target_url)
        if old_token:
            target_url = target_url.replace(old_token, latest_token)

    return redirect(target_url, code=302)

@app.route('/redirect', methods=['GET'])
def php_style_redirect():
    """PHP tarzı dinamik redirect endpoint'i (HTTP 302)"""
    target_url = request.args.get('url')
    if not target_url:
        return "Eksik parametre!", 400

    target_url = unquote(target_url)
    hls_url = asyncio.run(get_latest_hls_url())
    latest_token = extract_token_from_url(hls_url) if hls_url else None
    old_token = extract_token_from_url(target_url)

    if latest_token and old_token and latest_token != old_token:
        target_url = target_url.replace(old_token, latest_token)

    return redirect(target_url, code=302)

@app.route('/proxy', methods=['GET'])
def ffmpeg_proxy():
    """
    Arka planda gizli ve sessiz çalışan, CPU yormayan (-c copy) 
    ve token süresi doldukça otomatik taze linkle döngü kuran proxy.
    """
    def generate():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            while True:
                # 1. Her döngüde en taze HLS URL'sini al
                hls_url = loop.run_until_complete(get_latest_hls_url())
                if not hls_url:
                    time.sleep(2)
                    continue

                cmd = [
                    'ffmpeg',
                    '-hide_banner',
                    '-loglevel', 'quiet',  # FFmpeg loglarını tamamen gizler / sessizleştirir
                    '-i', hls_url,
                    '-c', 'copy',          # İşlemciye (CPU) yük bindirmez, ham veriyi doğrudan kopyalar
                    '-f', 'mpegts',
                    'pipe:1'
                ]

                # 2. FFmpeg sürecini başlat
                process = loop.run_until_complete(
                    asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                )

                try:
                    start_time = time.time()
                    while True:
                        # Token süresinin aşılmaması için her 90 saniyede bir bağlantıyı yenile
                        if time.time() - start_time > 90:
                            break

                        chunk = loop.run_until_complete(process.stdout.read(32768))
                        if not chunk:
                            break
                        yield chunk
                except (ConnectionResetError, GeneratorExit):
                    break
                finally:
                    if process.returncode is None:
                        try:
                            process.terminate()
                            loop.run_until_complete(process.wait())
                        except Exception:
                            pass
                    if process.stdout:
                        try:
                            process.stdout.feed_eof()
                        except Exception:
                            pass
        finally:
            loop.close()

    return Response(
        stream_with_context(generate()),
        mimetype='video/mp2t',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        }
    )

if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
