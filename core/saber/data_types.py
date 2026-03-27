from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Union, Dict
from functools import cached_property
import numpy as np
import cv2
from shapely.geometry import Polygon, MultiPoint


@dataclass
class TextLine:
    """
    Single text line (Quadrilateral)
    """
    pts: np.ndarray  # shape: (4, 2), four corner coordinates
    confidence: float = 1.0
    text: str = ""
    
    # Color info (optional)
    fg_color: Tuple[int, int, int] = (0, 0, 0)
    bg_color: Tuple[int, int, int] = (255, 255, 255)
    
    def __post_init__(self):
        # Ensure pts is correct format
        if isinstance(self.pts, list):
            self.pts = np.array(self.pts, dtype=np.int32)
        if self.pts.shape != (4, 2):
            self.pts = self.pts.reshape(4, 2)
        # Sort points
        self.pts, self._is_vertical = self._sort_points(self.pts)
    
    @staticmethod
    def _sort_points(pts: np.ndarray) -> Tuple[np.ndarray, bool]:
        """Sort the four points of the quadrilateral and determine if it is vertical text"""
        pts = pts.astype(np.float32)
        
        # Use structural vector to determine direction
        pairwise_vec = (pts[:, None] - pts[None]).reshape((16, -1))
        pairwise_vec_norm = np.linalg.norm(pairwise_vec, axis=1)
        long_side_ids = np.argsort(pairwise_vec_norm)[[8, 10]]
        long_side_vecs = pairwise_vec[long_side_ids]
        inner_prod = (long_side_vecs[0] * long_side_vecs[1]).sum()
        if inner_prod < 0:
            long_side_vecs[0] = -long_side_vecs[0]
        struc_vec = np.abs(long_side_vecs.mean(axis=0))
        is_vertical = struc_vec[0] <= struc_vec[1]
        
        if is_vertical:
            pts = pts[np.argsort(pts[:, 1])]
            pts = pts[[*np.argsort(pts[:2, 0]), *np.argsort(pts[2:, 0])[::-1] + 2]]
        else:
            pts = pts[np.argsort(pts[:, 0])]
            pts_sorted = np.zeros_like(pts)
            pts_sorted[[0, 3]] = sorted(pts[[0, 1]], key=lambda x: x[1])
            pts_sorted[[1, 2]] = sorted(pts[[2, 3]], key=lambda x: x[1])
            pts = pts_sorted
        
        return pts.astype(np.int32), is_vertical
    
    @cached_property
    def xyxy(self) -> Tuple[int, int, int, int]:
        """Axis-aligned bounding box (x1, y1, x2, y2)"""
        x1, y1 = self.pts.min(axis=0)
        x2, y2 = self.pts.max(axis=0)
        return int(x1), int(y1), int(x2), int(y2)
    
    @cached_property
    def xywh(self) -> Tuple[int, int, int, int]:
        """(x, y, width, height) format"""
        x1, y1, x2, y2 = self.xyxy
        return x1, y1, x2 - x1, y2 - y1
    
    @cached_property
    def center(self) -> np.ndarray:
        """Center point"""
        return np.mean(self.pts, axis=0)
    
    @cached_property
    def centroid(self) -> np.ndarray:
        """Centroid (same as center)"""
        return self.center
    
    @cached_property
    def structure(self) -> List[np.ndarray]:
        """Structure points: midpoints of the four sides"""
        p1 = ((self.pts[0] + self.pts[1]) / 2).astype(int)
        p2 = ((self.pts[2] + self.pts[3]) / 2).astype(int)
        p3 = ((self.pts[1] + self.pts[2]) / 2).astype(int)
        p4 = ((self.pts[3] + self.pts[0]) / 2).astype(int)
        return [p1, p2, p3, p4]
    
    @cached_property
    def font_size(self) -> float:
        """Estimated font size (short side length)"""
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        return min(np.linalg.norm(v2), np.linalg.norm(v1))
    
    @cached_property
    def aspect_ratio(self) -> float:
        """Aspect ratio"""
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        norm_v = np.linalg.norm(v1)
        if norm_v == 0:
            return 1.0
        return np.linalg.norm(v2) / norm_v
    
    @cached_property
    def is_vertical(self) -> bool:
        """Is vertical text"""
        return self._is_vertical
    
    @cached_property
    def direction(self) -> str:
        """Direction: 'h' (horizontal) or 'v' (vertical)"""
        return 'v' if self._is_vertical else 'h'
    
    @cached_property
    def angle(self) -> float:
        """Rotation angle (radians)"""
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        e2 = np.array([1, 0])
        norm = np.linalg.norm(v1)
        if norm == 0:
            return 0.0
        unit_vector = v1 / norm
        cos_angle = np.dot(unit_vector, e2)
        return np.fmod(np.arccos(np.clip(cos_angle, -1, 1)) + np.pi, np.pi)
    
    @cached_property
    def angle_degrees(self) -> float:
        """Rotation angle (degrees)"""
        return np.rad2deg(self.angle) - 90
    
    @cached_property
    def polygon(self) -> Polygon:
        """Shapely Polygon object"""
        return MultiPoint([tuple(p) for p in self.pts]).convex_hull
    
    @cached_property
    def area(self) -> float:
        """Area"""
        return self.polygon.area
    
    def distance_to(self, other: 'TextLine') -> float:
        """Calculate distance to another text line"""
        return self.polygon.distance(other.polygon)
    
    def poly_distance(self, other: 'TextLine') -> float:
        """Calculate distance between two boxes, prioritizing parallel side midpoints"""
        dir_a = self.direction
        dir_b = other.direction
        
        if dir_a == dir_b:
            if dir_a == 'h':
                self_top_mid = (self.pts[0] + self.pts[1]) / 2
                self_bottom_mid = (self.pts[2] + self.pts[3]) / 2
                other_top_mid = (other.pts[0] + other.pts[1]) / 2
                other_bottom_mid = (other.pts[2] + other.pts[3]) / 2
                
                distances = [
                    np.linalg.norm(self_top_mid - other_top_mid),
                    np.linalg.norm(self_top_mid - other_bottom_mid),
                    np.linalg.norm(self_bottom_mid - other_top_mid),
                    np.linalg.norm(self_bottom_mid - other_bottom_mid),
                ]
                return min(distances)
            else:
                self_left_mid = (self.pts[0] + self.pts[3]) / 2
                self_right_mid = (self.pts[1] + self.pts[2]) / 2
                other_left_mid = (other.pts[0] + other.pts[3]) / 2
                other_right_mid = (other.pts[1] + other.pts[2]) / 2
                
                distances = [
                    np.linalg.norm(self_left_mid - other_left_mid),
                    np.linalg.norm(self_left_mid - other_right_mid),
                    np.linalg.norm(self_right_mid - other_left_mid),
                    np.linalg.norm(self_right_mid - other_right_mid),
                ]
                return min(distances)
        
        return self.polygon.distance(other.polygon)
    
    def clip(self, width: int, height: int):
        """Clip to image boundaries"""
        self.pts[:, 0] = np.clip(self.pts[:, 0], 0, width)
        self.pts[:, 1] = np.clip(self.pts[:, 1], 0, height)


