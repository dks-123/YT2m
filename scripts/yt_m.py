import os
import re
import httpx
import paramiko
import json
import time
import random
from urllib.parse import urlparse
from datetime import datetime, timedelta

yt_info_path = "yt_info.txt"
output_dir = "output"
cookies_path = os.path.join(os.getcwd(), "cookies.txt")
API_KEY = os.getenv("YT_API_KEY", "")
if not API_KEY:
    print("❌ 環境變數 YT_API_KEY 未設置，改用 HTML 解析")

SF_L = os.getenv("SF_L", "")
if not SF_L:
    print("❌ 環境變數 SF_L 未設置")
    exit(1)

parsed_url = urlparse(SF_L)
SFTP_HOST = parsed_url.hostname
SFTP_PORT = parsed_url.port if parsed_url.port else 22
SFTP_USER = parsed_url.username
SFTP_PASSWORD = parsed_url.password
SFTP_REMOTE_DIR = parsed_url.path if parsed_url.path else "/"

os.makedirs(output_dir, exist_ok=True)

# 新增：User-Agent 輪替池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0"
]

def get_random_headers():
    """生成隨機 headers"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none"
    }

def add_random_delay():
    """添加隨機延遲，避免被偵測為機器人"""
    delay = random.uniform(1, 3)
    time.sleep(delay)

def get_channel_id(youtube_url):
    """從 YouTube URL 提取頻道 ID，優先使用 API"""
    handle = youtube_url.split("/")[-2] if "/@" in youtube_url else None
    if API_KEY and handle:
        try:
            url = f"https://www.googleapis.com/youtube/v3/channels?part=id&forHandle={handle}&key={API_KEY}"
            with httpx.Client(timeout=15) as client:
                res = client.get(url)
                res.raise_for_status()
                data = res.json()
                if data.get("items"):
                    print(f"✅ API 找到頻道 ID: {data['items'][0]['id']}")
                    return data["items"][0]["id"]
                print(f"⚠️ API 無法找到 {handle} 的頻道 ID，嘗試 HTML 解析")
        except Exception as e:
            print(f"⚠️ API 獲取頻道 ID 失敗: {e}")

    # 回退到 HTML 解析
    try:
        with httpx.Client(http2=True, follow_redirects=True, timeout=15) as client:
            headers = get_random_headers()
            add_random_delay()
            res = client.get(youtube_url, headers=headers)
            html = res.text
            patterns = [
                r'"channelId":"(UC[^"]+)"',
                r'<meta itemprop="channelId" content="(UC[^"]+)"',
                r'"externalId":"(UC[^"]+)"'
            ]
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    print(f"✅ HTML 找到頻道 ID: {match.group(1)}")
                    return match.group(1)
            print(f"⚠️ 無法從 {youtube_url} 提取頻道 ID")
            return None
    except Exception as e:
        print(f"⚠️ HTML 提取頻道 ID 失敗: {e}")
        return None

def get_live_video_id(channel_id):
    """使用 YouTube Data API 獲取直播 videoId"""
    try:
        url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&channelId={channel_id}&eventType=live&type=video&key={API_KEY}"
        with httpx.Client(timeout=15) as client:
            res = client.get(url)
            res.raise_for_status()
            data = res.json()
            if data.get("items"):
                video_id = data["items"][0]["id"]["videoId"]
                print(f"✅ 找到直播 videoId: {video_id}")
                return f"https://www.youtube.com/watch?v={video_id}"
            print(f"⚠️ 頻道 {channel_id} 目前無直播 (API 返回空結果)")
            return None
    except Exception as e:
        print(f"⚠️ API 請求失敗: {e}")
        return None

def grab_with_multiple_methods(youtube_url):
    """使用多種方法嘗試抓取 m3u8"""
    methods = [
        ("無 Cookie 方式", grab_without_cookies),
        ("使用 Cookie 方式", grab_with_cookies),
        ("API 輔助方式", grab_with_api_fallback)
    ]
    
    for method_name, method_func in methods:
        print(f"🔄 嘗試 {method_name}")
        try:
            result = method_func(youtube_url)
            if result and "googlevideo.com" in result:
                print(f"✅ {method_name} 成功")
                return result
            else:
                print(f"⚠️ {method_name} 失敗")
        except Exception as e:
            print(f"❌ {method_name} 發生錯誤: {e}")
        
        add_random_delay()
    
    return "https://raw.githubusercontent.com/jz168k/YT2m/main/assets/no_s.m3u8"

def grab_without_cookies(youtube_url):
    """不使用 cookies 的方式抓取"""
    with httpx.Client(http2=True, follow_redirects=True, timeout=15) as client:
        headers = get_random_headers()
        
        try:
            res = client.get(youtube_url, headers=headers)
            html = res.text

            if 'noindex' in html:
                print(f"⚠️ 頻道 {youtube_url} 目前未開啟直播")
                return None

            # 嘗試從 player_response JSON 中提取 m3u8
            player_response_match = re.search(r'ytInitialPlayerResponse\s*=\s*({.*?});', html, re.DOTALL)
            if player_response_match:
                player_response = json.loads(player_response_match.group(1))
                streaming_data = player_response.get("streamingData", {})
                hls_formats = streaming_data.get("hlsManifestUrl", "")
                if hls_formats:
                    print(f"✅ 找到 .m3u8 連結: {hls_formats}")
                    return hls_formats

            # 備用正則表達式
            m3u8_matches = re.findall(r'(https://[^"]+\.m3u8[^"]*)', html)
            for url in m3u8_matches:
                if "googlevideo.com" in url:
                    print(f"✅ 找到 .m3u8 連結: {url}")
                    return url

            print("⚠️ 未找到有效的 .m3u8 連結")
        except Exception as e:
            print(f"⚠️ 抓取頁面失敗: {e}")

        return None

def grab_with_cookies(youtube_url):
    """使用 cookies 的方式抓取"""
    with httpx.Client(http2=True, follow_redirects=True, timeout=15) as client:
        headers = get_random_headers()

        cookies = {}
        if os.path.exists(cookies_path):
            try:
                with open(cookies_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.startswith('#') and '\t' in line:
                            parts = line.strip().split('\t')
                            if len(parts) >= 6:
                                cookies[parts[5]] = parts[6]
                print(f"✅ 載入了 {len(cookies)} 個 cookies")
            except Exception as e:
                print(f"⚠️ Cookie 讀取失敗: {e}")

        try:
            res = client.get(youtube_url, headers=headers, cookies=cookies)
            html = res.text

            if 'noindex' in html:
                print(f"⚠️ 頻道 {youtube_url} 目前未開啟直播")
                return None

            # 嘗試從 player_response JSON 中提取 m3u8
            player_response_match = re.search(r'ytInitialPlayerResponse\s*=\s*({.*?});', html, re.DOTALL)
            if player_response_match:
                player_response = json.loads(player_response_match.group(1))
                streaming_data = player_response.get("streamingData", {})
                hls_formats = streaming_data.get("hlsManifestUrl", "")
                if hls_formats:
                    print(f"✅ 找到 .m3u8 連結: {hls_formats}")
                    return hls_formats

            # 備用正則表達式
            m3u8_matches = re.findall(r'(https://[^"]+\.m3u8[^"]*)', html)
            for url in m3u8_matches:
                if "googlevideo.com" in url:
                    print(f"✅ 找到 .m3u8 連結: {url}")
                    return url

            print("⚠️ 未找到有效的 .m3u8 連結")
        except Exception as e:
            print(f"⚠️ 抓取頁面失敗: {e}")

        return None

def grab_with_api_fallback(youtube_url):
    """使用 API 輔助的方式"""
    if not API_KEY:
        return None
    
    # 提取頻道 ID
    channel_id = get_channel_id(youtube_url)
    if not channel_id:
        return None
    
    # 使用 API 獲取直播 URL
    live_url = get_live_video_id(channel_id)
    if not live_url:
        return None
    
    # 使用無 cookies 方式抓取
    return grab_without_cookies(live_url)

def process_yt_info():
    with open(yt_info_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    i = 1
    for line in lines:
        line = line.strip()
        if line.startswith("~~") or not line:
            continue
        if "|" in line:
            parts = line.split("|")
            channel_name = parts[0].strip() if len(parts) > 0 else f"Channel {i}"
        else:
            youtube_url = line
            print(f"🔍 嘗試解析 M3U8: {youtube_url}")

            # 使用多種方法嘗試抓取
            m3u8_url = grab_with_multiple_methods(youtube_url)
            if m3u8_url is None:
                continue

            m3u8_content = f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1280000\n{m3u8_url}\n"
            output_m3u8 = os.path.join(output_dir, f"y{i:02d}.m3u8")
            with open(output_m3u8, "w", encoding="utf-8") as f:
                f.write(m3u8_content)

            php_content = f"""<?php
