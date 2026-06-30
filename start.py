#!/usr/bin/env python3
"""
AI模型识别器后端代理服务器
解决浏览器 CORS 限制，代理所有 API 请求
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import urllib.request
import urllib.error
import os
import webbrowser
import threading

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

            # 构建代理请求头
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            }

            # 复制必要的请求头
            for header in ['Content-Type', 'Authorization', 'anthropic-version', 'x-api-key']:
                if header in self.headers:
                    headers[header] = self.headers[header]

            # 构建请求
            req = urllib.request.Request(
                target_url,
                data=body,
                headers=headers,
                method='POST'
            )

            # 发送请求
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    response_body = response.read()

                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(response_body)

            except urllib.error.HTTPError as e:
                error_body = e.read()
                self.send_response(e.code)
                self.send_header('Content-Type', 'application/json')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(error_body)

            except urllib.error.URLError as e:
                self.send_json_response(500, {'error': f'网络错误: {str(e)}'})

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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, anthropic-version, x-api-key, X-Target-Base-URL')

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

    server = HTTPServer((HOST, PORT), ProxyHandler)
    url = f'http://{HOST}:{PORT}'
    print(f"""
╔════════════════════════════════════════════════════════╗
║      hlwy-ai-checker v2.0.0 - AI模型识别器              ║
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
