import os
import re
import httpx
import paramiko
import json
import time
import random
import base64
from urllib.parse import urlparse, unquote
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

# 擴展的 User-Agent 池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0"
]

def get_advanced_headers():
    """生成更真實的 headers"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"'
    }

def add_smart_delay():
    """智慧延遲，避免被偵測"""
    delay = random.uniform(2, 5)
    time.sleep(delay)

def get_channel_id(youtube_url):
    """從 YouTube URL 提取頻道 ID"""
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

    # HTML 解析備用方案
    try:
        with httpx.Client(http2=True, follow_redirects=True, timeout=20) as client:
            headers = get_advanced_headers()
            add_smart_delay()
            res = client.get(youtube_url, headers=headers)
            html = res.text
            
            patterns = [
                r'"channelId":"(UC[^"]+)"',
                r'<meta itemprop="channelId" content="(UC[^"]+)"',
                r'"externalId":"(UC[^"]+)"',
                r'"browseId":"(UC[^"]+)"',
                r'channelId["\']:\s*["\']([^"\']+)["\']'
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

def extract_m3u8_from_html(html, url):
    """從 HTML 中提取 m3u8 連結，使用多種方法"""
    print(f"🔍 分析 HTML 內容，長度: {len(html)} 字符")
    
    # 方法1: 尋找 ytInitialPlayerResponse
    try:
        player_response_patterns = [
            r'ytInitialPlayerResponse\s*=\s*({.*?});',
            r'ytInitialPlayerResponse":\s*({.*?})(?:,"|$)',
            r'player_response["\']:\s*["\']([^"\']+)["\']'
        ]
        
        for pattern in player_response_patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    if pattern.endswith(r'([^"\']+)["\']'):
                        # 這是 base64 編碼的情況
                        player_data = base64.b64decode(match.group(1)).decode('utf-8')
                        player_response = json.loads(player_data)
                    else:
                        player_response = json.loads(match.group(1))
                    
                    streaming_data = player_response.get("streamingData", {})
                    hls_url = streaming_data.get("hlsManifestUrl", "")
                    if hls_url:
                        print(f"✅ 從 ytInitialPlayerResponse 找到 m3u8: {hls_url}")
                        return hls_url
                except (json.JSONDecodeError, Exception) as e:
                    print(f"⚠️ 解析 player_response 失敗: {e}")
                    continue
    except Exception as e:
        print(f"⚠️ 搜尋 ytInitialPlayerResponse 失敗: {e}")

    # 方法2: 直接搜尋 m3u8 URL
    try:
        m3u8_patterns = [
            r'(https://[^"\'>\s]+\.m3u8[^"\'>\s]*)',
            r'"(https://[^"]+googlevideo\.com[^"]+\.m3u8[^"]*)"',
            r'\'(https://[^\']+googlevideo\.com[^\']+\.m3u8[^\']*)\''
        ]
        
        for pattern in m3u8_patterns:
            matches = re.findall(pattern, html)
            for match in matches:
                if "googlevideo.com" in match:
                    print(f"✅ 直接找到 m3u8 URL: {match}")
                    return match
    except Exception as e:
        print(f"⚠️ 搜尋 m3u8 URL 失敗: {e}")

    # 方法3: 尋找 JavaScript 變數中的串流資訊
    try:
        js_patterns = [
            r'streamingData["\']:\s*({[^}]+})',
            r'hlsManifestUrl["\']:\s*["\']([^"\']+)["\']',
            r'adaptiveFormats["\']:\s*\[([^\]]+)\]'
        ]
        
        for pattern in js_patterns:
            matches = re.findall(pattern, html)
            for match in matches:
                if "googlevideo.com" in match and ".m3u8" in match:
                    # 嘗試提取 URL
                    url_match = re.search(r'https://[^"\'>\s]+\.m3u8[^"\'>\s]*', match)
                    if url_match:
                        print(f"✅ 從 JavaScript 變數找到 m3u8: {url_match.group()}")
                        return url_match.group()
    except Exception as e:
        print(f"⚠️ 搜尋 JavaScript 變數失敗: {e}")

    # 方法4: 檢查是否有內嵌的 player
    try:
        embed_patterns = [
            r'embed/([a-zA-Z0-9_-]+)',
            r'watch\?v=([a-zA-Z0-9_-]+)'
        ]
        
        for pattern in embed_patterns:
            match = re.search(pattern, url)
            if match:
                video_id = match.group(1)
                print(f"🔍 嘗試直接訪問 video ID: {video_id}")
                return get_m3u8_from_video_id(video_id)
    except Exception as e:
        print(f"⚠️ 檢查內嵌 player 失敗: {e}")

    print("⚠️ 所有方法都未找到有效的 m3u8 連結")
    return None

def get_m3u8_from_video_id(video_id):
    """直接從 video ID 獲取 m3u8"""
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        with httpx.Client(http2=True, follow_redirects=True, timeout=20) as client:
            headers = get_advanced_headers()
            
            # 載入 cookies
            cookies = load_cookies()
            
            add_smart_delay()
            res = client.get(video_url, headers=headers, cookies=cookies)
            html = res.text
            
            return extract_m3u8_from_html(html, video_url)
    except Exception as e:
        print(f"⚠️ 從 video ID 獲取 m3u8 失敗: {e}")
        return None

def load_cookies():
    """載入 cookies"""
    cookies = {}
    if os.path.exists(cookies_path):
        try:
            with open(cookies_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.startswith('#') and '\t' in line:
                        parts = line.strip().split('\t')
                        if len(parts) >= 7:
                            cookies[parts[5]] = parts[6]
            print(f"✅ 載入了 {len(cookies)} 個 cookies")
        except Exception as e:
            print(f"⚠️ Cookie 讀取失敗: {e}")
    return cookies

def grab_with_multiple_methods(youtube_url):
    """使用多種方法嘗試抓取 m3u8"""
    methods = [
        ("優化無 Cookie 方式", lambda url: grab_optimized(url, use_cookies=False)),
        ("優化 Cookie 方式", lambda url: grab_optimized(url, use_cookies=True)),
        ("API 輔助優化方式", grab_with_api_enhanced)
    ]
    
    for method_name, method_func in methods:
        print(f"🔄 嘗試 {method_name}")
        try:
            result = method_func(youtube_url)
            if result and "googlevideo.com" in result and ".m3u8" in result:
                print(f"✅ {method_name} 成功")
                return result
            else:
                print(f"⚠️ {method_name} 失敗")
        except Exception as e:
            print(f"❌ {method_name} 發生錯誤: {e}")
        
        add_smart_delay()
    
    print("❌ 所有方法都失敗，使用備用連結")
    return "https://raw.githubusercontent.com/jz168k/YT2m/main/assets/no_s.m3u8"

def grab_optimized(youtube_url, use_cookies=False):
    """優化的抓取方法"""
    try:
        with httpx.Client(http2=True, follow_redirects=True, timeout=30) as client:
            headers = get_advanced_headers()
            
            cookies = {}
            if use_cookies:
                cookies = load_cookies()
            
            print(f"🌐 訪問 URL: {youtube_url}")
            add_smart_delay()
            
            res = client.get(youtube_url, headers=headers, cookies=cookies)
            html = res.text
            
            # 檢查是否為直播
            if any(indicator in html.lower() for indicator in ['noindex', 'offline', 'not available', 'this live stream recording is not available']):
                print(f"⚠️ 直播不可用或離線: {youtube_url}")
                return None
            
            # 使用增強的 m3u8 提取
            m3u8_url = extract_m3u8_from_html(html, youtube_url)
            return m3u8_url
            
    except Exception as e:
        print(f"⚠️ 優化抓取失敗: {e}")
        return None

def grab_with_api_enhanced(youtube_url):
    """使用 API 輔助的增強方式"""
    if not API_KEY:
        print("⚠️ 沒有 API 金鑰，跳過 API 輔助方式")
        return None
    
    try:
        # 提取頻道 ID
        channel_id = get_channel_id(youtube_url)
        if not channel_id:
            return None
        
        # 使用 API 獲取直播 URL
        live_url = get_live_video_id(channel_id)
        if not live_url:
            return None
        
        # 使用優化方式抓取
        return grab_optimized(live_url, use_cookies=True)
        
    except Exception as e:
        print(f"⚠️ API 輔助增強方式失敗: {e}")
        return None

def process_yt_info():
    """處理 YouTube 資訊"""
    print(f"📖 讀取 {yt_info_path}")
    try:
        with open(yt_info_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"❌ 讀取 {yt_info_path} 失敗: {e}")
        return

    i = 1
    success_count = 0
    total_count = 0
    
    for line in lines:
        line = line.strip()
        if line.startswith("~~") or not line:
            continue
            
        total_count += 1
        
        if "|" in line:
            parts = line.split("|")
            channel_name = parts[0].strip() if len(parts) > 0 else f"Channel {i}"
            youtube_url = parts[1].strip() if len(parts) > 1 else ""
        else:
            youtube_url = line
            channel_name = f"Channel {i}"
        
        if not youtube_url:
            continue
            
        print(f"\n🔍 [{i:02d}] 處理: {channel_name}")
        print(f"🔗 URL: {youtube_url}")

        # 使用多種方法嘗試抓取
        m3u8_url = grab_with_multiple_methods(youtube_url)
        
        if m3u8_url and "googlevideo.com" in m3u8_url:
            success_count += 1
            print(f"✅ 成功獲取 m3u8")
        else:
            print(f"⚠️ 使用備用連結")

        # 生成 m3u8 文件
        m3u8_content = f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1280000\n{m3u8_url}\n"
        output_m3u8 = os.path.join(output_dir, f"y{i:02d}.m3u8")
        with open(output_m3u8, "w", encoding="utf-8") as f:
            f.write(m3u8_content)

        # 生成 PHP 文件
        php_content = f"""<?php
