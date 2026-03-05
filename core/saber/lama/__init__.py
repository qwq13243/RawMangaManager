import logging
import numpy as np
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
            # Convert PIL to numpy
            image_np = np.array(image.convert("RGB"))
            mask_np = np.array(mask.convert("L"))
            
            # Invert mask: Input 0=Hole -> LAMA 255=Hole
            mask_np = (255 - mask_np).astype(np.uint8)
            
            result_np = inpaint_with_lama_mpe(image_np, mask_np, disable_resize=disable_resize)
            
            if result_np is not None:
                return Image.fromarray(result_np)
        except Exception as e:
            logger.error(f"LAMA inpainting failed: {e}", exc_info=True)
            
    logger.warning(f"LAMA model {lama_model} not available or failed.")
    return None
