# 模板引擎调试记录

## 会话日期
2026-05-11

## 实现过程

### 版本演进

1. **v1 (初始版本)** - 简单的字符串替换式模板引擎
   - 问题：无法处理嵌套块（if/for 嵌套）
   - 结果：生成的 Python 代码缩进错误

2. **v2 (令牌化版本)** - 使用 tokenize 分解模板
   - 问题：`if` 条件中的变量未通过 `_get()` 访问
   - 结果：`NameError: name 'items' is not defined`

3. **v3 (递归编译版本)** - 完整的编译式设计
   - 使用 `_compile_tokens` 和 `_compile_block` 递归处理嵌套
   - 使用 `_wrap_vars_in_condition` 将条件中的变量替换为 `_get()` 调用
   - 循环变量通过 `context[var_name] = var_name` 注入

### 关键调试问题

#### 问题 1: 循环变量显示为空

**现象**: `{% for item in items %}{{ item }}{% endfor %}` 输出空列表项

**原因**: 循环变量 `item` 未注入到 context 中，`_get('item', context)` 返回空

**修复**: 在 for 循环编译时添加:
```python
code.append(f'{indent}    {context_var}["{var_name}"] = {var_name}')
```

#### 问题 2: 嵌套条件缩进错误

**现象**: `IndentationError: expected an indented block`

**原因**: `_compile_block` 递归时未正确传递缩进级别

**修复**: 
1. `_compile_tokens` 中维护 `indent_level = 1`
2. 递归调用 `_compile_block(tokens, i + 1, context_var, indent_level + 1)`
3. `_compile_block` 中计算 `indent = '    ' * indent_level`

#### 问题 3: 条件表达式变量未定义

**现象**: `NameError: name 'items' is not defined`

**原因**: `{% if items %}` 中的 `items` 直接使用，未通过 `_get()` 访问

**修复**: 使用 `_wrap_vars_in_condition()` 将变量名替换为 `_get('var', context)`

```python
def _wrap_vars_in_condition(self, condition, context_var):
    result = []
    i = 0
    while i < len(condition):
        if condition[i:i+4] == '_get':
            # 保留已有的 _get 调用
            ...
        elif condition[i].isalpha() or condition[i] == '_':
            # 变量名替换为 _get 调用
            result.append(f"_get('{var_name}', {context_var})")
        else:
            result.append(condition[i])
            i += 1
    return ''.join(result)
```

#### 问题 4: 过滤器语法解析

**现象**: `{{ item.price|default('N/A') }}` 无法正确解析

**原因**: 变量表达式包含 `|` 分隔符

**修复**: `_get_var()` 中先检查 `|`，分离变量和过滤器:

```python
if '|' in expr:
    parts = expr.split('|', 1)
    var_expr = parts[0].strip()
    filter_expr = parts[1].strip()
    val = self._get_var(var_expr, context)
    return self._apply_filter(val, filter_expr)
```

### 测试用例

```python
# 1. 基本变量
assert engine.render_string('Hello, {{ name }}!', name='World') == 'Hello, World!'

# 2. 条件语句
result = engine.render_string('{% if show %}显示{% else %}隐藏{% endif %}', show=True)
assert '显示' in result

# 3. for 循环
result = engine.render_string('{% for item in items %}{{ item }}{% endfor %}', items=['a','b'])
assert 'ab' in result

# 4. 嵌套 if/for
result = engine.render_string('''
{% if users %}{% for user in users %}{{ user.name }}{% endfor %}{% endif %}
''', users=[{'name': 'Alice'}])
assert 'Alice' in result

# 5. 过滤器
result = engine.render_string('{{ price|default("免费") }}', price=None)
assert '免费' in result
```

## 设计决策

### 为什么选择编译式设计？

1. **性能**: 模板编译为 Python 代码后直接执行，比解释式解析更快
2. **灵活性**: 生成的代码可以是任意 Python 表达式
3. **调试**: 可以查看生成的代码进行调试

### 为什么使用 context 字典而非局部变量？

1. **循环变量注入**: `for x in y` 时需要将 `x` 注入 context 供嵌套访问
2. **嵌套作用域**: 嵌套 if/for 块共享同一个 context
3. **简单性**: 不需要维护复杂的变量作用域栈

### 为什么变量通过 `_get()` 访问？

1. **统一访问**: 所有变量（包括嵌套属性）都通过同一函数访问
2. **安全**: 不存在变量的情况下返回空字符串而非抛出异常
3. **支持嵌套**: `item.name` 自动解析为 `context['item']['name']`

## 文件结构

```
python-web-microframework/
├── SKILL.md              # 技能文档（已更新模板引擎章节）
├── templates/
│   └── miniweb.py        # 完整的单文件微框架（含模板引擎）
└── references/
    └── template-debug.md # 本文件（调试记录）
```
