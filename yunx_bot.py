#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Yunx - 多功能下载 Telegram 机器人

import os
import sys
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, Dict, Any
import time
import threading
import requests
import urllib3
import re
import uuid
import json

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    import yt_dlp
except ImportError as e:
    print(f"Error importing required packages: {e}")
    print("Please install: pip install python-telegram-bot yt-dlp requests")
    sys.exit(1)

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class QBittorrentClient:
    """qBittorrent 客户端类，用于与 qBittorrent WebUI API 交互"""
    
    def __init__(self, host: str, username: str, password: str, download_path: str = None):
        """初始化 qBittorrent 客户端
        
        Args:
            host: qBittorrent WebUI 地址，例如 http://localhost:8080
            username: qBittorrent 用户名
            password: qBittorrent 密码
            download_path: 下载路径，如果不指定则使用默认路径
        """
        self.host = host.rstrip('/')
        self.username = username
        self.password = password
        self.download_path = download_path
        self.session = requests.Session()
        self.is_logged_in = False
        
        # 测试连接并登录
        self.login()
    
    def login(self) -> bool:
        """登录到 qBittorrent WebUI"""
        try:
            login_url = f"{self.host}/api/v2/auth/login"
            data = {
                'username': self.username,
                'password': self.password
            }
            response = self.session.post(login_url, data=data, verify=False, timeout=10)
            
            if response.text == "Ok.":
                logger.info("qBittorrent 登录成功")
                self.is_logged_in = True
                return True
            else:
                logger.error(f"qBittorrent 登录失败: {response.text}")
                self.is_logged_in = False
                return False
        except Exception as e:
            logger.error(f"qBittorrent 连接失败: {str(e)}")
            self.is_logged_in = False
            return False
    
    def add_torrent(self, torrent_url: str) -> Dict[str, Any]:
        """添加种子下载任务
        
        Args:
            torrent_url: 种子链接或磁力链接
            
        Returns:
            Dict: 包含操作结果的字典
        """
        if not self.is_logged_in and not self.login():
            return {'success': False, 'error': '未登录到 qBittorrent'}
        
        try:
            add_url = f"{self.host}/api/v2/torrents/add"
            
            data = {'urls': torrent_url}
            
            # 如果指定了下载路径，添加到请求中
            if self.download_path:
                data['savepath'] = self.download_path
            
            response = self.session.post(add_url, data=data, verify=False, timeout=30)
            
            if response.text == "Ok.":
                logger.info(f"种子添加成功: {torrent_url}")
                return {'success': True, 'message': '种子添加成功'}
            else:
                logger.error(f"种子添加失败: {response.text}")
                return {'success': False, 'error': f'种子添加失败: {response.text}'}
        except Exception as e:
            logger.error(f"添加种子时出错: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def get_torrents(self) -> Dict[str, Any]:
        """获取所有种子的状态"""
        if not self.is_logged_in and not self.login():
            return {'success': False, 'error': '未登录到 qBittorrent'}
        
        try:
            list_url = f"{self.host}/api/v2/torrents/info"
            response = self.session.get(list_url, verify=False, timeout=10)
            
            if response.status_code == 200:
                torrents = response.json()
                return {'success': True, 'torrents': torrents}
            else:
                logger.error(f"获取种子列表失败: {response.text}")
                return {'success': False, 'error': f'获取种子列表失败: {response.text}'}
        except Exception as e:
            logger.error(f"获取种子列表时出错: {str(e)}")
            return {'success': False, 'error': str(e)}

class VideoDownloader:
    def __init__(self, base_download_path: str, x_cookies_path: str = None):
        self.base_download_path = Path(base_download_path)
        self.x_cookies_path = x_cookies_path
        # 添加 Bilibili cookies 路径
        self.b_cookies_path = os.getenv('B_COOKIES')
        
        # 从环境变量获取代理配置
        self.proxy_host = os.getenv('PROXY_HOST')
        if self.proxy_host:
            # 测试代理连接
            if self._test_proxy_connection():
                logger.info(f"代理服务器已配置并连接成功: {self.proxy_host}")
                logger.info(f"yt-dlp 使用代理: {self.proxy_host}")
                # 设置系统代理环境变量
                os.environ['HTTP_PROXY'] = self.proxy_host
                os.environ['HTTPS_PROXY'] = self.proxy_host
                os.environ['NO_PROXY'] = 'localhost,127.0.0.1'
            else:
                logger.warning(f"代理服务器已配置但连接失败: {self.proxy_host}")
                logger.info("yt-dlp 直接连接")
                self.proxy_host = None  # 连接失败时禁用代理
                # 清除系统代理环境变量
                os.environ.pop('HTTP_PROXY', None)
                os.environ.pop('HTTPS_PROXY', None)
                os.environ.pop('NO_PROXY', None)
        else:
            logger.info("代理服务器未配置，将直接连接")
            logger.info("yt-dlp 直接连接")
            # 确保系统代理环境变量被清除
            os.environ.pop('HTTP_PROXY', None)
            os.environ.pop('HTTPS_PROXY', None)
            os.environ.pop('NO_PROXY', None)
        
        # 从环境变量获取是否转换格式的配置
        self.convert_to_mp4 = os.getenv('CONVERT_TO_MP4', 'true').lower() == 'true'
        logger.info(f"视频格式转换: {'开启' if self.convert_to_mp4 else '关闭'}")
        
        # 支持自定义下载目录
        self.custom_download_path = os.getenv('CUSTOM_DOWNLOAD_PATH', 'false').lower() == 'true'
        if self.custom_download_path:
            self.x_download_path = Path(os.getenv('X_DOWNLOAD_PATH', '/downloads/x'))
            self.youtube_download_path = Path(os.getenv('YOUTUBE_DOWNLOAD_PATH', '/downloads/youtube'))
            self.xvideos_download_path = Path(os.getenv('XVIDEOS_DOWNLOAD_PATH', '/downloads/xvideos'))
            self.pornhub_download_path = Path(os.getenv('PORNHUB_DOWNLOAD_PATH', '/downloads/pornhub'))
            self.bilibili_download_path = Path(os.getenv('BILIBILI_DOWNLOAD_PATH', '/downloads/bilibili'))
            self.douyin_download_path = Path(os.getenv('DOUYIN_DOWNLOAD_PATH', '/downloads/douyin'))
            self.files_download_path = Path(os.getenv('FILES_DOWNLOAD_PATH', '/downloads/files'))
            self.images_download_path = Path(os.getenv('IMAGES_DOWNLOAD_PATH', '/downloads/images'))
        else:
            self.x_download_path = self.base_download_path / "x"
            self.youtube_download_path = self.base_download_path / "youtube"
            self.xvideos_download_path = self.base_download_path / "xvideos"
            self.pornhub_download_path = self.base_download_path / "pornhub"
            self.bilibili_download_path = self.base_download_path / "bilibili"
            self.douyin_download_path = self.base_download_path / "douyin"
            self.files_download_path = self.base_download_path / "files"
            self.images_download_path = self.base_download_path / "images"
        
        # 创建所有下载目录
        self.x_download_path.mkdir(parents=True, exist_ok=True)
        self.youtube_download_path.mkdir(parents=True, exist_ok=True)
        self.xvideos_download_path.mkdir(parents=True, exist_ok=True)
        self.pornhub_download_path.mkdir(parents=True, exist_ok=True)
        self.bilibili_download_path.mkdir(parents=True, exist_ok=True)
        self.douyin_download_path.mkdir(parents=True, exist_ok=True)
        self.files_download_path.mkdir(parents=True, exist_ok=True)
        self.images_download_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"X 下载路径: {self.x_download_path}")
        logger.info(f"YouTube 下载路径: {self.youtube_download_path}")
        logger.info(f"Xvideos 下载路径: {self.xvideos_download_path}")
        logger.info(f"Pornhub 下载路径: {self.pornhub_download_path}")
        logger.info(f"Bilibili 下载路径: {self.bilibili_download_path}")
        logger.info(f"抖音下载路径: {self.douyin_download_path}")
        logger.info(f"文件下载路径: {self.files_download_path}")
        logger.info(f"图片下载路径: {self.images_download_path}")
        
        # 如果设置了 Bilibili cookies，记录日志
        if self.b_cookies_path:
            logger.info(f"Bilibili Cookies 路径: {self.b_cookies_path}")
        
    def _test_proxy_connection(self) -> bool:
        """测试代理服务器连接"""
        if not self.proxy_host:
            return False
            
        try:
            # 解析代理地址
            proxy_url = urlparse(self.proxy_host)
            proxies = {
                'http': self.proxy_host,
                'https': self.proxy_host
            }
            
            # 设置超时时间为5秒
            response = requests.get('http://www.google.com', 
                                 proxies=proxies, 
                                 timeout=5,
                                 verify=False)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"代理连接测试失败: {str(e)}")
            return False
    
    def is_x_url(self, url: str) -> bool:
        """检查是否为 X (Twitter) URL"""
        parsed = urlparse(url)
        return parsed.netloc.lower() in ['twitter.com', 'x.com', 'www.twitter.com', 'www.x.com']
    
    def is_youtube_url(self, url: str) -> bool:
        """检查是否为 YouTube URL"""
        parsed = urlparse(url)
        return parsed.netloc.lower() in ['youtube.com', 'www.youtube.com', 'youtu.be', 'm.youtube.com']
    
    def is_xvideos_url(self, url: str) -> bool:
        """检查是否为 xvideos URL"""
        parsed = urlparse(url)
        return any(domain in parsed.netloc for domain in ['xvideos.com', 'www.xvideos.com'])
    
    def is_pornhub_url(self, url: str) -> bool:
        """检查是否为 pornhub URL"""
        parsed = urlparse(url)
        return any(domain in parsed.netloc for domain in ['pornhub.com', 'www.pornhub.com', 'cn.pornhub.com'])
    
    def is_bilibili_url(self, url: str) -> bool:
        """检查是否为 Bilibili URL"""
        parsed = urlparse(url)
        return parsed.netloc.lower() in ['bilibili.com', 'www.bilibili.com', 'b23.tv']
    
    def is_douyin_url(self, url: str) -> bool:
        """检查是否为抖音 URL"""
        parsed = urlparse(url)
        return parsed.netloc.lower() in ['douyin.com', 'www.douyin.com', 'v.douyin.com']
    
    def is_magnet_url(self, url: str) -> bool:
        """检查是否为磁力链接"""
        return url.startswith('magnet:?')
    
    def is_torrent_url(self, url: str) -> bool:
        """检查是否为种子链接"""
        parsed = urlparse(url)
        return parsed.path.lower().endswith('.torrent')
    
    def extract_douyin_url(self, text: str) -> str:
        """从文本中提取抖音链接"""
        # 匹配抖音短链接
        douyin_pattern = r'https?://v\.douyin\.com/[A-Za-z0-9]+'
        match = re.search(douyin_pattern, text)
        if match:
            return match.group(0)
        return ""
    
    def get_download_path(self, url: str) -> Path:
        """根据 URL 确定下载路径"""
        if self.is_x_url(url):
            return self.x_download_path
        elif self.is_youtube_url(url):
            return self.youtube_download_path
        elif self.is_xvideos_url(url):
            return self.xvideos_download_path
        elif self.is_pornhub_url(url):
            return self.pornhub_download_path
        elif self.is_bilibili_url(url):
            return self.bilibili_download_path
        elif self.is_douyin_url(url):
            return self.douyin_download_path
        else:
            return self.youtube_download_path
    
    def get_platform_name(self, url: str) -> str:
        """获取平台名称"""
        if self.is_x_url(url):
            return "x"
        elif self.is_youtube_url(url):
            return "youtube"
        elif self.is_xvideos_url(url):
            return "xvideos"
        elif self.is_pornhub_url(url):
            return "pornhub"
        elif self.is_bilibili_url(url):
            return "bilibili"
        elif self.is_douyin_url(url):
            return "douyin"
        elif self.is_magnet_url(url) or self.is_torrent_url(url):
            return "torrent"
        else:
            return "other"
    
    def check_ytdlp_version(self) -> Dict[str, Any]:
        """检查yt-dlp版本"""
        try:
            import yt_dlp
            version = yt_dlp.version.__version__
            
            return {
                'success': True,
                'version': version,
                'info': f'yt-dlp 版本: {version}'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def check_video_formats(self, url: str) -> Dict[str, Any]:
        """检查视频的可用格式"""
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'listformats': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                formats = info.get('formats', [])
                available_formats = []
                
                for fmt in formats[:10]:  # 只显示前10个格式
                    format_info = {
                        'id': fmt.get('format_id', 'unknown'),
                        'ext': fmt.get('ext', 'unknown'),
                        'quality': fmt.get('format_note', 'unknown'),
                        'filesize': fmt.get('filesize', 0)
                    }
                    available_formats.append(format_info)
                
                # 检查是否有高分辨率格式
                has_high_res = any(f.get('height', 0) >= 2160 for f in formats)
                if has_high_res:
                    logger.info("检测到4K分辨率可用")
                
                return {
                    'success': True,
                    'title': info.get('title', 'Unknown'),
                    'formats': available_formats
                }
                
        except Exception as e:
            logger.error(f"格式检查失败: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def cleanup_duplicates(self):
        """清理重复文件"""
        try:
            cleaned_count = 0
            for directory in [self.x_download_path, self.youtube_download_path]:
                if directory.exists():
                    for file in directory.glob("*"):
                        if file.is_file() and " #" in file.name:
                            # 检查是否是视频文件
                            if any(file.name.endswith(ext) for ext in ['.mp4', '.mkv', '.webm', '.mov', '.avi']):
                                try:
                                    file.unlink()
                                    logger.info(f"删除重复文件: {file.name}")
                                    cleaned_count += 1
                                except Exception as e:
                                    logger.error(f"删除文件失败: {e}")
            return cleaned_count
        except Exception as e:
            logger.error(f"清理重复文件失败: {e}")
            return 0
    
    def _generate_display_filename(self, original_filename, timestamp):
        """生成用户友好的显示文件名"""
        try:
            # 移除时间戳前缀
            if original_filename.startswith(f'{timestamp}_'):
                display_name = original_filename[len(f'{timestamp}_'):]
            else:
                display_name = original_filename
            
            # 如果文件名太长，截断它
            if len(display_name) > 35:
                name, ext = os.path.splitext(display_name)
                display_name = name[:30] + "..." + ext
            
            return display_name
        except:
            return original_filename
    
    async def download_file(self, file_url: str, file_name: str, is_image: bool = False) -> Dict[str, Any]:
        """下载文件或图片
        
        Args:
            file_url: 文件URL
            file_name: 文件名
            is_image: 是否为图片
            
        Returns:
            Dict: 包含下载结果的字典
        """
        try:
            # 确定下载路径
            download_path = self.images_download_path if is_image else self.files_download_path
            
            # 生成唯一文件名
            timestamp = int(time.time())
            file_ext = os.path.splitext(file_name)[1]
            unique_filename = f"{timestamp}_{file_name}"
            
            # 完整文件路径
            file_path = download_path / unique_filename
            
            # 使用 requests 下载文件
            loop = asyncio.get_running_loop()
            
            def download_task():
                try:
                    # 设置代理（如果有）
                    proxies = None
                    if self.proxy_host:
                        proxies = {
                            'http': self.proxy_host,
                            'https': self.proxy_host
                        }
                    
                    # 下载文件
                    with requests.get(file_url, stream=True, proxies=proxies, verify=False, timeout=60) as r:
                        r.raise_for_status()
                        with open(file_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                    
                    # 获取文件大小
                    file_size = os.path.getsize(file_path)
                    
                    return {
                        'success': True,
                        'file_path': str(file_path),
                        'file_name': unique_filename,
                        'display_name': file_name,
                        'size': file_size,
                        'size_mb': round(file_size / (1024 * 1024), 2)
                    }
                except Exception as e:
                    logger.error(f"文件下载失败: {str(e)}")
                    # 如果文件已创建但下载失败，删除它
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    return {'success': False, 'error': str(e)}
            
            # 执行下载任务
            result = await loop.run_in_executor(None, download_task)
            return result
            
        except Exception as e:
            logger.error(f"文件下载处理失败: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    async def download_video(self, url: str, message_updater=None) -> Dict[str, Any]:
        download_path = self.get_download_path(url)
        platform = self.get_platform_name(url)
        import time
        timestamp = int(time.time())

        # X 平台单独处理
        if self.is_x_url(url):
            outtmpl = str(download_path / "%(id)s.%(ext)s")
            ydl_opts = {
                'outtmpl': outtmpl,
                'format': 'best',
                'writeinfojson': False,
                'writedescription': False,
                'writesubtitles': False,
                'writeautomaticsub': False,
                'nooverwrites': True,
                'restrictfilenames': True,
                'socket_timeout': 30,
                'retries': 10,
                'fragment_retries': 10,
                'extractor_retries': 10,
                'skip_unavailable_fragments': True,
                'nocheckcertificate': True,
                'prefer_insecure': True,
                'merge_output_format': 'mp4',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }],
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                }
            }
            if self.x_cookies_path and os.path.exists(self.x_cookies_path):
                ydl_opts['cookiefile'] = self.x_cookies_path
                logger.info(f"使用 X cookies: {self.x_cookies_path}")
            # ... 其余 X 平台下载流程不变 ...
        elif self.is_bilibili_url(url):
            # extract_info
            with yt_dlp.YoutubeDL({'quiet': True, 'cookiefile': self.b_cookies_path if self.b_cookies_path else None}) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title') or 'bilibili'
                title = re.sub(r'[\\/:*?"<>|]', '', title).strip() or 'bilibili'
                outtmpl = str(download_path / f"{title}.%(ext)s")
                formats = info.get('formats', [])
                video_streams = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
                audio_streams = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
                best_video = max(video_streams, key=lambda f: f.get('height', 0), default=None)
                best_audio = max(audio_streams, key=lambda f: f.get('abr', 0) if f.get('abr') else 0, default=None)
                combo_format = f"{best_video['format_id']}+{best_audio['format_id']}" if best_video and best_audio else 'best'
            ydl_opts = {
                'outtmpl': outtmpl,
                'format': combo_format,
                'writeinfojson': False,
                'writedescription': False,
                'writesubtitles': False,
                'writeautomaticsub': False,
                'nooverwrites': True,
                'restrictfilenames': True,
                'socket_timeout': 30,
                'retries': 10,
                'fragment_retries': 10,
                'extractor_retries': 10,
                'skip_unavailable_fragments': True,
                'nocheckcertificate': True,
                'prefer_insecure': True,
                'merge_output_format': 'mp4',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }],
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                }
            }
            if self.b_cookies_path and os.path.exists(self.b_cookies_path):
                ydl_opts['cookiefile'] = self.b_cookies_path
                logger.info(f"使用 Bilibili cookies: {self.b_cookies_path}")
        else:
            # 其它平台
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title')
                if not title or not title.strip():
                    logger.warning(f"未获取到视频标题，使用默认命名: {url}")
                    title = platform
                title = re.sub(r'[\\/:*?"<>|]', '', title)
                title = title.strip() or platform
                outtmpl = str(download_path / f"{title}.%(ext)s")

            ydl_opts = {
                'outtmpl': outtmpl,
                'format': 'best',
                'writeinfojson': False,
                'writedescription': False,
                'writesubtitles': False,
                'writeautomaticsub': False,
                'nooverwrites': True,
                'restrictfilenames': True,
                'socket_timeout': 30,
                'retries': 10,
                'fragment_retries': 10,
                'extractor_retries': 10,
                'skip_unavailable_fragments': True,
                'nocheckcertificate': True,
                'prefer_insecure': True,
                'merge_output_format': 'mp4',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }],
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                }
            }
            # Bilibili cookies
            if self.is_bilibili_url(url) and self.b_cookies_path and os.path.exists(self.b_cookies_path):
                ydl_opts['cookiefile'] = self.b_cookies_path
                logger.info(f"使用 Bilibili cookies: {self.b_cookies_path}")
            # ... 其余下载流程同原有（如 run_download、进度钩子等） ...

        # 3. 添加代理配置（如果设置了代理）
        if self.proxy_host:
            ydl_opts['proxy'] = self.proxy_host
            logger.info(f"使用代理服务器下载: {self.proxy_host}")
        else:
            logger.info("未使用代理服务器，直接连接下载")

        # 4. 添加进度钩子
        progress_data = {
            'filename': '',
            'total_bytes': 0,
            'downloaded_bytes': 0,
            'speed': 0,
            'status': 'downloading',
            'final_filename': '',
            'last_update': 0,
            'lock': threading.Lock(),
            'progress': 0.0
        }
        def progress_hook(d):
            try:
                with progress_data['lock']:
                    current_time = time.time()
                    if d['status'] == 'downloading':
                        raw_filename = d.get('filename', '')
                        display_filename = os.path.basename(raw_filename) if raw_filename else 'video.mp4'
                        progress_data.update({
                            'filename': display_filename,
                            'total_bytes': d.get('total_bytes') or d.get('total_bytes_estimate', 0),
                            'downloaded_bytes': d.get('downloaded_bytes', 0),
                            'speed': d.get('speed', 0),
                            'status': 'downloading',
                            'progress': (d.get('downloaded_bytes', 0) / (d.get('total_bytes') or d.get('total_bytes_estimate', 1))) * 100 if (d.get('total_bytes') or d.get('total_bytes_estimate', 0)) > 0 else 0.0
                        })
                        if current_time - progress_data['last_update'] > 1.0:
                            progress_data['last_update'] = current_time
                            if message_updater:
                                message_updater(progress_data.copy())
                    elif d['status'] == 'finished':
                        final_filename = d.get('filename', '')
                        display_filename = os.path.basename(final_filename) if final_filename else 'video.mp4'
                        progress_data.update({
                            'filename': display_filename,
                            'status': 'finished',
                            'final_filename': final_filename,
                            'progress': 100.0
                        })
                        if message_updater:
                            message_updater(progress_data.copy())
            except Exception as e:
                logger.error(f"进度钩子错误: {str(e)}")
        ydl_opts['progress_hooks'] = [progress_hook]

        def run_download():
            """下载视频"""
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        # 首先尝试获取视频信息
                        info = ydl.extract_info(url, download=False)
                        if not info:
                            raise Exception("无法获取视频信息")
                        
                        # 如果成功获取信息，开始下载
                        ydl.download([url])
                        logger.info("下载成功")
                        return True
                        
                    except Exception as e:
                        logger.error(f"下载失败: {str(e)}")
                        return False
                        
            except Exception as e:
                logger.error(f"下载器初始化失败: {str(e)}")
                return False
        
        try:
            # 运行下载
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, run_download)
            
            # 下载完成后兜底推送一次"完成"消息（防止小文件只触发一次进度）
            if progress_data['status'] != 'finished' and message_updater:
                progress_data['status'] = 'finished'
                progress_data['progress'] = 100.0
                message_updater(progress_data.copy())

            if not success:
                return {'success': False, 'error': '下载失败'}
            
            # 等待文件系统同步
            await asyncio.sleep(1)
            
            # 查找下载的文件
            final_file = progress_data.get('final_filename', '')
            downloaded_file = None
            file_size = 0
            original_filename = ""

            if final_file and os.path.exists(final_file):
                downloaded_file = final_file
                file_size = os.path.getsize(final_file)
                original_filename = os.path.basename(final_file)
            else:
                logger.warning("未能通过 progress_hook 获取最终文件名，尝试目录查找")
                try:
                    video_files = []
                    if self.is_x_url(url):
                        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                            info = ydl.extract_info(url, download=False)
                            video_id = info.get('id', 'x')
                        for ext in ['*.mp4', '*.mkv', '*.webm', '*.mov', '*.avi']:
                            video_files.extend(download_path.glob(f"{video_id}{ext[1:]}"))
                    else:
                        for ext in ['*.mp4', '*.mkv', '*.webm', '*.mov', '*.avi']:
                            video_files.extend(download_path.glob(ext))
                    if video_files:
                        import time
                        now = time.time()
                        recent_files = [f for f in video_files if now - f.stat().st_mtime < 3600]
                        if recent_files:
                            latest_file = max(recent_files, key=lambda f: f.stat().st_mtime)
                        else:
                            latest_file = max(video_files, key=lambda f: f.stat().st_mtime)
                        downloaded_file = str(latest_file)
                        file_size = latest_file.stat().st_size
                        original_filename = latest_file.name
                except Exception as e:
                    logger.error(f"搜索下载文件失败: {str(e)}")
            
            if downloaded_file and os.path.exists(downloaded_file):
                file_size_mb = file_size / (1024 * 1024)
                display_filename = progress_data.get('filename', original_filename)
                # 获取分辨率信息
                video_width = None
                video_height = None
                try:
                    import ffmpeg
                    probe = ffmpeg.probe(downloaded_file)
                    for stream in probe['streams']:
                        if stream['codec_type'] == 'video':
                            video_width = stream.get('width')
                            video_height = stream.get('height')
                            break
                except Exception as e:
                    logger.warning(f"获取分辨率失败: {e}")
                resolution = f"{video_width}x{video_height}" if video_width and video_height else "未知"
                if video_height:
                    if video_height >= 2160:
                        resolution += " (2160p)"
                    elif video_height >= 1440:
                        resolution += " (1440p)"
                    elif video_height >= 1080:
                        resolution += " (1080p)"
                    elif video_height >= 720:
                        resolution += " (720p)"
                    elif video_height >= 480:
                        resolution += " (480p)"
                    elif video_height >= 360:
                        resolution += " (360p)"
                    else:
                        resolution += " (240p)"
                
                return {
                    'success': True,
                    'filename': display_filename,
                    'full_path': downloaded_file,
                    'size_mb': round(file_size_mb, 2),
                    'platform': platform,
                    'download_path': str(download_path),
                    'original_filename': original_filename,
                    'resolution': resolution
                }
            else:
                return {'success': False, 'error': '无法找到下载的文件'}
                
        except Exception as e:
            logger.error(f"下载失败: {str(e)}")
            return {'success': False, 'error': str(e)}

