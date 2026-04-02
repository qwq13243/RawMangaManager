import os
import shutil
import time
import subprocess
import re
import datetime

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QWidget, QFormLayout, 
                               QLineEdit, QPushButton, QHBoxLayout, QCheckBox, QFileDialog, 
                               QFrame, QLabel, QGroupBox, QListWidget, QListWidgetItem, 
                               QTextEdit, QMessageBox, QComboBox, QToolBox, QSpinBox, QApplication)
from PySide6.QtCore import Qt, QTimer, QUrl, QEvent
from PySide6.QtGui import QPixmap, QDesktopServices

from core.database import db
from core.workers import WorkerThread, ServerCheckWorker
from core.task_guard import core_task_guard
from core.utils import generate_white_cover
from saber_api_client import TranslationWorker
from reader import MangaReader
from fast_scrapers import clean_filename

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("系统设置")
        self.setFixedSize(600, 700)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        self.toolbox = QToolBox()
        
        # --- Page 1: 常规设置 ---
        page_general = QWidget()
        form_general = QFormLayout(page_general)
        
        self.dir_input = QLineEdit(db.get_setting("base_dir"))
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(lambda: self.browse_dir(self.dir_input))
        h1 = QHBoxLayout(); h1.addWidget(self.dir_input); h1.addWidget(btn_browse)
        form_general.addRow("默认下载根目录", h1)

        self.retry = QLineEdit(db.get_setting("retry_count"))
        form_general.addRow("失败重试次数 (部分爬虫内置)", self.retry)
        
        self.toolbox.addItem(page_general, "常规设置")
        
        # --- Page 2: 翻译设置 ---
        page_trans = QWidget()
        form_trans = QFormLayout(page_trans)
        
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(['siliconflow', 'deepseek', 'volcano', 'caiyun', 'gemini', 'sakura', 'ollama', 'custom'])
        current_provider = db.get_setting('saber_model_provider', 'siliconflow')
        self.provider_combo.setCurrentText(current_provider)
        form_trans.addRow("服务提供商", self.provider_combo)
        
        self.api_key_input = QLineEdit(db.get_setting('saber_api_key', ''))
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("请输入 API Key")
        form_trans.addRow("API Key", self.api_key_input)
        
        self.model_name_input = QLineEdit(db.get_setting('saber_model_name', 'Qwen/Qwen2.5-7B-Instruct'))
        form_trans.addRow("模型名称", self.model_name_input)

        self.cb_use_lama = QCheckBox("启用 LAMA 修复")
        self.cb_use_lama.setChecked(db.get_setting('saber_use_lama', '1') == '1')
        form_trans.addRow(self.cb_use_lama, QWidget())
        
        btn_test = QPushButton("🔗 测试连接")
        btn_test.setStyleSheet("background: #009688; color: white; font-weight: bold; padding: 5px;")
        btn_test.clicked.connect(self.on_test_connection)
        form_trans.addRow(btn_test)
        
        self.toolbox.addItem(page_trans, "翻译设置")
        self.toolbox.setCurrentIndex(1) # 默认展开翻译设置
        
        # --- Page 3: 检测设置 ---
        page_detect = QWidget()
        form_detect = QFormLayout(page_detect)
        
        # Global Expand
        self.spin_expand_global = QSpinBox()
        self.spin_expand_global.setRange(0, 50)
        self.spin_expand_global.setSuffix("%")
        self.spin_expand_global.setValue(int(db.get_setting('saber_detect_expand_global', '0')))
        form_detect.addRow("整体扩展 (%)", self.spin_expand_global)
        form_detect.addRow(QLabel("<span style='color:gray; font-size:10px;'>向四周均匀扩展的百分比 (0-50%)</span>"))
        
        # Directional Expand
        hbox_top_bottom = QHBoxLayout()
        self.spin_expand_top = QSpinBox(); self.spin_expand_top.setRange(0, 50); self.spin_expand_top.setSuffix("%")
        self.spin_expand_top.setValue(int(db.get_setting('saber_detect_expand_top', '0')))
        self.spin_expand_bottom = QSpinBox(); self.spin_expand_bottom.setRange(0, 50); self.spin_expand_bottom.setSuffix("%")
        self.spin_expand_bottom.setValue(int(db.get_setting('saber_detect_expand_bottom', '0')))
        hbox_top_bottom.addWidget(QLabel("上:")); hbox_top_bottom.addWidget(self.spin_expand_top)
        hbox_top_bottom.addWidget(QLabel("下:")); hbox_top_bottom.addWidget(self.spin_expand_bottom)
        form_detect.addRow("上下扩展 (%)", hbox_top_bottom)
        
        hbox_left_right = QHBoxLayout()
        self.spin_expand_left = QSpinBox(); self.spin_expand_left.setRange(0, 50); self.spin_expand_left.setSuffix("%")
        self.spin_expand_left.setValue(int(db.get_setting('saber_detect_expand_left', '0')))
        self.spin_expand_right = QSpinBox(); self.spin_expand_right.setRange(0, 50); self.spin_expand_right.setSuffix("%")
        self.spin_expand_right.setValue(int(db.get_setting('saber_detect_expand_right', '0')))
        hbox_left_right.addWidget(QLabel("左:")); hbox_left_right.addWidget(self.spin_expand_left)
        hbox_left_right.addWidget(QLabel("右:")); hbox_left_right.addWidget(self.spin_expand_right)
        form_detect.addRow("左右扩展 (%)", hbox_left_right)
        
        # Precise Mask
        form_detect.addRow(QLabel("<b>精确文字掩膜</b>"))
        
        self.spin_mask_dilate = QSpinBox()
        self.spin_mask_dilate.setRange(0, 50)
        self.spin_mask_dilate.setValue(int(db.get_setting('saber_mask_dilate_size', '10')))
        form_detect.addRow("膨胀大小 (px)", self.spin_mask_dilate)
        form_detect.addRow(QLabel("<span style='color:gray; font-size:10px;'>掩膜膨胀像素数</span>"))
        
        self.spin_box_expand = QSpinBox()
        self.spin_box_expand.setRange(0, 100)
        self.spin_box_expand.setSuffix("%")
        self.spin_box_expand.setValue(int(db.get_setting('saber_mask_box_expand_ratio', '20')))
        form_detect.addRow("标注框扩大比例 (%)", self.spin_box_expand)
        form_detect.addRow(QLabel("<span style='color:gray; font-size:10px;'>标注框区域扩大百分比</span>"))
        
        self.toolbox.addItem(page_detect, "检测设置")
        
        layout.addWidget(self.toolbox)
        
        btn_save = QPushButton("保存并关闭")
        btn_save.clicked.connect(self.save_settings)
        layout.addWidget(btn_save)

    def browse_dir(self, line_edit):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d: line_edit.setText(d)
    def browse_file(self, line_edit, filter_str):
        f, _ = QFileDialog.getOpenFileName(self, "选择文件", filter=filter_str)
        if f: line_edit.setText(f)

    def on_test_connection(self):
        provider = self.provider_combo.currentText()
        api_key = self.api_key_input.text().strip()
        model_name = self.model_name_input.text().strip()
        
        # 自动获取 Base URL
        from core.saber.config import PROVIDER_BASE_URLS
        base_url = PROVIDER_BASE_URLS.get(provider, "")
        
        # 如果是 custom，尝试从数据库获取（虽然界面上没法改了，但为了兼容性）
        if provider == 'custom':
            base_url = db.get_setting('saber_base_url', '')
            if not base_url:
                QMessageBox.warning(self, "提示", "自定义服务需要在配置文件中预先设置 Base URL")
                return

        from core.saber.translator import test_connection
        
        # 显示等待光标
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            success, msg = test_connection(provider, api_key, model_name, base_url)
        finally:
            QApplication.restoreOverrideCursor()
            
        if success:
            QMessageBox.information(self, "连接成功", f"测试通过！\n{msg}")
        else:
            QMessageBox.critical(self, "连接失败", f"测试未通过：\n{msg}")

    def save_settings(self):
        # General
        db.set_setting("base_dir", self.dir_input.text())
        db.set_setting("retry_count", self.retry.text())
        
        # Translation
        db.set_setting("saber_model_provider", self.provider_combo.currentText())
        db.set_setting("saber_api_key", self.api_key_input.text())
        db.set_setting("saber_model_name", self.model_name_input.text())
        db.set_setting("saber_use_lama", "1" if self.cb_use_lama.isChecked() else "0")
        # Base URL, RPM, Retry, Mode are no longer in UI, but keep existing values or defaults in DB
        # We don't overwrite them here since input fields are gone.
        
        # Detection
        db.set_setting("saber_detect_expand_global", str(self.spin_expand_global.value()))
        db.set_setting("saber_detect_expand_top", str(self.spin_expand_top.value()))
        db.set_setting("saber_detect_expand_bottom", str(self.spin_expand_bottom.value()))
        db.set_setting("saber_detect_expand_left", str(self.spin_expand_left.value()))
        db.set_setting("saber_detect_expand_right", str(self.spin_expand_right.value()))
        db.set_setting("saber_mask_dilate_size", str(self.spin_mask_dilate.value()))
        db.set_setting("saber_mask_box_expand_ratio", str(self.spin_box_expand.value()))
        
        self.accept()

