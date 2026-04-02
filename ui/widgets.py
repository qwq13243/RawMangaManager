from PySide6.QtWidgets import QPushButton, QWidget, QApplication
from PySide6.QtCore import Qt, Signal, QTimer, QMimeData, QPoint
from PySide6.QtGui import QDrag, QPixmap, QPainter, QColor
import os
import time

class DraggableCard(QPushButton):
    """
    支持左键长按拖动的卡片组件
    """
    dragStarted = Signal()
    dragFinished = Signal()

    def __init__(self, manga_id, parent=None):
        super().__init__(parent)
        self.manga_id = manga_id
        self._drag_start_pos = None
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.timeout.connect(self.on_long_press)
        self._is_dragging = False
        self._debug_ui = os.environ.get("MANGA_UI_DEBUG", "0") == "1"

    def _dbg(self, msg: str) -> None:
        if self._debug_ui:
            print(f"[UI][{time.strftime('%H:%M:%S')}] Card#{self.manga_id} {msg}", flush=True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
            self._is_dragging = False
            # 开启长按计时器 (例如 300ms)
            self._long_press_timer.start(300)
            self._dbg(f"mousePress L pos={event.pos().x()},{event.pos().y()} timer=on")
        elif event.button() == Qt.RightButton:
            self._dbg(f"mousePress R pos={event.pos().x()},{event.pos().y()}")
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos:
            # 如果移动距离超过阈值，视为取消长按，转为普通移动或取消
            dist = (event.pos() - self._drag_start_pos).manhattanLength()
            if dist > QApplication.startDragDistance():
                if self._long_press_timer.isActive():
                     self._long_press_timer.stop()
                     self._dbg(f"mouseMove cancel longPress dist={dist}")
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._long_press_timer.stop()
        self._drag_start_pos = None
        self._dbg(f"mouseRelease btn={event.button()} is_dragging={self._is_dragging}")
        # 只有在没有触发拖拽的情况下，才传递点击事件
        if not self._is_dragging:
            super().mouseReleaseEvent(event)

    def on_long_press(self):
        # 长按触发，开始拖拽
        self._is_dragging = True
        self.dragStarted.emit()
        self._dbg("longPress -> start drag")
        
        drag = QDrag(self)
        mime = QMimeData()
        # 传递 manga_id
        mime.setText(str(self.manga_id))
        drag.setMimeData(mime)
        
        # 设置拖拽时的视觉反馈 (App式的悬浮感动效)
        original_pixmap = self.grab()
        
        # 将原始截图放大一点并添加半透明度来制造“被提起”的效果
        scale_factor = 1.05
        new_width = int(original_pixmap.width() * scale_factor)
        new_height = int(original_pixmap.height() * scale_factor)
        
        scaled_pixmap = original_pixmap.scaled(new_width, new_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        
        # 创建一个带有阴影边距和透明通道的透明画布
        shadow_margin = 10
        canvas_width = new_width + shadow_margin * 2
        canvas_height = new_height + shadow_margin * 2
        drag_pixmap = QPixmap(canvas_width, canvas_height)
        drag_pixmap.fill(Qt.transparent)
        
        painter = QPainter(drag_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 绘制半透明的阴影
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(shadow_margin + 5, shadow_margin + 5, new_width, new_height, 8, 8)
        
        # 绘制卡片本体（增加一点全局透明度，使得不会完全遮挡底下的界面）
        painter.setOpacity(0.9)
        painter.drawPixmap(shadow_margin, shadow_margin, scaled_pixmap)
        painter.end()

        drag.setPixmap(drag_pixmap)
        # 将抓取点对准放大的图片的中心（修正上阴影带来的偏移）
        drag.setHotSpot(QPoint(drag_pixmap.width() // 2, drag_pixmap.height() // 2))
        
        # 隐藏自身，制造“卡片被拿起来带走”的错觉
        self.setHidden(True)
        
        # 执行拖拽
        result = drag.exec_(Qt.MoveAction)
        self._dbg(f"drag finished result={int(result)}")
        
        # 拖拽结束恢复显示
        self.setHidden(False)
        self.dragFinished.emit()

class MangaGridWidget(QWidget):
    """
    支持拖放的网格容器
    """
    orderChanged = Signal(int, QPoint) # source_id, drop_pos
    dragMovedSignal = Signal(int, QPoint) # source_id, hover_pos

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._debug_ui = os.environ.get("MANGA_UI_DEBUG", "0") == "1"

    def _dbg(self, msg: str) -> None:
        if self._debug_ui:
            print(f"[UI][{time.strftime('%H:%M:%S')}] Grid {msg}", flush=True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            self._dbg(f"dragEnter text={event.mimeData().text()}")
            event.acceptProposedAction()
        else:
            self._dbg("dragEnter ignored (no text)")

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            self._dbg(f"dragMove pos={event.pos().x()},{event.pos().y()} text={event.mimeData().text()}")
            try:
                source_id = int(event.mimeData().text())
                self.dragMovedSignal.emit(source_id, event.pos())
            except ValueError:
                pass
            event.acceptProposedAction()

    def dropEvent(self, event):
        try:
            source_id = int(event.mimeData().text())
        except:
            self._dbg(f"drop ignored (bad text={event.mimeData().text()})")
            return

        # 直接传递落点坐标，由 MainWindow 计算索引
        drop_pos = event.pos()
        self._dbg(f"drop source_id={source_id} pos={drop_pos.x()},{drop_pos.y()}")
        self.orderChanged.emit(source_id, drop_pos)
        event.acceptProposedAction()
