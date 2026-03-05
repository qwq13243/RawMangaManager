import cv2
import numpy as np


def resize_aspect_ratio(img, square_size, interpolation, mag_ratio=1):
    """
    Resize image maintaining aspect ratio and padding to multiple of 256.
    """
    height, width, channel = img.shape
    target_size = mag_ratio * square_size
    ratio = target_size / max(height, width)

    target_h, target_w = int(round(height * ratio)), int(round(width * ratio))
    proc = cv2.resize(img, (target_w, target_h), interpolation=interpolation)

    # Pad to multiple of 256
    MULT = 256
    target_h32, target_w32 = target_h, target_w
    pad_h = pad_w = 0
    
    if target_h % MULT != 0:
        pad_h = (MULT - target_h % MULT)
        target_h32 = target_h + pad_h
    if target_w % MULT != 0:
        pad_w = (MULT - target_w % MULT)
        target_w32 = target_w + pad_w

    resized = np.zeros((target_h32, target_w32, channel), dtype=np.uint8)
    resized[0:target_h, 0:target_w, :] = proc

    size_heatmap = (int(target_w32 / 2), int(target_h32 / 2))
    return resized, ratio, size_heatmap, pad_w, pad_h


def adjustResultCoordinates(polys, ratio_w, ratio_h, ratio_net=2):
    """
    Adjust coordinates back to original image size.
    """
    if len(polys) > 0:
        polys = np.array(polys)
        for k in range(len(polys)):
            if polys[k] is not None:
                polys[k] *= (ratio_w * ratio_net, ratio_h * ratio_net)
    return polys