class TelegramBot:
    def __init__(self, token: str, downloader: VideoDownloader, qbittorrent_client=None):
        self.downloader = downloader
        self.qbittorrent_client = qbittorrent_client
        
        if self.downloader.proxy_host:
            logger.info(f"Telegram Bot 使用代理: {self.downloader.proxy_host}")
            self.application = Application.builder().token(token).proxy(self.downloader.proxy_host).build()
        else:
            logger.info("Telegram Bot 直接连接")
            self.application = Application.builder().token(token).build()
        self.active_downloads = {}  # task_id: True
        self.progress_data = {}     # task_id: progress_data dict
        self.progress_message = {}  # task_id: telegram message object
        
    async def version_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /version 命令 - 显示版本信息"""
        try:
            version_info = self.downloader.check_ytdlp_version()
            
            if version_info['success']:
                version_text = f"""系统版本信息

yt-dlp: {version_info['version']}
Python: {sys.version.split()[0]}
机器人: v2.1 (Yunx 多功能版)

支持的功能:
✅ 多平台视频下载
✅ 文件和图片下载
✅ 种子下载转发
✅ 智能错误恢复
✅ 详细调试日志

如果下载仍有问题，请使用 /formats 命令检查视频格式"""
                
                await update.message.reply_text(version_text)
            else:
                await update.message.reply_text(f"无法获取版本信息: {version_info['error']}")
                
        except Exception as e:
            await update.message.reply_text(f"版本检查失败: {str(e)}")
    
    async def formats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /formats 命令 - 检查视频格式"""
        try:
            # 获取用户发送的URL
            if not context.args:
                await update.message.reply_text("""格式检查命令

使用方法：
/formats <视频链接>

示例：
/formats https://www.youtube.com/watch?v=xxx

此命令会显示视频的可用格式，帮助调试下载问题。""")
                return
            
            url = context.args[0]
            
            # 验证URL
            if not url.startswith(('http://', 'https://')):
                await update.message.reply_text("请提供有效的视频链接")
                return
            
            check_message = await update.message.reply_text("正在检查视频格式...")
            
            # 检查格式
            result = self.downloader.check_video_formats(url)
            
            if result['success']:
                formats_text = f"""视频格式信息

标题：{result['title']}

可用格式（前10个）：
"""
                for i, fmt in enumerate(result['formats'], 1):
                    size_info = ""
                    if fmt['filesize'] and fmt['filesize'] > 0:
                        size_mb = fmt['filesize'] / (1024 * 1024)
                        size_info = f" ({size_mb:.1f}MB)"
                    
                    formats_text += f"{i}. ID: {fmt['id']} | {fmt['ext']} | {fmt['quality']}{size_info}\n"
                
                formats_text += "\n如果下载失败，可以尝试其他视频或报告此信息。"
                
                await check_message.edit_text(formats_text)
            else:
                await check_message.edit_text(f"格式检查失败: {result['error']}")
                
        except Exception as e:
            await update.message.reply_text(f"格式检查出错: {str(e)}")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令"""
        welcome_message = """Yunx 多功能下载机器人已启动！