header('Location: {m3u8_url}');
?>"""
        output_php = os.path.join(output_dir, f"y{i:02d}.php")
        with open(output_php, "w", encoding="utf-8") as f:
            f.write(php_content)

        print(f"📁 生成 {output_m3u8} 和 {output_php}")
        i += 1
        
        # 處理間隔
        if i <= total_count:
            add_smart_delay()
    
    print(f"\n📊 處理完成統計:")
    print(f"   總計: {total_count} 個頻道")
    print(f"   成功: {success_count} 個")
    print(f"   失敗: {total_count - success_count} 個")
    print(f"   成功率: {(success_count/total_count*100):.1f}%")

def upload_files():
    """上傳檔案到 SFTP"""
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

        upload_count = 0
        for file in os.listdir(output_dir):
            local_path = os.path.join(output_dir, file)
            remote_path = os.path.join(SFTP_REMOTE_DIR, file)
            if os.path.isfile(local_path):
                print(f"⬆️ 上傳 {local_path} → {remote_path}")
                sftp.put(local_path, remote_path)
                upload_count += 1

        sftp.close()
        transport.close()
        print(f"✅ SFTP 上傳完成！共上傳 {upload_count} 個檔案")

    except Exception as e:
        print(f"❌ SFTP 上傳失敗: {e}")

if __name__ == "__main__":
    print("🎬 YouTube M3U8 解析器啟動")
    print(f"⏰ 開始時間: {datetime.now()}")
    
    try:
        process_yt_info()
        upload_files()
    except KeyboardInterrupt:
        print("\n⏹️ 程序被用戶中斷")
    except Exception as e:
        print(f"❌ 程序執行失敗: {e}")
    finally:
        print(f"⏰ 結束時間: {datetime.now()}")
        print("🏁 程序結束")
