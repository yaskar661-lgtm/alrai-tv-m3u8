from flask import Flask, jsonify, redirect, request, Response
import asyncio
import aiohttp
import re
import time
from urllib.parse import unquote, urljoin

app = Flask(__name__)

API_URL = "https://www.alraimedia.com/livestream/"

cache = {
    "hls_url": None,
    "timestamp": 0
}

async def fetch_fresh_hls():
    """Asenkron olarak dış API'den en taze HLS master linkini çeker."""
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
    """Cache mekanizmasıyla CPU yormadan her 1 saniyede bir hls_url adresini tazeleyen fonksiyon."""
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

if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