@dataclass
class TextBlock:
    lines: List[TextLine] = field(default_factory=list)
    texts: List[str] = field(default_factory=list)
    
    # Rendering & State Info
    original_text: str = ""
    translated_text: str = ""
    translation_successful: bool = False
    
    font_size: int = -1
    font_family: str = ""
    _angle: float = 0  # degrees
    
    text_color: Union[str, Tuple[int, int, int]] = (0, 0, 0)
    fill_color: Union[str, Tuple[int, int, int]] = (255, 255, 255)
    
    stroke_enabled: bool = True
    stroke_color: Union[str, Tuple[int, int, int]] = (255, 255, 255)
    stroke_width: int = 3
    
    _direction: str = 'auto'
    alignment: str = 'center'
    
    position_offset: Dict[str, int] = field(default_factory=lambda: {'x': 0, 'y': 0})
    line_spacing: float = 1.0
    char_spacing: float = 0.0
    
    # Confidence
    prob: float = 1.0
    
    # Label (for YOLO)
    label: str = ""
    
    # Panel index (for smart sort)
    panel_index: int = -1
    
    def __post_init__(self):
        # If lines is list of np.ndarray, convert to TextLine
        if self.lines and isinstance(self.lines[0], np.ndarray):
            self.lines = [TextLine(pts=pts) for pts in self.lines]
    
    @property
    def coords(self) -> Tuple[int, int, int, int]:
        return self.xyxy

    @cached_property
    def xyxy(self) -> Tuple[int, int, int, int]:
        """
        Angle-aware bounding box (x1, y1, x2, y2)
        """
        if not self.lines:
            return (0, 0, 0, 0)
        
        all_pts = np.vstack([line.pts for line in self.lines]).astype(np.float32)
        
        # Get angle
        angle_deg = self.angle
        
        # If angle is close to 0, calculate AABB directly
        if abs(angle_deg) < 1:
            x1, y1 = all_pts.min(axis=0)
            x2, y2 = all_pts.max(axis=0)
            return int(x1), int(y1), int(x2), int(y2)
        
        # Use minAreaRect center
        rect = cv2.minAreaRect(all_pts)
        cx, cy = rect[0]
        
        # Rotate points to 0 degrees
        angle_rad = np.deg2rad(-angle_deg)
        cos_a = np.cos(angle_rad)
        sin_a = np.sin(angle_rad)
        
        # Translate to origin -> Rotate
        pts_centered = all_pts - np.array([cx, cy])
        pts_rotated = np.zeros_like(pts_centered)
        pts_rotated[:, 0] = pts_centered[:, 0] * cos_a - pts_centered[:, 1] * sin_a
        pts_rotated[:, 1] = pts_centered[:, 0] * sin_a + pts_centered[:, 1] * cos_a
        
        # Calculate rotated AABB
        half_w = (pts_rotated[:, 0].max() - pts_rotated[:, 0].min()) / 2
        half_h = (pts_rotated[:, 1].max() - pts_rotated[:, 1].min()) / 2
        
        # Return bounding box relative to center
        x1 = int(np.floor(cx - half_w))
        y1 = int(np.floor(cy - half_h))
        x2 = int(np.ceil(cx + half_w))
        y2 = int(np.ceil(cy + half_h))
        
        return x1, y1, x2, y2
    
    @cached_property
    def xywh(self) -> Tuple[int, int, int, int]:
        x1, y1, x2, y2 = self.xyxy
        return x1, y1, x2 - x1, y2 - y1
    
    @cached_property
    def center(self) -> np.ndarray:
        xyxy = np.array(self.xyxy)
        return (xyxy[:2] + xyxy[2:]) / 2
    
    @property
    def angle(self) -> float:
        """Rotation angle (degrees)"""
        if self._angle != 0:
            return self._angle
        if not self.lines:
            return 0
        # Calculate average angle
        angles = [line.angle_degrees for line in self.lines]
        avg = np.mean(angles)
        # Zero out small angles
        if abs(avg) < 3:
            return 0
        return avg
    
    @angle.setter
    def angle(self, value: float):
        self._angle = value
    
    @property
    def rotation_angle(self) -> float:
        return self.angle

    @property
    def direction(self) -> str:
        """Layout direction: 'h' (horizontal) or 'v' (vertical)"""
        if self._direction in ('h', 'v', 'hr', 'vr', 'horizontal', 'vertical'):
            # Standardize
            if self._direction == 'horizontal': return 'h'
            if self._direction == 'vertical': return 'v'
            return self._direction
        
        # Auto-detect
        if not self.lines:
            x1, y1, x2, y2 = self.xyxy
            return 'v' if (y2 - y1) > (x2 - x1) else 'h'
        
        # Determine by largest text line
        max_area = 0
        max_direction = 'h'
        for line in self.lines:
            if line.area > max_area:
                max_area = line.area
                max_direction = line.direction
        return max_direction
    
    @property
    def text_direction(self) -> str:
        """Alias for direction (for compatibility with renderer)"""
        d = self.direction
        return 'vertical' if d == 'v' else 'horizontal'

    @direction.setter
    def direction(self, value: str):
        self._direction = value
    
    @cached_property
    def vertical(self) -> bool:
        return self.direction.startswith('v')
    
    @cached_property
    def horizontal(self) -> bool:
        return self.direction.startswith('h')
    
    @cached_property
    def min_rect(self) -> np.ndarray:
        """Minimum bounding rectangle"""
        if not self.lines:
            x1, y1, x2, y2 = self.xyxy
            return np.array([[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]])
        
        all_pts = np.vstack([line.pts for line in self.lines])
        rect = cv2.minAreaRect(all_pts.astype(np.float32))
        box = cv2.boxPoints(rect).astype(np.int32)
        return np.array([box])
    
    @cached_property
    def polygon(self) -> List[List[int]]:
        """Quadrilateral vertices list"""
        return self.min_rect[0].tolist()
    
    @cached_property
    def area(self) -> float:
        """Area"""
        x1, y1, x2, y2 = self.xyxy
        return (x2 - x1) * (y2 - y1)
    
    @property
    def text(self) -> str:
        """Merged text"""
        if self.texts:
            return ' '.join(self.texts)
        return ' '.join([line.text for line in self.lines if line.text])
    
    @text.setter
    def text(self, value: str):
        self.texts = [value]
    
    def adjust_bbox(self, im_w: int = None, im_h: int = None):
        """Adjust bounding box to image boundaries"""
        if im_w is None or im_h is None:
            return
        for line in self.lines:
            line.clip(im_w, im_h)
        # Clear cache
        if 'xyxy' in self.__dict__:
            del self.__dict__['xyxy']
        if 'min_rect' in self.__dict__:
            del self.__dict__['min_rect']


@dataclass
class DetectionResult:
    """
    Unified detection result
    """
    blocks: List[TextBlock] = field(default_factory=list)
    mask: Optional[np.ndarray] = None
    
    # Raw text lines (before merging)
    raw_lines: List[TextLine] = field(default_factory=list)
    
    def __len__(self) -> int:
        return len(self.blocks)
    
    def __iter__(self):
        return iter(self.blocks)
    
    def to_legacy_format(self) -> dict:
        """
        Convert to legacy coords/polygons/angles format
        """
        coords = []
        polygons = []
        angles = []
        
        for block in self.blocks:
            coords.append(block.xyxy)
            polygons.append(block.polygon)
            angles.append(block.angle)
        
        return {
            'coords': coords,
            'polygons': polygons,
            'angles': angles
        }
    
    @property
    def coords(self) -> List[Tuple[int, int, int, int]]:
        return [block.xyxy for block in self.blocks]
    
    @property
    def polygons(self) -> List[List[List[int]]]:
        return [block.polygon for block in self.blocks]
    
    @property
    def angles(self) -> List[float]:
        return [block.angle for block in self.blocks]
