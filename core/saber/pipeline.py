import logging
from PIL import Image
import numpy as np
import os
from .detector import SaberDetector, expand_coordinates
from .ocr import SaberOCR
from .translator import translate_single_text, translate_batch_text
from .inpainter import inpaint_bubbles
from .renderer import render_bubbles_unified
from .config import config
from .data_types import DetectionResult

logger = logging.getLogger("SaberPipeline")

class SaberPipeline:
    def __init__(self):
        self.detector = SaberDetector()
        self.ocr = SaberOCR()
        # Translator is module based
        # Inpainter is module based
        # Renderer is module based

    def process_image(self, image_path, output_path, 
                      target_language='zh',
                      detector_key='default',
                      translator_key=None, # e.g. 'siliconflow'
                      api_key=None,
                      model_name=None):
        
        logger.info(f"Processing image: {image_path}")
        
        # 1. Load Image
        try:
            image = Image.open(image_path).convert("RGB")
        except Exception as e:
            logger.error(f"Failed to load image: {e}")
            return False

        # 2. Detect
        logger.info("Detecting bubbles...")
        # detector_key is ignored as we only have one default detector
        det_result = self.detector.detect(image)
        logger.info(f"Detected {len(det_result.blocks)} bubbles.")
        
        if not det_result.blocks:
            logger.warning("No bubbles detected.")
            # Save original image as result?
            image.save(output_path)
            return True

        # 2.1 Expand boxes if configured
        im_w, im_h = image.size
        coords = [b.xyxy for b in det_result.blocks]
        expanded_coords = expand_coordinates(
            coords, im_w, im_h,
            expand_ratio=config.detect_expand_global,
            expand_top=config.detect_expand_top,
            expand_bottom=config.detect_expand_bottom,
            expand_left=config.detect_expand_left,
            expand_right=config.detect_expand_right
        )
        
        # Update blocks with expanded coordinates
        for i, block in enumerate(det_result.blocks):
            block.xyxy = expanded_coords[i]

        # 3. OCR & Translate
        logger.info("OCR and Translating...")
        
        # Use config if not provided
        if translator_key is None:
            translator_key = config.model_provider
        if api_key is None:
            api_key = config.api_key
        if model_name is None:
            model_name = config.model_name

        for i, block in enumerate(det_result.blocks):
            # Crop
            x1, y1, x2, y2 = block.xyxy
            # Ensure crop is valid
            if x2 <= x1 or y2 <= y1:
                block.original_text = ""
                continue
                
            crop = image.crop((x1, y1, x2, y2))
            
            # OCR
            text = self.ocr.recognize(crop)
            block.original_text = text
            block.text = text # For compatibility
            
            logger.debug(f"Bubble {i} OCR: {text}")

        # 3.1 Batch Translate
        # Collect all valid texts
        texts_to_translate = []
        blocks_to_translate = []
        
        for block in det_result.blocks:
            # Check if block has text (from OCR step above)
            if hasattr(block, 'original_text') and block.original_text.strip():
                texts_to_translate.append(block.original_text)
                blocks_to_translate.append(block)
            else:
                # Initialize empty for safety
                block.translated_text = ""
                block.translation_successful = False

        if texts_to_translate:
            logger.info(f"Batch translating {len(texts_to_translate)} bubbles...")
            try:
                translated_results = translate_batch_text(
                    texts_to_translate,
                    target_language=target_language,
                    model_provider=translator_key,
                    api_key=api_key,
                    model_name=model_name,
                    custom_base_url=config.base_url,
                    rpm_limit_translation=config.rpm_limit,
                    max_retries=config.max_retries
                )
                
                # Assign back to blocks
                for i, block in enumerate(blocks_to_translate):
                    trans_text = translated_results[i]
                    block.translated_text = trans_text
                    block.translation_successful = "翻译失败" not in trans_text and bool(trans_text.strip())
                    logger.debug(f"Bubble Trans: {trans_text[:20]}...")
                    
            except Exception as e:
                logger.error(f"Batch translation failed: {e}")
                # Fallback? Or just mark failed
                for block in blocks_to_translate:
                    block.translated_text = "翻译失败"
                    block.translation_successful = False
        else:
             logger.info("No text to translate.")

        # 4. Inpaint
        logger.info("Inpainting...")
        bubble_coords = [block.xyxy for block in det_result.blocks]
        bubble_polygons = [block.polygon for block in det_result.blocks]

        inpaint_method = 'lama' if config.use_lama else 'solid'
        
        inpainted_image, clean_bg = inpaint_bubbles(
            image, 
            bubble_coords, 
            method=inpaint_method,
            fill_color='auto',
            bubble_polygons=bubble_polygons,
            precise_mask=det_result.mask,
            mask_dilate_size=config.mask_dilate_size,
            mask_box_expand_ratio=config.mask_box_expand_ratio
        )
        
        # 5. Render
        logger.info("Rendering...")
        final_image = render_bubbles_unified(inpainted_image, det_result.blocks)
        
        # 6. Save
        logger.info(f"Saving to {output_path}")
        final_image.save(output_path)
        return True
