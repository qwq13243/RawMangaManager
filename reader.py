import sys
import re
import os
import glob
import json
from PySide6.QtWidgets import QWidget, QVBoxLayout, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QApplication, QComboBox, QHBoxLayout, QLabel, QSpacerItem, QSizePolicy, QPushButton
from PySide6.QtGui import QPixmap, Qt, QWheelEvent, QMouseEvent, QKeyEvent, QPainter
from PySide6.QtCore import Signal, QPoint, QTimer, QEvent, QRectF

from core.database import db

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

class ReaderView(QGraphicsView):
    click_left = Signal()
    click_right = Signal()
    click_middle = Signal()

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        # 增加平滑缩放和抗锯齿渲染
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.click_pos = QPoint()

    def wheelEvent(self, event: QWheelEvent):
        if event.angleDelta().y() > 0:
            self.scale(1.15, 1.15)
        else:
            self.scale(1 / 1.15, 1 / 1.15)

    def mousePressEvent(self, event: QMouseEvent):
        self.click_pos = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        super().mouseReleaseEvent(event)
        if (event.pos() - self.click_pos).manhattanLength() < 5:
            if event.button() == Qt.LeftButton:
                self.click_left.emit()
            elif event.button() == Qt.RightButton:
                self.click_right.emit()
            elif event.button() == Qt.MiddleButton:
                self.click_middle.emit()

