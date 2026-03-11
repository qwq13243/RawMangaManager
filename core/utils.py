import os
import glob
import sys
from PySide6.QtGui import QColor, QImage, QPainter

def find_playwright_executable():
    """查找本地ms-playwright中的chromium可执行文件"""
    # 优先检查环境变量
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browsers_path or not os.path.isdir(browsers_path):
        return None

    # 查找 chromium-* 目录
    # 注意：Playwright 版本更新可能导致目录名变更，需模糊匹配
    # 优先找 chromium-1208 (如果有的话)，否则找任何 chromium-*
    candidates = glob.glob(os.path.join(browsers_path, "chromium-*"))
    if not candidates:
        return None
        
    # 按名称倒序排列（通常版本号越高越好？）
    # chromium-1208 vs chromium-1140
    candidates.sort(reverse=True)
    
    for folder in candidates:
        # 检查常见路径
        # win: chrome-win/chrome.exe or chrome-win64/chrome.exe
        exe_paths = [
            os.path.join(folder, "chrome-win", "chrome.exe"),
            os.path.join(folder, "chrome-win64", "chrome.exe"),
        ]
        
        for p in exe_paths:
            if os.path.exists(p):
                return p
                
    return None

def split_image_in_place(image_path):
    """就地分割长图并删除原图，按宽度×(1600/1115)计算目标高度比例，确保高度能被整除"""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    try:
        img = Image.open(image_path)
        width, height = img.size
        dir_name = os.path.dirname(image_path)
        file_name, ext = os.path.splitext(os.path.basename(image_path))
        ext = ext.lower() if ext.lower() in ['.jpg', '.jpeg', '.png', '.webp', '.bmp'] else '.jpg'

        # 计算目标高度比例 (1600/1115 ≈ 1.4354)
        target_ratio = 1600 / 1115.0
        
        # 1. 计算目标高度 (宽度 × 1600/1115)
        target_height = width * target_ratio
        
        # 2. 计算需要分割的整数段数 (高度 ÷ 目标高度，四舍五入取整)
        num_segments = round(height / target_height)
        num_segments = max(1, num_segments)  # 确保至少1段
        
        # 3. 计算每段的实际高度 (总高度 ÷ 段数，确保能整除)
        segment_height = height // num_segments
        
        # 4. 按计算出的段数和高度分割
        count = 0
        for y in range(0, height, segment_height):
            crop_img = img.crop((0, y, width, min(y + segment_height, height)))
            if ext in ['.jpg', '.jpeg'] and crop_img.mode in ("RGBA", "P"):
                crop_img = crop_img.convert("RGB")
            crop_img.save(os.path.join(dir_name, f"{file_name}_{count:03d}{ext}"), quality=95)
            count += 1
        img.close()
        os.remove(image_path)
    except Exception as e:
        print(f"分割图片失败: {e}")

def generate_white_cover(path, size=(200, 300)):
    img = QImage(size[0], size[1], QImage.Format_RGB32)
    img.fill(QColor("#FFFFFF"))
    painter = QPainter(img)
    painter.setPen(QColor("#EEEEEE"))
    painter.drawRect(0, 0, size[0]-1, size[1]-1)
    painter.end()
    img.save(path, "PNG")
    return path
