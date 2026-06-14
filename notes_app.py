#!/usr/bin/env python3
"""
notes_app.py - 纯 Python 标准库笔记应用
=========================================
无需任何第三方依赖，基于 miniweb 框架 (http.server + json)

功能:
  - 创建笔记 (标题 + 内容)
  - 查看笔记列表
  - 查看笔记详情
  - 编辑笔记
  - 删除笔记
  - 搜索笔记
  - SQLite 持久化

用法:
  python3 notes_app.py              # 启动 (localhost:8080)
  python3 notes_app.py --port 3000  # 指定端口
  python3 notes_app.py --debug      # 调试模式
"""

import http.server
import socketserver
import json
import re
import sys
import os
import time
import uuid
import mimetypes
import sqlite3
import secrets
from urllib.parse import urlparse, parse_qs
from datetime import datetime

# ============================================================
# 数据层 (SQLite)
# ============================================================

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'notes.db')
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB max per file


def get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """初始化数据库表"""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER NOT NULL,
                stored_name TEXT NOT NULL,
                original_name TEXT NOT NULL,
                size INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL,
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
            )
        """)
        conn.commit()


def load_notes():
    """从 SQLite 加载所有笔记（含附件）"""
    with get_connection() as conn:
        cur = conn.execute("SELECT id, title, content, created_at, updated_at FROM notes ORDER BY id DESC")
        notes = [dict(r) for r in cur.fetchall()]
        if notes:
            note_ids = [n['id'] for n in notes]
            placeholders = ','.join('?' * len(note_ids))
            cur2 = conn.execute(
                f"SELECT note_id, stored_name, original_name, size, uploaded_at FROM attachments WHERE note_id IN ({placeholders}) ORDER BY uploaded_at",
                note_ids
            )
            att_map = {}
            for r in cur2.fetchall():
                d = dict(r)
                nid = d.pop('note_id')
                att_map.setdefault(nid, []).append(d)
            for n in notes:
                n['attachments'] = att_map.get(n['id'], [])
        return notes


def get_note(note_id):
    """按 ID 获取笔记（含附件）"""
    with get_connection() as conn:
        cur = conn.execute("SELECT id, title, content, created_at, updated_at FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        if row is None:
            return None
        note = dict(row)
        cur2 = conn.execute(
            "SELECT stored_name, original_name, size, uploaded_at FROM attachments WHERE note_id = ? ORDER BY uploaded_at",
            (note_id,)
        )
        note['attachments'] = [dict(r) for r in cur2.fetchall()]
        return note


def create_note(title, content):
    """创建新笔记"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title.strip(), content.strip(), now, now)
        )
        note_id = cur.lastrowid
        conn.commit()
    return {
        'id': note_id,
        'title': title.strip(),
        'content': content.strip(),
        'created_at': now,
        'updated_at': now,
        'attachments': [],
    }


def update_note(note_id, title=None, content=None):
    """更新笔记"""
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        if row is None:
            return None

        new_title = title.strip() if title is not None else row['title']
        new_content = content.strip() if content is not None else row['content']
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        conn.execute(
            "UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
            (new_title, new_content, now, note_id)
        )
        conn.commit()

        note = dict(row)
        note['title'] = new_title
        note['content'] = new_content
        note['updated_at'] = now

        cur2 = conn.execute(
            "SELECT stored_name, original_name, size, uploaded_at FROM attachments WHERE note_id = ? ORDER BY uploaded_at",
            (note_id,)
        )
        note['attachments'] = [dict(r) for r in cur2.fetchall()]
        return note


def delete_note(note_id):
    """删除笔记（含附件文件）"""
    note = get_note(note_id)
    if note is None:
        return None

    # 先清理附件文件（CASCADE 会删除数据库记录）
    delete_note_attachments(note)

    with get_connection() as conn:
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
    return note


def search_notes(keyword):
    """搜索笔记（标题和内容）"""
    keyword = keyword.lower().strip()
    if not keyword:
        return load_notes()

    with get_connection() as conn:
        cur = conn.execute(
            "SELECT id, title, content, created_at, updated_at FROM notes WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ? ORDER BY id DESC",
            (f'%{keyword}%', f'%{keyword}%')
        )
        notes = [dict(r) for r in cur.fetchall()]

        if notes:
            note_ids = [n['id'] for n in notes]
            placeholders = ','.join('?' * len(note_ids))
            cur2 = conn.execute(
                f"SELECT note_id, stored_name, original_name, size, uploaded_at FROM attachments WHERE note_id IN ({placeholders}) ORDER BY uploaded_at",
                note_ids
            )
            att_map = {}
            for r in cur2.fetchall():
                d = dict(r)
                nid = d.pop('note_id')
                att_map.setdefault(nid, []).append(d)
            for n in notes:
                n['attachments'] = att_map.get(n['id'], [])
        return notes


# ============================================================
# 附件管理
# ============================================================

def ensure_upload_dir():
    """确保上传目录存在"""
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_uploaded_file(file_data, original_name):
    """保存上传文件，返回 (存储文件名, 文件大小)"""
    ensure_upload_dir()
    ext = os.path.splitext(original_name)[1]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, stored_name)
    with open(filepath, 'wb') as f:
        f.write(file_data)
    return stored_name, len(file_data)


def add_attachment(note_id, stored_name, original_name, file_size):
    """为笔记添加附件记录"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with get_connection() as conn:
        cur = conn.execute("SELECT id FROM notes WHERE id = ?", (note_id,))
        if cur.fetchone() is None:
            return None

        conn.execute(
            "INSERT INTO attachments (note_id, stored_name, original_name, size, uploaded_at) VALUES (?, ?, ?, ?, ?)",
            (note_id, stored_name, original_name, file_size, now)
        )
        conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?", (now, note_id))
        conn.commit()

    return {
        'stored_name': stored_name,
        'original_name': original_name,
        'size': file_size,
        'uploaded_at': now,
    }


def remove_attachment(note_id, stored_name):
    """删除笔记的附件（记录 + 文件）"""
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT stored_name, original_name, size, uploaded_at FROM attachments WHERE note_id = ? AND stored_name = ?",
            (note_id, stored_name)
        )
        row = cur.fetchone()
        if row is None:
            return False

        filepath = os.path.join(UPLOAD_DIR, stored_name)
        if os.path.exists(filepath):
            os.remove(filepath)

        conn.execute("DELETE FROM attachments WHERE note_id = ? AND stored_name = ?", (note_id, stored_name))
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?", (now, note_id))
        conn.commit()
        return True


def delete_note_attachments(note):
    """删除笔记关联的所有附件文件"""
    for att in note.get('attachments', []):
        filepath = os.path.join(UPLOAD_DIR, att['stored_name'])
        if os.path.exists(filepath):
            os.remove(filepath)


def format_file_size(size):
    """格式化文件大小显示"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