class DetailDialog(QDialog):
    def __init__(self, manga_id, parent=None):
        super().__init__(parent)
        self.manga_id = manga_id
        self._core_task_token = None
        self.manga_info = db.get_manga_detail(manga_id)
        self.setWindowTitle(f"详情: {self.manga_info['title_romaji']}")
        self.resize(900, 650)
        self.init_ui()
        self.load_chapters()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # --- 顶部区域 ---
        top_frame = QFrame()
        top_layout = QHBoxLayout(top_frame)
        
        self.lbl_cover = QLabel()
        self.lbl_cover.setFixedSize(150, 220)
        self.lbl_cover.setStyleSheet("background: #EEE; border: 1px solid #DDD;")
        self.update_cover_display()
        
        info_group = QGroupBox("元数据编辑")
        info_layout = QFormLayout()
        
        # 【修改】调整输入框定义顺序，虽然后面 addRow 顺序才是关键，但保持一致更好
        self.edit_zh = QLineEdit(self.manga_info['title_zh'] or "")
        self.edit_jp = QLineEdit(self.manga_info['title_jp'] or "")
        self.edit_romaji = QLineEdit(self.manga_info['title_romaji'] or "")
        
        btn_upload = QPushButton("上传封面")
        btn_upload.clicked.connect(self.upload_cover)
        
        btn_save_info = QPushButton("💾 保存修改")
        btn_save_info.setStyleSheet("background: #E8F5E9; font-weight: bold; color: #2E7D32;")
        btn_save_info.clicked.connect(self.save_manga_info)
        
        btn_hbox = QHBoxLayout()
        btn_hbox.addWidget(btn_upload)
        btn_hbox.addWidget(btn_save_info)
        
        # 【修改】严格按照 中文 > 日文 > 罗马音 的顺序添加行
        info_layout.addRow("中文名", self.edit_zh)
        info_layout.addRow("日文名", self.edit_jp)
        info_layout.addRow("罗马音", self.edit_romaji)
        info_layout.addRow(btn_hbox)
        
        btn_layout = QVBoxLayout()
        btn_refresh = QPushButton("🔄 更新章节")
        btn_refresh.clicked.connect(self.refresh_chapters)
        
        # 将原有的按钮修改为类属性并默认禁用
        self.btn_trans = QPushButton("🤖 启动机翻")
        self.btn_trans.setEnabled(False)
        self.btn_trans.clicked.connect(self.run_translator)
        
        btn_folder = QPushButton("📂 打开目录")
        btn_folder.clicked.connect(self.open_folder)
        
        # 新增：术语表按钮
        btn_glossary = QPushButton("📖 术语表")
        btn_glossary.setStyleSheet("background: #FFF9C4; color: #F57F17; font-weight: bold;")
        btn_glossary.clicked.connect(self.open_glossary_editor)

        btn_import = QPushButton("📥 导入漫画")
        btn_import.setStyleSheet("background: #E3F2FD; color: #1565C0; font-weight: bold;")
        btn_import.clicked.connect(self.import_local_chapter)
        
        btn_delete = QPushButton("❌ 删除记录")
        btn_delete.setStyleSheet("color: red;")
        btn_delete.clicked.connect(self.delete_manga_confirm)
        
        # 修改循环
        for btn in[btn_refresh, self.btn_trans, btn_folder, btn_glossary, btn_import, btn_delete]:
            btn_layout.addWidget(btn)
        btn_layout.addStretch()

        info_group.setLayout(info_layout)
        top_layout.addWidget(self.lbl_cover)
        top_layout.addWidget(info_group, 1)
        top_layout.addLayout(btn_layout) 
        

        # --- 中部：章节列表 ---
        list_group = QGroupBox("章节列表")
        list_layout = QVBoxLayout(list_group)
        
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.itemDoubleClicked.connect(self.on_chapter_double_click)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self.on_chapter_context_menu)
        
        # 启用多选模式与框选功能
        from PySide6.QtWidgets import QAbstractItemView
        self.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_widget.setDragEnabled(False) # 我们不需要拖拽移动，只需要框选
        
        # 监听选择变化，同步勾选框
        self.list_widget.itemSelectionChanged.connect(self.on_selection_changed)
        
        # [新增] 安装事件过滤器以拦截空格键
        self.list_widget.installEventFilter(self)
        
        ctrl_layout = QHBoxLayout()
        # [修改] 替换为是否追更
        self.chk_follow = QCheckBox("是否追更")
        is_following = 1
        # 此时 self.manga_info 是 Row 对象，可以通过索引访问
        # 原始字段8个，新增2个，is_following 是第8个(下标从0开始)
        if len(self.manga_info) > 8:
            is_following = self.manga_info[8]
        self.chk_follow.setChecked(is_following == 1)
        self.chk_follow.stateChanged.connect(self.toggle_follow)

        btn_dl_sel = QPushButton("下载选中")
        btn_dl_sel.clicked.connect(self.download_selected)
        
        self.btn_trans_sel = QPushButton("翻译选中")
        self.btn_trans_sel.setEnabled(False)
        self.btn_trans_sel.clicked.connect(self.translate_selected)
        
        btn_dl_all = QPushButton("一键下载未下载")
        btn_dl_all.clicked.connect(self.download_pending)

        self.btn_cancel = QPushButton("⏹ 取消下载")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setStyleSheet("color: red;")
        self.btn_cancel.clicked.connect(self.cancel_download)
        
        # 新增取消翻译按钮
        self.btn_cancel_trans = QPushButton("⏹ 取消翻译")
        self.btn_cancel_trans.setEnabled(False)
        self.btn_cancel_trans.setStyleSheet("color: red;")
        self.btn_cancel_trans.clicked.connect(self.cancel_translation)
        
        btn_scroll_top = QPushButton("⏫ 顶部")
        btn_scroll_top.clicked.connect(lambda: self.list_widget.verticalScrollBar().setValue(self.list_widget.verticalScrollBar().minimum()))

        btn_scroll_bottom = QPushButton("⏬ 底部")
        btn_scroll_bottom.clicked.connect(lambda: self.list_widget.verticalScrollBar().setValue(self.list_widget.verticalScrollBar().maximum()))
        
        ctrl_layout.addWidget(self.chk_follow)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.btn_cancel)
        ctrl_layout.addWidget(self.btn_cancel_trans)
        ctrl_layout.addWidget(btn_dl_sel)
        ctrl_layout.addWidget(self.btn_trans_sel) 
        ctrl_layout.addWidget(btn_dl_all)
        ctrl_layout.addWidget(btn_scroll_top)
        ctrl_layout.addWidget(btn_scroll_bottom)

        list_layout.addWidget(self.list_widget)
        list_layout.addLayout(ctrl_layout)

        # --- 底部：日志 ---
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(120)
        self.log_output.setStyleSheet("background: #F5F5F5; font-family: Consolas; font-size: 11px;")

        layout.addWidget(top_frame)
        layout.addWidget(list_group, 1)
        layout.addWidget(self.log_output)

        # 在 init_ui 底部新增后台定时器，检测服务状态
        self.server_check_timer = QTimer(self)
        self.server_check_timer.timeout.connect(self.check_server_status)
        self.server_check_timer.start(2000)

    def update_cover_display(self):
        path = self.manga_info['cover_path']
        if path and os.path.exists(path):
            pixmap = QPixmap(path).scaled(150, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.lbl_cover.setPixmap(pixmap)
        else:
            temp_path = os.path.join(os.getcwd(), "temp_cover.png")
            generate_white_cover(temp_path)
            self.lbl_cover.setPixmap(QPixmap(temp_path))

    def upload_cover(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择封面", filter="Images (*.png *.jpg *.jpeg)")
        if f:
            dest_dir = os.path.join(os.getcwd(), "covers")
            os.makedirs(dest_dir, exist_ok=True)
            ext = os.path.splitext(f)[1]
            dest_path = os.path.join(dest_dir, f"manga_{self.manga_id}{ext}")
            shutil.copy(f, dest_path)
            db.update_manga_cover(self.manga_id, dest_path)
            self.manga_info = db.get_manga_detail(self.manga_id)
            self.update_cover_display()
            self.log("封面已更新")

    def open_reader(self, cap):
        # [Fix] 解包 10 个参数
        cap_id, ch_str, orig_title, url, is_dl, local_path, source_site, is_trans, read_status, chapter_num = cap
        
        base_dir = db.get_setting("base_dir")
        folder = self.manga_info['folder_name'] or self.manga_info['title_romaji']
        safe_title = clean_filename(orig_title)
        
        # 1. 优先尝试使用 local_path
        raw_dir = ""
        if local_path and os.path.exists(local_path):
            raw_dir = local_path
        
        # 2. 如果 local_path 无效，尝试根据标题推断
        if not raw_dir or not os.path.exists(raw_dir):
            # 构造生肉目录 (新的两层结构)
            raw_dir = os.path.join(base_dir, folder, safe_title, safe_title)
            # 如果新结构不存在，尝试旧结构 (兼容性)
            if not os.path.exists(raw_dir):
                raw_dir = os.path.join(base_dir, folder, safe_title)

        # 构造熟肉目录 (推断)
        # 如果是 local_path，我们假设熟肉目录在同级或符合命名规范
        if local_path and os.path.exists(local_path):
            # 假设 local_path 是 .../safe_title/safe_title
            parent = os.path.dirname(local_path)
            trans_dir = os.path.join(parent, f"Trans_{os.path.basename(local_path)}")
        else:
            trans_dir = os.path.join(base_dir, folder, safe_title, f"Trans_{safe_title}")
        
        # 确定优先显示的目录：如果有翻译且有文件，优先显示翻译；否则显示生肉
        target_dir = raw_dir
        is_showing_trans = False
        
        import glob
        valid_exts = ('*.png', '*.jpg', '*.jpeg', '*.webp', '*.bmp', '*.gif', '*.tif', '*.tiff')
        
        def collect_images(dir_path):
            if not dir_path or not os.path.exists(dir_path):
                return []
            out = []
            try:
                # 避免使用 glob.glob，因为路径中的 [local] 等方括号会被误认为是通配符
                files = os.listdir(dir_path)
                for f in files:
                    full = os.path.join(dir_path, f)
                    if os.path.isfile(full):
                        # 检查扩展名
                        ext = os.path.splitext(f)[1].lower()
                        if ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'):
                            if "Trans_" not in f:
                                out.append(full)
            except Exception as e:
                self.log(f"读取目录失败: {e}")
            return out

        # 检查翻译目录是否有图片
        trans_images = collect_images(trans_dir)
        
        if trans_images:
            target_dir = trans_dir
            is_showing_trans = True
        else:
            # 如果没有翻译，检查生肉
            if not os.path.exists(raw_dir):
                self.log(f"找不到章节目录: {raw_dir}")
                return
            
            raw_images = collect_images(raw_dir)
            
            # 如果两层目录没找到，尝试退回上一层找 (针对某些旧数据或手动移动的情况)
            if not raw_images:
                parent_dir = os.path.dirname(raw_dir)
                if os.path.exists(parent_dir) and os.path.normpath(parent_dir) != os.path.normpath(base_dir): # 防止回退过头
                     # 再次检查上一层
                     raw_images = collect_images(parent_dir)
                     if raw_images:
                         raw_dir = parent_dir
            
            if not raw_images:
                self.log(f"章节目录下没有找到图片: {raw_dir}")
                # 尝试列出目录下文件，辅助调试
                try:
                    files = os.listdir(raw_dir)
                    self.log(f"目录内容: {files[:5]}...")
                except: pass
                return
            
            # 修复：即使数据库里 is_downloaded 为 0，只要目录有图片也允许阅读
            if not is_dl:
                db.mark_chapter_downloaded(cap_id, raw_dir)
                is_dl = 1
                
        # 更新阅读状态
        db.update_chapter_read_status(cap_id, 1)
        self.load_chapters()
        
        # [修改] 传递 raw_dir 和 trans_dir 给阅读器，支持中键切换
        # 如果 raw_dir 是旧结构（包含 Trans 子目录），阅读器加载时会自动过滤，或者我们在这里传好路径
        self.reader = MangaReader(target_dir, parent=None, raw_path=raw_dir, trans_path=trans_dir, chapter_title=f"{ch_str} - {orig_title}")
        self.reader.finished_reading.connect(lambda: self.on_reading_finished(cap_id))
        self.reader.show()
        
        self.reader.raise_()
        self.reader.activateWindow()

    def on_chapter_double_click(self, item):
        cap = item.data(Qt.UserRole)
        self.open_reader(cap)

    def on_chapter_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        cap = item.data(Qt.UserRole)
        if not cap:
            return
        source_site = cap[6]
        if source_site != "local":
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        cap_id = cap[0]
        act_del = menu.addAction("删除本地导入章节记录")
        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(pos))
        if chosen != act_del:
            return
        if QMessageBox.question(self, "确认", "确定删除该本地导入章节记录？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        db.delete_chapter(cap_id)
        self.load_chapters()

    def on_reading_finished(self, cap_id):
        # 标绿，状态2
        db.update_chapter_read_status(cap_id, 2)
        self.load_chapters()
        self.log("章节阅读完成。")
        
        # 自动跳转逻辑
        chapters = db.get_chapters(self.manga_id)
        curr_idx = -1
        for i, cap in enumerate(chapters):
            if cap[0] == cap_id:
                curr_idx = i
                break
                
        # chapters 列表已经是 DESC(降序) 排列的 (例如：102话, 101话, 100话)
        # 如果当前在 100话 (索引2)，下一话 101话 的索引就是 curr_idx - 1 (即1)
        if curr_idx > 0:
            next_cap = chapters[curr_idx - 1]
            self.log(f"自动跳转到下一话: {next_cap[1]}")
            self.open_reader(next_cap)
        else:
            self.log("已经是最新一话，没有下一话可阅读。")
    
    def save_manga_info(self):
        new_zh = self.edit_zh.text().strip()
        old_folder = self.manga_info['folder_name']
        data = {
            'zh': new_zh,
            'jp': self.edit_jp.text().strip(),
            'romaji': self.edit_romaji.text().strip(),
            'cover': self.manga_info['cover_path']
        }
        
        # 文件夹重命名支持
        if new_zh and new_zh != self.manga_info['title_zh']:
            new_folder = clean_filename(new_zh)
            base_dir = db.get_setting("base_dir")
            old_path = os.path.join(base_dir, old_folder)
            new_path = os.path.join(base_dir, new_folder)
            
            if os.path.exists(old_path) and not os.path.exists(new_path):
                try:
                    os.rename(old_path, new_path)
                    db.conn.cursor().execute("UPDATE manga SET folder_name=? WHERE id=?", (new_folder, self.manga_id))
                    db.conn.commit()
                    
                    # [修改] 自动修正封面路径或重新检测
                    old_cover = self.manga_info['cover_path']
                    if old_cover:
                        # 尝试简单路径替换
                        new_cover_path = old_cover.replace(old_folder, new_folder)
                        if os.path.exists(new_cover_path):
                            data['cover'] = new_cover_path
                            db.update_manga_cover(self.manga_id, new_cover_path)
                            self.log(f"封面路径已自动修正")
                        else:
                            # 如果路径替换失败，尝试在目录下搜寻图片
                            import glob
                            valid_exts = ('*.jpg', '*.jpeg', '*.png', '*.webp')
                            found = None
                            for ext in valid_exts:
                                matches = glob.glob(os.path.join(new_path, ext))
                                if matches:
                                    found = matches[0]
                                    break
                            if found:
                                data['cover'] = found
                                db.update_manga_cover(self.manga_id, found)
                                self.log(f"已重新检测并设置封面: {os.path.basename(found)}")

                except Exception as e:
                    self.log(f"重命名文件夹失败: {e}")

        db.update_manga_info(self.manga_id, data)
        self.manga_info = db.get_manga_detail(self.manga_id)
        
        self.log("✅ 漫画元数据已保存！")
        
        if self.parent():
            self.parent().refresh_grid()

    def toggle_follow(self, state):
        db.update_manga_following(self.manga_id, 1 if state else 0)
        self.log(f"追更状态已更新: {'是' if state else '否'}")

    def load_chapters(self):
        self.list_widget.clear()
        chapters = db.get_chapters(self.manga_id)
        
        base_dir = db.get_setting("base_dir")
        folder = self.manga_info['folder_name'] or self.manga_info['title_romaji']
        full_path = os.path.join(base_dir, folder)
        
        for cap in chapters:
            # [Fix] 解包 10 个字段，匹配 db.get_chapters 返回的列数
            # 字段顺序: id, chapter_str, title_original, url, is_downloaded, local_path, source_site, is_translated, read_status, chapter_num
            cap_id, ch_str, orig_title, url, is_dl, local_path, source_site, is_trans, read_status, chapter_num = cap
            
            safe_title = clean_filename(orig_title)
            level_1_dir = os.path.join(full_path, safe_title)
            trans_dir = os.path.join(level_1_dir, f"Trans_{safe_title}")
            
            actual_images = 0
            trans_images = 0
            valid_exts = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff')
            
            if not os.path.exists(level_1_dir):
                is_dl = 0
                is_trans = 0
                db.conn.cursor().execute("UPDATE chapters SET is_downloaded=0, is_translated=0, local_path=NULL WHERE id=?", (cap_id,))
            else:
                for root, dirs, files in os.walk(level_1_dir):
                    if root == trans_dir: continue
                    actual_images += len([f for f in files if f.lower().endswith(valid_exts)])
                    
                if os.path.exists(trans_dir):
                    trans_images = len([f for f in os.listdir(trans_dir) if f.lower().endswith(valid_exts)])

                if actual_images == 0:
                    is_dl = 0
                    is_trans = 0
                else:
                    is_dl = 1
                    if trans_images > 0 and trans_images >= actual_images:
                        is_trans = 1
                    else:
                        is_trans = 0
                
                db.conn.cursor().execute("UPDATE chapters SET is_downloaded=?, is_translated=? WHERE id=?", (is_dl, is_trans, cap_id))
            db.conn.commit()

            dl_icon = "✅" if is_dl else "⬜"
            tr_icon = "✅" if is_trans else "⬜"
            status_text = f"[下:{dl_icon}|翻:{tr_icon}]"
            
            progress_text = ""
            if is_dl and not is_trans and trans_images > 0 and actual_images > 0:
                progress_text = f" ({trans_images}/{actual_images})"
            
            # 阅读状态标色逻辑
            status_val = int(read_status) if read_status else 0
            read_mark = ""
            if status_val == 1:
                read_mark = " 🔵[阅读中]"
            elif status_val == 2:
                read_mark = " 🟢[已读]"
            
            source_tag = "" if source_site == "local" else (f"[{source_site}]" if source_site else "")
            text = f"{status_text} {ch_str} - {orig_title} {source_tag}{progress_text}{read_mark}"
            
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked) 
            # [Fix] 存入 item 的 UserRole 数据也需要更新为 10 元组，保持一致性
            new_cap = (cap_id, ch_str, orig_title, url, is_dl, local_path, source_site, is_trans, read_status, chapter_num)
            item.setData(Qt.UserRole, new_cap)
            self.list_widget.addItem(item)

    def log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        self.log_output.append(f"[{timestamp}] {msg}")
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def _try_acquire_core_task(self, task_name):
        token, busy = core_task_guard.try_acquire(task_name)
        if token is None:
            QMessageBox.information(self, "提示", f"当前有任务正在执行：{busy}")
            self.log(f"拦截：当前有任务正在执行：{busy}")
            return None
        self._core_task_token = token
        return token

    def refresh_chapters(self):
        url = self.manga_info['source_url']
        if not url:
            self.log("错误：该漫画没有关联源 URL。")
            return
        
        # 简单推断源，实际应存储 source_site 在 manga 表
        source = "rawkuma" 
        if "nicomanga" in url: source = "nicomanga"
        elif "klmanga" in url: source = "klmanga"
        
        title_zh = ""
        title_jp = ""
        title_romaji = ""
        try:
            title_zh = self.manga_info["title_zh"] or ""
        except Exception:
            pass
        try:
            title_jp = self.manga_info["title_jp"] or ""
        except Exception:
            pass
        try:
            title_romaji = self.manga_info["title_romaji"] or ""
        except Exception:
            pass
        title = title_zh or title_jp or title_romaji or ""
        self.log(f"正在检查: {title} ({source})")
        self.workthread = WorkerThread("get_chapters", {"url": url, "source": source, "title": title})
        self.workthread.finished_signal.connect(self.on_refresh_done)
        self.workthread.error_signal.connect(lambda e: self.log(f"错误：{e}"))
        self.workthread.progress_signal.connect(lambda msg: self.log(msg))
        self.workthread.start()

    def on_refresh_done(self, result):
        if result['type'] == 'chapters':
            db.save_chapters(self.manga_id, result['data'], result['source'])
            latest_text = None
            try:
                rows = db.get_chapters(self.manga_id)
                if rows:
                    latest = rows[0]
                    latest_text = latest[1]
                    if latest[2] and latest[2] != latest[1]:
                        latest_text = f"{latest[1]} - {latest[2]}"
            except Exception:
                latest_text = None

            if latest_text:
                self.log(f"检查完成，目前最新章节为 {latest_text}")
            else:
                self.log("检查完成，但未获取到最新章节信息")
            self.load_chapters()

    def run_translator(self):
        """点击 🤖 启动机翻：从上到下按顺序翻译，直到遇到翻译过的章节停止"""
        tasks =[]
        # 按列表顺序（从上到下即从新到旧）遍历
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            cap = item.data(Qt.UserRole)
            is_dl = cap[4]
            is_trans = cap[7]
            
            if is_trans == 1:
                break # 遇到翻译过的，立即停止向下收集
                
            # 只有已下载且未翻译的加入任务队列
            if is_dl == 1:
                tasks.append(cap)
                
        self.execute_translation(tasks)

    def eventFilter(self, source, event):
        # 拦截 list_widget 的 KeyPress 事件
        if source == self.list_widget and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space:
                items = self.list_widget.selectedItems()
                if items:
                    # 只要有一个是未勾选的，就全勾；全是已勾选的，才全不勾
                    has_unchecked = any(i.checkState() == Qt.Unchecked for i in items)
                    new_state = Qt.Checked if has_unchecked else Qt.Unchecked
                    
                    for item in items:
                        item.setCheckState(new_state)
                # 阻止事件继续传播，防止默认行为（只切换 focusItem）
                return True
        return super().eventFilter(source, event)

    def on_selection_changed(self):
        # 当用户使用鼠标框选或 Shift/Ctrl 多选时，自动同步 CheckBox 状态
        # 策略：不自动修改 CheckBox，而是让“下载选中”/“翻译选中”按钮同时识别 Selection 和 CheckBox。
        pass

    def keyPressEvent(self, event):
        # 监听空格键，切换当前选中项的 Check 状态
        if event.key() == Qt.Key_Space:
            # 必须使用 list_widget.selectedItems() 获取所有被框选的项目
            # 注意：默认的 keyPressEvent 处理可能会被 QListWidget 内部的键盘导航抢占
            # 所以如果焦点在 list_widget 上，可能需要安装事件过滤器或者在这里强制处理
            items = self.list_widget.selectedItems()
            if items:
                # 统计当前状态，决定是全勾还是全不勾
                # 只要有一个是未勾选的，就全勾；全是已勾选的，才全不勾
                has_unchecked = any(i.checkState() == Qt.Unchecked for i in items)
                new_state = Qt.Checked if has_unchecked else Qt.Unchecked
                
                for item in items:
                    item.setCheckState(new_state)
            
            # 阻止默认的空格行为（防止只触发当前 focusItem 的切换）
            event.accept()
            return
            
        super().keyPressEvent(event)

    def translate_selected(self):
        """仅翻译打勾或框选的项目"""
        # 合并 CheckBox 选中的和 HighLight 选中的
        selected_items = self.list_widget.selectedItems()
        checked_items = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                checked_items.append(item)
        
        # 去重合并 (QListWidgetItem 不可哈希，改用 id 去重)
        seen = set()
        all_targets = []
        for item in selected_items + checked_items:
            if id(item) not in seen:
                all_targets.append(item)
                seen.add(id(item))

        tasks = []
        for item in all_targets:
            cap = item.data(Qt.UserRole)
            # 必须是已下载才能翻译
            if cap[4] == 1:
                tasks.append(cap)
        self.execute_translation(tasks)

    def execute_translation(self, tasks):
        if not tasks:
            self.log("没有找到已下载且待翻译的章节。")
            return
            
        # Req1: 每次启动机翻先进行程序的启动
        exe = db.get_setting("translator_path")
        if exe and os.path.exists(exe):
            self.log("正在启动服务端引擎...")
            try:
                # 不阻塞启动外部程序
                subprocess.Popen(f'"{exe}"', shell=True)
                time.sleep(3) # 稍微等待服务器端口绑定
            except Exception as e:
                self.log(f"服务端启动失败: {e}")
        else:
            self.log("未配置机翻程序路径或路径无效，尝试直接请求本地接口...")

        base_dir = db.get_setting("base_dir")
        folder = self.manga_info['folder_name'] or self.manga_info['title_romaji']
        full_path = os.path.join(base_dir, folder)
        
        # 组装传给后台的精简任务数据
        worker_tasks =[]
        for cap in tasks:
            # 确保传递安全清洗后的标题
            safe_title = clean_filename(cap[2])
            worker_tasks.append({"cap_id": cap[0], "base_dir": full_path, "ch_title": safe_title})
            
        self.log(f"开始翻译任务，共计 {len(worker_tasks)} 个章节。")
        self.btn_cancel.setEnabled(True)
        
        # 启动后台翻译线程
        self.trans_thread = TranslationWorker(worker_tasks)
        self.trans_thread.log_signal.connect(self.log) # Req5: 输出到下方信息栏
        self.trans_thread.chapter_finished.connect(self.on_chapter_translated)
        self.trans_thread.finished_all.connect(self.on_translation_done)
        self.trans_thread.start()

    def on_chapter_translated(self, cap_id):
        # 某个章节完成，更新数据库并刷新列表UI
        db.mark_chapter_translated(cap_id)
        self.load_chapters()

    def on_translation_done(self):
        self.btn_cancel.setEnabled(False)
        self.log("所有翻译任务已结束。")

    def open_folder(self):
        base_dir = db.get_setting("base_dir")
        folder = self.manga_info['folder_name'] or self.manga_info['title_romaji']
        path = os.path.join(base_dir, folder)
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def import_local_chapter(self):
        base_dir = db.get_setting("base_dir")
        folder = self.manga_info['folder_name'] or self.manga_info['title_romaji']
        full_path = os.path.join(base_dir, folder)
        os.makedirs(full_path, exist_ok=True)

        chosen_dir = QFileDialog.getExistingDirectory(self, "选择要导入的图片目录", full_path)
        if not chosen_dir:
            return

        valid_exts = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff')
        images = []
        chosen_dir_abs = os.path.abspath(chosen_dir)
        for root, _, files in os.walk(chosen_dir_abs):
            for f in files:
                full = os.path.join(root, f)
                if f.lower().endswith(valid_exts):
                    images.append(full)

        if not images:
            QMessageBox.information(self, "提示", "该目录下没有找到图片文件。")
            return

        def sort_key(p):
            rel = os.path.relpath(p, chosen_dir_abs)
            parts = re.split(r'(\d+)', rel)
            out = []
            for t in parts:
                if t.isdigit():
                    out.append(int(t))
                else:
                    out.append(t.lower())
            return out

        images.sort(key=sort_key)

        base_name = os.path.basename(os.path.normpath(chosen_dir_abs)) or "Imported"
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        title_original = f"{base_name} {stamp} [local]"
        chapter_str = f"Local {stamp}"

        safe_title = clean_filename(title_original)
        level_1_dir = os.path.join(full_path, safe_title)
        raw_dir = os.path.join(level_1_dir, safe_title)
        os.makedirs(raw_dir, exist_ok=True)

        for idx, src in enumerate(images):
            ext = os.path.splitext(src)[1].lower()
            dst_name = f"{idx + 1:04d}{ext}"
            dst = os.path.join(raw_dir, dst_name)
            shutil.copy2(src, dst)

        cap_id = db.add_local_chapter(
            self.manga_id,
            chapter_str=chapter_str,
            title_original=title_original,
            local_path=os.path.abspath(raw_dir),
            source_site="local",
        )

        self.load_chapters()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            cap = item.data(Qt.UserRole)
            if cap and cap[0] == cap_id:
                item.setCheckState(Qt.Checked)
                break

        self.log(f"✅ 已导入本地章节: {title_original} ({len(images)} 页)")

    def open_glossary_editor(self):
        """打开术语表编辑器"""
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑术语表")
        dlg.resize(400, 300)
        layout = QVBoxLayout(dlg)
        
        layout.addWidget(QLabel("请填写专有名词（只有中文），每行一个："))
        
        editor = QTextEdit()
        # 获取现有术语
        # self.manga_info 是一个 Row 对象，可以通过列名或索引访问
        # 由于我们刚刚添加了 glossary 列，需要重新获取最新的 manga_info
        self.manga_info = db.get_manga_detail(self.manga_id)
        current_glossary = ""
        # 兼容处理：检查是否存在 glossary 字段
        if 'glossary' in self.manga_info.keys():
            current_glossary = self.manga_info['glossary'] or ""
        
        editor.setPlainText(current_glossary)
        layout.addWidget(editor)
        
        btn_save = QPushButton("保存")
        def save():
            text = editor.toPlainText().strip()
            db.update_manga_glossary(self.manga_id, text)
            self.manga_info = db.get_manga_detail(self.manga_id) # 刷新缓存
            self.log("术语表已更新")
            dlg.accept()
            
        btn_save.clicked.connect(save)
        layout.addWidget(btn_save)
        
        dlg.exec()

    def delete_manga_confirm(self):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("确认")
        msg_box.setText("确定要删除该漫画的记录吗？")
        cb = QCheckBox("同时删除本地文件")
        msg_box.setCheckBox(cb)
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        
        if msg_box.exec() == QMessageBox.Yes:
            if cb.isChecked():
                base_dir = db.get_setting("base_dir")
                folder = self.manga_info['folder_name'] or self.manga_info['title_romaji']
                path = os.path.join(base_dir, folder)
                if os.path.exists(path):
                    import shutil
                    try:
                        shutil.rmtree(path)
                    except Exception as e:
                        self.log(f"删除文件失败: {e}")
            
            db.delete_manga(self.manga_id)
            self.accept()
            if self.parent(): self.parent().refresh_grid()

    def execute_download(self, tasks):
        if not tasks:
            self.log("没有需要下载的章节。")
            return

        token = self._try_acquire_core_task("批量下载")
        if token is None:
            return
        
        base_dir = db.get_setting("base_dir")
        folder = self.manga_info['folder_name'] or self.manga_info['title_romaji']
        full_path = os.path.join(base_dir, folder)
        
        # 1. 创建线程
        self.workthread = WorkerThread("download", {"chapters": tasks, "base_dir": full_path})
        # 2. 连接信号
        self.workthread.finished_signal.connect(self.on_download_event)
        self.workthread.error_signal.connect(lambda e: self.log(e))
        self.workthread.progress_signal.connect(self.log)
        self.workthread.finished.connect(lambda token=token: core_task_guard.release(token))
        
        # 3. [新增] 启动前启用取消按钮
        self.btn_cancel.setEnabled(True) 
        
        # 4. 启动线程
        self.workthread.start()
        self.log("下载任务已启动...")

    def on_download_event(self, data):
        if data.get("type") == "progress":
            self.load_chapters()
        elif data.get("type") == "done":
            # [新增] 完成后禁用取消按钮
            self.btn_cancel.setEnabled(False) 
            self.log("所有任务完成。")
            self.load_chapters()
            core_task_guard.release(getattr(self, "_core_task_token", None))
            self._core_task_token = None

    def download_selected(self):
        """下载打勾或框选的项目"""
        # 合并 CheckBox 选中的和 HighLight 选中的
        selected_items = self.list_widget.selectedItems()
        checked_items = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                checked_items.append(item)
        
        # 去重合并 (QListWidgetItem 不可哈希，改用 id 去重)
        seen = set()
        all_targets = []
        for item in selected_items + checked_items:
            if id(item) not in seen:
                all_targets.append(item)
                seen.add(id(item))
        
        tasks = []
        for item in all_targets:
            cap = item.data(Qt.UserRole)
            # cap[4] 是 is_dl
            if cap[4] == 0:
                tasks.append(cap)
        self.execute_download(tasks)

    def download_pending(self):
        tasks = []
        # list_widget 是倒序排列 (DESC)，即最新的在最上面
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            cap = item.data(Qt.UserRole)
            
            # cap[4] 是 is_dl (1=已下载, 0=未下载)
            if cap[4] == 1:
                # 遇到最新一个已下载的章节，停止往回查找
                # 这意味着只下载最新的连续未下载章节（追更逻辑）
                break
                
            tasks.append(cap)
            
        # 将任务列表反转，改为从旧到新下载 (符合阅读顺序)
        tasks.reverse()
        
        self.execute_download(tasks)

    def cancel_download(self):
        if hasattr(self, 'workthread') and self.workthread.isRunning():
            self.workthread.cancel()
            self.log("下载任务已提交取消请求，将在当前图片完成后停止")
        if hasattr(self, 'trans_thread') and self.trans_thread.isRunning():
            self.trans_thread.cancel()
            self.log("翻译任务已提交取消请求")
        self.btn_cancel.setEnabled(False)

    def closeEvent(self, event):
        if hasattr(self, 'server_check_timer'):
            self.server_check_timer.stop()
            
        dl_running = hasattr(self, 'workthread') and self.workthread.isRunning() and self.workthread.task_type == "download"
        trans_running = hasattr(self, 'trans_thread') and self.trans_thread.isRunning()
        
        if dl_running or trans_running:
            reply = QMessageBox.question(self, '确认退出', '任务正在进行中（下载或翻译），是否确认中止任务并退出？',
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                # 退出前断开信号，防止后台线程继续向已销毁的窗口发送数据导致崩溃
                if dl_running:
                    try:
                        self.workthread.progress_signal.disconnect()
                        self.workthread.finished_signal.disconnect()
                    except: pass
                    self.workthread.cancel()
                if trans_running:
                    try:
                        self.trans_thread.log_signal.disconnect()
                        self.trans_thread.chapter_finished.disconnect()
                        self.trans_thread.finished_all.disconnect()
                    except: pass
                    self.trans_thread.cancel()
                super().closeEvent(event)
            else:
                event.ignore()
        else:
            super().closeEvent(event)

    def start_translator_server(self):
        exe = db.get_setting("translator_path")
        if exe and os.path.exists(exe):
            self.log("正在启动翻译器...")
            subprocess.Popen(f'"{exe}"', shell=True)
        else:
            self.log("未配置机翻程序路径或路径无效")

    def check_server_status(self):
        if hasattr(self, 'server_checker') and self.server_checker.isRunning():
            return
            
        self.server_checker = ServerCheckWorker()
        self.server_checker.status_signal.connect(self.update_server_status_ui)
        self.server_checker.start()

    def update_server_status_ui(self, is_alive):
        self.btn_trans.setEnabled(is_alive)
        self.btn_trans_sel.setEnabled(is_alive)


    def execute_translation(self, tasks):
        if not tasks:
            self.log("没有找到已下载且待翻译的章节。")
            return

        token = self._try_acquire_core_task("漫画页面翻译")
        if token is None:
            return

        base_dir = db.get_setting("base_dir")
        folder = self.manga_info['folder_name'] or self.manga_info['title_romaji']
        full_path = os.path.join(base_dir, folder)

        self.manga_info = db.get_manga_detail(self.manga_id)
        glossary = ""
        if 'glossary' in self.manga_info.keys():
            glossary = self.manga_info['glossary'] or ""
        
        worker_tasks =[]
        for cap in tasks:
            safe_title = clean_filename(cap[2])
            worker_tasks.append({"cap_id": cap[0], "base_dir": full_path, "ch_title": safe_title, "glossary": glossary})
            
        self.log(f"开始翻译任务，共计 {len(worker_tasks)} 个章节。")
        self.btn_cancel_trans.setEnabled(True)
        
        self.trans_thread = TranslationWorker(worker_tasks)
        self.trans_thread.log_signal.connect(self.log)
        self.trans_thread.chapter_finished.connect(self.on_chapter_translated)
        self.trans_thread.finished_all.connect(self.on_translation_done)
        self.trans_thread.finished.connect(lambda token=token: core_task_guard.release(token))
        self.trans_thread.start()

    def on_translation_done(self):
        self.btn_cancel_trans.setEnabled(False)
        self.log("所有翻译任务已结束。")
        self.load_chapters()
        core_task_guard.release(getattr(self, "_core_task_token", None))
        self._core_task_token = None

    def cancel_translation(self):
        if hasattr(self, 'trans_thread') and self.trans_thread.isRunning():
            self.trans_thread.cancel()
            self.log("正在停止翻译任务，请稍候...")
        self.btn_cancel_trans.setEnabled(False)

class AddMangaDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建漫画")
        self.setModal(True)
        self.resize(700, 500)
        self.results_data = [] # 存储所有源的搜索结果
        self.search_threads = [] # 存储当前的搜索线程列表
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # 【修改】移除源站选择下拉框，只保留搜索框
        input_layout = QHBoxLayout()
        self.input_keyword = QLineEdit()
        self.input_keyword.setPlaceholderText("输入日文名或罗马音搜索 (将同时搜索所有源站)...")
        self.input_keyword.returnPressed.connect(self.do_search) # 支持回车搜索
        
        btn_search = QPushButton("🔍 全站搜索")
        btn_search.setStyleSheet("background: #E3F2FD; font-weight: bold;")
        btn_search.clicked.connect(self.do_search)
        
        input_layout.addWidget(self.input_keyword, 1)
        input_layout.addWidget(btn_search)
        
        self.list_results = QListWidget()
        self.list_results.itemClicked.connect(self.on_result_click)
        # 增加提示标签
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("color: #666; font-style: italic;")
        
        preview_frame = QFrame()
        preview_frame.setEnabled(False) # 初始禁用，直到选择结果
        preview_layout = QFormLayout(preview_frame)
        self.preview_title = QLabel("-")
        self.preview_title.setWordWrap(True)
        self.edit_zh = QLineEdit()
        self.edit_jp = QLineEdit()
        self.edit_romaji = QLineEdit()
        
        preview_layout.addRow("标题", self.preview_title)
        preview_layout.addRow("中文名", self.edit_zh)
        preview_layout.addRow("日文名", self.edit_jp)
        preview_layout.addRow("罗马音", self.edit_romaji)
        
        btn_confirm = QPushButton("确认添加并获取章节")
        btn_confirm.clicked.connect(self.confirm_add)
        btn_confirm.setEnabled(False)
        self.btn_confirm = btn_confirm

        layout.addLayout(input_layout)
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.list_results, 1)
        layout.addWidget(preview_frame)
        layout.addWidget(btn_confirm)

    def do_search(self):
        kw = self.input_keyword.text().strip()
        if not kw: 
            QMessageBox.warning(self, "提示", "请输入搜索关键词")
            return
        
        # 中止之前未完成的搜索线程
        for t in getattr(self, 'search_threads',[]):
            if t.isRunning():
                t.cancel()
                t.wait(1000)
        self.search_threads =[]
        
        self.list_results.clear()
        self.results_data =[]
        self.preview_title.setText("-")
        self.btn_confirm.setEnabled(False)
        self.lbl_status.setText("正在并发搜索并获取章节统计，请稍候...")
        
        sources =["klmanga", "nicomanga", "rawkuma"]
        for source in sources:
            thread = WorkerThread("search", {"keyword": kw, "source": source})
            thread.finished_signal.connect(self.on_single_search_done)
            thread.error_signal.connect(lambda e, s=source: self.log_error(s, e))
            thread.start()
            self.search_threads.append(thread)

    def log_error(self, source, error_msg):
        # 简单的错误记录，不弹窗打断，只在状态栏或日志体现，因为是多线程
        print(f"[{source}] 搜索错误: {error_msg}")

    def closeEvent(self, event):
        is_searching = any(t.isRunning() for t in getattr(self, 'search_threads',[]))
        if is_searching:
            reply = QMessageBox.question(self, '确认退出', '后台正在进行全网搜索操作，是否中止任务并退出？',
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                for t in getattr(self, 'search_threads',[]):
                    if t.isRunning():
                        try: t.finished_signal.disconnect()
                        except: pass
                        t.cancel()
                super().closeEvent(event)
            else:
                event.ignore()
        else:
            super().closeEvent(event)

    def on_single_search_done(self, result):
        if result.get('type') == 'search_item':
            item = result.get('item')
            src = result.get('source')
            item['source_key'] = src
            self.results_data.append(item)
            
            # 显示格式: Title[source]1-56
            display_text = f"{item['title']}[{src}]{item.get('chapter_range', '')}"
            self.list_results.addItem(display_text)
            
        elif result.get('type') == 'search_done':
            src = result.get('source')
            self.lbl_status.setText(f"{src} 节点数据处理完成。")

    def on_result_click(self, item):
        row = self.list_results.row(item)
        if 0 <= row < len(self.results_data):
            data = self.results_data[row]
            self.show_preview(data)


    def show_preview(self, data):
        """填充预览区域"""
        self.preview_title.setText(f"{data['title']} ({data['source_key']})")
        self.edit_romaji.setText(data['title']) # 默认填入
        self.edit_zh.setText("")
        self.edit_jp.setText("")
        
        # 保存当前选中的 URL 和源到实例变量供 confirm_add 使用
        self.selected_url = data['url']
        self.selected_source = data['source_key']
        self.selected_chapters = data.get('chapters_raw') # 如果上面加载了，可以直接用
        
        # 启用确认按钮
        self.btn_confirm.setEnabled(True)
        # 启用预览框控件
        for child in self.findChildren(QWidget):
            if child.parent() == self.findChild(QFrame): # 粗略判断在 preview_frame 内
                child.setEnabled(True)

    def confirm_add(self):
        # 新增：强制中止后台搜索线程，释放资源
        for t in getattr(self, 'search_threads',[]):
            if t.isRunning():
                t.cancel()
                t.terminate()

        if not hasattr(self, 'selected_url'):
            return
            
        romaji = self.edit_romaji.text() or self.preview_title.text().split(' [')[0]
        folder_name = clean_filename(romaji)
        
        # 1. 先写入 manga 表
        manga_id = db.add_manga({
            "zh": self.edit_zh.text(),
            "jp": self.edit_jp.text(),
            "romaji": romaji,
            "cover": "",
            "folder": folder_name,
            "url": self.selected_url
        })
        
        self.new_manga_id = manga_id
        self.new_folder = folder_name

        self.btn_confirm.setEnabled(False)
        self.lbl_status.setText("正在保存章节数据到数据库...")
        
        # 2. 保存章节
        if hasattr(self, 'selected_chapters') and self.selected_chapters:
            db.save_chapters(manga_id, self.selected_chapters, self.selected_source)
            QMessageBox.information(self, "成功", f"漫画已添加！\n来源：{self.selected_source}\n章节数：{len(self.selected_chapters)}")
            self.accept()
        else:
            thread = WorkerThread("get_chapters", {"url": self.selected_url, "source": self.selected_source})
            thread.finished_signal.connect(lambda r: self.on_final_save_done(manga_id, r))
            thread.start()

    def on_final_save_done(self, manga_id, result):
        if result['type'] == 'chapters':
            db.save_chapters(manga_id, result['data'], result['source'])
            QMessageBox.information(self, "成功", "漫画已添加！")
            self.accept()
        else:
            QMessageBox.warning(self, "失败", "获取章节失败，添加取消。")
            # 这里可能需要回滚 manga 记录，简单起见暂不回滚，用户可手动删除
            self.reject()