class MangaReader(QWidget):
    finished_reading = Signal()

    def __init__(self, images_or_dir, parent=None, raw_path=None, trans_path=None, chapter_title=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.Window) # 确保作为独立窗口浮动

        self._window_state_restored = False
        self._save_geometry_timer = QTimer(self)
        self._save_geometry_timer.setSingleShot(True)
        self._save_geometry_timer.timeout.connect(self._save_window_state)
        
        # 初始化路径
        self.raw_path = raw_path
        self.trans_path = trans_path
        self.chapter_title = chapter_title or ""
        
        # 确定当前模式和图片列表
        if isinstance(images_or_dir, list):
            self.images = sorted(images_or_dir, key=natural_sort_key)
            # 尝试推断当前是生肉还是熟肉
            if self.images and "Trans_" in self.images[0]:
                self.current_mode = "trans"
            else:
                self.current_mode = "raw"
        else:
            # 如果传入的是目录路径（兼容性处理）
            self.images = self._load_images_from_dir(images_or_dir)
            if self.trans_path and os.path.normpath(images_or_dir) == os.path.normpath(self.trans_path):
                self.current_mode = "trans"
            else:
                self.current_mode = "raw"
                
        self.current_index = 0
        self.merge_mode = None
        self.merge_gap = 0
        self._update_window_title()
        
        # UI 初始化
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # [新增] 顶部工具栏
        self.top_bar = QWidget()
        self.top_bar.setStyleSheet("background-color: #f0f0f0; border-bottom: 1px solid #ccc;")
        self.top_bar.setFixedHeight(40)
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(10, 5, 10, 5)
        
        self.lbl_page = QLabel("页码:")
        self.combo_page = QComboBox()
        self.combo_page.setFocusPolicy(Qt.NoFocus) # 防止抢占键盘焦点
        self.combo_page.currentIndexChanged.connect(self.on_page_selected)

        self.btn_merge_prev = QPushButton("与上一页拼页")
        self.btn_merge_prev.setFocusPolicy(Qt.NoFocus)
        self.btn_merge_prev.clicked.connect(self.merge_with_prev)

        self.btn_merge_next = QPushButton("与下一页拼页")
        self.btn_merge_next.setFocusPolicy(Qt.NoFocus)
        self.btn_merge_next.clicked.connect(self.merge_with_next)

        self.btn_merge_cancel = QPushButton("取消拼页")
        self.btn_merge_cancel.setFocusPolicy(Qt.NoFocus)
        self.btn_merge_cancel.clicked.connect(self.cancel_merge)
        
        self.lbl_mode = QLabel(f"当前: {'熟肉' if self.current_mode == 'trans' else '生肉'}")
        self.lbl_mode.setStyleSheet("font-weight: bold; color: #333;")
        
        top_layout.addWidget(self.lbl_page)
        top_layout.addWidget(self.combo_page)
        top_layout.addWidget(self.btn_merge_prev)
        top_layout.addWidget(self.btn_merge_next)
        top_layout.addWidget(self.btn_merge_cancel)
        top_layout.addStretch()
        top_layout.addWidget(self.lbl_mode)
        
        layout.addWidget(self.top_bar)
        
        self.scene = QGraphicsScene(self)
        self.view = ReaderView(self.scene, self)
        self.view.click_left.connect(self.next_page)
        self.view.click_right.connect(self.prev_page)
        self.view.click_middle.connect(self.toggle_mode) # [新增] 中键切换
        self.view.installEventFilter(self)
        
        layout.addWidget(self.view)
        
        self.pixmap_item_left = QGraphicsPixmapItem()
        self.pixmap_item_left.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.pixmap_item_left)

        self.pixmap_item_right = QGraphicsPixmapItem()
        self.pixmap_item_right.setTransformationMode(Qt.SmoothTransformation)
        self.pixmap_item_right.setVisible(False)
        self.scene.addItem(self.pixmap_item_right)
        
        self._restore_window_state()

        # 初始化下拉框
        self.update_page_combo()
        self.load_image()

    def eventFilter(self, obj, event):
        if obj is self.view and event.type() == QEvent.KeyPress:
            self.keyPressEvent(event)
            return True
        return super().eventFilter(obj, event)

    def _update_window_title(self):
        total = len(self.images)
        page_part = f"{self.current_index + 1} / {total}" if total else "-"
        mode_part = "熟肉" if self.current_mode == "trans" else "生肉"
        if self.chapter_title:
            title = f"{self.chapter_title} - {page_part} ({mode_part})"
        else:
            title = f"{page_part} ({mode_part})"
        self.setWindowTitle(title)

    def _current_content_rect(self) -> QRectF:
        rect = self.pixmap_item_left.sceneBoundingRect()
        if self.pixmap_item_right.isVisible():
            rect = rect.united(self.pixmap_item_right.sceneBoundingRect())
        return rect

    def _restore_window_state(self):
        raw = db.get_setting("reader_window_geometry", "")
        if not raw:
            return
        try:
            data = json.loads(raw)
            x = int(data.get("x"))
            y = int(data.get("y"))
            w = int(data.get("w"))
            h = int(data.get("h"))
        except Exception:
            return

        screen = QApplication.primaryScreen().availableGeometry()
        min_w, min_h = 200, 200
        w = max(min_w, min(w, screen.width()))
        h = max(min_h, min(h, screen.height()))

        x_min = screen.left()
        y_min = screen.top()
        x_max = max(x_min, screen.right() - w + 1)
        y_max = max(y_min, screen.bottom() - h + 1)
        x = max(x_min, min(x, x_max))
        y = max(y_min, min(y, y_max))

        self.setGeometry(x, y, w, h)
        self._window_state_restored = True

    def _schedule_save_window_state(self):
        self._save_geometry_timer.start(200)

    def _save_window_state(self):
        geo = self.geometry()
        data = {"x": int(geo.x()), "y": int(geo.y()), "w": int(geo.width()), "h": int(geo.height())}
        db.set_setting("reader_window_geometry", json.dumps(data, ensure_ascii=False))

    def _load_images_from_dir(self, directory):
        if not directory or not os.path.exists(directory):
            return []
        valid_exts = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff')
        images = []
        try:
            # 避免使用 glob.glob，改用 os.listdir
            files = os.listdir(directory)
            for f in files:
                full = os.path.join(directory, f)
                if os.path.isfile(full):
                    ext = os.path.splitext(f)[1].lower()
                    if ext in valid_exts:
                        if "Trans_" not in os.path.basename(f) and "Trans_" not in f.replace(directory, ""):
                            images.append(full)
                        elif "Trans_" in directory: # 如果本身就是翻译目录，则保留
                            images.append(full)
        except Exception as e:
            print(f"Error loading images: {e}")
        return sorted(images, key=natural_sort_key)

    def update_page_combo(self):
        self.combo_page.blockSignals(True)
        self.combo_page.clear()
        total = len(self.images)
        items = [f"第 {i+1} 页" for i in range(total)]
        self.combo_page.addItems(items)
        if 0 <= self.current_index < total:
            self.combo_page.setCurrentIndex(self.current_index)
        self.combo_page.blockSignals(False)

    def on_page_selected(self, index):
        if 0 <= index < len(self.images):
            self.current_index = index
            self.load_image()
            # 重新聚焦到 View，确保键盘翻页可用
            self.view.setFocus()

    def toggle_mode(self):
        # 只有当两个路径都存在时才能切换
        if not self.raw_path or not self.trans_path:
            return
            
        target_mode = "trans" if self.current_mode == "raw" else "raw"
        target_path = self.trans_path if target_mode == "trans" else self.raw_path
        
        if not os.path.exists(target_path):
            print(f"切换失败，目录不存在: {target_path}")
            return
            
        new_images = self._load_images_from_dir(target_path)
        if not new_images:
            print(f"切换失败，目录无图片: {target_path}")
            return
            
        # 尝试保持当前页码进度
        # 如果新列表长度不够，则回退到最后一页
        if self.current_index >= len(new_images):
            self.current_index = len(new_images) - 1
            
        self.images = new_images
        self.current_mode = target_mode
        self.lbl_mode.setText(f"当前: {'熟肉' if self.current_mode == 'trans' else '生肉'}")
        
        self.update_page_combo()
        self.load_image()

    def merge_with_prev(self):
        if len(self.images) <= 1:
            self.merge_mode = None
            return
        if self.current_index == 0:
            self.merge_mode = "spread"
            self.load_image()
            return
        self.current_index = max(0, self.current_index - 1)
        self.merge_mode = "spread"
        self.load_image()

    def merge_with_next(self):
        if len(self.images) <= 1:
            self.merge_mode = None
            return
        if self.current_index >= len(self.images) - 1:
            self.current_index = max(0, len(self.images) - 2)
        self.merge_mode = "spread"
        self.load_image()

    def cancel_merge(self):
        self.merge_mode = None
        self.load_image()

    def load_image(self):
        if 0 <= self.current_index < len(self.images):
            pixmap = QPixmap(self.images[self.current_index])
            self.pixmap_item_left.setPixmap(pixmap)
            self.pixmap_item_left.setPos(0, 0)

            other_index = None
            if self.merge_mode == "spread" and self.current_index < len(self.images) - 1:
                other_index = self.current_index + 1

            if other_index is not None:
                other_pixmap = QPixmap(self.images[other_index])
                self.pixmap_item_right.setPixmap(other_pixmap)
                self.pixmap_item_right.setPos(pixmap.width() + self.merge_gap, 0)
                self.pixmap_item_right.setVisible(True)
            else:
                self.pixmap_item_right.setPixmap(QPixmap())
                self.pixmap_item_right.setPos(0, 0)
                self.pixmap_item_right.setVisible(False)

            rect = self._current_content_rect()
            if rect.isValid():
                self.scene.setSceneRect(rect)
            self._update_window_title()
            
            # 同步下拉框状态（防止翻页后下拉框不同步）
            self.combo_page.blockSignals(True)
            self.combo_page.setCurrentIndex(self.current_index)
            self.combo_page.blockSignals(False)
            
            screen = QApplication.primaryScreen().availableGeometry()
            rect = self.scene.sceneRect()
            target_w = min(int(rect.width()) + 40, screen.width() - 100)
            target_h = min(int(rect.height()) + 80, screen.height() - 100)
            
            # 只有当窗口明显过小的时候才去 resize，避免每次翻页都抖动
            if not self._window_state_restored and (self.width() < 200 or self.height() < 200):
                self.resize(target_w, target_h)
            
            # 使用 QTimer 延迟 10 毫秒执行缩放，等待窗口真正计算好尺寸
            QTimer.singleShot(10, self.fit_image)

    def fit_image(self):
        rect = self._current_content_rect()
        if rect.isValid():
            self.view.fitInView(rect, Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        # 当窗口被用户拉伸或初始展示时，自动贴合尺寸
        super().resizeEvent(event)
        self.fit_image()
        self._schedule_save_window_state()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._schedule_save_window_state()

    def closeEvent(self, event):
        try:
            self._save_window_state()
        finally:
            super().closeEvent(event)

    def next_page(self):
        if self.merge_mode == "spread":
            if self.current_index >= len(self.images) - 2:
                self.finished_reading.emit()
                self.close()
                return
            self.current_index += 2
            self.load_image()
            return

        if self.current_index < len(self.images) - 1:
            self.current_index += 1
            self.load_image()
            return

        self.finished_reading.emit()
        self.close()

    def prev_page(self):
        if self.merge_mode == "spread":
            if self.current_index >= 2:
                self.current_index -= 2
            else:
                self.current_index = 0
            self.load_image()
            return

        if self.current_index > 0:
            self.current_index -= 1
            self.load_image()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Right:
            self.next_page()
        elif event.key() == Qt.Key_Left:
            self.prev_page()
        elif event.key() == Qt.Key_Space: # [新增] 空格键切换模式
            self.toggle_mode()
        elif event.key() == Qt.Key_1:
            self.merge_with_prev()
        elif event.key() == Qt.Key_2:
            self.merge_with_next()
        elif event.key() == Qt.Key_3:
            self.cancel_merge()
        else:
            super().keyPressEvent(event)