# ============================================================
# Markdown 渲染（纯正则，零依赖）
# ============================================================

def markdown_to_html(text):
    """将 Markdown 文本渲染为 HTML（支持标题/粗体/斜体/列表/代码/引用/链接/分割线）"""
    if not text:
        return ''

    # 第1步：保护 fenced code blocks 不被后续处理污染
    protected_blocks = {}
    block_counter = [0]

    def protect_code(m):
        block_counter[0] += 1
        # Use a sentinel that can't be matched by any inline regex
        key = f'\x00CODEBLOCK_{block_counter[0]}\x00'
        lang = m.group(1) or ''
        code = m.group(2)
        # 转义 HTML
        code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        protected_blocks[key] = f'<pre><code class="language-{lang}">{code}</code></pre>'
        return key

    text = re.sub(
        r'```(\w*)\n(.*?)```',
        protect_code,
        text,
        flags=re.DOTALL
    )

# 第2步：按行处理块级元素
    lines = text.split('\n')
    html_lines = []
    i = 0
    in_list = False       # 是否在列表中
    list_type = None      # 'ul' 或 'ol'
    list_items = []       # 暂存当前列表项
    in_blockquote = False
    quote_lines = []
    in_paragraph = False
    para_lines = []

    def flush_paragraph():
        nonlocal in_paragraph, para_lines
        if para_lines:
            p_text = ' '.join(para_lines)
            p_text = _render_inline(p_text)
            html_lines.append(f'<p>{p_text}</p>')
            para_lines = []
        in_paragraph = False

    def flush_list():
        nonlocal in_list, list_type, list_items
        if list_items:
            tag = list_type or 'ul'
            html_lines.append(f'<{tag}>')
            for item in list_items:
                html_lines.append(f'<li>{item}</li>')
            html_lines.append(f'</{tag}>')
            list_items = []
        in_list = False
        list_type = None

    def flush_blockquote():
        nonlocal in_blockquote, quote_lines
        if quote_lines:
            q_text = '<br>'.join(quote_lines)
            q_text = _render_inline(q_text)
            html_lines.append(f'<blockquote>{q_text}</blockquote>')
            quote_lines = []
        in_blockquote = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 恢复受保护块
        if stripped in protected_blocks:
            flush_paragraph()
            flush_list()
            flush_blockquote()
            html_lines.append(protected_blocks[stripped])
            i += 1
            continue

        # 空行 → 刷新所有未完成块
        if not stripped:
            flush_paragraph()
            flush_list()
            flush_blockquote()
            i += 1
            continue

        # 水平分割线 ---
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            flush_paragraph()
            flush_list()
            flush_blockquote()
            html_lines.append('<hr>')
            i += 1
            continue

        # 标题 # ~ ######
        heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if heading_match:
            flush_paragraph()
            flush_list()
            flush_blockquote()
            level = len(heading_match.group(1))
            h_text = _render_inline(heading_match.group(2))
            html_lines.append(f'<h{level}>{h_text}</h{level}>')
            i += 1
            continue

        # 引用 >
        if stripped.startswith('> '):
            flush_paragraph()
            flush_list()
            quote_text = stripped[2:]
            if in_blockquote:
                quote_lines.append(quote_text)
            else:
                in_blockquote = True
                quote_lines.append(quote_text)
            i += 1
            continue

        # 无序列表 - 或 *
        ul_match = re.match(r'^[-*+]\s+(.+)$', stripped)
        if ul_match:
            flush_paragraph()
            flush_blockquote()
            item_text = _render_inline(ul_match.group(1))
            if in_list and list_type == 'ul':
                list_items.append(item_text)
            else:
                flush_list()
                in_list = True
                list_type = 'ul'
                list_items.append(item_text)
            i += 1
            continue

        # 有序列表 1. 2. 3.
        ol_match = re.match(r'^(\d+)\.\s+(.+)$', stripped)
        if ol_match:
            flush_paragraph()
            flush_blockquote()
            item_text = _render_inline(ol_match.group(2))
            if in_list and list_type == 'ol':
                list_items.append(item_text)
            else:
                flush_list()
                in_list = True
                list_type = 'ol'
                list_items.append(item_text)
            i += 1
            continue

        # 普通文本 → 段落
        flush_list()
        flush_blockquote()
        in_paragraph = True
        para_lines.append(stripped)
        i += 1

    # 清理残留
    flush_paragraph()
    flush_list()
    flush_blockquote()

    return '\n'.join(html_lines)


def _render_inline(text):
    """渲染行内 Markdown 元素（粗体、斜体、删除线、链接、代码）"""
    # 先转义 HTML（受保护块已在前面处理）
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 图片 ![alt](url)
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1" style="max-width:100%">', text)

    # 链接 [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)

    # 删除线 ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<del>\1</del>', text)

    # 粗体+斜体 ***text*** 或 ___text___
    text = re.sub(r'(\*\*\*|___)(.+?)\1', r'<strong><em>\2</em></strong>', text)

    # 粗体 **text** 或 __text__
    text = re.sub(r'(\*\*|__)(.+?)\1', r'<strong>\2</strong>', text)

    # 斜体 *text* 或 _text_（避免匹配下划线包围的英文单词如 foo_bar）
    text = re.sub(r'(?<![a-zA-Z0-9_])(\*|_)(.+?)\1(?![a-zA-Z0-9_])', r'<em>\2</em>', text)

    # 行内代码 `code`（放在最后处理，避免被其他规则干扰）
    # 需要小心：` 内可能包含 * _ [ 等字符，但我们已经完成了其他处理
    def replace_inline_code(m):
        code = m.group(1)
        code = code.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        return f'<code>{code}</code>'

    text = re.sub(r'`([^`]+)`', replace_inline_code, text)

    return text


# ============================================================
# 全局请求上下文
# ============================================================

_current_request = None
_current_app = None


