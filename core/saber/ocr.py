import os
import logging
import manga_ocr
from PIL import Image
import torch
from core.saber.utils import resource_path

logger = logging.getLogger("SaberOCR")

# Constants
DEFAULT_MANGA_OCR_PATH = 'models/manga_ocr'

class SaberOCR:
    def __init__(self, device=None):
        if device is None:
            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        else:
            self.device = device
            
        self.model_path = DEFAULT_MANGA_OCR_PATH
        self.mocr = None
        
    def load_model(self):
        if self.mocr is not None:
            return
            
        abs_model_path = resource_path(self.model_path)
        logger.info(f"Loading MangaOCR model from {abs_model_path}")

        required_files = [
            "config.json",
            "preprocessor_config.json",
            "pytorch_model.bin",
            "special_tokens_map.json",
            "tokenizer_config.json",
            "vocab.txt",
        ]
        missing = [f for f in required_files if not os.path.exists(os.path.join(abs_model_path, f))]
        if missing:
            raise FileNotFoundError(f"MangaOCR model files missing: {missing} in {abs_model_path}")
            
        try:
            # force_cpu logic
            force_cpu = (self.device == 'cpu')
            
            self.mocr = manga_ocr.MangaOcr(
                pretrained_model_name_or_path=abs_model_path,
                force_cpu=force_cpu
            )
            logger.info("MangaOCR loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load MangaOCR: {e}")
            raise e

    def recognize(self, image):
        """
        Recognize text in image.
        image: PIL Image or numpy array
        """
        self.load_model()
        
        if isinstance(image, Image.Image):
            pil_image = image
        else:
            # Assume numpy array
            pil_image = Image.fromarray(image)
            
        return self.mocr(pil_image)

# Singleton
ocr_engine = SaberOCR()