header('Location: {m3u8_url}');
?>"""
            output_php = os.path.join(output_dir, f"y{i:02d}.php")
            with open(output_php, "w", encoding="utf-8") as f:
                f.write(php_content)

            print(f"✅ 生成 {output_m3u8} 和 {output_php}")
            i += 1
            
            # 添加延遲避免過於頻繁的請求
            add_random_delay()

def upload_files():
    print("🚀 啟動 SFTP 上傳程序...")
    try:
        transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
        transport.connect(username=SFTP_USER, password=SFTP_PASSWORD)
        sftp = paramiko.SFTPClient.from_transport(transport)

        print(f"✅ 成功連接到 SFTP：{SFTP_HOST}")

        try:
            sftp.chdir(SFTP_REMOTE_DIR)
        except IOError:
            print(f"📁 遠端目錄 {SFTP_REMOTE_DIR} 不存在，正在創建...")
            sftp.mkdir(SFTP_REMOTE_DIR)
            sftp.chdir(SFTP_REMOTE_DIR)

        for file in os.listdir(output_dir):
            local_path = os.path.join(output_dir, file)
            remote_path = os.path.join(SFTP_REMOTE_DIR, file)
            if os.path.isfile(local_path):
                print(f"⬆️ 上傳 {local_path} → {remote_path}")
                sftp.put(local_path, remote_path)

        sftp.close()
        transport.close()
        print("✅ SFTP 上傳完成！")

    except Exception as e:
        print(f"❌ SFTP 上傳失敗: {e}")

if __name__ == "__main__":
    process_yt_info()
    upload_files()