class Request:
    """HTTP 请求对象"""

    def __init__(self, method, path, query_string, headers, body):
        self.method = method
        self.path = path
        self.query_string = query_string
        self.headers = headers
        self.body = body
        self.params = {}
        self._query = None
        self._json = None

    @property
    def query(self):
        if self._query is None:
            self._query = parse_qs(self.query_string)
            for key, value in self._query.items():
                if len(value) == 1:
                    self._query[key] = value[0]
        return self._query

    @property
    def json(self):
        if self._json is None and self.body:
            content_type = self.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                try:
                    self._json = json.loads(self.body.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._json = None
        return self._json

    @property
    def form(self):
        if self.body and 'application/x-www-form-urlencoded' in self.headers.get('Content-Type', ''):
            try:
                raw = parse_qs(self.body.decode('utf-8'))
                # 扁平化单值列表
                result = {}
                for k, v in raw.items():
                    result[k] = v[0] if len(v) == 1 else v
                return result
            except UnicodeDecodeError:
                pass
        return {}

    def get(self, key, default=None):
        return self.query.get(key, default)

    @property
    def files(self):
        """解析 multipart/form-data 上传的文件和字段"""
        content_type = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type or not self.body:
            return {}, {}

        # 提取 boundary
        match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type, re.I)
        if not match:
            return {}, {}
        boundary = (match.group(1) or match.group(2)).encode()

        fields = {}
        uploaded_files = {}

        # 按 boundary 分割
        parts = self.body.split(b'--' + boundary)
        for part in parts:
            if part in (b'', b'\r\n', b'--\r\n'):
                continue

            # 分离头部和内容（以 \r\n\r\n 为界）
            header_end = part.find(b'\r\n\r\n')
            if header_end == -1:
                continue
            raw_headers = part[:header_end].decode('utf-8', errors='replace')
            content = part[header_end + 4:]
            # 去掉末尾的 \r\n
            if content.endswith(b'\r\n'):
                content = content[:-2]

            # 解析 Content-Disposition
            disp_match = re.search(r'Content-Disposition:\s*form-data;\s*name="([^"]*)"(?:;\s*filename="([^"]*)")?', raw_headers, re.I)
            if not disp_match:
                continue

            field_name = disp_match.group(1)
            file_name = disp_match.group(2)

            if file_name:
                # 文件字段
                content_type_header = ''
                ct_match = re.search(r'Content-Type:\s*(\S+)', raw_headers, re.I)
                if ct_match:
                    content_type_header = ct_match.group(1)
                uploaded_files[field_name] = {
                    'filename': file_name,
                    'content': content,
                    'content_type': content_type_header,
                }
            else:
                # 普通字段
                fields[field_name] = content.decode('utf-8', errors='replace')

        return fields, uploaded_files


class Response:
    """HTTP 响应对象"""

    def __init__(self, body='', status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self._set_default_headers()

    def _set_default_headers(self):
        if 'Content-Type' not in self.headers:
            self.headers['Content-Type'] = 'text/html; charset=utf-8'

    def json(self, data, status=200):
        self.body = json.dumps(data, ensure_ascii=False, indent=2)
        self.headers['Content-Type'] = 'application/json; charset=utf-8'
        self.status = status
        return self

    def html(self, html_str):
        self.body = html_str
        self.headers['Content-Type'] = 'text/html; charset=utf-8'
        return self

    def redirect(self, location, status=302):
        self.status = status
        self.headers['Location'] = location
        self.body = f'<a href="{location}">Redirecting...</a>'
        return self


class Route:
    """路由定义"""

    def __init__(self, pattern, method='GET', func=None):
        self.pattern = pattern
        self.method = method.upper()
        self.func = func
        regex_pattern = re.sub(r'<(\w+)>', r'(?P<\1>[^/]+)', pattern)
        regex_pattern = f'^{regex_pattern}$'
        self.regex = re.compile(regex_pattern)
        self.priority = -pattern.count('<')

    def match(self, path, method):
        if self.method != method:
            return None
        match = self.regex.match(path)
        if match:
            return match.groupdict()
        return None


class miniweb:
    """主应用类"""

    def __init__(self, host='0.0.0.0', port=8080):
        self.host = host
        self.port = port
        self.routes = []
        self.before_handlers = []
        self.after_handlers = []
        self.static_dirs = {}

    def route(self, pattern, method='GET'):
        def decorator(func):
            self.routes.append(Route(pattern, method, func))
            self.routes.sort(key=lambda r: r.priority, reverse=True)
            return func
        return decorator

    def before(self, func):
        self.before_handlers.append(func)
        return func

    def after(self, func):
        self.after_handlers.append(func)
        return func

    def _handle_request(self, method, path, query_string, headers, body):
        global _current_request, _current_app
        _current_app = self

        req = Request(method, path, query_string, headers, body)
        _current_request = req

        for handler in self.before_handlers:
            handler(req)

        matched_route = None
        matched_params = None

        for route in self.routes:
            params = route.match(path, method)
            if params is not None:
                matched_route = route
                matched_params = params
                break

        if matched_route:
            req.params = matched_params
            try:
                result = matched_route.func(**matched_params)
            except TypeError:
                result = matched_route.func()
        else:
            return Response(f'<h1>404 Not Found</h1><p>Path: {path}</p>', status=404)

        if isinstance(result, Response):
            response = result
        elif isinstance(result, tuple) and len(result) == 2:
            data, status = result
            if isinstance(data, dict):
                response = Response().json(data, status)
            else:
                response = Response(str(data), status=status)
        elif isinstance(result, dict):
            response = Response().json(result)
        else:
            response = Response(str(result))

        for handler in self.after_handlers:
            handler(req, response)

        return response

    def run(self, host=None, port=None, debug=False):
        app = self
        host = host or self.host
        port = port or self.port

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                if debug:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}")

            def _handle(self, method):
                parsed = urlparse(self.path)
                path = parsed.path
                query_string = parsed.query

                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length) if content_length > 0 else b''

                response = app._handle_request(method, path, query_string,
                                               dict(self.headers), body)

                self.send_response(response.status)
                for key, value in response.headers.items():
                    if value:
                        self.send_header(key, value)
                self.end_headers()

                if isinstance(response.body, bytes):
                    self.wfile.write(response.body)
                else:
                    self.wfile.write(response.body.encode('utf-8'))

            def do_GET(self):
                self._handle('GET')

            def do_POST(self):
                self._handle('POST')

            def do_PUT(self):
                self._handle('PUT')

            def do_DELETE(self):
                self._handle('DELETE')

        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer((host, port), Handler) as httpd:
            print(f"╔══════════════════════════════════════════════╗")
            print(f"║        Notes App - 纯 Python 笔记应用        ║")
            print(f"║                                              ║")
            print(f"║  http://{host if host != '0.0.0.0' else 'localhost'}:{port}                  ║")
            print(f"║  数据文件: notes.db                      ║")
            print(f"║  上传目录: {os.path.basename(UPLOAD_DIR)}/                      ║")
            print(f"╚══════════════════════════════════════════════╝")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n服务器已停止")


