#!/usr/bin/env python3
"""
miniweb.py - 单文件 Web 微框架（纯 Python 标准库）+ 模板引擎

用法:
    python miniweb.py                    # 启动默认服务器 (0.0.0.0:8080)
    python miniweb.py --port 3000        # 指定端口
    python miniweb.py --debug            # 调试模式

示例:
    @app.route('/')
    def index():
        return 'Hello, World!'
    
    @app.route('/user/<name>')
    def user(name):
        return f'Hello, {name}!'
    
    @app.route('/api/data', method='POST')
    def api():
        req = request()
        data = req.json or req.form
        return {'received': data}
    
    # 模板渲染
    @app.route('/products')
    def products():
        return app.render_string('''
            <h1>{{ title }}</h1>
            {% if items %}
            <ul>
            {% for item in items %}
                <li>{{ item.name }} - {{ item.price|default('N/A') }}</li>
            {% endfor %}
            </ul>
            {% else %}
            <p>暂无商品</p>
            {% endif %}
        ''', title='商品列表', items=[...])
"""

import http.server
import socketserver
import json
import re
import sys
import os
from urllib.parse import urlparse, parse_qs
from datetime import datetime

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
                # 扁平化单值列表：{'field': ['val']} → {'field': 'val'}
                result = {}
                for k, v in raw.items():
                    result[k] = v[0] if len(v) == 1 else v
                return result
            except UnicodeDecodeError:
                pass
        return {}
    
    @property
    def args(self):
        return self.query
    
    def get(self, key, default=None):
        return self.query.get(key, default)


# ============================================================
# 模板引擎 - 编译式设计
# ============================================================

