## 有效代码文件清单（当前运行链路）

### 主程序入口与 UI

- **main.py**：应用入口，初始化 Qt、日志与主窗口。
- **ui/main_window.py**：主界面与全局交互调度，负责触发搜索/下载/更新/翻译任务。
- **ui/dialogs.py**：设置、详情、下载等对话框逻辑。
- **ui/widgets.py**：漫画卡片与网格等复用 UI 组件。
- **reader.py**：本地章节阅读器窗口。

### 业务核心与并发任务

- **core/database.py**：SQLite 数据读写与模型映射。
- **core/workers.py**：后台线程（搜索、下载、批量更新、服务状态检测）。
- **core/scrapers_registry.py**：站点爬虫实例注册表（当前接入 fast_scrapers）。
- **core/task_guard.py**：核心任务互斥控制。
- **core/utils.py**：通用图像工具（长图分割、占位封面）。
- **core/bootstrap.py**：运行时基础初始化。

### 快速爬虫实现（当前有效）

- **fast_scrapers.py**：当前实际生效的无头爬虫实现（Rawkuma/NicoManga/KlManga）与下载逻辑。

### AI 翻译链路（当前有效）

- **saber_api_client.py**：UI 与 SABER 流水线桥接。
- **core/saber/pipeline.py**：OCR→翻译→修复→渲染总流程编排。
- **core/saber/ocr.py**、**core/saber/detector.py**、**core/saber/inpainter.py**、**core/saber/translator.py**、**core/saber/renderer.py**：核心子模块。
- **core/saber/config.py**、**core/saber/data_types.py**、**core/saber/utils.py**、**core/saber/openai_helpers.py**：配置与通用支撑模块。
- **core/saber/dbnet/**、**core/saber/lama/**、**core/saber/detector_utils/**：检测/修复模型与算法实现。

### 环境与启动脚本

- **01_环境安装.bat**：安装 Python 依赖环境。
- **02_点击我启动.bat**：启动脚本。
- **requirements.txt**：当前依赖清单。


