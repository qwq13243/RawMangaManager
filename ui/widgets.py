from PySide6.QtWidgets import QPushButton, QWidget, QApplication
from PySide6.QtCore import Qt, Signal, QTimer, QMimeData, QPoint
from PySide6.QtGui import QDrag

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

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
            self._is_dragging = False
            # 开启长按计时器 (例如 300ms)
            self._long_press_timer.start(300)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos:
            # 如果移动距离超过阈值，视为取消长按，转为普通移动或取消
            dist = (event.pos() - self._drag_start_pos).manhattanLength()
            if dist > QApplication.startDragDistance():
                if self._long_press_timer.isActive():
                     self._long_press_timer.stop()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._long_press_timer.stop()
        self._drag_start_pos = None
        # 只有在没有触发拖拽的情况下，才传递点击事件
        if not self._is_dragging:
            super().mouseReleaseEvent(event)

    def on_long_press(self):
        # 长按触发，开始拖拽
        self._is_dragging = True
        self.dragStarted.emit()
        
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
        drag.exec_(Qt.MoveAction)
        
        self.dragFinished.emit()

class MangaGridWidget(QWidget):
    """
    支持拖放的网格容器
    """
    orderChanged = Signal(int, QPoint) # source_id, drop_pos

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        try:
            source_id = int(event.mimeData().text())
        except:
            return

        # 直接传递落点坐标，由 MainWindow 计算索引
        drop_pos = event.pos()
        self.orderChanged.emit(source_id, drop_pos)
        event.acceptProposedAction()
