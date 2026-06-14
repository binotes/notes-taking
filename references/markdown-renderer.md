# Markdown 渲染器（纯正则，零依赖）

## 概述

`notes_app.py` 内置了一个纯正则实现的 Markdown-to-HTML 渲染器，无需任何第三方库。支持以下语法：

| 特性 | 语法 | 示例 |
|------|------|------|
| 标题 | `# ~ ######` | `# 一级标题` |
| 粗体 | `**text**` 或 `__text__` | `**加粗**` |
| 斜体 | `*text*` 或 `_text_` | `*斜体*` |
| 粗斜体 | `***text***` 或 `___text___` | `***粗斜体***` |
| 删除线 | `~~text~~` | `~~删除~~` |
| 无序列表 | `- item` 或 `* item` | `- 苹果` |
| 有序列表 | `1. item` | `1. 第一项` |
| 行内代码 | `` `code` `` | `` `print("hello")` `` |
| 代码块 | ```` ```lang ... ``` ```` | ```` ```python ... ``` ```` |
| 引用 | `> text` | `> 引用内容` |
| 链接 | `[text](url)` | `[GitHub](https://github.com)` |
| 图片 | `![alt](url)` | `![logo](logo.png)` |
| 分割线 | `---` 或 `***` | `---` |
| 段落 | 空行分隔 | 连续文本行 |

## 核心函数

### `markdown_to_html(text)`

主入口函数。将 Markdown 文本转换为 HTML。

**处理流程**：
1. 保护 fenced code blocks（使用 `\x00CODEBLOCK_N\x00` 哨兵）
2. 按行处理块级元素（标题、列表、引用、分割线、段落）
3. 行内元素由 `_render_inline()` 处理

### `_render_inline(text)`

处理行内元素。**处理顺序**（重要）：
1. HTML 转义（`&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`）
2. 图片 `![alt](url)`
3. 链接 `[text](url)`
4. 删除线 `~~text~~`
5. 粗斜体 `***text***`
6. 粗体 `**text**`
7. 斜体 `*text*`（使用 ASCII 词边界 `(?<![a-zA-Z0-9_])`）
8. 行内代码 `` `code` ``（最后处理，避免被其他规则干扰）

## 关键设计决策

### 1. 行内代码放在最后处理

```python
# 行内代码 `code`（放在最后处理，避免被其他规则干扰）
def replace_inline_code(m):
    code = m.group(1)
    code = code.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    return f'<code>{code}</code>'

text = re.sub(r'`([^`]+)`', replace_inline_code, text)
```

**原因**：反引号内的内容可能包含 `*`, `_`, `[` 等字符，如果先处理行内代码，这些字符会被粗体/斜体/链接规则误匹配。

### 2. 斜体使用 ASCII 词边界

```python
# 斜体 *text* 或 _text_（避免匹配下划线包围的英文单词如 foo_bar）
text = re.sub(r'(?<![a-zA-Z0-9_])(\*|_)(.+?)\1(?![a-zA-Z0-9_])', r'<em>\2</em>', text)
```

**原因**：Python 3 的 `\w` 在 Unicode 模式下匹配中文字符（如 `和`），导致 `*斜体*` 无法匹配。使用 `[a-zA-Z0-9_]` 显式指定 ASCII 词边界。

### 3. Fenced code block 使用哨兵保护

```python
key = f'\x00CODEBLOCK_{block_counter[0]}\x00'
```

**原因**：使用 `\x00`（空字符）作为哨兵，确保不会被任何正则表达式误匹配。哨兵在块级处理阶段被恢复为 `<pre><code>` HTML。

### 4. 块级处理使用状态机

使用 `in_list`, `in_blockquote`, `in_paragraph` 等状态变量跟踪当前块类型，遇到空行或不同类型的块时自动 flush。

## CSS 样式

`.markdown-body` 类提供完整的 Markdown 渲染样式：

- **标题**：h1/h2 带底部边框，字号递减
- **代码块**：深色背景（`#1f2937`），圆角，水平滚动
- **行内代码**：浅灰背景，红色文字
- **引用**：左侧紫色边框（`#6366f1`），浅紫背景
- **列表**：标准缩进，行间距
- **链接**：紫色，悬停下划线
- **图片**：最大宽度 100%，圆角

## 使用方式

```python
# 在路由中
content_html = markdown_to_html(note['content'])

# 在模板中
html = f'<div class="content markdown-body">{content_html}</div>'
```

## 已知限制

- 不支持嵌套列表（如 `- 子项` 缩进）
- 不支持表格
- 不支持任务列表（`- [x]`）
- 不支持脚注
- 不支持 HTML 标签内嵌（会被转义）