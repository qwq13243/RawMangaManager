import logging
import cv2
import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger("PanelDetector")


@dataclass
class Panel:
    """Panel data structure"""
    x: int
    y: int
    w: int
    h: int
    
    @property
    def x1(self) -> int:
        return self.x
    
    @property
    def y1(self) -> int:
        return self.y
    
    @property
    def x2(self) -> int:
        return self.x + self.w
    
    @property
    def y2(self) -> int:
        return self.y + self.h
    
    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)
    
    @property
    def area(self) -> int:
        return self.w * self.h
    
    def to_xywh(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)
    
    def to_xyxy(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)
    
    def contains_point(self, px: int, py: int) -> bool:
        """Check if point is inside panel"""
        return self.x <= px <= self.x2 and self.y <= py <= self.y2


class PanelDetector:
    """
    Panel Detector based on traditional CV methods.
    """
    
    DEFAULT_MIN_PANEL_RATIO = 1 / 10
    
    def __init__(self, min_panel_ratio: float = None):
        self.min_panel_ratio = min_panel_ratio or self.DEFAULT_MIN_PANEL_RATIO
    
    def detect_panels(self, img: np.ndarray) -> List[Panel]:
        """
        Detect panels in image.
        img: BGR image array
        """
        if img is None or img.size == 0:
            logger.warning("Input image is empty")
            return []
        
        img_h, img_w = img.shape[:2]
        min_panel_area = int(img_w * img_h * self.min_panel_ratio)
        
        # 1. Grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 2. Sobel Edge Detection
        sobel = self._apply_sobel(gray)
        
        # 3. Threshold and Close
        thresh = self._threshold_and_close(sobel)
        
        # 4. Find Contours
        contours = self._find_contours(thresh)
        
        # 5. Convert to Panel objects
        panels = []
        for contour in contours:
            panel = self._contour_to_panel(contour)
            if panel and panel.area >= min_panel_area:
                panels.append(panel)
        
        # 6. Sort by area descending
        panels.sort(key=lambda p: p.area, reverse=True)
        
        logger.debug(f"Detected {len(panels)} panels")
        return panels
    
    def _apply_sobel(self, gray: np.ndarray) -> np.ndarray:
        ddepth = cv2.CV_16S
        grad_x = cv2.Sobel(gray, ddepth, 1, 0, ksize=3, scale=1, delta=0, borderType=cv2.BORDER_DEFAULT)
        grad_y = cv2.Sobel(gray, ddepth, 0, 1, ksize=3, scale=1, delta=0, borderType=cv2.BORDER_DEFAULT)
        abs_grad_x = cv2.convertScaleAbs(grad_x)
        abs_grad_y = cv2.convertScaleAbs(grad_y)
        sobel = cv2.addWeighted(abs_grad_x, 0.5, abs_grad_y, 0.5, 0)
        return sobel
    
    def _threshold_and_close(self, sobel: np.ndarray) -> np.ndarray:
        _, thresh = cv2.threshold(sobel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=1)
        return thresh
    
    def _find_contours(self, thresh: np.ndarray) -> List[np.ndarray]:
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return contours
    
    def _contour_to_panel(self, contour: np.ndarray) -> Optional[Panel]:
        arclength = cv2.arcLength(contour, True)
        epsilon = 0.001 * arclength
        approx = cv2.approxPolyDP(contour, epsilon, True)
        x, y, w, h = cv2.boundingRect(approx)
        if w <= 0 or h <= 0:
            return None
        return Panel(x=x, y=y, w=w, h=h)


def get_panels_from_array(img: np.ndarray, rtl: bool = True, min_panel_ratio: float = None) -> List[Tuple[int, int, int, int]]:
    """
    Detect panels from image array.
    """
    detector = PanelDetector(min_panel_ratio=min_panel_ratio)
    panels = detector.detect_panels(img)
    return [p.to_xywh() for p in panels]