支持的功能：
• 视频下载：支持多个平台
• 文件下载：直接发送文件给机器人
• 图片下载：直接发送图片给机器人
• 种子下载：发送磁力链接或种子链接

支持的平台：
• X (Twitter)
• YouTube
• Bilibili
• 抖音
• Xvideos
• Pornhub

使用方法：
• 视频下载：直接发送视频链接
• 文件/图片下载：直接发送文件或图片
• 种子下载：发送磁力链接或种子链接

命令：
• /start - 显示此帮助信息
• /status - 查看下载统计
• /cleanup - 清理重复文件
• /formats <链接> - 检查视频格式
• /version - 查看版本信息

特性：
✅ 实时下载进度显示
✅ 智能格式选择和备用方案
✅ 自动格式转换
✅ 按平台分类存储
✅ 支持 NSFW 内容下载
✅ 唯一文件名，避免覆盖"""
        await update.message.reply_text(welcome_message)
    
    async def cleanup_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /cleanup 命令"""
        cleanup_message = await update.message.reply_text("开始清理重复文件...")
        
        try:
            cleaned_count = self.downloader.cleanup_duplicates()
            
            if cleaned_count > 0:
                completion_text = f"""清理完成!
删除了 {cleaned_count} 个重复文件
释放了存储空间"""
            else:
                completion_text = "清理完成! 未发现重复文件"
                
            await cleanup_message.edit_text(completion_text)
        except Exception as e:
            await cleanup_message.edit_text(f"清理失败: {str(e)}")
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /status 命令"""
        try:
            # 统计文件
            video_extensions = ['*.mp4', '*.mkv', '*.webm', '*.mov', '*.avi']
            
            x_files = []
            youtube_files = []
            bilibili_files = []
            douyin_files = []
            xvideos_files = []
            pornhub_files = []
            files_count = 0
            images_count = 0
            
            for ext in video_extensions:
                x_files.extend(self.downloader.x_download_path.glob(ext))
                youtube_files.extend(self.downloader.youtube_download_path.glob(ext))
                bilibili_files.extend(self.downloader.bilibili_download_path.glob(ext))
                douyin_files.extend(self.downloader.douyin_download_path.glob(ext))
                xvideos_files.extend(self.downloader.xvideos_download_path.glob(ext))
                pornhub_files.extend(self.downloader.pornhub_download_path.glob(ext))
            
            # 统计文件和图片
            files_count = len(list(self.downloader.files_download_path.glob('*')))
            images_count = len(list(self.downloader.images_download_path.glob('*')))
            
            total_size = 0
            for file_list in [x_files, youtube_files, bilibili_files, douyin_files, xvideos_files, pornhub_files]:
                for file in file_list:
                    try:
                        total_size += file.stat().st_size
                    except:
                        pass
            
            # 统计文件和图片大小
            for file_path in self.downloader.files_download_path.glob('*'):
                try:
                    total_size += file_path.stat().st_size
                except:
                    pass
                    
            for file_path in self.downloader.images_download_path.glob('*'):
                try:
                    total_size += file_path.stat().st_size
                except:
                    pass
            
            total_size_mb = total_size / (1024 * 1024)
            total_size_gb = total_size_mb / 1024
            
            # 获取种子下载状态
            torrents_info = ""
            if self.qbittorrent_client:
                try:
                    result = self.qbittorrent_client.get_torrents()
                    if result['success']:
                        torrents = result['torrents']
                        active_torrents = len([t for t in torrents if t.get('state') in ['downloading', 'stalledDL', 'checkingDL']])
                        completed_torrents = len([t for t in torrents if t.get('state') in ['uploading', 'stalledUP', 'checkingUP', 'pausedUP']])
                        
                        torrents_info = f"\n\n种子下载状态:\n活跃下载: {active_torrents} 个\n已完成: {completed_torrents} 个\n总计: {len(torrents)} 个"
                except:
                    torrents_info = "\n\n种子下载状态: 无法获取"
            
            status_text = f"""下载统计