class TemplateEngine:
    """模板引擎 - 将模板编译为 Python 代码"""
    
    def __init__(self, directory='templates', cache=True):
        self.directory = directory
        self.cache = cache
        self._cache = {}
    
    def compile(self, template_str, context_var='context'):
        """将模板编译为 Python 代码"""
        tokens = self._tokenize(template_str)
        code = self._compile_tokens(tokens, 0, context_var)[0]
        header = [
            f'def render({context_var}):',
            '    output = []',
            '    def _w(s): output.append(str(s) if s is not None else "")',
        ]
        return '\n'.join(header) + '\n' + '\n'.join(code) + '\n    return "".join(output)'
    
    def _tokenize(self, template_str):
        """将模板字符串分解为令牌"""
        tokens = []
        # 按模板指令分割
        parts = re.split(r'(\{\%.*?\%\}|\{\{.*?\}\})', template_str, flags=re.DOTALL)
        for part in parts:
            if not part:
                continue
            if part.startswith('{%') and part.endswith('%}'):
                tokens.append(('cmd', part[2:-2].strip()))
            elif part.startswith('{{') and part.endswith('}}'):
                tokens.append(('var', part[2:-2].strip()))
            else:
                # 文本按行分割，保留换行符
                for line in part.split('\n'):
                    tokens.append(('text', line))
                tokens.append(('newline', ''))
        # 移除末尾多余的 newline
        while tokens and tokens[-1][0] == 'newline':
            tokens.pop()
        return tokens
    
    def _compile_tokens(self, tokens, start, context_var):
        """编译令牌列表，返回 (代码行列表, 结束位置)"""
        code = []
        i = start
        indent = '    '  # 顶层缩进
        indent_level = 1  # 顶层缩进级别
        
        while i < len(tokens):
            token_type, token_value = tokens[i]
            
            if token_type == 'text':
                escaped = token_value.replace('\\', '\\\\').replace('"', '\\"')
                code.append(f'{indent}_w("{escaped}")')
                i += 1
            
            elif token_type == 'newline':
                code.append(f'{indent}_w("\\n")')
                i += 1
            
            elif token_type == 'var':
                # 变量输出
                code.append(f'{indent}_w(_get({token_value!r}, {context_var}))')
                i += 1
            
            elif token_type == 'cmd':
                # 命令
                if token_value.startswith('if '):
                    condition = token_value[3:]
                    condition = self._wrap_vars_in_condition(condition, context_var)
                    code.append(f'{indent}if {condition}:')
                    # 收集 if 块
                    if_code, new_i = self._compile_block(tokens, i + 1, context_var, indent_level + 1)
                    code.extend(if_code)
                    
                    # 检查是否有 else
                    if new_i < len(tokens) and tokens[new_i] == ('cmd', 'else'):
                        code.append(f'{indent}else:')
                        else_code, new_i = self._compile_block(tokens, new_i + 1, context_var, indent_level + 1)
                        code.extend(else_code)
                    
                    # 跳过 endif
                    if new_i < len(tokens) and tokens[new_i] == ('cmd', 'endif'):
                        i = new_i + 1
                    else:
                        i = new_i
                    
                elif token_value.startswith('for '):
                    match = re.match(r'for\s+(\w+)\s+in\s+(\w+)', token_value)
                    if match:
                        var_name = match.group(1)
                        iter_name = match.group(2)
                        code.append(f'{indent}for {var_name} in _get({iter_name!r}, {context_var}) or []:')
                        # 循环变量注入 context，使嵌套变量可访问
                        code.append(f'{indent}    {context_var}["{var_name}"] = {var_name}')
                        block_code, new_i = self._compile_block(tokens, i + 1, context_var, indent_level + 1)
                        code.extend(block_code)
                        
                        # 跳过 endfor
                        if new_i < len(tokens) and tokens[new_i] == ('cmd', 'endfor'):
                            i = new_i + 1
                        else:
                            i = new_i
                else:
                    i += 1
            else:
                i += 1
        
        return code, i
    
    def _compile_block(self, tokens, start, context_var, indent_level):
        """编译一个代码块（if/for 内部）"""
        code = []
        i = start
        indent = '    ' * indent_level  # 计算当前缩进
        
        while i < len(tokens):
            token_type, token_value = tokens[i]
            
            if token_type == 'cmd':
                if token_value in ('endif', 'endfor', 'else'):
                    break
                elif token_value.startswith('if '):
                    condition = token_value[3:]
                    condition = self._wrap_vars_in_condition(condition, context_var)
                    code.append(f'{indent}if {condition}:')
                    block_code, new_i = self._compile_block(tokens, i + 1, context_var, indent_level + 1)
                    code.extend(block_code)
                    if new_i < len(tokens) and tokens[new_i] == ('cmd', 'else'):
                        code.append(f'{indent}else:')
                        else_code, new_i = self._compile_block(tokens, new_i + 1, context_var, indent_level + 1)
                        code.extend(else_code)
                    if new_i < len(tokens) and tokens[new_i] == ('cmd', 'endif'):
                        i = new_i + 1
                    else:
                        i = new_i
                    continue
                elif token_value.startswith('for '):
                    match = re.match(r'for\s+(\w+)\s+in\s+(\w+)', token_value)
                    if match:
                        var_name = match.group(1)
                        iter_name = match.group(2)
                        code.append(f'{indent}for {var_name} in _get({iter_name!r}, {context_var}) or []:')
                        code.append(f'{indent}    {context_var}["{var_name}"] = {var_name}')
                        block_code, new_i = self._compile_block(tokens, i + 1, context_var, indent_level + 1)
                        code.extend(block_code)
                        if new_i < len(tokens) and tokens[new_i] == ('cmd', 'endfor'):
                            i = new_i + 1
                        else:
                            i = new_i
                        continue
            
            # 普通令牌
            if token_type == 'text':
                escaped = token_value.replace('\\', '\\\\').replace('"', '\\"')
                code.append(f'{indent}_w("{escaped}")')
            elif token_type == 'newline':
                code.append(f'{indent}_w("\\n")')
            elif token_type == 'var':
                code.append(f'{indent}_w(_get({token_value!r}, {context_var}))')
            
            i += 1
        
        return code, i
    
    def _wrap_vars_in_condition(self, condition, context_var):
        """将条件表达式中的变量替换为 _get 调用"""
        result = []
        i = 0
        while i < len(condition):
            if condition[i:i+4] == '_get':
                end = condition.find(')', i)
                if end != -1:
                    result.append(condition[i:end+1])
                    i = end + 1
                    continue
            elif condition[i].isalpha() or condition[i] == '_':
                start = i
                while i < len(condition) and (condition[i].isalnum() or condition[i] == '_'):
                    i += 1
                var_name = condition[start:i]
                result.append(f"_get('{var_name}', {context_var})")
            else:
                result.append(condition[i])
                i += 1
        return ''.join(result)
    
    def render_string(self, template_str, **context):
        """渲染模板字符串"""
        code = self.compile(template_str)
        namespace = {'_get': self._get_var, '_filter': self._apply_filter}
        exec(code, namespace)
        return namespace['render'](context)
    
    def _get_var(self, expr, context):
        """获取变量值，支持嵌套属性如 item.name 和 filter 如 price|default('N/A')"""
        # 处理 filter
        if '|' in expr:
            parts = expr.split('|', 1)
            var_expr = parts[0].strip()
            filter_expr = parts[1].strip()
            val = self._get_var(var_expr, context)
            return self._apply_filter(val, filter_expr)
        
        parts = expr.split('.')
        val = context.get(parts[0])
        for p in parts[1:]:
            if isinstance(val, dict):
                val = val.get(p, '')
            elif hasattr(val, p):
                val = getattr(val, p)
            else:
                return ''
        return val if val is not None else ''
    
    def _apply_filter(self, value, filter_expr):
        """应用过滤器"""
        # 解析 filter: default('value') 或 upper/lower/length
        if filter_expr.startswith('default('):
            default_val = filter_expr[8:-1]
            # 移除引号
            default_val = default_val.strip("'\"")
            if not value:
                return default_val
            return value
        elif filter_expr == 'upper':
            return str(value).upper()
        elif filter_expr == 'lower':
            return str(value).lower()
        elif filter_expr == 'length':
            return len(value) if value else 0
        return value
    
    def render(self, filename, **context):
        """渲染模板文件"""
        filepath = os.path.join(self.directory, filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Template not found: {filename}")
        with open(filepath, 'r', encoding='utf-8') as f:
            template_str = f.read()
        return self.render_string(template_str, **context)


# ============================================================
# Response 类
# ============================================================

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
    
    def set_cookie(self, name, value, **kwargs):
        cookie = f"{name}={value}"
        for key, val in kwargs.items():
            cookie += f"; {key}={val}"
        existing = self.headers.get('Set-Cookie', '')
        self.headers['Set-Cookie'] = f"{existing}; {cookie}" if existing else cookie
        return self


# ============================================================
# Route 类
# ============================================================

class Route:
    """路由定义"""
    
    def __init__(self, pattern, method='GET', func=None):
        self.pattern = pattern
        self.method = method.upper()
        self.func = func
        # 编译正则：将 <name> 转换为 (?P<name>[^/]+)
        regex_pattern = re.sub(r'<(\w+)>', r'(?P<\1>[^/]+)', pattern)
        regex_pattern = f'^{regex_pattern}$'
        self.regex = re.compile(regex_pattern)
        # 优先级：静态路径 > 带参数的路径（负数，越大越优先）
        self.priority = -pattern.count('<')
    
    def match(self, path, method):
        if self.method != method:
            return None
        match = self.regex.match(path)
        if match:
            return match.groupdict()
        return None


# ============================================================
# miniweb 主应用类
# ============================================================

class miniweb:
    """主应用类"""
    
    def __init__(self, host='0.0.0.0', port=8080, template_dir='templates'):
        self.host = host
        self.port = port
        self.routes = []
        self.before_handlers = []
        self.after_handlers = []
        self.static_dirs = {}
        self.template_engine = TemplateEngine(directory=template_dir)
    
    def route(self, pattern, method='GET'):
        """路由装饰器"""
        def decorator(func):
            self.routes.append(Route(pattern, method, func))
            # 按优先级排序（优先级高的在前）
            self.routes.sort(key=lambda r: r.priority, reverse=True)
            return func
        return decorator
    
    def before(self, func):
        self.before_handlers.append(func)
        return func
    
    def after(self, func):
        self.after_handlers.append(func)
        return func
    
    def static(self, url_prefix, directory):
        self.static_dirs[url_prefix] = directory
    
    def render_string(self, template_str, **context):
        """渲染模板字符串"""
        html = self.template_engine.render_string(template_str, **context)
        return Response(html).html(html)
    
    def render(self, filename, **context):
        """渲染模板文件"""
        html = self.template_engine.render(filename, **context)
        return Response(html).html(html)
    
    def _handle_request(self, method, path, query_string, headers, body):
        """处理单个请求"""
        global _current_request, _current_app
        _current_app = self
        
        req = Request(method, path, query_string, headers, body)
        _current_request = req
        
        # 运行 before 处理器
        for handler in self.before_handlers:
            handler(req)
        
        # 查找匹配的路由
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
            # 检查静态文件
            for prefix, directory in self.static_dirs.items():
                if path.startswith(prefix):
                    file_path = os.path.join(directory, path[len(prefix):])
                    if os.path.isfile(file_path):
                        try:
                            with open(file_path, 'rb') as f:
                                content = f.read()
                            ext = os.path.splitext(file_path)[1].lower()
                            content_types = {
                                '.html': 'text/html', '.css': 'text/css',
                                '.js': 'application/javascript', '.json': 'application/json',
                                '.png': 'image/png', '.jpg': 'image/jpeg',
                            }
                            return Response(content, headers={
                                'Content-Type': content_types.get(ext, 'application/octet-stream')
                            })
                        except Exception:
                            pass
                    break
            
            return Response(f'<h1>404 Not Found</h1><p>Path: {path}</p>', status=404)
        
        # 处理返回值
        if isinstance(result, Response):
            response = result
        elif isinstance(result, tuple) and len(result) == 2:
            # 支持 (data, status) 元组返回，如 return {'key': 'val'}, 201
            data, status = result
            if isinstance(data, dict):
                response = Response().json(data, status)
            else:
                response = Response(str(data), status=status)
        elif isinstance(result, dict):
            response = Response().json(result)
        else:
            response = Response(str(result))
        
        # 运行 after 处理器
        for handler in self.after_handlers:
            handler(req, response)
        
        return response
    
    def run(self, host=None, port=None, debug=False):
        """启动服务器"""
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
            print(f"╔══════════════════════════════════════╗")
            print(f"║       miniweb 服务器已启动            ║")
            print(f"║  http://{host if host != '0.0.0.0' else 'localhost'}:{port}          ║")
            print(f"╚══════════════════════════════════════╝")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n服务器已停止")


def request():
    """获取当前请求对象（在路由处理函数中调用）"""
    return _current_request


# ============================================================
# 示例应用
# ============================================================

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='miniweb - 单文件 Web 微框架')
    parser.add_argument('--host', default='0.0.0.0', help='监听地址')
    parser.add_argument('--port', type=int, default=8080, help='监听端口')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    args = parser.parse_args()
    
    app = miniweb(host=args.host, port=args.port)
    
    @app.route('/')
    def index():
        return '''
        <!DOCTYPE html>
        <html>
        <head><title>miniweb 示例</title></head>
        <body style="font-family: system-ui; max-width: 800px; margin: 50px auto; padding: 20px;">
            <h1>🚀 miniweb 微框架</h1>
            <p>一个用 Python 内建库实现的单文件 Web 框架</p>
            <h2>示例路由:</h2>
            <ul>
                <li><a href="/hello">/hello</a> - 简单响应</li>
                <li><a href="/user/Alice">/user/&lt;name&gt;</a> - 动态路由</li>
                <li><a href="/api/info">/api/info</a> - JSON API</li>
                <li><a href="/template">/template</a> - 模板渲染</li>
                <li><a href="/users">/users</a> - 循环示例</li>
            </ul>
            <h2>测试 POST:</h2>
            <form method="POST" action="/api/echo">
                <input name="message" placeholder="输入消息" style="padding: 8px;">
                <button type="submit">提交</button>
            </form>
        </body>
        </html>
        '''
    
    @app.route('/hello')
    def hello():
        return 'Hello, World! 🌍'
    
    @app.route('/user/<name>')
    def user_page(name):
        return f'Hello, {name}! 👋'
    
    @app.route('/api/info')
    def api_info():
        return {
            'framework': 'miniweb',
            'version': '1.1',
            'python': f'{sys.version_info.major}.{sys.version_info.minor}',
            'features': ['路由', '动态参数', 'JSON', '模板引擎', '条件/循环']
        }
    
    @app.route('/api/echo', method='POST')
    def api_echo():
        req = request()
        data = req.json or req.form
        message = req.get('message', '')
        if not message and data:
            msg = data.get('message', [''])[0] if isinstance(data.get('message'), list) else data.get('message', '')
            message = msg
        return {
            'received': message,
            'json': req.json,
            'form': req.form,
            'timestamp': datetime.now().isoformat()
        }
    
    @app.route('/template')
    def template_demo():
        return app.render_string('''
        <html>
        <head><title>{{ title }}</title></head>
        <body style="font-family: system-ui; padding: 20px;">
            <h1>{{ heading }}</h1>
            {% if items %}
            <table border="1" cellpadding="8" style="border-collapse: collapse;">
                <tr><th>名称</th><th>价格</th></tr>
            {% for item in items %}
                <tr>
                    <td>{{ item.name }}</td>
                    <td>{{ item.price|default('未定价') }}</td>
                </tr>
            {% endfor %}
            </table>
            {% else %}
            <p>暂无商品</p>
            {% endif %}
            <p>共 {{ items|length }} 件商品</p>
            <a href="/">← 返回首页</a>
        </body>
        </html>
        ''', title='模板演示', heading='商品列表',
           items=[
               {'name': 'Python 编程', 'price': 59},
               {'name': 'Linux 入门', 'price': 39},
               {'name': '算法导论'}
           ])
    
    @app.route('/users')
    def users_list():
        users = [
            {'id': 1, 'name': 'Alice', 'email': 'alice@example.com'},
            {'id': 2, 'name': 'Bob', 'email': 'bob@example.com'},
            {'id': 3, 'name': 'Charlie', 'email': 'charlie@example.com'},
        ]
        return app.render_string('''
        <html>
        <head><title>{{ title }}</title></head>
        <body style="font-family: system-ui; padding: 20px;">
            <h1>{{ title }}</h1>
            <ul>
            {% for user in users %}
                <li><strong>{{ user.name }}</strong> - {{ user.email }}</li>
            {% endfor %}
            </ul>
            <a href="/">← 返回首页</a>
        </body>
        </html>
        ''', users=users, title='用户列表')
    
    @app.before
    def log_request(req):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {req.method} {req.path}")
    
    app.run(debug=args.debug)
