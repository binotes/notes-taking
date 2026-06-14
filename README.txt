Notes App v2 - 纯 Python 标准库笔记应用
========================================

无需任何第三方依赖，仅使用 Python 3 内置库。

启动:
  python3 notes_app.py

然后在浏览器打开 http://localhost:8080

功能:
  - 创建/查看/编辑/删除笔记
  - Markdown 渲染（标题、粗体、斜体、列表、代码、引用、链接等）
  - 附件上传/下载/删除
  - 搜索笔记
  - JSON 文件持久化 (自动生成 notes_data.json)
  - RESTful API
  - 响应式 UI

选项:
  python3 notes_app.py --port 3000   # 自定义端口
  python3 notes_app.py --debug       # 调试日志

文件说明:
  notes_app.py          - 主程序（内嵌 miniweb 框架）
  miniweb.py            - 独立版 miniweb 框架
  references/
    todo-app-pattern.md - CRUD 应用开发模式
    template-debug.md   - 模板引擎调试记录
    markdown-renderer.md - Markdown 渲染器文档