# ============================================================
# 认证管理
# ============================================================

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin123'

_sessions = {}


def generate_session_token():
    """生成随机 session token"""
    return secrets.token_hex(32)


def check_auth():
    """检查当前请求是否已登录，返回 True/False"""
    req = request()
    if not req:
        return False
    cookie_header = req.headers.get('Cookie', '')
    cookies = {}
    for part in cookie_header.split(';'):
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1)
            cookies[k.strip()] = v.strip()
    token = cookies.get('session', '')
    return token in _sessions


def require_auth():
    """如果未登录，返回重定向到 /login 的 Response；否则返回 None"""
    if not check_auth():
        return Response().redirect('/login')
    return None


def parse_cookies(req):
    """解析 Cookie header 返回 dict"""
    cookies = {}
    cookie_header = req.headers.get('Cookie', '')
    for part in cookie_header.split(';'):
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1)
            cookies[k.strip()] = v.strip()
    return cookies


# ============================================================
# 应用核心 (Request/Response)
# ============================================================

def request():
    return _current_request


# ============================================================
# HTML 模板
# ============================================================

CSS_STYLE = '''
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f0f2f5;
    color: #1a1a2e;
    min-height: 100vh;
}

.container {
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
}

/* 头部 */
.header {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 30px 0;
    margin-bottom: 30px;
    box-shadow: 0 2px 10px rgba(102, 126, 234, 0.3);
}

.header .container {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.header h1 {
    font-size: 28px;
    font-weight: 700;
}

.header h1 a {
    color: white;
    text-decoration: none;
}

.header-actions {
    display: flex;
    gap: 10px;
    align-items: center;
}

/* 按钮 */
.btn {
    display: inline-block;
    padding: 10px 20px;
    border-radius: 8px;
    text-decoration: none;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    border: none;
    transition: all 0.2s;
}

.btn-primary {
    background: white;
    color: #667eea;
}

.btn-primary:hover {
    background: #f0f0ff;
    transform: translateY(-1px);
}

.btn-success {
    background: #10b981;
    color: white;
}

.btn-success:hover {
    background: #059669;
}

.btn-danger {
    background: #ef4444;
    color: white;
}

.btn-danger:hover {
    background: #dc2626;
}

.btn-secondary {
    background: #6b7280;
    color: white;
}

.btn-secondary:hover {
    background: #4b5563;
}

.btn-sm {
    padding: 6px 14px;
    font-size: 13px;
}

/* 搜索框 */
.search-box {
    display: flex;
    gap: 8px;
    margin-bottom: 25px;
}

.search-box input {
    flex: 1;
    padding: 12px 16px;
    border: 2px solid #e5e7eb;
    border-radius: 8px;
    font-size: 15px;
    outline: none;
    transition: border-color 0.2s;
}

.search-box input:focus {
    border-color: #667eea;
}

/* 笔记卡片 */
.note-card {
    background: white;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 16px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    transition: all 0.2s;
    border-left: 4px solid #667eea;
}

.note-card:hover {
    box-shadow: 0 4px 12px rgba(0,0,0,0.12);
    transform: translateY(-2px);
}

.note-card h3 {
    font-size: 18px;
    margin-bottom: 8px;
}

.note-card h3 a {
    color: #1a1a2e;
    text-decoration: none;
}

.note-card h3 a:hover {
    color: #667eea;
}

.note-card .preview {
    color: #6b7280;
    font-size: 14px;
    line-height: 1.6;
    margin-bottom: 12px;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
}

.note-card .meta {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    color: #9ca3af;
}

.note-card .actions {
    display: flex;
    gap: 8px;
}

/* 空状态 */
.empty-state {
    text-align: center;
    padding: 60px 20px;
    color: #9ca3af;
}

.empty-state .icon {
    font-size: 64px;
    margin-bottom: 16px;
}

.empty-state h2 {
    font-size: 22px;
    color: #6b7280;
    margin-bottom: 8px;
}

.empty-state p {
    font-size: 15px;
}

/* 表单 */
.form-card {
    background: white;
    border-radius: 12px;
    padding: 30px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.form-group {
    margin-bottom: 20px;
}

.form-group label {
    display: block;
    font-size: 14px;
    font-weight: 600;
    color: #374151;
    margin-bottom: 6px;
}

.form-group input,
.form-group textarea {
    width: 100%;
    padding: 12px 16px;
    border: 2px solid #e5e7eb;
    border-radius: 8px;
    font-size: 15px;
    outline: none;
    transition: border-color 0.2s;
    font-family: inherit;
}

.form-group input:focus,
.form-group textarea:focus {
    border-color: #667eea;
}

.form-group textarea {
    min-height: 250px;
    resize: vertical;
    line-height: 1.6;
}

.form-actions {
    display: flex;
    gap: 10px;
    justify-content: flex-end;
    padding-top: 10px;
    border-top: 1px solid #f3f4f6;
}

/* 笔记详情 */
.note-detail {
    background: white;
    border-radius: 12px;
    padding: 30px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.note-detail h2 {
    font-size: 26px;
    margin-bottom: 8px;
    color: #1a1a2e;
}

.note-detail .meta {
    font-size: 13px;
    color: #9ca3af;
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid #f3f4f6;
}

.note-detail .content {
    font-size: 16px;
    line-height: 1.8;
    color: #374151;
}

/* Markdown 渲染样式 */
.markdown-body h1,
.markdown-body h2,
.markdown-body h3,
.markdown-body h4,
.markdown-body h5,
.markdown-body h6 {
    margin-top: 24px;
    margin-bottom: 12px;
    font-weight: 600;
    line-height: 1.3;
    color: #111827;
}
.markdown-body h1 { font-size: 28px; border-bottom: 2px solid #e5e7eb; padding-bottom: 8px; }
.markdown-body h2 { font-size: 22px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }
.markdown-body h3 { font-size: 18px; }
.markdown-body h4 { font-size: 16px; }
.markdown-body p {
    margin: 8px 0;
    line-height: 1.8;
}
.markdown-body ul, .markdown-body ol {
    margin: 8px 0;
    padding-left: 24px;
}
.markdown-body li {
    margin: 4px 0;
    line-height: 1.7;
}
.markdown-body blockquote {
    margin: 12px 0;
    padding: 8px 16px;
    border-left: 4px solid #6366f1;
    background: #f5f3ff;
    color: #4c1d95;
    border-radius: 0 6px 6px 0;
}
.markdown-body pre {
    margin: 12px 0;
    padding: 14px 16px;
    background: #1f2937;
    color: #f3f4f6;
    border-radius: 8px;
    overflow-x: auto;
    font-size: 14px;
    line-height: 1.5;
}
.markdown-body code {
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 0.9em;
    padding: 2px 6px;
    background: #f3f4f6;
    color: #dc2626;
    border-radius: 4px;
}
.markdown-body pre code {
    background: transparent;
    color: inherit;
    padding: 0;
    border-radius: 0;
    font-size: inherit;
}
.markdown-body hr {
    margin: 20px 0;
    border: 0;
    height: 1px;
    background: #e5e7eb;
}
.markdown-body strong {
    font-weight: 700;
    color: #111827;
}
.markdown-body em {
    font-style: italic;
}
.markdown-body del {
    color: #9ca3af;
    text-decoration: line-through;
}
.markdown-body a {
    color: #6366f1;
    text-decoration: none;
}
.markdown-body a:hover {
    text-decoration: underline;
}
.markdown-body img {
    max-width: 100%;
    border-radius: 8px;
    margin: 12px 0;
}

.note-detail .actions {
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px solid #f3f4f6;
    display: flex;
    gap: 10px;
}

/* 消息提示 */
.flash {
    padding: 12px 20px;
    border-radius: 8px;
    margin-bottom: 20px;
    font-size: 14px;
    font-weight: 500;
}

.flash-success {
    background: #d1fae5;
    color: #065f46;
    border: 1px solid #a7f3d0;
}

.flash-error {
    background: #fee2e2;
    color: #991b1b;
    border: 1px solid #fecaca;
}

/* 统计 */
.stats {
    display: flex;
    gap: 20px;
    margin-bottom: 20px;
    font-size: 14px;
    color: #6b7280;
}

.stats span {
    background: white;
    padding: 8px 16px;
    border-radius: 8px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}

/* 响应式 */
@media (max-width: 600px) {
    .header .container {
        flex-direction: column;
        gap: 12px;
        text-align: center;
    }
    .note-card .meta {
        flex-direction: column;
        gap: 8px;
        align-items: flex-start;
    }
}

/* 附件区域 */
.attachments-section {
    background: white;
    border-radius: 12px;
    padding: 24px 30px;
    margin-top: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.attachments-section h3 {
    font-size: 16px;
    color: #374151;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
}

.attachment-list {
    list-style: none;
}

.attachment-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 14px;
    background: #f9fafb;
    border-radius: 8px;
    margin-bottom: 8px;
    border: 1px solid #e5e7eb;
    transition: background 0.2s;
}

.attachment-item:hover {
    background: #f3f4f6;
}

.attachment-info {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
}

.attachment-icon {
    font-size: 20px;
    flex-shrink: 0;
}

.attachment-name {
    font-size: 14px;
    color: #374151;
    word-break: break-all;
}

.attachment-name a {
    color: #667eea;
    text-decoration: none;
}

.attachment-name a:hover {
    text-decoration: underline;
}

.attachment-meta {
    font-size: 12px;
    color: #9ca3af;
    margin-left: 8px;
}

.attachment-actions {
    flex-shrink: 0;
}

/* 上传表单 */
.upload-form {
    margin-top: 16px;
    padding: 16px;
    background: #f9fafb;
    border-radius: 8px;
    border: 2px dashed #d1d5db;
    transition: border-color 0.2s;
}

.upload-form:hover {
    border-color: #667eea;
}

.upload-form form {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
}

.upload-form input[type="file"] {
    flex: 1;
    font-size: 14px;
    padding: 6px 0;
    min-width: 180px;
}

.upload-form input[type="file"]::file-selector-button {
    padding: 6px 14px;
    border-radius: 6px;
    border: 1px solid #d1d5db;
    background: white;
    font-size: 13px;
    cursor: pointer;
    margin-right: 10px;
}

.upload-form input[type="file"]::file-selector-button:hover {
    background: #f3f4f6;
}

.no-attachments {
    color: #9ca3af;
    font-size: 14px;
    padding: 8px 0;
}
'''


