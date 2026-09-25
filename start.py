#!/usr/bin/env python3
"""
hlwy-ai-checker AI 模型鉴别器后端代理服务器
解决浏览器 CORS 限制，代理所有 API 请求
"""

import importlib.util
import subprocess
import sys


def _ensure_dependencies():
    """自动检查并安装第三方依赖。

    项目仅依赖 requests。若运行环境未安装，则尝试通过 pip 自动安装，
    默认源失败时回退到清华 PyPI 镜像，避免用户手动处理依赖。
    """
    if importlib.util.find_spec("requests") is not None:
        return

    print("[依赖检查] 未检测到 requests，正在自动安装 ...")
    requirement = "requests>=2.34.2"
    base_cmd = [sys.executable, "-m", "pip", "install", requirement]
    mirrors = (
        ([], "默认源"),
        (["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"], "清华镜像"),
    )
    for extra, name in mirrors:
        try:
            subprocess.check_call(base_cmd + extra)
            print("[依赖检查] requests 安装完成。")
            return
        except Exception as e:
            if name == "默认源":
                print(f"[依赖检查] {name}安装失败（{e}），改用清华镜像重试 ...")
            else:
                print(f"[依赖检查] 自动安装失败：{e}")

    print("[依赖检查] 请手动执行：pip install requests")
    sys.exit(1)


_ensure_dependencies()

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
import json
import re
import time
import requests as req_lib
import os
import webbrowser
import threading
import uuid
import shutil
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


# ========================================
#  一键鉴别 — 官方基准仓库
# ========================================
APP_VERSION   = '2.5.0'
GITHUB_OWNER  = 'hanlinwenyuan'
GITHUB_REPO   = 'hlwy-ai-checker'
GITHUB_BRANCH = 'main'
BASELINE_DIR  = 'baselines'

# 缓存 TTL（秒）— 避免频繁请求 GitHub 触发匿名 API 限流 (60 次/小时)
BASELINE_LIST_TTL = 1800    # 模型列表缓存 30 分钟
BASELINE_FILE_TTL = 1800    # 单个基准文件缓存 30 分钟

# 列表清单文件：走 raw.githubusercontent（不计入 API 限额），优先于 Contents API
BASELINE_INDEX = 'index.json'

# 磁盘缓存：重启后不必重新消耗 API 限额
CACHE_PATH = os.path.join(os.path.expanduser('~'), '.hlwy-ai-checker', 'cache.json')

# 可选的 GitHub Token：配置后限额从 60 次/小时提升到 5000 次/小时
GITHUB_TOKEN = (os.environ.get('HLWY_GITHUB_TOKEN')
                or os.environ.get('GITHUB_TOKEN') or '').strip()

_baseline_cache = {'list': None, 'list_source': '', 'list_ts': 0.0, 'files': {}}
_baseline_lock  = threading.Lock()

# 运行中的 HTTP server，自动重启时需要先释放端口
_server_ref = None

# 重启后的子进程不再重复打开浏览器（页面自己会刷新）
NO_BROWSER_ENV = 'HLWY_NO_BROWSER'

# 合法基准文件名（防止路径穿越 / URL 注入）
_SAFE_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\-]{0,80}$')

# 拉取 GitHub 用的独立 Session：不带 accept-encoding，避免 zstd 等压缩解码失败
_gh_session = req_lib.Session()
_gh_session.headers.clear()

GITHUB_HEADERS = {
    'accept': 'application/vnd.github+json, application/json, */*',
    'accept-language': 'en-US,en;q=0.9',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}

if GITHUB_TOKEN:
    GITHUB_HEADERS['authorization'] = f'Bearer {GITHUB_TOKEN}'


