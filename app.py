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

@app.route('/playlist.m3u8', methods=['GET'])
def proxy_hls_playlist():
    """
    Oynatıcılara vereceğiniz ana m3u8 proxy endpoint'idir. 
    İçindeki .ts segmentlerini hls_url tabanıyla (urljoin) birleştirip taze token ekler.
    """
    hls_url = asyncio.run(get_latest_hls_url())
    if not hls_url:
        return "Yayın kaynağına ulaşılamadı!", 500

    try:
        async def fetch_playlist():
            async with aiohttp.ClientSession() as session:
                async with session.get(hls_url, timeout=5) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    return None

        playlist_content = asyncio.run(fetch_playlist())
        if not playlist_content:
            return "Playlist indirilemedi", 500

        latest_token = extract_token_from_url(hls_url)
        lines = playlist_content.splitlines()
        new_lines = []
        server_host = f"{request.scheme}://{request.host}"

        for line in lines:
            line = line.strip()
            # Yorum veya etiket olmayan satırlar (segmentler veya alt m3u8 dosyaları)
            if line and not line.startswith('#'):
                # Base URL birleştirmesi (relative segmentleri tam URL yapar)
                absolute_url = urljoin(hls_url, line)

                # En taze token entegrasyonu
                if latest_token:
                    old_token = extract_token_from_url(absolute_url)
                    if old_token:
                        absolute_url = absolute_url.replace(old_token, latest_token)
                    elif "?" in absolute_url:
                        absolute_url += f"&{latest_token}"
                    else:
                        absolute_url += f"?{latest_token}"

                # İstemci bu segmenti çekerken bizim segment-proxy'mize yönlendirilir
                proxied_segment_url = f"{server_host}/segment-proxy?url={absolute_url}"
                new_lines.append(proxied_segment_url)
            else:
                new_lines.append(line)

        final_playlist = "\n".join(new_lines)

        return Response(
            final_playlist, 
            mimetype='application/vnd.apple.mpegurl',
            headers={
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Access-Control-Allow-Origin': '*'
            }
        )

    except Exception as e:
        return f"Playlist İşleme Hatası: {str(e)}", 500

@app.route('/segment-proxy', methods=['GET'])
def segment_proxy():
    """
    Playlist içindeki .ts parça dosyalarını veya alt m3u8'leri CPU yormadan 
    doğru mimetype ile anlık proxy eden servis.
    """
    target_url = request.args.get('url')
    if not target_url:
        return "Eksik parametre!", 400

    target_url = unquote(target_url)

    hls_url = asyncio.run(get_latest_hls_url())
    latest_token = extract_token_from_url(hls_url) if hls_url else None
    old_token = extract_token_from_url(target_url)

    if latest_token and old_token and latest_token != old_token:
        target_url = target_url.replace(old_token, latest_token)

    async def fetch_media_segment():
        async with aiohttp.ClientSession() as session:
            async with session.get(target_url, timeout=10) as resp:
                content = await resp.read()
                return resp.status, content

    try:
        status, content = asyncio.run(fetch_media_segment())
        if status != 200:
            return f"Segment Kaynak Hatası: {status}", status

        # Esnek mimetype tespiti (URL içinde .m3u8 geçiyorsa m3u8, aksi halde video/mp2t)
        mimetype = 'application/vnd.apple.mpegurl' if '.m3u8' in target_url else 'video/mp2t'

        return Response(
            content, 
            mimetype=mimetype, 
            headers={
                'Cache-Control': 'no-cache',
                'Access-Control-Allow-Origin': '*'
            }
        )
    except Exception as e:
        return f"Segment Proxy Hatası: {str(e)}", 500

if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
