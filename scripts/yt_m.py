import os
import re
import requests
import paramiko
from urllib.parse import urlparse
import urllib3
import socket

# 設置 urllib3 的 socket 選項
urllib3.util.connection.HTTPConnection.default_socket_options = (
    urllib3.util.connection.HTTPConnection.default_socket_options + [
        (socket.SOL_SOCKET, socket.SO_SNDTIMEO, (int(10), 0)),
        (socket.SOL_SOCKET, socket.SO_RCVTIMEO, (int(10), 0)),
    ]
)

yt_info_path = "yt_info.txt"
output_dir = "output"
cookies_path = os.path.join(os.getcwd(), "cookies.txt")

# 從 GitHub Secrets 環境變數讀取 SFTP 資訊
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

def resolve_to_watch_url(youtube_url):
    """解析 @channel/live → 實際 watch?v=xxx 頁面"""
    try:
        session = requests.Session()
        session.max_redirects = 30
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive"
        }
        res = session.get(youtube_url, headers=headers, timeout=10)
        html = res.text

        match = re.search(r'<link rel="canonical" href="(https://www\.youtube\.com/watch\?v=[^"]+)"', html) or \
                re.search(r'"canonicalUrl":"(https://www\.youtube\.com/watch\?v=[^"]+)"', html)
        if match:
            return match.group(1)
        else:
            print(f"⚠️ 無法從 HTML 中提取 watch?v=xxx URL: {youtube_url}")
    except Exception as e:
        print(f"⚠️ 無法取得最終直播網址: {e}")
    return youtube_url

def grab(youtube_url):
    session = requests.Session()
    session.max_redirects = 30
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive"
    }

    cookies = {}
    if os.path.exists(cookies_path):
        try:
            with open(cookies_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.startswith('#') and '\t' in line:
                        parts = line.strip().split('\t')
                        if len(parts) >= 6:
                            cookies[parts[5]] = parts[6]
        except Exception as e:
            print(f"⚠️ Cookie 讀取失敗: {e}")

    try:
        resolved_url = resolve_to_watch_url(youtube_url)
        print(f"🔁 轉址後 URL: {resolved_url}")

        res = session.get(resolved_url, headers=headers, cookies=cookies, timeout=10)
        html = res.text

        if 'noindex' in html:
            print(f"⚠️ 頻道 {youtube_url} 目前未開啟直播")
            return None

        m3u8_matches = re.findall(r'https://[^"]+\.m3u8[^"]*', html)
        for url in m3u8_matches:
            if "googlevideo.com" in url and "mime=video" in url:
                return url

        print("⚠️ 未找到有效的 .m3u8 連結")
    except Exception as e:
        print(f"⚠️ 抓取頁面失敗: {e}")

    return "https://raw.githubusercontent.com/jz168k/YT2m/main/assets/no_s.m3u8"

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
            m3u8_url = grab(youtube_url)
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