X 视频: {len(x_files)} 个
YouTube 视频: {len(youtube_files)} 个
Bilibili 视频: {len(bilibili_files)} 个
抖音视频: {len(douyin_files)} 个
Xvideos 视频: {len(xvideos_files)} 个
Pornhub 视频: {len(pornhub_files)} 个
文件: {files_count} 个
图片: {images_count} 个

总计视频: {len(x_files) + len(youtube_files) + len(bilibili_files) + len(douyin_files) + len(xvideos_files) + len(pornhub_files)} 个
总计文件: {files_count + images_count} 个
总大小: {total_size_mb:.2f}MB ({total_size_gb:.2f}GB)

机器人状态: 正常运行
活跃下载: {len(self.active_downloads)} 个{torrents_info}"""

            await update.message.reply_text(status_text)
        except Exception as e:
            await update.message.reply_text(f"获取状态失败: {str(e)}")
    
    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理用户发送的 URL"""
        url = update.message.text.strip()
        
        # 检查是否是抖音链接
        if not url.startswith(('http://', 'https://')):
            url = self.downloader.extract_douyin_url(url)
            if not url:
                await update.message.reply_text("请发送有效的视频链接或磁力链接")
                return
        
        # 检查是否是磁力链接或种子链接
        if self.downloader.is_magnet_url(url) or self.downloader.is_torrent_url(url):
            if not self.qbittorrent_client:
                await update.message.reply_text("未配置种子下载功能，无法处理磁力链接或种子链接")
                return
            
            # 处理种子下载
            torrent_message = await update.message.reply_text("正在添加种子下载任务...")
            
            try:
                result = self.qbittorrent_client.add_torrent(url)
                
                if result['success']:
                    await torrent_message.edit_text(f"种子添加成功!\n\n已推送到 qBittorrent 下载\n\n使用 /status 命令查看下载状态")
                else:
                    await torrent_message.edit_text(f"种子添加失败: {result.get('error', '未知错误')}")
            except Exception as e:
                await torrent_message.edit_text(f"处理种子时出错: {str(e)}")
            
            return
        
        # 检查是否是支持的视频链接
        if not (self.downloader.is_x_url(url) or 
                self.downloader.is_youtube_url(url) or
                self.downloader.is_xvideos_url(url) or 
                self.downloader.is_pornhub_url(url) or
                self.downloader.is_bilibili_url(url) or
                self.downloader.is_douyin_url(url)):
            await update.message.reply_text("目前只支持 X (Twitter)、YouTube、Xvideos、Pornhub、Bilibili 和抖音链接")
            return

        # 生成唯一 task_id
        task_id = str(uuid.uuid4())
        self.active_downloads[task_id] = True
        self.progress_data[task_id] = {}
        progress_message = await update.message.reply_text(f"开始下载 {self.downloader.get_platform_name(url)} 视频...")
        self.progress_message[task_id] = progress_message
        current_loop = asyncio.get_running_loop()

        def update_progress(progress_info):
            try:
                self.progress_data[task_id] = progress_info.copy()
                filename = progress_info.get('filename', 'video.mp4')
                total_bytes = progress_info.get('total_bytes', 0)
                downloaded_bytes = progress_info.get('downloaded_bytes', 0)
                speed = progress_info.get('speed', 0)
                status = progress_info.get('status', 'downloading')
                eta_text = ""
                if speed and total_bytes and downloaded_bytes < total_bytes:
                    remaining = total_bytes - downloaded_bytes
                    eta = int(remaining / speed)
                    mins, secs = divmod(eta, 60)
                    if mins > 0:
                        eta_text = f"{mins}分{secs}秒"
                    else:
                        eta_text = f"{secs}秒"
                elif speed:
                    eta_text = "计算中"
                else:
                    eta_text = "未知"
                display_filename = self._clean_filename_for_display(filename)
                if status == 'finished' or progress_info.get('progress') == 100.0:
                    progress = 100.0
                    progress_bar = self._create_progress_bar(progress)
                    size_mb = total_bytes / (1024 * 1024) if total_bytes > 0 else downloaded_bytes / (1024 * 1024)
                    progress_text = (
                        f"📝 文件：{display_filename}\n"
                        f"💾 大小：{size_mb:.2f}MB\n"
                        f"⚡ 速度：完成\n"
                        f"⏳ 预计剩余：0秒\n"
                        f"📊 进度：{progress_bar} ({progress:.1f}%)"
                    )
                    asyncio.run_coroutine_threadsafe(
                        self.progress_message[task_id].edit_text(progress_text),
                        current_loop
                    )
                    return
                if total_bytes > 0:
                    progress = (downloaded_bytes / total_bytes) * 100
                    progress_bar = self._create_progress_bar(progress)
                    size_mb = total_bytes / (1024 * 1024)
                    speed_mb = (speed or 0) / (1024 * 1024)
                    progress_text = (
                        f"📝 文件：{display_filename}\n"
                        f"💾 大小：{size_mb:.2f}MB\n"
                        f"⚡ 速度：{speed_mb:.2f}MB/s\n"
                        f"⏳ 预计剩余：{eta_text}\n"
                        f"📊 进度：{progress_bar} ({progress:.1f}%)"
                    )
                    asyncio.run_coroutine_threadsafe(
                        self.progress_message[task_id].edit_text(progress_text),
                        current_loop
                    )
                else:
                    downloaded_mb = downloaded_bytes / (1024 * 1024) if downloaded_bytes > 0 else 0
                    speed_mb = (speed or 0) / (1024 * 1024)
                    progress_text = (
                        f"📝 文件：{display_filename}\n"
                        f"💾 大小：{downloaded_mb:.2f}MB\n"
                        f"⚡ 速度：{speed_mb:.2f}MB/s\n"
                        f"⏳ 预计剩余：未知\n"
                        f"📊 进度：下载中..."
                    )
                    asyncio.run_coroutine_threadsafe(
                        self.progress_message[task_id].edit_text(progress_text),
                        current_loop
                    )
            except Exception as e:
                logger.error(f"进度更新失败: {e}")

        try:
            result = await self.downloader.download_video(url, update_progress)
            
            if result['success']:
                progress_info = self.progress_data.get(task_id, {})
                display_filename = self._clean_filename_for_display(result.get('filename', progress_info.get('filename', 'video.mp4')))
                resolution = result.get('resolution', '未知')
                completion_text = f"""下载完成!\n📝 文件名：{display_filename}\n📂 保存位置：{result.get('platform', '未知')} 文件夹\n💾 文件大小：{result.get('size_mb', 0)}MB\n🎥 分辨率：{resolution}\n✅ 进度：████████████████████ (100%)"""
                await self.progress_message[task_id].edit_text(completion_text)
            else:
                await self.progress_message[task_id].edit_text(f"下载失败：{result.get('error', '未知错误')}")
        except Exception as e:
            logger.error(f"下载过程中发生错误: {str(e)}")
            await self.progress_message[task_id].edit_text(f"下载失败：{str(e)}")
        finally:
            self.active_downloads.pop(task_id, None)
            self.progress_data.pop(task_id, None)
            self.progress_message.pop(task_id, None)
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理用户发送的图片"""
        try:
            # 获取最大尺寸的图片
            photo = update.message.photo[-1]
            
            # 获取文件信息
            file = await context.bot.get_file(photo.file_id)
            file_url = file.file_path
            
            # 生成文件名
            timestamp = int(time.time())
            file_name = f"photo_{timestamp}.jpg"
            
            # 发送下载中消息
            download_message = await update.message.reply_text("正在下载图片...")
            
            # 下载图片
            result = await self.downloader.download_file(file_url, file_name, is_image=True)
            
            if result['success']:
                size_kb = result['size'] / 1024
                size_text = f"{size_kb:.2f}KB" if size_kb < 1024 else f"{result['size_mb']:.2f}MB"
                
                await download_message.edit_text(
                    f"图片下载完成!\n"
                    f"📝 文件名：{result['display_name']}\n"
                    f"📂 保存位置：images 文件夹\n"
                    f"💾 文件大小：{size_text}\n"
                    f"✅ 状态：已保存"
                )
            else:
                await download_message.edit_text(f"图片下载失败：{result.get('error', '未知错误')}")
                
        except Exception as e:
            logger.error(f"处理图片时出错: {str(e)}")
            await update.message.reply_text(f"处理图片时出错: {str(e)}")
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理用户发送的文件"""
        try:
            # 获取文件信息
            document = update.message.document
            file_name = document.file_name or f"file_{int(time.time())}"
            
            # 获取文件
            file = await context.bot.get_file(document.file_id)
            file_url = file.file_path
            
            # 发送下载中消息
            download_message = await update.message.reply_text("正在下载文件...")
            
            # 下载文件
            result = await self.downloader.download_file(file_url, file_name)
            
            if result['success']:
                size_text = f"{result['size_mb']:.2f}MB"
                
                await download_message.edit_text(
                    f"文件下载完成!\n"
                    f"📝 文件名：{result['display_name']}\n"
                    f"📂 保存位置：files 文件夹\n"
                    f"💾 文件大小：{size_text}\n"
                    f"✅ 状态：已保存"
                )
            else:
                await download_message.edit_text(f"文件下载失败：{result.get('error', '未知错误')}")
                
        except Exception as e:
            logger.error(f"处理文件时出错: {str(e)}")
            await update.message.reply_text(f"处理文件时出错: {str(e)}")
    
    def _clean_filename_for_display(self, filename):
        """清理文件名用于显示"""
        try:
            # 移除时间戳前缀如果存在
            import re
            if re.match(r'^\d{10}_', filename):
                display_name = filename[11:]
            else:
                display_name = filename
            
            # 如果文件名太长，进行智能截断
            if len(display_name) > 35:
                name, ext = os.path.splitext(display_name)
                display_name = name[:30] + "..." + ext
            
            return display_name
        except:
            return filename if len(filename) <= 35 else filename[:32] + "..."
    
    def _create_progress_bar(self, progress: float, length: int = 20) -> str:
        """创建进度条"""
        filled_length = int(length * progress / 100)
        bar = '█' * filled_length + '░' * (length - filled_length)
        return bar
    
    def run(self):
        """启动机器人"""
        logger.info("Yunx 多功能下载机器人启动中...")
        
        # 添加处理器
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("cleanup", self.cleanup_command))
        self.application.add_handler(CommandHandler("formats", self.formats_command))
        self.application.add_handler(CommandHandler("version", self.version_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        
        logger.info("Yunx 机器人已经正常启动")
        
        # 启动机器人
        self.application.run_polling()


def main():
    """主函数"""
    # 从环境变量获取配置
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    download_path = os.getenv('DOWNLOAD_PATH', '/downloads')
    x_cookies_path = os.getenv('X_COOKIES')
    
    if not bot_token:
        logger.error("请设置 TELEGRAM_BOT_TOKEN 环境变量")
        sys.exit(1)
    
    logger.info(f"下载路径: {download_path}")
    if x_cookies_path:
        logger.info(f"X Cookies 路径: {x_cookies_path}")
    
    # 创建下载器
    downloader = VideoDownloader(download_path, x_cookies_path)
    
    # 检查是否配置了 qBittorrent
    qbittorrent_client = None
    qbittorrent_host = os.getenv('QBITTORRENT_HOST')
    qbittorrent_username = os.getenv('QBITTORRENT_USERNAME')
    qbittorrent_password = os.getenv('QBITTORRENT_PASSWORD')
    qbittorrent_download_path = os.getenv('QBITTORRENT_DOWNLOAD_PATH')
    
    if qbittorrent_host and qbittorrent_username and qbittorrent_password:
        logger.info(f"qBittorrent 配置: {qbittorrent_host}")
        qbittorrent_client = QBittorrentClient(
            qbittorrent_host,
            qbittorrent_username,
            qbittorrent_password,
            qbittorrent_download_path
        )
    else:
        logger.info("未配置 qBittorrent，种子下载功能将不可用")
    
    # 创建机器人
    bot = TelegramBot(bot_token, downloader, qbittorrent_client)
    
    # 启动机器人
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("机器人已停止")
    except Exception as e:
        logger.error(f"机器人运行出错: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main()