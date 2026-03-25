from PySide6.QtWidgets import QPushButton, QWidget, QApplication
from PySide6.QtCore import Qt, Signal, QTimer, QMimeData, QPoint
from PySide6.QtGui import QDrag
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
        
        # 设置拖拽时的视觉反馈 (半透明截图)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(self.rect().center()) # 中心对齐
        
        # 执行拖拽
        result = drag.exec_(Qt.MoveAction)
        self._dbg(f"drag finished result={int(result)}")
        
        self.dragFinished.emit()

class MangaGridWidget(QWidget):
    """
    支持拖放的网格容器
    """
    orderChanged = Signal(int, QPoint) # source_id, drop_pos

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
