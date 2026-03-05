## 文件职责速览

### 入口与应用层

- **main.py**：应用入口，初始化 Qt 应用与默认字体，创建并展示主窗口。
- **ui/main_window.py**：主窗口（漫画库网格、全局操作按钮、任务状态展示、服务器状态检测），负责调度后台下载/更新/翻译线程并刷新 UI。
- **ui/dialogs.py**：设置/新增漫画/详情页等对话框集合，负责单漫画维度的下载、翻译、阅读入口及相关 UI 交互。
- **ui/widgets.py**：可拖拽漫画卡片与网格容器等通用 UI 小组件。
- **reader.py**：章节阅读器窗口（支持生肉/翻译目录的自动切换与展示）。

### 核心层（业务与数据）

- **core/database.py**：SQLite 数据库访问层，保存漫画、章节与设置项；提供读取/写入/排序等接口。
- **core/workers.py**：后台线程集合（搜索、下载章节、下载封面、批量检查更新、服务器状态检测），通过 Qt 信号与 UI 通信。
- **core/scrapers_registry.py**：爬虫注册表，统一管理不同站点 scraper 实例。
- **core/task_guard.py**：核心任务互斥锁，防止“一键追更/检查更新/批量下载/页面翻译”等核心任务并发执行。
- **core/utils.py**：通用工具函数（长图就地分割、生成白色占位封面）。
- **saber_api_client.py**：连接 UI 与 AI 引擎的桥梁，负责在独立线程中运行 AI 翻译任务。

### 引擎 (核心更新)

位于 `core/saber/` 目录下，负责 OCR、翻译与图像处理：
- **core/saber/pipeline.py**：AI 处理总流水线，串联检测、OCR、翻译、去字与渲染流程。
- **core/saber/ocr.py**：OCR 文字识别模块 (MangaOCR)。
- **core/saber/detector.py**：文本区域检测模块 (DBNet)。
- **core/saber/inpainter.py**：图像去字/修复模块 (LaMa)。
- **core/saber/translator.py**：翻译模块 (OpenAI/兼容接口)。
- **core/saber/renderer.py**：文本回填渲染模块。
- **core/saber/config.py**：AI 引擎相关配置管理。
- **core/saber/dbnet/** & **core/saber/lama/**：具体的深度学习模型实现代码。

### 站点抓取与下载

- **scrapers.py**：站点爬虫实现（搜索、拉取章节、下载章节/封面），包含 Rawkuma/NicoManga/KlManga 等实现与文件名清理工具。

### 文档与部署

- **RawMangaManager.md**：项目最新文档，包含详细的功能介绍与使用说明。
- **01_环境安装.bat**：一键部署脚本，自动配置国内源并安装所有依赖。
- **requirements.txt**：项目依赖列表，包含 AI 相关的 PyTorch、MangaOCR 等库。
- **FILE_GUIDE.md**：(本文档) 项目文件结构说明。

### 训练/调试脚本（非运行必需）

- **download_rendering.py**、**inspect_model.py**：开发/调试脚本，不参与主程序启动链路。
