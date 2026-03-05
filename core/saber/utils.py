import cv2
import numpy as np
from PIL import Image
import os
import sys

def ensure_rgb(image):
    """
    Ensure image is in RGB format.
    Accepts PIL Image or numpy array.
    Returns PIL Image in RGB.
    """
    if isinstance(image, np.ndarray):
        # Assuming numpy array is BGR (cv2 default) or RGB
        # If 2 channels, gray
        # If 3 channels, assume BGR if coming from cv2, but here we want PIL RGB
        # It's safer to convert to PIL first
        if len(image.shape) == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        elif len(image.shape) == 3 and image.shape[2] == 3:
            # Assume BGR to RGB
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        elif len(image.shape) == 3 and image.shape[2] == 4:
            # BGRA to RGB
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
        image = Image.fromarray(image)
        
    if isinstance(image, Image.Image):
        if image.mode != 'RGB':
            image = image.convert('RGB')
    
    return image

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    candidates = []

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if exe_dir:
            candidates.append(exe_dir)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(meipass)

    candidates.append(os.path.abspath("."))

    for base_path in candidates:
        p = os.path.join(base_path, relative_path)
        if os.path.exists(p):
            return p

    return os.path.join(candidates[0], relative_path)

def get_debug_dir(subdir=None):
    debug_dir = os.path.join(resource_path("debug"), subdir) if subdir else resource_path("debug")
    if not os.path.exists(debug_dir):
        os.makedirs(debug_dir)
    return debug_dir

def get_font_path(font_name):
    """
    Get absolute path to font file.
    """
    if not font_name or not font_name.strip():
        font_name = 'msyh.ttc' # Default to YaHei

    # Check bundled fonts
    bundled_font = os.path.join(resource_path('fonts'), font_name)
    if os.path.exists(bundled_font):
        return bundled_font
        
    # Check Windows system fonts
    # Common windows font paths
    win_font_dirs = ['C:/Windows/Fonts', 'D:/Windows/Fonts', os.environ.get('WINDIR', 'C:/Windows') + '/Fonts']
    
    for font_dir in win_font_dirs:
        if not os.path.exists(font_dir): continue
        win_font = os.path.join(font_dir, font_name)
        if os.path.exists(win_font) and os.path.isfile(win_font):
            return win_font
            
    # Try to find by lower case if not found
    for font_dir in win_font_dirs:
        if not os.path.exists(font_dir): continue
        win_font = os.path.join(font_dir, font_name.lower())
        if os.path.exists(win_font) and os.path.isfile(win_font):
            return win_font
    
    # Try to find YaHei specifically if not found
    if 'msyh' in font_name.lower():
        for font_dir in win_font_dirs:
            if not os.path.exists(font_dir): continue
            for ext in ['.ttc', '.ttf']:
                p = os.path.join(font_dir, f'msyh{ext}')
                if os.path.exists(p): return p

    return font_name
