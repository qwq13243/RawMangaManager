import logging
import numpy as np
import cv2
from PIL import Image
from .model import inpaint_with_lama_mpe, is_lama_mpe_available

logger = logging.getLogger("SaberLamaInterface")

def is_lama_available(model_name='lama_mpe'):
    if model_name == 'lama_mpe':
        return is_lama_mpe_available()
    return False

def clean_image_with_lama(image, mask, lama_model='lama_mpe', disable_resize=False):
    """
    Clean image using LAMA model.
    image: PIL Image (RGB)
    mask: PIL Image (L), black (0) is hole to fill, white (255) is keep.
          (Note: This function will invert it for LAMA which expects white for hole)
    """
    if lama_model == 'lama_mpe' and is_lama_mpe_available():
        try:
            image_np = np.array(image.convert("RGB"), dtype=np.uint8)
            mask_np = np.array(mask.convert("L"), dtype=np.uint8)
            
            # 检测原图是否为黑白图像
            # 策略：缩小图片后检测 R, G, B 通道的差异。避免局部小面积色彩影响整体判断
            thumb = cv2.resize(image_np, (256, 256))
            r, g, b = cv2.split(thumb)
            color_diff = np.maximum(cv2.absdiff(r, g), cv2.absdiff(g, b))
            # 如果绝大多数像素的通道差异很小，认为是黑白图（忽略由于 JPEG 压缩引起的轻微色彩偏差）
            is_bw = np.mean(color_diff) < 2.0 and np.percentile(color_diff, 98) < 15
            
            _, mask_np = cv2.threshold(mask_np, 127, 255, cv2.THRESH_BINARY)
            mask_np = 255 - mask_np
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
            mask_np = cv2.dilate(mask_np, kernel, iterations=1)
            mask_np = np.where(mask_np > 0, 255, 0).astype(np.uint8)
            result_np = inpaint_with_lama_mpe(image_np, mask_np, disable_resize=disable_resize)
            if result_np is not None:
                result_np = np.asarray(result_np, dtype=np.uint8)
                if result_np.ndim == 2:
                    result_np = cv2.cvtColor(result_np, cv2.COLOR_GRAY2RGB)
                elif result_np.shape[2] == 4:
                    result_np = cv2.cvtColor(result_np, cv2.COLOR_RGBA2RGB)
                
                # 如果原图是黑白，将 LAMA 输出强制转换为黑白，消除彩色伪影；彩图则保留彩色
                if is_bw:
                    gray = cv2.cvtColor(result_np, cv2.COLOR_RGB2GRAY)
                    result_np = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
                    
                return Image.fromarray(result_np, mode="RGB")
        except Exception as e:
            logger.error(f"LAMA inpainting failed: {e}", exc_info=True)
            
    logger.warning(f"LAMA model {lama_model} not available or failed.")
    return None
