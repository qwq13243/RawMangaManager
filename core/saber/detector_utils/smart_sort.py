import logging
import numpy as np
from typing import List, Tuple
from core.saber.data_types import TextBlock
from .panel_detector import get_panels_from_array

logger = logging.getLogger("SmartSort")


def sort_regions(
    regions: List[TextBlock],
    right_to_left: bool = True,
    img: np.ndarray = None,
    force_simple_sort: bool = False
) -> List[TextBlock]:
    """
    Smart sort text regions.
    """
    if not regions:
        return []
    
    if force_simple_sort:
        return _simple_sort(regions, right_to_left)
    
    # 1. Panel Detection + Within-panel sorting
    if img is not None:
        try:
            panels_raw = get_panels_from_array(img, rtl=right_to_left)
            panels = [(x, y, x + w, y + h) for x, y, w, h in panels_raw]
            panels = _sort_panels_fill(panels, right_to_left)
            
            for r in regions:
                cx, cy = r.center
                r.panel_index = -1
                for idx, (x1, y1, x2, y2) in enumerate(panels):
                    if x1 <= cx <= x2 and y1 <= cy <= y2:
                        r.panel_index = idx
                        break
                
                if r.panel_index < 0:
                    dists = [
                        ((max(x1 - cx, 0, cx - x2)) ** 2 + (max(y1 - cy, 0, cy - y2)) ** 2, i)
                        for i, (x1, y1, x2, y2) in enumerate(panels)
                    ]
                    if dists:
                        r.panel_index = min(dists)[1]
            
            grouped = {}
            for r in regions:
                grouped.setdefault(r.panel_index, []).append(r)
            
            sorted_all = []
            for pi in sorted(grouped.keys()):
                panel_sorted = sort_regions(grouped[pi], right_to_left, img=None, force_simple_sort=False)
                sorted_all += panel_sorted
            
            logger.debug(f"Used panel detection sort, detected {len(panels)} panels")
            return sorted_all
        
        except Exception as e:
            logger.debug(f"Panel detection failed ({e.__class__.__name__}: {str(e)[:100]}), falling back to simple sort")
            return _simple_sort(regions, right_to_left)
    
    # 2. Smart sort (no image or detection failed)
    xs = [r.center[0] for r in regions]
    ys = [r.center[1] for r in regions]
    
    if len(regions) > 1:
        x_std = np.std(xs) if len(xs) > 1 else 0
        y_std = np.std(ys) if len(ys) > 1 else 0
        is_horizontal = x_std > y_std
    else:
        is_horizontal = False
    
    sorted_regions = []
    if is_horizontal:
        primary = sorted(regions, key=lambda r: -r.center[0] if right_to_left else r.center[0])
        group, prev = [], None
        for r in primary:
            cx = r.center[0]
            if prev is not None and abs(cx - prev) > 20:
                group.sort(key=lambda r: r.center[1])
                sorted_regions += group
                group = []
            group.append(r)
            prev = cx
        if group:
            group.sort(key=lambda r: r.center[1])
            sorted_regions += group
    else:
        primary = sorted(regions, key=lambda r: r.center[1])
        group, prev = [], None
        for r in primary:
            cy = r.center[1]
            if prev is not None and abs(cy - prev) > 15:
                group.sort(key=lambda r: -r.center[0] if right_to_left else r.center[0])
                sorted_regions += group
                group = []
            group.append(r)
            prev = cy
        if group:
            group.sort(key=lambda r: -r.center[0] if right_to_left else r.center[0])
            sorted_regions += group
    
    logger.debug(f"Used smart sort ({'horizontal' if is_horizontal else 'vertical'} dispersion)")
    return sorted_regions


def _simple_sort(regions: List[TextBlock], right_to_left: bool = True) -> List[TextBlock]:
    """
    Simple sort (fallback).
    """
    if right_to_left:
        # Manga: Top-right priority
        return sorted(regions, key=lambda r: (-r.center[0], r.center[1]))
    else:
        # Standard: Top-left priority
        return sorted(regions, key=lambda r: (r.center[1], r.center[0]))


def _sort_panels_fill(panels: List[Tuple[int, int, int, int]], right_to_left: bool) -> List[Tuple[int, int, int, int]]:
    """
    Sort panels (keep vertically stacked panels together).
    """
    if not panels:
        return []
    
    if len(panels) == 1:
        return panels
    
    panels_sorted_by_y = sorted(panels, key=lambda p: p[1])
    avg_height = sum(p[3] - p[1] for p in panels) / len(panels)
    threshold = avg_height * 0.5
    
    groups = []
    current_group = [panels_sorted_by_y[0]]
    
    for i in range(1, len(panels_sorted_by_y)):
        prev_y = panels_sorted_by_y[i-1][1]
        curr_y = panels_sorted_by_y[i][1]
        
        if abs(curr_y - prev_y) < threshold:
            current_group.append(panels_sorted_by_y[i])
        else:
            groups.append(current_group)
            current_group = [panels_sorted_by_y[i]]
    
    groups.append(current_group)
    
    sorted_panels = []
    for group in groups:
        if right_to_left:
            group_sorted = sorted(group, key=lambda p: -p[0])
        else:
            group_sorted = sorted(group, key=lambda p: p[0])
        sorted_panels.extend(group_sorted)
    
    return sorted_panels


def sort_blocks_by_reading_order(
    blocks: List[TextBlock],
    right_to_left: bool = True,
    img: np.ndarray = None
) -> List[TextBlock]:
    """
    Main entry point for sorting text blocks by reading order.
    """
    return sort_regions(blocks, right_to_left=right_to_left, img=img, force_simple_sort=False)
