#!/usr/bin/env python3
"""
AI模型识别器后端代理服务器
解决浏览器 CORS 限制，代理所有 API 请求
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import json
import requests as req_lib
import os
import webbrowser
import threading
import uuid
import platform

# ========================================
#  请求头伪装预设 (全小写 key，匹配真实 Node.js SDK)
# ========================================
_STAINLESS_OS = {
    'Darwin': 'MacOS', 'Linux': 'Linux', 'Windows': 'Windows'
}.get(platform.system(), f'Other:{platform.system()}')

_STAINLESS_ARCH = {
    'x86_64': 'x64', 'AMD64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64',
    'x86': 'x32', 'i386': 'x32', 'i686': 'x32',
}.get(platform.machine(), f'other:{platform.machine()}')

# Codex 安装 ID — 进程生命周期内固定
_CODEX_INSTALLATION_ID = str(uuid.uuid4())

HEADER_PRESETS = {
    'claude-code': {
        'accept': 'application/json',
        'accept-encoding': 'gzip, deflate, br',
        'connection': 'keep-alive',
        'user-agent': 'Anthropic/JS 0.109.0',
        'x-stainless-lang': 'js',
        'x-stainless-package-version': '0.109.0',
        'x-stainless-os': _STAINLESS_OS,
        'x-stainless-arch': _STAINLESS_ARCH,
        'x-stainless-runtime': 'node',
        'x-stainless-runtime-version': 'v22.13.1',
        'x-stainless-retry-count': '0',
    },
    'codex': {
        'accept': 'application/json',
        'accept-encoding': 'gzip, deflate, br',
        'connection': 'keep-alive',
        'user-agent': 'OpenAI/JS 6.45.0',
        'x-stainless-lang': 'js',
        'x-stainless-package-version': '6.45.0',
        'x-stainless-os': _STAINLESS_OS,
        'x-stainless-arch': _STAINLESS_ARCH,
        'x-stainless-runtime': 'node',
        'x-stainless-runtime-version': 'v22.13.1',
        'x-stainless-retry-count': '0',
        'openai-beta': 'responses_websockets=2026-02-06',
        'x-codex-installation-id': _CODEX_INSTALLATION_ID,
    },
}

# 默认浏览器伪装头
DEFAULT_BROWSER_HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'accept-encoding': 'gzip, deflate, br, zstd',
    'accept-language': 'en-US,en;q=0.9',
    'connection': 'keep-alive',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}

# 创建全局 Session，清除默认头，避免泄漏 python-requests 指纹
_session = req_lib.Session()
_session.headers.clear()

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class ProxyHandler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        """处理 GET 请求 - 提供 HTML 文件和静态资源"""
        if self.path == '/' or self.path == '/index.html':
            self.serve_html()
        elif self.path == '/chart.js':
            self.serve_static('chart.js', 'application/javascript')
        else:
            self.send_error(404, "File not found")

    def do_POST(self):
        """处理 POST 请求 - 代理 API 调用"""
        # 代理所有 OpenAI 和 Anthropic API 请求
        if '/chat/completions' in self.path or '/messages' in self.path:
            self.proxy_api_request()
        else:
            self.send_error(404, "Endpoint not found")

    def serve_html(self):
        """返回 HTML 文件"""
        try:
            with open('hlwy-ai-checker.html', 'r', encoding='utf-8') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))
        except FileNotFoundError:
            self.send_error(404, "hlwy-ai-checker.html not found")

    def serve_static(self, filename, content_type):
        """返回静态文件"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))
        except FileNotFoundError:
            self.send_error(404, f"{filename} not found")

    def proxy_api_request(self):
        """代理 API 请求到真实的 API 端点"""
        try:
            # 读取请求体
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)

            # 确定目标 URL
            if '/chat/completions' in self.path:
                # OpenAI API
                base_url = self.headers.get('X-Target-Base-URL', 'https://api.openai.com/v1')
                target_url = f"{base_url.rstrip('/')}/chat/completions"
            elif '/messages' in self.path:
                # Anthropic API
                base_url = self.headers.get('X-Target-Base-URL', 'https://api.anthropic.com/v1')
                target_url = f"{base_url.rstrip('/')}/messages"
            else:
                self.send_json_response(400, {'error': '不支持的 API 端点'})
                return

            # 获取请求头伪装预设
            header_preset = self.headers.get('X-Header-Preset', 'default')

            # 构建代理请求头 (全小写 key)
            if header_preset in HEADER_PRESETS:
                headers = dict(HEADER_PRESETS[header_preset])
                # 每次请求动态生成唯一 request-id
                headers['x-request-id'] = f'req_{uuid.uuid4().hex}'
            else:
                headers = dict(DEFAULT_BROWSER_HEADERS)

            # 复制必要的业务请求头 (保持小写 key)
            header_map = {
                'Content-Type': 'content-type',
                'Authorization': 'authorization',
                'anthropic-version': 'anthropic-version',
                'x-api-key': 'x-api-key',
            }
            for src_key, dst_key in header_map.items():
                val = self.headers.get(src_key)
                if val:
                    headers[dst_key] = val

            # 使用 requests 发送请求 (保留原始 header 大小写)
            try:
                resp = _session.post(
                    target_url,
                    data=body,
                    headers=headers,
                    timeout=30,
                )

                self.send_response(resp.status_code)
                self.send_header('Content-Type', 'application/json')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(resp.content)

            except req_lib.exceptions.ConnectionError as e:
                self.send_json_response(500, {'error': f'网络错误: {str(e)}'})
            except req_lib.exceptions.Timeout as e:
                self.send_json_response(504, {'error': f'请求超时: {str(e)}'})

        except Exception as e:
            self.send_json_response(500, {'error': f'服务器错误: {str(e)}'})

    def send_json_response(self, status_code, data):
        """发送 JSON 响应"""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def send_cors_headers(self):
        """添加 CORS 头"""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, anthropic-version, x-api-key, X-Target-Base-URL, X-Header-Preset')

    def log_message(self, format, *args):
        """自定义日志格式"""
        print(f"[{self.log_date_time_string()}] {format % args}")


def main():
    HOST = 'localhost'
    PORT = 8000

    # 检查 HTML ��件是否存在
    if not os.path.exists('hlwy-ai-checker.html'):
        print("错误: 找不到 hlwy-ai-checker.html 文件")
        print("请确保在包含该文件的目录中运行此脚本")
        return

    server = ThreadingHTTPServer((HOST, PORT), ProxyHandler)
    url = f'http://{HOST}:{PORT}'
    print(f"""
╔════════════════════════════════════════════════════════╗
║      hlwy-ai-checker v2.2.0 - AI模型识别器               ║
╚════════════════════════════════════════════════════════╝
本项目github地址：https://github.com/hanlinwenyuan/hlwy-ai-checker

🌐 前端访问地址: {url}

按 Ctrl+C 停止
""")

    threading.Timer(0.5, webbrowser.open, args=[url]).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n已停止")
        server.shutdown()


if __name__ == '__main__':
    main()