class RateLimited(RuntimeError):
    """GitHub 返回 403/429 限流，附带建议的等待秒数"""
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        if retry_after:
            mins = max(1, int(retry_after // 60))
            super().__init__(f'GitHub 请求过于频繁（匿名限额 60 次/小时），'
                             f'约 {mins} 分钟后自动恢复')
        else:
            super().__init__('GitHub 请求过于频繁（匿名限额 60 次/小时），请稍后再试')


def _retry_after_seconds(resp):
    """从限流响应里推算还需等待多久"""
    ra = resp.headers.get('retry-after')
    if ra:
        try:
            return max(0, int(float(ra)))
        except ValueError:
            pass
    reset = resp.headers.get('x-ratelimit-reset')
    if reset:
        try:
            return max(0, int(float(reset)) - int(time.time()))
        except ValueError:
            pass
    return None


def _gh_request(url, timeout=15):
    """带伪装头的 GET，返回 Response。限流时抛 RateLimited"""
    resp = _gh_session.get(url, headers=GITHUB_HEADERS, timeout=timeout)

    remaining = resp.headers.get('x-ratelimit-remaining')
    if resp.status_code == 429 or (
            resp.status_code == 403 and remaining == '0'):
        raise RateLimited(_retry_after_seconds(resp))

    resp.raise_for_status()
    return resp


def _gh_get(url, timeout=15):
    """带伪装头的 GET，返回解析后的 JSON。限流时抛 RateLimited"""
    return _gh_request(url, timeout=timeout).json()


def _raw_url(path):
    """raw.githubusercontent 直链 — 不计入 GitHub API 限额"""
    return (f'https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}'
            f'/{GITHUB_BRANCH}/{path}')


def fetch_baseline_list(force=False):
    """
    获取 baselines 目录下的全部模型。
    数据源: GitHub Contents API
    返回 (models, source)，models 形如 [{'id':..., 'file':...}]
    """
    now = time.time()
    with _baseline_lock:
        cached = _baseline_cache['list']
        if not force and cached and (now - _baseline_cache['list_ts']) < BASELINE_LIST_TTL:
            return cached, _baseline_cache['list_source'] + '(缓存)'

    errors = []

    # 1) 优先读 baselines/index.json（raw 直链，不消耗 API 限额）
    try:
        data = _gh_get(_raw_url(f'{BASELINE_DIR}/{BASELINE_INDEX}'), timeout=20)
        models = _parse_index(data)
        if models:
            return _store_list(models, 'GitHub')
        errors.append('index.json: 内容为空')
    except Exception as e:
        errors.append(f'index.json: {e}')

    # 2) 退回 Contents API（会消耗匿名限额 60 次/小时）
    try:
        data = _gh_get(
            f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
            f'/contents/{BASELINE_DIR}?ref={GITHUB_BRANCH}'
        )
        models = [
            {'id': it['name'][:-5], 'file': it['name']}
            for it in data
            if it.get('type') == 'file'
            and it.get('name', '').endswith('.json')
            and it.get('name') != BASELINE_INDEX
            and _SAFE_NAME_RE.match(it['name'][:-5] or '')
        ]
        if models:
            return _store_list(models, 'GitHub')
        errors.append('GitHub API: baselines 目录为空')
    except RateLimited as e:
        errors.append(str(e))
    except Exception as e:
        errors.append(f'GitHub API: {e}')

    # 3) 失败：如果有过期缓存（含上次运行落盘的），降级返回，总比什么都没有强
    with _baseline_lock:
        if _baseline_cache['list']:
            return _baseline_cache['list'], _baseline_cache['list_source'] + '(过期缓存)'

    raise RuntimeError('；'.join(errors))


def _parse_index(data):
    """
    解析 baselines/index.json。兼容两种写法：
      ["gpt-6-astra", ...]      纯名称数组（生成器输出这种）
      {"models": [...]}         对象数组也接受，多余字段忽略
    """
    items = data.get('models', []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    models, seen = [], set()
    for it in items:
        if isinstance(it, str):
            name = it
        elif isinstance(it, dict):
            name = it.get('id') or it.get('name') or ''
        else:
            continue
        if name.endswith('.json'):
            name = name[:-5]
        if _SAFE_NAME_RE.match(name) and name not in seen:
            seen.add(name)
            models.append({'id': name, 'file': f'{name}.json'})
    return models


def _store_list(models, source):
    models.sort(key=lambda m: m['id'].lower())
    with _baseline_lock:
        _baseline_cache['list']        = models
        _baseline_cache['list_source'] = source
        _baseline_cache['list_ts']     = time.time()
    save_disk_cache()
    return models, source


def load_disk_cache():
    """启动时载入上次的缓存，避免每次重启都重新消耗 API 限额"""
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return

    models = _parse_index(raw.get('list') or [])
    if not models:
        return
    with _baseline_lock:
        _baseline_cache['list']        = models
        _baseline_cache['list_source'] = raw.get('list_source') or 'GitHub'
        _baseline_cache['list_ts']     = float(raw.get('list_ts') or 0)


def save_disk_cache():
    """把模型列表写入磁盘。失败不影响主流程"""
    with _baseline_lock:
        payload = {
            'list':        _baseline_cache['list'] or [],
            'list_source': _baseline_cache['list_source'],
            'list_ts':     _baseline_cache['list_ts'],
        }
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
    except Exception as e:
        print(f'⚠️  缓存写入失败（忽略）: {e}')


def fetch_baseline_file(name, force=False):
    """从 raw.githubusercontent 下载单个基准文件。返回 (data, source)"""
    if not _SAFE_NAME_RE.match(name):
        raise ValueError('基准名称不合法')

    now = time.time()
    with _baseline_lock:
        hit = _baseline_cache['files'].get(name)
        if not force and hit and (now - hit['ts']) < BASELINE_FILE_TTL:
            return hit['data'], hit['source'] + '(缓存)'

    errors = []
    source = 'GitHub'
    url = (f'https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}'
           f'/{GITHUB_BRANCH}/{BASELINE_DIR}/{name}.json')

    try:
        data = _gh_get(url, timeout=20)
        with _baseline_lock:
            _baseline_cache['files'][name] = {'data': data, 'source': source, 'ts': time.time()}
        return data, source
    except Exception as e:
        errors.append(f'{source}: {e}')

    with _baseline_lock:
        hit = _baseline_cache['files'].get(name)
        if hit:
            return hit['data'], hit['source'] + '(过期缓存)'

    raise RuntimeError('；'.join(errors))


# ========================================
#  自动更新 — GitHub Releases
# ========================================
UPDATE_FILES     = ('start.py', 'hlwy-ai-checker.html')
UPDATE_CHECK_TTL = 3600     # 更新检查缓存 1 小时

_update_cache = {}          # {include_prerelease: {'data':..., 'ts':...}}
_update_lock  = threading.Lock()

_VERSION_RE  = re.compile(r'^v?(\d+(?:\.\d+)*)(?:[-.]?([0-9A-Za-z.\-]+))?$')
_SAFE_TAG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\-]{0,60}$')
_PRE_WORDS   = ('pre', 'rc', 'beta', 'alpha', 'dev', 'test', 'snapshot')


def parse_version(text):
    """把版本号解析成 (数字元组, 是否测试版, 后缀)；无法解析返回 None"""
    m = _VERSION_RE.match((text or '').strip())
    if not m:
        return None
    nums = tuple(int(p) for p in m.group(1).split('.'))
    nums = (nums + (0, 0, 0))[:3]
    suffix = (m.group(2) or '').lower()
    is_pre = any(w in suffix for w in _PRE_WORDS)
    return nums, is_pre, suffix


def is_newer(remote, local):
    """remote 版本号是否比 local 更新"""
    r, l = parse_version(remote), parse_version(local)
    if not r or not l:
        return False
    if r[0] != l[0]:
        return r[0] > l[0]
    # 同一数字版本：正式版比测试版新，测试版之间按后缀字典序
    if r[1] != l[1]:
        return l[1] and not r[1]
    return r[2] > l[2]


def fetch_latest_release(include_prerelease=False, force=False):
    """
    查询 GitHub Releases 里可用的最新版本。
    默认跳过测试版（prerelease / 带 pre-rc-beta 后缀的 tag）。
    返回 dict：current / latest / has_update / name / notes / url / prerelease
    """
    key = bool(include_prerelease)
    now = time.time()
    with _update_lock:
        hit = _update_cache.get(key)
        if not force and hit and (now - hit['ts']) < UPDATE_CHECK_TTL:
            return dict(hit['data'], cached=True)

    releases = _gh_get(
        f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
        f'/releases?per_page=30'
    )
    if not isinstance(releases, list):
        raise RuntimeError('GitHub 返回的 releases 格式异常')

    best = None
    for rel in releases:
        if rel.get('draft'):
            continue
        tag = (rel.get('tag_name') or '').strip()
        parsed = parse_version(tag)
        if not parsed or not _SAFE_TAG_RE.match(tag):
            continue
        pre = bool(rel.get('prerelease')) or parsed[1]
        if pre and not include_prerelease:
            continue
        if best is None or is_newer(tag, best['tag']):
            best = {'tag': tag, 'rel': rel, 'prerelease': pre}

    if best is None:
        info = {
            'current': APP_VERSION, 'latest': None, 'has_update': False,
            'name': None, 'notes': '', 'url': None, 'prerelease': False,
            'include_prerelease': key,
        }
    else:
        rel = best['rel']
        info = {
            'current': APP_VERSION,
            'latest': best['tag'],
            'has_update': is_newer(best['tag'], APP_VERSION),
            'name': rel.get('name') or best['tag'],
            'notes': (rel.get('body') or '')[:2000],
            'url': rel.get('html_url'),
            'prerelease': best['prerelease'],
            'include_prerelease': key,
        }

    with _update_lock:
        _update_cache[key] = {'data': info, 'ts': time.time()}
    return dict(info, cached=False)


def apply_update(tag):
    """
    下载指定 tag 的程序文件并覆盖本地文件，旧文件备份为 *.bak。
    任一文件下载失败则整体放弃，不动本地文件。
    """
    if not _SAFE_TAG_RE.match(tag or '') or not parse_version(tag):
        raise ValueError('版本号不合法')

    fetched = {}
    for name in UPDATE_FILES:
        url = (f'https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}'
               f'/{tag}/{name}')
        text = _gh_request(url, timeout=30).text
        if not text.strip():
            raise RuntimeError(f'{name} 内容为空，已取消更新')
        fetched[name] = text

    base = os.path.dirname(os.path.abspath(__file__))
    written, backups = [], []
    for name, text in fetched.items():
        path = os.path.join(base, name)
        if os.path.exists(path):
            backup = path + '.bak'
            shutil.copy2(path, backup)
            backups.append(os.path.basename(backup))
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        written.append(name)

    with _update_lock:
        _update_cache.clear()

    return {'tag': tag, 'files': written, 'backups': backups}


def restart_self(delay=0.8):
    """
    在后台线程里重启自身：先让 HTTP 响应发完，再释放端口并拉起新进程。
    新进程沿用同一个解释器和端口，前端轮询到服务恢复后自行刷新页面。
    """
    def _worker():
        time.sleep(delay)
        print('\n🔄 正在重启以应用新版本 ...')

        if _server_ref is not None:
            try:
                _server_ref.shutdown()       # 停止 serve_forever 循环
                _server_ref.server_close()   # 释放监听端口，避免新进程绑定失败
            except Exception as e:
                print(f'   关闭旧服务失败: {e}')

        env = dict(os.environ, **{NO_BROWSER_ENV: '1'})
        script = os.path.abspath(__file__)
        try:
            subprocess.Popen([sys.executable, script],
                             cwd=os.path.dirname(script),
                             env=env,
                             close_fds=True)
        except Exception as e:
            print(f'   ❌ 自动重启失败: {e}')
            print('   请手动重新运行 start.py')
            return

        os._exit(0)

    threading.Thread(target=_worker, daemon=True).start()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class ProxyHandler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        """处理 GET 请求 - 提供 HTML 文件、静态资源和官方基准"""
        parsed = urlparse(self.path)
        path   = parsed.path
        query  = parse_qs(parsed.query)

        if path == '/' or path == '/index.html':
            self.serve_html()
        elif path == '/chart.js':
            self.serve_static('chart.js', 'application/javascript')
        elif path == '/api/baselines':
            self.serve_baseline_list(query)
        elif path == '/api/baseline':
            self.serve_baseline_file(query)
        elif path == '/api/version':
            self.send_json_response(200, {'version': APP_VERSION})
        elif path == '/api/update/check':
            self.serve_update_check(query)
        else:
            self.send_error(404, "File not found")

    def serve_baseline_list(self, query):
        """返回 GitHub baselines 目录下的模型列表"""
        force = query.get('refresh', ['0'])[0] in ('1', 'true')
        try:
            models, source = fetch_baseline_list(force=force)
            self.send_json_response(200, {
                'models': models,
                'count': len(models),
                'source': source,
                'repo': f'{GITHUB_OWNER}/{GITHUB_REPO}',
            })
        except Exception as e:
            self.send_json_response(502, {
                'error': '无法获取官方基准列表',
                'detail': str(e),
            })

    def serve_baseline_file(self, query):
        """下载并返回单个官方基准"""
        name = (query.get('name', [''])[0] or '').strip()
        if not name:
            self.send_json_response(400, {'error': '缺少 name 参数'})
            return
        if not _SAFE_NAME_RE.match(name):
            self.send_json_response(400, {'error': '基准名称不合法'})
            return

        force = query.get('refresh', ['0'])[0] in ('1', 'true')
        try:
            data, source = fetch_baseline_file(name, force=force)
            self.send_json_response(200, {'name': name, 'source': source, 'data': data})
        except Exception as e:
            self.send_json_response(502, {
                'error': f'无法下载基准「{name}」',
                'detail': str(e),
            })

    def serve_update_check(self, query):
        """检查是否有新版本（默认不含测试版）"""
        include_pre = query.get('prerelease', ['0'])[0] in ('1', 'true')
        force       = query.get('refresh', ['0'])[0] in ('1', 'true')
        try:
            self.send_json_response(200, fetch_latest_release(include_pre, force=force))
        except Exception as e:
            self.send_json_response(502, {
                'error': '无法检查更新',
                'detail': str(e),
                'current': APP_VERSION,
            })

    def serve_update_apply(self):
        """用户确认后下载并覆盖程序文件"""
        try:
            length = int(self.headers.get('Content-Length') or 0)
            body   = json.loads(self.rfile.read(length) or b'{}')
            tag    = (body.get('tag') or '').strip()
        except Exception as e:
            self.send_json_response(400, {'error': '请求体解析失败', 'detail': str(e)})
            return

        if not tag:
            self.send_json_response(400, {'error': '缺少 tag 参数'})
            return

        try:
            result = apply_update(tag)
            print(f'\n✅ 已更新到 {tag}，旧文件备份为 {"、".join(result["backups"]) or "（无）"}')
            self.send_json_response(200, dict(result, restarting=True))
            restart_self()
        except Exception as e:
            self.send_json_response(502, {'error': '更新失败', 'detail': str(e)})

    def do_POST(self):
        """处理 POST 请求 - 代理 API 调用"""
        if urlparse(self.path).path == '/api/update/apply':
            self.serve_update_apply()
            return
        # 代理所有 OpenAI 和 Anthropic API 请求
        if '/chat/completions' in self.path or '/messages' in self.path or '/responses' in self.path:
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
                # OpenAI Chat Completions API
                base_url = self.headers.get('X-Target-Base-URL', 'https://api.openai.com/v1')
                target_url = f"{base_url.rstrip('/')}/chat/completions"
            elif '/responses' in self.path:
                # OpenAI Responses API
                base_url = self.headers.get('X-Target-Base-URL', 'https://api.openai.com/v1')
                target_url = f"{base_url.rstrip('/')}/responses"
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

            # 移除 accept-encoding 避免收到压缩响应后原样转发导致浏览器解析失败
            headers.pop('accept-encoding', None)

            # 使用 requests 发送请求 (保留原始 header 大小写)
            try:
                resp = self._post_adaptive(target_url, body, headers)

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

    def _post_adaptive(self, target_url, body, headers):
        """发送 POST，遇到 max_tokens / max_completion_tokens 参数不兼容时自动切换重试。

        新版模型（gpt-5 / o 系列等）只接受 max_completion_tokens，而旧模型/部分中转
        只接受 max_tokens，前端写死 max_tokens 会导致 400。这里根据报错自动降级切换。
        """
        resp = _session.post(target_url, data=body, headers=headers, timeout=30)
        if resp.status_code != 400 or '/chat/completions' not in self.path:
            return resp

        try:
            err_text = resp.text.lower()
        except Exception:
            return resp

        # 仅当报错确实指向这两个参数时才处理，避免误伤其他 400 错误
        if 'max_tokens' not in err_text and 'max_completion_tokens' not in err_text:
            return resp
        if 'unsupported_parameter' not in err_text and 'unknown parameter' not in err_text:
            return resp

        try:
            body_obj = json.loads(body)
        except Exception:
            return resp
        if not isinstance(body_obj, dict):
            return resp

        changed = False
        if 'max_tokens' in body_obj and 'max_completion_tokens' in err_text:
            body_obj['max_completion_tokens'] = body_obj.pop('max_tokens')
            changed = True
        elif 'max_completion_tokens' in body_obj and 'max_tokens' in err_text:
            body_obj['max_tokens'] = body_obj.pop('max_completion_tokens')
            changed = True

        if not changed:
            return resp
        return _session.post(target_url, data=json.dumps(body_obj), headers=headers, timeout=30)

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

    global _server_ref

    # 载入上次的模型列表缓存，减少对 GitHub 限额的消耗
    load_disk_cache()

    server = ThreadingHTTPServer((HOST, PORT), ProxyHandler)
    _server_ref = server
    url = f'http://{HOST}:{PORT}'
    print(f"""
╔════════════════════════════════════════════════════════╗
║      hlwy-ai-checker v2.5.0 - AI 模型鉴别器           ║
╚════════════════════════════════════════════════════════╝
本项目github地址：https://github.com/hanlinwenyuan/hlwy-ai-checker

🌐 前端访问地址: {url}
⚡ 默认页面「一键鉴别」会自动从 GitHub 下载官方基准，无需手动标定

按 Ctrl+C 停止
""")

    # 自动重启拉起的进程不再抢焦点开新标签，原页面会自行恢复
    if os.environ.get(NO_BROWSER_ENV) != '1':
        threading.Timer(0.5, webbrowser.open, args=[url]).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n已停止")
        server.shutdown()


if __name__ == '__main__':
    main()