def page(title, content, flash=None, is_admin=False):
    """生成完整 HTML 页面"""
    flash_html = ''
    if flash:
        cls = 'flash-success' if flash.get('type') == 'success' else 'flash-error'
        flash_html = f'<div class="flash {cls}">{flash["msg"]}</div>'

    new_btn = '<a href="/new" class="btn btn-primary btn-sm">✏️ 新建笔记</a>' if is_admin else ''
    login_btn = '<a href="/login" class="btn btn-primary btn-sm">🔑 登录</a>' if not is_admin else '<a href="/logout" class="btn btn-secondary btn-sm">🚪 退出</a>'

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Notes App</title>
    <style>{CSS_STYLE}</style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1><a href="/">📝 Notes App</a></h1>
            <div class="header-actions">
                <a href="/" class="btn btn-primary btn-sm">📋 所有笔记</a>
                {new_btn}
                {login_btn}
            </div>
        </div>
    </div>
    <div class="container">
        {flash_html}
        {content}
    </div>
</body>
</html>'''


def render_note_card(note, is_admin=False):
    """渲染单张笔记卡片"""
    preview = note['content'][:200]
    if len(note['content']) > 200:
        preview += '...'
    preview = preview.replace('<', '&lt;').replace('>', '&gt;')
    title = note['title'].replace('<', '&lt;').replace('>', '&gt;')

    # 附件标记
    attachments = note.get('attachments', [])
    attach_badge = f' <span style="font-size:12px;color:#667eea;">📎{len(attachments)}</span>' if attachments else ''

    actions_html = ''
    if is_admin:
        actions_html = f'''
                <a href="/edit/{note['id']}" class="btn btn-secondary btn-sm">编辑</a>
                <a href="/delete/{note['id']}" class="btn btn-danger btn-sm"
                   onclick="return confirm('确定删除「{title}」？')">删除</a>'''

    return f'''
    <div class="note-card">
        <h3><a href="/note/{note['id']}">{title}{attach_badge}</a></h3>
        <div class="preview">{preview}</div>
        <div class="meta">
            <span>🕐 {note['updated_at']}</span>
            <div class="actions">
                {actions_html}
            </div>
        </div>
    </div>'''


def get_attachment_icon(filename):
    """根据文件扩展名返回图标"""
    ext = os.path.splitext(filename)[1].lower()
    icons = {
        '.pdf': '📄', '.doc': '📝', '.docx': '📝',
        '.xls': '📊', '.xlsx': '📊', '.csv': '📊',
        '.ppt': '📽️', '.pptx': '📽️',
        '.jpg': '🖼️', '.jpeg': '🖼️', '.png': '🖼️', '.gif': '🖼️', '.svg': '🖼️', '.webp': '🖼️',
        '.mp3': '🎵', '.wav': '🎵', '.flac': '🎵',
        '.mp4': '🎬', '.mov': '🎬', '.avi': '🎬',
        '.zip': '📦', '.tar': '📦', '.gz': '📦', '.rar': '📦', '.7z': '📦',
        '.py': '🐍', '.js': '📜', '.html': '🌐', '.css': '🎨', '.json': '📋',
        '.txt': '📃', '.md': '📝',
    }
    return icons.get(ext, '📎')


def render_attachments_html(note_id, attachments, is_admin=False):
    """渲染附件列表 HTML"""
    if not attachments:
        return '<div class="no-attachments">暂无附件</div>'

    items = []
    for att in attachments:
        icon = get_attachment_icon(att['original_name'])
        name = att['original_name'].replace('<', '&lt;').replace('>', '&gt;')
        size_str = format_file_size(att['size'])
        time_str = att.get('uploaded_at', '')
        delete_btn = ''
        if is_admin:
            delete_btn = f'''
            <div class="attachment-actions">
                <form method="POST" action="/note/{note_id}/attachment/{att['stored_name']}/delete"
                      style="display:inline;"
                      onsubmit="return confirm('确定删除附件「{name}」？')">
                    <button type="submit" class="btn btn-danger btn-sm" style="padding:4px 10px;font-size:12px;">删除</button>
                </form>
            </div>'''
        items.append(f'''
        <li class="attachment-item">
            <div class="attachment-info">
                <span class="attachment-icon">{icon}</span>
                <span class="attachment-name">
                    <a href="/uploads/{att['stored_name']}" download="{name}">{name}</a>
                    <span class="attachment-meta">{size_str} · {time_str}</span>
                </span>
            </div>
            {delete_btn}
        </li>''')
    return '<ul class="attachment-list">' + ''.join(items) + '</ul>'


# ============================================================
# 应用路由
# ============================================================

app = miniweb(host='0.0.0.0', port=8080)


@app.route('/')
def index():
    """首页 - 笔记列表"""
    is_admin = check_auth()
    keyword = request().get('q', '')
    notes = search_notes(keyword) if keyword else load_notes()
    total = len(load_notes())

    # 搜索框
    search_value = keyword.replace('<', '&lt;').replace('>', '&gt;')
    search_html = f'''
    <form class="search-box" method="GET" action="/">
        <input type="text" name="q" placeholder="搜索笔记标题或内容..." value="{search_value}">
        <button type="submit" class="btn btn-primary">🔍 搜索</button>
        {'' if not keyword else '<a href="/" class="btn btn-secondary">✕ 清除</a>'}
    </form>'''

    # 统计
    stats_html = f'''
    <div class="stats">
        <span>📊 共 {total} 篇笔记</span>
        {f'<span>🔍 搜索到 {len(notes)} 篇</span>' if keyword else ''}
    </div>'''

    # 笔记列表
    if notes:
        cards = ''.join(render_note_card(n, is_admin=is_admin) for n in notes)
        content = search_html + stats_html + cards
    else:
        if keyword:
            content = search_html + f'''
            <div class="empty-state">
                <div class="icon">🔍</div>
                <h2>未找到匹配的笔记</h2>
                <p>尝试其他关键词</p>
            </div>'''
        else:
            content = search_html + '''
            <div class="empty-state">
                <div class="icon">📝</div>
                <h2>还没有笔记</h2>
                <p>点击上方「新建笔记」开始记录</p>
            </div>'''

    return page('首页', content, is_admin=is_admin)


@app.route('/new')
def new_note_form():
    """新建笔记表单"""
    r = require_auth()
    if r:
        return r
    content = '''
    <div class="form-card">
        <h2 style="margin-bottom: 20px; font-size: 22px;">✏️ 新建笔记</h2>
        <form method="POST" action="/create">
            <div class="form-group">
                <label for="title">标题</label>
                <input type="text" id="title" name="title" placeholder="输入笔记标题..." required autofocus>
            </div>
            <div class="form-group">
                <label for="content">内容</label>
                <textarea id="content" name="content" placeholder="开始写点什么..." required></textarea>
            </div>
            <div class="form-actions">
                <a href="/" class="btn btn-secondary">取消</a>
                <button type="submit" class="btn btn-success">💾 保存笔记</button>
            </div>
        </form>
    </div>'''
    return page('新建笔记', content, is_admin=True)


@app.route('/create', method='POST')
def create_note_handler():
    """处理创建笔记"""
    r = require_auth()
    if r:
        return r
    req = request()
    form = req.form
    title = form.get('title', '').strip()
    content = form.get('content', '').strip()

    if not title or not content:
        return page('新建笔记', '''
        <div class="form-card">
            <h2 style="margin-bottom: 20px;">✏️ 新建笔记</h2>
            <div class="flash flash-error">标题和内容不能为空</div>
            <a href="/new" class="btn btn-primary">返回</a>
        </div>''', is_admin=True)

    note = create_note(title, content)
    return Response().redirect(f'/note/{note["id"]}')


@app.route('/note/<note_id>')
def view_note(note_id):
    """查看笔记详情"""
    is_admin = check_auth()
    try:
        nid = int(note_id)
    except ValueError:
        return Response('<h1>404 Not Found</h1>', status=404)

    note = get_note(nid)
    if not note:
        return page('未找到', '''
        <div class="empty-state">
            <div class="icon">😕</div>
            <h2>笔记未找到</h2>
            <p><a href="/">返回首页</a></p>
        </div>''')

    title = note['title'].replace('<', '&lt;').replace('>', '&gt;')
    content_html = markdown_to_html(note['content'])
    attachments = note.get('attachments', [])

    # 附件区域
    att_html = render_attachments_html(note['id'], attachments, is_admin=is_admin)
    upload_form = f'''
        <div class="upload-form">
            <form method="POST" action="/note/{note['id']}/upload" enctype="multipart/form-data">
                <input type="file" name="file" required>
                <button type="submit" class="btn btn-primary btn-sm">📤 上传</button>
            </form>
        </div>''' if is_admin else ''
    attachments_section = f'''
    <div class="attachments-section">
        <h3>📎 附件 ({len(attachments)})</h3>
        {att_html}
        {upload_form}
    </div>'''

    edit_btn = f'<a href="/edit/{note["id"]}" class="btn btn-success btn-sm">✏️ 编辑</a>' if is_admin else ''
    delete_btn = f'<a href="/delete/{note["id"]}" class="btn btn-danger btn-sm" onclick="return confirm(\'确定删除「{title}」？\')">🗑️ 删除</a>' if is_admin else ''

    body = f'''
    <div class="note-detail">
        <h2>{title}</h2>
        <div class="meta">
            创建于 {note['created_at']} ｜ 最后编辑 {note['updated_at']}
        </div>
        <div class="content markdown-body">{content_html}</div>
        {attachments_section}
        <div class="actions">
            {edit_btn}
            {delete_btn}
            <a href="/" class="btn btn-secondary btn-sm">← 返回列表</a>
        </div>
    </div>'''

    return page(title, body, is_admin=is_admin)


@app.route('/edit/<note_id>')
def edit_note_form(note_id):
    """编辑笔记表单"""
    r = require_auth()
    if r:
        return r
    try:
        nid = int(note_id)
    except ValueError:
        return Response('<h1>404 Not Found</h1>', status=404)

    note = get_note(nid)
    if not note:
        return page('未找到', '''
        <div class="empty-state">
            <div class="icon">😕</div>
            <h2>笔记未找到</h2>
            <p><a href="/">返回首页</a></p>
        </div>''', is_admin=True)

    title_val = note['title'].replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
    content_val = note['content'].replace('<', '&lt;').replace('>', '&gt;')

    body = f'''
    <div class="form-card">
        <h2 style="margin-bottom: 20px; font-size: 22px;">✏️ 编辑笔记</h2>
        <form method="POST" action="/update/{note['id']}">
            <div class="form-group">
                <label for="title">标题</label>
                <input type="text" id="title" name="title" value="{title_val}" required autofocus>
            </div>
            <div class="form-group">
                <label for="content">内容</label>
                <textarea id="content" name="content" required>{content_val}</textarea>
            </div>
            <div class="form-actions">
                <a href="/note/{note['id']}" class="btn btn-secondary">取消</a>
                <button type="submit" class="btn btn-success">💾 保存修改</button>
            </div>
        </form>
    </div>'''

    return page(f'编辑: {note["title"]}', body, is_admin=True)


@app.route('/update/<note_id>', method='POST')
def update_note_handler(note_id):
    """处理更新笔记"""
    r = require_auth()
    if r:
        return r
    try:
        nid = int(note_id)
    except ValueError:
        return Response('<h1>404 Not Found</h1>', status=404)

    req = request()
    form = req.form
    title = form.get('title', '').strip()
    content = form.get('content', '').strip()

    if not title or not content:
        return page('编辑笔记', f'''
        <div class="flash flash-error">标题和内容不能为空</div>
        <a href="/edit/{note_id}" class="btn btn-primary">返回重试</a>''', is_admin=True)

    note = update_note(nid, title, content)
    if not note:
        return page('未找到', '''
        <div class="empty-state">
            <div class="icon">😕</div>
            <h2>笔记未找到</h2>
            <p><a href="/">返回首页</a></p>
        </div>''', is_admin=True)

    return Response().redirect(f'/note/{nid}')


@app.route('/delete/<note_id>')
def delete_note_handler(note_id):
    """处理删除笔记"""
    r = require_auth()
    if r:
        return r
    try:
        nid = int(note_id)
    except ValueError:
        return Response('<h1>404 Not Found</h1>', status=404)

    note = delete_note(nid)
    if not note:
        return page('未找到', '''
        <div class="empty-state">
            <div class="icon">😕</div>
            <h2>笔记未找到</h2>
            <p><a href="/">返回首页</a></p>
        </div>''', is_admin=True)

    return page('已删除', f'''
    <div class="empty-state">
        <div class="icon">🗑️</div>
        <h2>笔记已删除</h2>
        <p>「{note["title"]}」已被永久删除</p>
        <p style="margin-top: 16px;"><a href="/" class="btn btn-primary">← 返回首页</a></p>
    </div>''', is_admin=True)


# ============================================================
# 附件路由
# ============================================================

@app.route('/note/<note_id>/upload', method='POST')
def upload_attachment(note_id):
    """上传附件到笔记"""
    r = require_auth()
    if r:
        return r
    try:
        nid = int(note_id)
    except ValueError:
        return Response('<h1>404 Not Found</h1>', status=404)

    note = get_note(nid)
    if not note:
        return page('未找到', '''
        <div class="empty-state">
            <div class="icon">😕</div>
            <h2>笔记未找到</h2>
            <p><a href="/">返回首页</a></p>
        </div>''', is_admin=True)

    req = request()
    fields, uploaded_files = req.files

    if 'file' not in uploaded_files:
        return page('上传失败', f'''
        <div class="flash flash-error">未选择文件</div>
        <a href="/note/{note_id}" class="btn btn-primary">返回</a>''', is_admin=True)

    file_info = uploaded_files['file']
    original_name = file_info['filename']
    file_data = file_info['content']

    # 检查文件大小
    if len(file_data) > MAX_UPLOAD_SIZE:
        return page('上传失败', f'''
        <div class="flash flash-error">文件过大（最大 50MB）</div>
        <a href="/note/{note_id}" class="btn btn-primary">返回</a>''', is_admin=True)

    if not original_name:
        return page('上传失败', f'''
        <div class="flash flash-error">文件名不能为空</div>
        <a href="/note/{note_id}" class="btn btn-primary">返回</a>''', is_admin=True)

    # 保存文件
    stored_name, file_size = save_uploaded_file(file_data, original_name)

    # 记录到笔记
    add_attachment(nid, stored_name, original_name, file_size)

    return Response().redirect(f'/note/{note_id}')


@app.route('/uploads/<filename>')
def download_attachment(filename):
    """下载/查看附件"""
    # 安全检查：防止路径穿越
    if '..' in filename or '/' in filename or '\\' in filename:
        return Response('<h1>403 Forbidden</h1>', status=403)

    filepath = os.path.join(UPLOAD_DIR, filename)
    if not os.path.isfile(filepath):
        return Response('<h1>404 Not Found</h1>', status=404)

    # 获取原始文件名（从 SQLite 查询）
    original_name = filename
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT original_name FROM attachments WHERE stored_name = ? LIMIT 1",
            (filename,)
        )
        row = cur.fetchone()
        if row is not None:
            original_name = row['original_name']

    # 检测 MIME 类型
    content_type, _ = mimetypes.guess_type(original_name)
    if content_type is None:
        content_type = 'application/octet-stream'

    try:
        with open(filepath, 'rb') as f:
            data = f.read()
        return Response(data, headers={
            'Content-Type': content_type,
            'Content-Disposition': f'attachment; filename="{original_name}"',
            'Content-Length': str(len(data)),
        })
    except Exception:
        return Response('<h1>500 Internal Server Error</h1>', status=500)


@app.route('/note/<note_id>/attachment/<stored_name>/delete', method='POST')
def delete_attachment(note_id, stored_name):
    """删除笔记的附件"""
    r = require_auth()
    if r:
        return r
    try:
        nid = int(note_id)
    except ValueError:
        return Response('<h1>404 Not Found</h1>', status=404)

    if '..' in stored_name or '/' in stored_name:
        return Response('<h1>403 Forbidden</h1>', status=403)

    if remove_attachment(nid, stored_name):
        return Response().redirect(f'/note/{note_id}')

    return page('未找到', f'''
    <div class="empty-state">
        <div class="icon">😕</div>
        <h2>附件未找到</h2>
        <p><a href="/note/{note_id}">返回笔记</a></p>
    </div>''', is_admin=True)


# ============================================================
# 认证路由
# ============================================================

@app.route('/login')
def login_form():
    """登录页面"""
    content = '''
    <div class="form-card" style="max-width:400px;margin:40px auto;">
        <h2 style="margin-bottom:20px;font-size:22px;">🔑 登录管理</h2>
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">用户名</label>
                <input type="text" id="username" name="username" placeholder="admin" required autofocus>
            </div>
            <div class="form-group">
                <label for="password">密码</label>
                <input type="password" id="password" name="password" placeholder="Enter password..." required>
            </div>
            <div class="form-actions">
                <a href="/" class="btn btn-secondary">返回</a>
                <button type="submit" class="btn btn-success">🔑 登录</button>
            </div>
        </form>
    </div>'''
    return page('登录管理', content)


@app.route('/login', method='POST')
def login_handler():
    """处理登录"""
    req = request()
    form = req.form
    username = form.get('username', '').strip()
    password = form.get('password', '').strip()

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = generate_session_token()
        _sessions[token] = username
        resp = Response().redirect('/')
        resp.headers['Set-Cookie'] = f'session={token}; Path=/; HttpOnly'
        return resp

    return page('登录管理', '''
    <div class="form-card" style="max-width:400px;margin:40px auto;">
        <h2 style="margin-bottom:20px;font-size:22px;">🔑 登录管理</h2>
        <div class="flash flash-error">用户名或密码错误</div>
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">用户名</label>
                <input type="text" id="username" name="username" placeholder="admin" required autofocus>
            </div>
            <div class="form-group">
                <label for="password">密码</label>
                <input type="password" id="password" name="password" placeholder="Enter password..." required>
            </div>
            <div class="form-actions">
                <a href="/" class="btn btn-secondary">返回</a>
                <button type="submit" class="btn btn-success">🔑 登录</button>
            </div>
        </form>
    </div>''')


@app.route('/logout')
def logout_handler():
    """处理退出登录"""
    cookies = parse_cookies(request())
    token = cookies.get('session', '')
    _sessions.pop(token, None)
    resp = Response().redirect('/')
    resp.headers['Set-Cookie'] = 'session=; Path=/; HttpOnly; Max-Age=0'
    return resp


# ============================================================
# API 路由（JSON 接口）
# ============================================================

@app.route('/api/notes')
def api_list():
    """API: 获取笔记列表"""
    notes = load_notes()
    return {'success': True, 'notes': notes, 'count': len(notes)}


@app.route('/api/notes/<note_id>')
def api_get(note_id):
    """API: 获取单篇笔记"""
    try:
        nid = int(note_id)
    except ValueError:
        return {'error': 'Invalid ID'}, 400
    note = get_note(nid)
    if note:
        return {'success': True, 'note': note}
    return {'error': 'Not found'}, 404


@app.route('/api/notes', method='POST')
def api_create():
    """API: 创建笔记"""
    r = require_auth()
    if r:
        return r
    req = request()
    data = req.json or {}
    title = data.get('title', '').strip()
    content = data.get('content', '').strip()
    if not title or not content:
        return {'error': 'title and content are required'}, 400
    note = create_note(title, content)
    return {'success': True, 'note': note}, 201


@app.route('/api/notes/<note_id>', method='POST')
def api_update(note_id):
    """API: 更新笔记"""
    r = require_auth()
    if r:
        return r
    try:
        nid = int(note_id)
    except ValueError:
        return {'error': 'Invalid ID'}, 400
    req = request()
    data = req.json or {}
    title = data.get('title')
    content = data.get('content')
    note = update_note(nid, title, content)
    if note:
        return {'success': True, 'note': note}
    return {'error': 'Not found'}, 404


@app.route('/api/notes/<note_id>', method='DELETE')
def api_delete(note_id):
    """API: 删除笔记"""
    r = require_auth()
    if r:
        return r
    try:
        nid = int(note_id)
    except ValueError:
        return {'error': 'Invalid ID'}, 400
    note = delete_note(nid)
    if note:
        return {'success': True, 'deleted': note['title']}
    return {'error': 'Not found'}, 404


# ============================================================
# 启动
# ============================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Notes App - 纯 Python 笔记应用')
    parser.add_argument('--host', default='0.0.0.0', help='监听地址')
    parser.add_argument('--port', type=int, default=8080, help='监听端口')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    args = parser.parse_args()

    app.host = args.host
    app.port = args.port

    # 初始化数据库
    init_db()

    app.run(debug=args.debug)