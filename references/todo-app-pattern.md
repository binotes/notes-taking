# Todo List 应用开发参考

## 会话日期
2026-05-13

## 项目概览

基于 miniweb 开发的完整 Todo List 应用，展示 CRUD 应用开发模式。

**文件**: `/home/joyo/todo.py` (~450 行)

## 核心模式

### 1. 数据模型

```python
import json
import os
import uuid
from datetime import datetime

DATA_FILE = 'todo_data.json'

def load_todos():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

def save_todos(todos):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)
```

### 2. CRUD 操作

```python
def add_todo(title, description='', priority='medium'):
    todos = load_todos()
    todo = {
        'id': str(uuid.uuid4())[:8],
        'title': title.strip(),
        'description': description.strip(),
        'priority': priority,
        'completed': False,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    todos.append(todo)
    save_todos(todos)
    return todo

def get_todo(todo_id):
    todos = load_todos()
    for todo in todos:
        if todo['id'] == todo_id:
            return todo
    return None

def update_todo(todo_id, **kwargs):
    todos = load_todos()
    for todo in todos:
        if todo['id'] == todo_id:
            for key, value in kwargs.items():
                if value is not None:
                    todo[key] = value
            todo['updated_at'] = datetime.now().isoformat()
            save_todos(todos)
            return todo
    return None

def delete_todo(todo_id):
    todos = load_todos()
    original_len = len(todos)
    todos = [t for t in todos if t['id'] != todo_id]
    if len(todos) < original_len:
        save_todos(todos)
        return True
    return False

def toggle_todo(todo_id):
    todo = get_todo(todo_id)
    if todo:
        return update_todo(todo_id, completed=not todo['completed'])
    return None
```

### 3. BASE_TEMPLATE 模式

```python
BASE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{{ title }}</title>
    <style>/* 完整 CSS */</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{{ title }}</h1>
            <div class="stats">
                <div class="stat">
                    <div class="stat-number">{{ total }}</div>
                    <div class="stat-label">总计</div>
                </div>
                <!-- 更多统计 -->
            </div>
        </div>
        <div class="content">
            {{ content }}
        </div>
    </div>
</body>
</html>
'''
```

### 4. 路由设计

```python
# 页面路由
@app.route('/')
def index():
    todos = load_todos()
    # 渲染 HTML 页面
    return app.render_string(BASE_TEMPLATE, ...)

# API 路由
@app.route('/api/todos')
def api_list():
    return {'todos': load_todos(), 'count': len(todos)}

@app.route('/api/todos/<todo_id>')
def api_get(todo_id):
    todo = get_todo(todo_id)
    return todo or ({'error': 'Not found'}, 404)

@app.route('/api/todos', method='POST')
def api_create():
    from miniweb import _current_request
    req = _current_request
    data = req.json or {}
    # 验证和创建
    return {'success': True, 'todo': todo}, 201
```

### 5. 前端交互

```javascript
// 添加任务
async function addTodo(e) {
    e.preventDefault();
    const form = e.target;
    const formData = new FormData(form);
    
    const res = await fetch('/add', {
        method: 'POST',
        body: formData
    });
    location.reload();
}

// 切换完成状态
async function toggleTodo(id) {
    await fetch(`/toggle/${id}`);
    location.reload();
}

// 删除任务
async function deleteTodo(id) {
    if (confirm('确定删除？')) {
        await fetch(`/delete/${id}`);
        location.reload();
    }
}

// 筛选
function filterTodos(status) {
    // 更新按钮样式
    document.querySelectorAll('.filter-btn').forEach(btn => 
        btn.classList.remove('active')
    );
    event.target.classList.add('active');
    
    // 过滤显示
    const items = document.querySelectorAll('.todo-item');
    items.forEach(item => {
        const isCompleted = item.classList.contains('completed');
        if (status === 'all' || 
            (status === 'completed' && isCompleted) ||
            (status === 'pending' && !isCompleted)) {
            item.style.display = '';
        } else {
            item.style.display = 'none';
        }
    });
}
```

## 启动命令

```bash
python3 todo.py --port 8081
```

**注意**: 环境中使用 `python3` 而非 `python`。

## 测试验证

```bash
# 检查首页
curl -s http://localhost:8081/ | head -20

# API 列表
curl -s http://localhost:8081/api/todos

# 创建任务
curl -s -X POST http://localhost:8081/api/todos \
  -H "Content-Type: application/json" \
  -d '{"title":"测试任务","priority":"high"}'

# 获取单个任务
curl -s http://localhost:8081/api/todos/97284941
```

## 关键设计决策

### 为什么使用 JSON 文件而非数据库？

1. **简单性**: 无需安装数据库，单文件部署
2. **可移植性**: 数据文件可轻松备份/迁移
3. **适合场景**: 个人工具、小数据量应用

### 为什么使用 BASE_TEMPLATE？

1. **一致性**: 所有页面共享相同布局
2. **维护性**: 修改样式只需改一处
3. **性能**: 模板字符串在内存中，无需文件 I/O

### 为什么使用短 ID（8 位 UUID）？

1. **可读性**: 比完整 UUID 更易读
2. **足够唯一**: 8 位 hex = 2^32 种可能，足够个人使用
3. **URL 友好**: 短且无特殊字符

## 扩展建议

1. **添加搜索**: `/search?q=keyword`
2. **添加分类**: `category` 字段 + 筛选
3. **添加截止日期**: `due_date` 字段 + 过期提醒
4. **添加用户系统**: 多用户支持
5. **添加前端框架**: Vue/React 替代原生 JS
