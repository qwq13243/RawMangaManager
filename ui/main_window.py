import os
import time
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QPushButton, QScrollArea, QGridLayout, QSizePolicy, QDialog, QMessageBox)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QIcon, QCursor, QPixmap

from core.database import db
from core.workers import WorkerThread, BatchUpdateWorker, ServerCheckWorker
from core.task_guard import core_task_guard
from core.utils import generate_white_cover
from ui.widgets import DraggableCard, MangaGridWidget
from ui.dialogs import SettingsDialog, AddMangaDialog, DetailDialog
from saber_api_client import TranslationWorker

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("漫画管理器")  # 添加窗口标题
        self.resize(1200, 800)  # 设置默认窗口大小
        self._core_task_token = None
        self._core_task_name = None
        self._core_task_parts = set()
        self._debug_ui = os.environ.get("MANGA_UI_DEBUG", "0") == "1"
        self.init_ui()
        self.refresh_grid()  # 初始化时加载漫画网格
    
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        
        self.is_user_dragging = False # 初始化拖拽状态标志
        
        layout = QVBoxLayout(central)
        layout.setContentsMargins(15, 15, 15, 15)

        # --- 顶部：标题与操作按钮 ---
        header = QHBoxLayout()
        lbl_title = QLabel("📚 我的漫画库")
        lbl_title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        
        btn_catch_up = QPushButton("⚡ 一键追更")
        btn_catch_up.setStyleSheet("background: #FFF3E0; border-color: #FFCC80; font-weight: bold;")
        btn_catch_up.clicked.connect(self.run_catch_up)
        
        self.btn_check_update = QPushButton("🔄 检查更新")
        self.btn_check_update.setStyleSheet("background: #E8F5E9; border-color: #A5D6A7; font-weight: bold;")
        self.btn_check_update.clicked.connect(self.run_check_update)

        # 新增停止按钮
        self.btn_stop = QPushButton("⏹ 停止任务")
        self.btn_stop.setStyleSheet("background: #FFEBEE; border-color: #EF9A9A; font-weight: bold; color: #C62828;")
        self.btn_stop.clicked.connect(self.stop_all_tasks)
        self.btn_stop.setEnabled(False)
        
        btn_new = QPushButton("➕ 新建漫画")
        btn_new.setStyleSheet("background: #E3F2FD; border-color: #90CAF9; font-weight: bold;")
        btn_new.clicked.connect(self.open_add)
        
        btn_settings = QPushButton("⚙️ 设置")
        btn_settings.clicked.connect(self.open_settings)
        
        header.addWidget(lbl_title)
        header.addStretch() # 标题和按钮之间增加弹簧
        header.addWidget(btn_catch_up)
        header.addWidget(self.btn_check_update)
        header.addWidget(self.btn_stop)
        header.addWidget(btn_new)
        header.addWidget(btn_settings)
        
        # --- 中部：滚动区域 ---
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        self.container = MangaGridWidget()
        # 连接信号
        self.container.orderChanged.connect(self.on_drag_reorder)
        
        self.grid_layout = QGridLayout(self.container)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        # 设置更紧凑的间距
        self.grid_layout.setSpacing(15) 
        self.scroll.setWidget(self.container)
        
        # --- 底部：信息栏 ---
        footer_layout = QVBoxLayout()
        footer_layout.setSpacing(2)
        
        self.lbl_dl_status = QLabel("下载情况: 空闲")
        self.lbl_dl_status.setStyleSheet("color: #00509E; font-weight: bold; padding: 2px;")
        # Allow expanding
        self.lbl_dl_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        
        self.lbl_trans_status = QLabel("翻译情况: 空闲")
        self.lbl_trans_status.setStyleSheet("color: #00509E; font-weight: bold; padding: 2px;")
        # Allow expanding
        self.lbl_trans_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        
        # 新增：服务器状态指示灯
        # 移除服务器状态指示灯逻辑
        
        footer_layout.addWidget(self.lbl_dl_status)
        h_trans = QHBoxLayout()
        h_trans.addWidget(self.lbl_trans_status)
        h_trans.addStretch()
        footer_layout.addLayout(h_trans)

        layout.addLayout(header)
        layout.addWidget(self.scroll, 1) # 让中间区域占据主要空间
        layout.addLayout(footer_layout)
        
        # 移除服务器状态检测定时器
        # self.server_check_timer = QTimer(self)
        # self.server_check_timer.timeout.connect(self.run_server_check)
        # self.server_check_timer.start(5000) # 每5秒检测一次

    def run_server_check(self):
        # 移除服务器检测逻辑
        pass

    def update_server_status(self, is_alive):
        # 移除服务器状态更新逻辑
        pass

    def resizeEvent(self, event):
        # 窗口大小改变时重新排布网格
        self.reflow_grid()
        super().resizeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # 确保窗口显示后立即重新排布，适配初始大小
        QTimer.singleShot(0, self.reflow_grid)

    def request_refresh(self):
        if not hasattr(self, '_refresh_timer'):
            self._refresh_timer = QTimer(self)
            self._refresh_timer.setSingleShot(True)
            self._refresh_timer.timeout.connect(self.refresh_grid)
        # 如果 500 毫秒内有新的刷新请求，计时器会重置，避免高频全量重绘
        self._refresh_timer.start(500)

    def _dbg(self, msg: str) -> None:
        if self._debug_ui:
            print(msg, flush=True)

    def _on_card_drag_started(self, mid):
        self.is_user_dragging = True
        self._dbg(f"[UI][{time.strftime('%H:%M:%S')}] dragStarted mid={mid}")

    def _on_card_drag_finished(self, mid):
        self.is_user_dragging = False
        self._dbg(f"[UI][{time.strftime('%H:%M:%S')}] dragFinished mid={mid}")
        self.request_refresh()

    def _acquire_core_task(self, task_name):
        token, busy = core_task_guard.try_acquire(task_name)
        if token is None:
            QMessageBox.information(self, "提示", f"当前有任务正在执行：{busy}")
            return None
        self._core_task_token = token
        self._core_task_name = task_name
        return token

    def _release_core_task(self):
        if core_task_guard.release(getattr(self, "_core_task_token", None)):
            self._core_task_token = None
            self._core_task_name = None
            self._core_task_parts = set()

    def _mark_core_task_part_done(self, part_name):
        if getattr(self, "_core_task_name", None) != "一键追更":
            return
        if part_name in self._core_task_parts:
            self._core_task_parts.remove(part_name)
        if not self._core_task_parts:
            self._release_core_task()

    def stop_all_tasks(self):
        # 停止更新检查
        if hasattr(self, 'update_worker') and self.update_worker.isRunning():
            try: self.update_worker.finished_signal.disconnect()
            except: pass
            self.update_worker.cancel()
            self.btn_check_update.setEnabled(True)
            self.lbl_dl_status.setText("下载情况: 检查更新已强制停止")

        # 停止全局下载
        if hasattr(self, 'catchup_dl_worker') and self.catchup_dl_worker.isRunning():
            try: self.catchup_dl_worker.finished_signal.disconnect()
            except: pass
            self.catchup_dl_worker.cancel()
            self.lbl_dl_status.setText("下载情况: 下载任务已强制停止")

        # 清空翻译队列
        if hasattr(self, 'pending_trans_tasks'):
            self.pending_trans_tasks.clear()

        # 停止全局翻译
        if hasattr(self, 'catchup_trans_worker') and self.catchup_trans_worker.isRunning():
            try: self.catchup_trans_worker.finished_all.disconnect()
            except: pass
            try: self.catchup_trans_worker.log_signal.disconnect()
            except: pass
            self.catchup_trans_worker.cancel()
            self.lbl_trans_status.setText("翻译情况: 翻译任务已强制停止")

        self.btn_stop.setEnabled(False)

    def show_context_menu(self, pos, mid):
        from PySide6.QtWidgets import QMenu
        self._dbg(f"[UI][{time.strftime('%H:%M:%S')}] contextMenu mid={mid} pos={pos.x()},{pos.y()}")
        menu = QMenu(self)
        
        menu.addAction(QIcon(), "⏪ 置顶", lambda: self.move_manga(mid, 'first'))
        menu.addAction(QIcon(), "⬅️ 前移", lambda: self.move_manga(mid, 'prev'))
        menu.addAction(QIcon(), "➡️ 后移", lambda: self.move_manga(mid, 'next'))
        menu.addAction(QIcon(), "⏩ 置底", lambda: self.move_manga(mid, 'last'))
        
        menu.exec(QCursor.pos())

    def on_drag_reorder(self, source_id, pos):
        self._dbg(f"[UI][{time.strftime('%H:%M:%S')}] dragDrop source_id={source_id} pos={pos.x()},{pos.y()}")
        # 1. 获取当前列表
        mangas = [list(m) for m in db.get_all_manga()]
        
        # 2. 找到源的索引
        source_idx = -1
        for i, m in enumerate(mangas):
            if m[0] == source_id:
                source_idx = i
                break
        
        if source_idx == -1: return
        
        # 3. 计算目标索引 (基于坐标)
        # 注意：需要与 reflow_grid 的逻辑保持一致
        width = self.scroll.viewport().width() - 30
        card_width = 150
        spacing = 15
        col_count = max(1, (width + spacing) // (card_width + spacing))
        
        # 估算行高和列宽 (含间距)
        card_height = 245
        row_height = card_height + spacing
        col_width = card_width + spacing
        
        # 考虑 grid layout 的默认 margin (通常为9-11，这里忽略或粗略处理)
        # 如果 pos 在 margin 内，整除结果为 0，符合预期
        target_row = max(0, pos.y() // row_height)
        target_col = max(0, pos.x() // col_width)
        
        # 限制列索引不超过当前列数
        if target_col >= col_count:
            target_col = col_count - 1
            
        target_index = target_row * col_count + target_col
        
        # 限制最大索引
        if target_index >= len(mangas):
            target_index = len(mangas) - 1
        
        if source_idx == target_index: return
        
        # 4. 移动元素
        item = mangas.pop(source_idx)
        mangas.insert(target_index, item)
        print(f"[UI][{time.strftime('%H:%M:%S')}] reorder source_idx={source_idx} target_index={target_index} col_count={col_count}", flush=True)
        
        # 5. 更新数据库排序
        for i, m in enumerate(mangas):
            db.update_manga_order(m[0], i)
            
        self.refresh_grid()

    def move_manga(self, manga_id, direction):
        self._dbg(f"[UI][{time.strftime('%H:%M:%S')}] move_manga id={manga_id} dir={direction}")
        # 1. 获取当前列表（已排序）
        # 注意：fetchall返回的是 tuple list，转为 list of list 以便修改
        mangas = [list(m) for m in db.get_all_manga()]
        
        # 2. 找到当前漫画的索引
        idx = -1
        for i, m in enumerate(mangas):
            if m[0] == manga_id:
                idx = i
                break
        
        if idx == -1: return

        # 3. 计算目标索引并执行移动
        current_item = mangas.pop(idx)
        
        if direction == 'first':
            mangas.insert(0, current_item)
        elif direction == 'last':
            mangas.append(current_item)
        elif direction == 'prev':
            new_idx = max(0, idx - 1)
            mangas.insert(new_idx, current_item)
        elif direction == 'next':
            new_idx = min(len(mangas), idx + 1)
            mangas.insert(new_idx, current_item)

        # 4. 更新所有漫画的 sort_order
        for i, m in enumerate(mangas):
            db.update_manga_order(m[0], i)
            
        self.refresh_grid()

    def refresh_grid(self):
        # 如果用户正在拖拽，暂停刷新以防崩溃或冲突
        if getattr(self, 'is_user_dragging', False):
            self._dbg(f"[UI][{time.strftime('%H:%M:%S')}] refresh_grid skipped (is_user_dragging=True)")
            return

        # 清空布局中的控件（但不删除对象，因为我们要复用 self.current_cards? 不，这里是全量刷新，应该删除旧的）
        # 如果是全量刷新，我们需要销毁旧卡片
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget(): 
                item.widget().deleteLater()
        
        # 重置卡片列表
        self.current_cards = []
        
        mangas = db.get_all_manga()
        try:
            ids = [m[0] for m in mangas]
            self._dbg(f"[UI][{time.strftime('%H:%M:%S')}] refresh_grid manga_ids={ids}")
        except Exception as e:
            self._dbg(f"[UI][{time.strftime('%H:%M:%S')}] refresh_grid manga_ids error={e}")
        
        target_width = 130
        target_height = int(target_width * 1.414) 
        
        for idx, m_data in enumerate(mangas):
            # 解包所有字段，确保兼容新旧结构
            m_id = m_data[0]
            zh = m_data[1]
            jp = m_data[2]
            romaji = m_data[3]
            cover = m_data[4]
            # is_following = m_data[5] if len(m_data) > 5 else 1
            # sort_order = m_data[6] if len(m_data) > 6 else 0
            
            title = zh if zh else (jp if jp else romaji)
            
            chapters = db.get_chapters(m_id)
            border_color = "#EEE"
            if chapters:
                latest = chapters[0]
                is_dl = latest[4]
                is_trans = latest[7]
                
                # 严格判定边界颜色
                if is_dl == 0 and is_trans == 0:
                    border_color = "#FFFF00"
                elif is_dl == 1 and is_trans == 0:
                    border_color = "#00FFFF"
            
            # 计算阅读进度
            total_chapters = len(chapters)
            
            latest_num_display = "-"
            read_num_display = "-"
            is_caught_up = False
            
            if total_chapters > 0:
                latest_chap = chapters[0]
                # index 9 is chapter_num (刚刚在 database.py 中新增的)
                latest_num = latest_chap[9] 
                
                # 寻找已读的最新章节 (chapters 按 num 降序排列，找到第一个已读即为最大序号)
                # read_status == 2 (已读完)
                read_chap = next((c for c in chapters if c[8] == 2), None)
                read_num = read_chap[9] if read_chap else 0
                
                # 格式化显示
                def fmt(n): return str(int(n)) if float(n).is_integer() else str(n)
                
                latest_num_display = fmt(latest_num)
                read_num_display = fmt(read_num) if read_chap else "-"
                
                # 判断是否追上最新
                if read_chap and abs(read_num - latest_num) < 0.001:
                    is_caught_up = True
            
            # 状态指示灯颜色
            status_indicator_color = "#2196F3" # 默认蓝色（未读完）
            status_indicator_tooltip = f"已读: {read_num_display} / 最新: {latest_num_display}"
            
            if is_caught_up:
                status_indicator_color = "#4CAF50" # 绿色（已追上最新）
                status_indicator_tooltip = "已读完最新章节"
            elif total_chapters == 0:
                status_indicator_color = "#9E9E9E" # 灰色（无章节）
                status_indicator_tooltip = "暂无章节"

            card = DraggableCard(m_id)
            # 连接拖拽信号，管理刷新锁
            card.dragStarted.connect(lambda mid=m_id: self._on_card_drag_started(mid))
            card.dragFinished.connect(lambda mid=m_id: self._on_card_drag_finished(mid))
            
            # 【修复】使用 setFixedSize 强制锁死尺寸，防止按钮内部布局被压扁
            card.setFixedSize(150, 245)
            # 添加右键菜单支持
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            # 使用默认参数捕获循环变量
            card.customContextMenuRequested.connect(lambda pos, mid=m_id: self.show_context_menu(pos, mid))

            card.setStyleSheet(f"""
                DraggableCard {{
                    background: #FFF; 
                    border: 2px solid {border_color}; 
                    border-radius: 8px; 
                    padding: 5px; 
                    text-align: center;
                }}
            """)
            
            v_layout = QVBoxLayout(card)
            v_layout.setContentsMargins(5, 5, 5, 5) # 减小边距以容纳更多内容
            v_layout.setSpacing(4)
            
            lbl_img = QLabel()
            lbl_img.setAlignment(Qt.AlignCenter)
            lbl_img.setFixedSize(target_width, target_height)
            
            if cover and os.path.exists(cover):
                pixmap = QPixmap(cover)
            else:
                temp = os.path.join(os.getcwd(), "temp.png")
                if not os.path.exists(temp): generate_white_cover(temp)
                pixmap = QPixmap(temp)
            
            scaled_pixmap = pixmap.scaled(target_width, target_height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            lbl_img.setPixmap(scaled_pixmap)
            
            # --- 标题栏容器 ---
            title_container = QWidget()
            title_layout = QHBoxLayout(title_container)
            title_layout.setContentsMargins(0, 0, 0, 0)
            title_layout.setSpacing(2)
            
            # 左侧：进度显示
            lbl_progress = QLabel(f"{read_num_display}/{latest_num_display}")
            lbl_progress.setStyleSheet("color: #666; font-size: 10px; font-weight: bold;")
            lbl_progress.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            
            # 中间：标题
            lbl_title = QLabel(title)
            metrics = lbl_title.fontMetrics()
            # 动态计算标题可用宽度：总宽(130) - 进度宽(约30) - 指示灯宽(10) - 间距
            available_width = 130 - 35 - 15 
            elided_title = metrics.elidedText(title, Qt.ElideRight, available_width)
            lbl_title.setText(elided_title)
            lbl_title.setWordWrap(False)
            lbl_title.setAlignment(Qt.AlignCenter)
            lbl_title.setStyleSheet("font-weight: bold; color: #333;")
            
            # 右侧：状态指示灯
            lbl_status = QLabel()
            lbl_status.setFixedSize(8, 8)
            lbl_status.setStyleSheet(f"background-color: {status_indicator_color}; border-radius: 4px;")
            lbl_status.setToolTip(status_indicator_tooltip)
            
            title_layout.addWidget(lbl_progress)
            title_layout.addWidget(lbl_title, 1) # 1 = stretch factor, let title take remaining space
            title_layout.addWidget(lbl_status)
            
            v_layout.addWidget(lbl_img)
            v_layout.addWidget(title_container)

            for w in card.findChildren(QWidget):
                if w is not card:
                    w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            
            card.clicked.connect(lambda checked, mid=m_id: self.open_detail(mid))
            
            # 将卡片加入列表，而不是直接计算坐标加入布局
            self.current_cards.append(card)
            
        # 初始排布
        self.reflow_grid()

    def reflow_grid(self):
        """
        根据当前窗口宽度，重新计算列数并排布卡片
        """
        if not hasattr(self, 'current_cards') or not self.current_cards:
            return
            
        # 获取容器可视宽度（减去滚动条可能的宽度和边距）
        # 注意：self.container 在 ScrollArea 中，其宽度可能会被拉伸
        # 我们应该使用 scroll area 的 viewport 宽度作为基准，或者 container 的当前宽度
        width = self.scroll.viewport().width() - 30 # 减去 layout margins
        
        card_width = 150
        spacing = 15 # 与 layout.setSpacing 一致
        
        # 计算每行能放多少个
        # width = cols * card_width + (cols - 1) * spacing
        # width + spacing = cols * (card_width + spacing)
        col_count = max(1, (width + spacing) // (card_width + spacing))
        
        # 重新添加到布局
        # 注意：QGridLayout addWidget 会自动移动已存在的 widget
        for idx, card in enumerate(self.current_cards):
            row = idx // col_count
            col = idx % col_count
            self.grid_layout.addWidget(card, row, col)

    def _on_check_update_progress(self, msg: str) -> None:
        if not hasattr(self, "_check_update_last_done"):
            self._check_update_last_done = ""
            self._check_update_current = ""
            self.lbl_dl_status.setWordWrap(True)

        if msg.startswith("正在检查:"):
            self._check_update_current = msg
        elif msg.startswith("检查完成:"):
            self._check_update_last_done = msg
        else:
            self._check_update_current = ""
            self._check_update_last_done = ""
            self.lbl_dl_status.setWordWrap(False)
            self.lbl_dl_status.setText("下载情况: 检查更新完成")
            return

        lines = []
        if self._check_update_current:
            lines.append(self._check_update_current)
        if self._check_update_last_done:
            lines.append(self._check_update_last_done)

        self.lbl_dl_status.setText("更新情况: " + "\n".join(lines))

    def run_check_update(self):
        if not self._acquire_core_task("检查更新"):
            return
        self.btn_check_update.setEnabled(False)
        self.btn_stop.setEnabled(True) 
        self.update_worker = BatchUpdateWorker()
        self._check_update_last_done = ""
        self._check_update_current = ""
        self.lbl_dl_status.setWordWrap(True)
        self.update_worker.progress_signal.connect(self._on_check_update_progress)
        
        # 【修改这里】连接到带防抖的 request_refresh
        self.update_worker.refresh_signal.connect(self.request_refresh) 
        
        self.update_worker.finished_signal.connect(self.on_check_update_done)
        self.update_worker.finished.connect(self._release_core_task)
        self.update_worker.start()

    def on_check_update_done(self):
        self.btn_check_update.setEnabled(True)
        self.btn_stop.setEnabled(False) # 禁用停止按钮
        self._on_check_update_progress("检查更新完成")
        self.refresh_grid()
        self._release_core_task()

    def run_catch_up(self):
        mangas = db.get_all_manga()
        dl_tasks =[]
        # 初始化翻译队列
        self.pending_trans_tasks = getattr(self, 'pending_trans_tasks', [])
        
        for m in mangas:
            # is_following check
            if len(m) > 5 and m[5] == 0:
                continue

            m_id = m[0]
            detail = db.get_manga_detail(m_id)
            base_dir = db.get_setting("base_dir")
            folder = detail['folder_name'] or detail['title_romaji']
            full_path = os.path.join(base_dir, folder)
            chapters = db.get_chapters(m_id)
            manga_title = detail['title_zh'] or detail['title_jp'] or detail['title_romaji']
            glossary = ""
            try:
                if 'glossary' in detail.keys():
                    glossary = detail['glossary'] or ""
            except Exception:
                glossary = ""
            
            # 收集待下载任务 (逆序：旧的先下)
            temp_dl = []
            for cap in chapters:
                if cap[4] == 1: break
                temp_dl.append((cap, full_path, manga_title))
            temp_dl.reverse()
            dl_tasks.extend(temp_dl)
            
            # 收集【已经下载，但尚未翻译】的存量章节 (直接加入翻译队列)
            from scrapers import clean_filename
            temp_trans =[]
            for cap in chapters:
                if cap[7] == 1: break
                if cap[4] == 1: # 核心逻辑：只准将已下载完毕的送入队列
                    temp_trans.append({
                        "cap_id": cap[0], 
                        "base_dir": full_path, 
                        "ch_title": clean_filename(cap[2]), 
                        "manga_title": manga_title,
                        "glossary": glossary
                    })
            temp_trans.reverse()
            self.pending_trans_tasks.extend(temp_trans)
                
        if not dl_tasks and not self.pending_trans_tasks:
            self.lbl_dl_status.setText("下载情况: 无需追更")
            self.lbl_trans_status.setText("翻译情况: 无需追更")
            return

        if not self._acquire_core_task("一键追更"):
            return
        self._core_task_parts = set()
        if dl_tasks:
            self._core_task_parts.add("dl")
        if self.pending_trans_tasks:
            self._core_task_parts.add("trans")
            
        self.btn_stop.setEnabled(True) # 启用停止按钮
            
        if dl_tasks:
            self.catchup_dl_worker = WorkerThread("download", {"chapters": dl_tasks})
            self.catchup_dl_worker.progress_signal.connect(lambda msg: self.lbl_dl_status.setText(f"下载情况: {msg}"))
            self.catchup_dl_worker.finished_signal.connect(self.on_catchup_dl_event)
            self.catchup_dl_worker.finished.connect(lambda: self._mark_core_task_part_done("dl"))
            self.catchup_dl_worker.start()
        else:
            self.lbl_dl_status.setText("下载情况: 已是最新")
            self._mark_core_task_part_done("dl")
            
        self.start_next_trans_batch()
            
        # 立即启动翻译队列
        self.start_next_trans_batch()

    def start_next_trans_batch(self):
        if hasattr(self, 'catchup_trans_worker') and self.catchup_trans_worker.isRunning():
            return
            
        if not getattr(self, 'pending_trans_tasks',[]):
            dl_running = hasattr(self, 'catchup_dl_worker') and self.catchup_dl_worker.isRunning()
            if not dl_running:
                self.lbl_trans_status.setText("翻译情况: 所有追更翻译完成")
                self.btn_stop.setEnabled(False) # 任务全空，禁用停止按钮
            self._mark_core_task_part_done("trans")
            return
            
        tasks_to_run = self.pending_trans_tasks[:]
        self.pending_trans_tasks.clear()
        
        self.catchup_trans_worker = TranslationWorker(tasks_to_run)
        self.catchup_trans_worker.log_signal.connect(lambda msg: self.lbl_trans_status.setText(f"翻译情况: {msg}"))
        self.catchup_trans_worker.chapter_finished.connect(self.on_catchup_trans_progress)
        self.catchup_trans_worker.finished_all.connect(self.start_next_trans_batch)
        self.catchup_trans_worker.finished.connect(lambda: self._mark_core_task_part_done("trans") if not getattr(self, "pending_trans_tasks", []) and not (hasattr(self, "catchup_trans_worker") and self.catchup_trans_worker.isRunning()) else None)
        self.catchup_trans_worker.start()

    def on_catchup_dl_event(self, data):
        if data.get("type") == "progress":
            self.refresh_grid()
            
            # 自动衔接翻译逻辑
            cap_id = data.get("id")
            if cap_id and self._core_task_name == "一键追更":
                try:
                    cap = db.get_chapter_by_id(cap_id)
                    if cap and cap['is_translated'] == 0:
                        manga_id = cap['manga_id']
                        manga = db.get_manga_detail(manga_id)
                        if manga:
                            base_dir = db.get_setting("base_dir")
                            folder = manga['folder_name'] or manga['title_romaji']
                            full_path = os.path.join(base_dir, folder)
                            manga_title = manga['title_zh'] or manga['title_jp'] or manga['title_romaji']
                            glossary = ""
                            try:
                                if 'glossary' in manga.keys():
                                    glossary = manga['glossary'] or ""
                            except Exception:
                                glossary = ""
                            
                            from scrapers import clean_filename
                            
                            task = {
                                "cap_id": cap['id'], 
                                "base_dir": full_path, 
                                "ch_title": clean_filename(cap['title_original']), 
                                "manga_title": manga_title,
                                "glossary": glossary
                            }
                            
                            if not hasattr(self, 'pending_trans_tasks'):
                                self.pending_trans_tasks = []
                            self.pending_trans_tasks.append(task)
                            
                            # 标记翻译任务部分为活跃
                            self._core_task_parts.add("trans")
                            
                            # 尝试启动或追加到翻译队列
                            self.start_next_trans_batch()
                except Exception as e:
                    print(f"自动追更翻译触发失败: {e}")
                
        elif data.get("type") == "done":
            self.lbl_dl_status.setText("下载情况: 所有追更下载完成")
            self.refresh_grid()

    def on_catchup_trans_progress(self, cid):
        db.mark_chapter_translated(cid)
        self.refresh_grid()

    def open_add(self):
        dlg = AddMangaDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh_grid()
            # 从外部触发完全独立的封面获取逻辑
            if hasattr(dlg, 'new_manga_id'):
                self.start_cover_download(dlg.new_manga_id, dlg.selected_url, dlg.selected_source, dlg.new_folder)

    def open_settings(self):
        SettingsDialog(self).exec()

    def open_detail(self, manga_id):
        if not hasattr(self, 'active_dlgs'):
            self.active_dlgs =[]
        dlg = DetailDialog(manga_id, self)
        self.active_dlgs.append(dlg) # 防止被Python垃圾回收
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.finished.connect(lambda: self.active_dlgs.remove(dlg) if dlg in self.active_dlgs else None)
        dlg.finished.connect(self.refresh_grid)
        dlg.show()  # 替代原先的 dlg.exec()

    def start_cover_download(self, manga_id, url, source, folder_name):
        base_dir = db.get_setting("base_dir")
        save_dir = os.path.join(base_dir, folder_name)
        
        self.cover_thread = WorkerThread("download_cover", {
            "source": source, 
            "url": url, 
            "save_dir": save_dir, 
            "manga_id": manga_id
        })
        self.cover_thread.finished_signal.connect(self.on_cover_downloaded)
        self.cover_thread.start()

    def on_cover_downloaded(self, result):
        if result.get("type") == "cover_downloaded":
            self.refresh_grid()
