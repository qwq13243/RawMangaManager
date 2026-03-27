import os
import cv2
import numpy as np
import torch
import einops
from PIL import Image

from core.saber.dbnet.model import TextDetection
from core.saber.dbnet.postprocess import SegDetectorRepresenter
from core.saber.dbnet.imgproc import resize_aspect_ratio, adjustResultCoordinates
from core.saber.data_types import TextLine, DetectionResult, TextBlock
from core.saber.utils import resource_path
from core.saber.detector_utils.textline_merge import merge_textlines
from core.saber.detector_utils.smart_sort import sort_blocks_by_reading_order

# Constants
DEFAULT_MODEL_PATH = 'models/default/detect-20241225.ckpt'
DEFAULT_DETECT_SIZE = 1536
DEFAULT_TEXT_THRESHOLD = 0.5
DEFAULT_BOX_THRESHOLD = 0.7
DEFAULT_UNCLIP_RATIO = 2.2

class SaberDetector:
    def __init__(self, device=None):
        if device is None:
            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        else:
            self.device = device
            
        self.model = None
        self.seg_rep = None
        self.model_path = DEFAULT_MODEL_PATH
        
    def load_model(self):
        if self.model is not None:
            return

        abs_path = resource_path(self.model_path)
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"Model not found at {abs_path}. Please place it in models/default/")

        self.model = TextDetection(pretrained=False)
        sd = torch.load(abs_path, map_location='cpu')
        self.model.load_state_dict(sd['model'] if 'model' in sd else sd)
        self.model.eval()
        self.model.to(self.device)
        
        self.seg_rep = SegDetectorRepresenter(
            thresh=DEFAULT_TEXT_THRESHOLD,
            box_thresh=DEFAULT_BOX_THRESHOLD,
            unclip_ratio=DEFAULT_UNCLIP_RATIO
        )

    def detect(self, image_input, expand_ratio=0, merge_lines=True):
        """
        Detect text regions in image.
        image_input: PIL Image or path str or numpy array (BGR or RGB)
        """
        self.load_model()
        
        # Standardize to BGR numpy array for processing
        if isinstance(image_input, str):
            image = cv2.imread(image_input) # BGR
            if image is None:
                raise ValueError(f"Failed to load image: {image_input}")
            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB) # Keep RGB copy for sorting/merging if needed
        elif isinstance(image_input, Image.Image):
            image_rgb = np.array(image_input.convert('RGB'))
            image = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
        elif isinstance(image_input, np.ndarray):
            # Check channels
            if len(image_input.shape) == 2: # Gray
                image = cv2.cvtColor(image_input, cv2.COLOR_GRAY2BGR)
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            elif image_input.shape[2] == 4: # BGRA
                image = cv2.cvtColor(image_input, cv2.COLOR_BGRA2BGR)
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            else:
                # Assume BGR if coming from cv2 usage in this project
                image = image_input 
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        else:
            raise ValueError("Unsupported image type")
            
        im_h, im_w = image.shape[:2]
        
        # 1. Bilateral Filter (on BGR image)
        image_filtered = cv2.bilateralFilter(image, 17, 80, 80)
        
        # 2. Resize
        img_resized, ratio, _, pad_w, pad_h = resize_aspect_ratio(
            image_filtered,
            DEFAULT_DETECT_SIZE,
            cv2.INTER_LINEAR,
            mag_ratio=1
        )
        img_resized_h, img_resized_w = img_resized.shape[:2]
        ratio_h = ratio_w = 1 / ratio
        
        # 3. To Tensor
        batch = einops.rearrange(
            img_resized.astype(np.float32) / 127.5 - 1.0,
            'h w c -> 1 c h w'
        )
        batch = torch.from_numpy(batch).to(self.device)
        
        # 4. Inference
        with torch.no_grad():
            db_out, mask_out = self.model(batch)
            db_out = db_out.sigmoid().cpu().numpy()
            mask_out = mask_out.cpu().numpy()
            
        # 5. Postprocess
        mask_squeezed = mask_out[0, 0, :, :]
        boxes, scores = self.seg_rep(
            None, db_out,
            height=img_resized_h,
            width=img_resized_w
        )
        boxes, scores = boxes[0], scores[0]
        
        textlines = []
        if boxes.size > 0:
            idx = boxes.reshape(boxes.shape[0], -1).sum(axis=1) > 0
            polys = boxes[idx].astype(np.float64)
            valid_scores = scores[idx]
            
            polys = adjustResultCoordinates(polys, ratio_w, ratio_h, ratio_net=1)
            polys = polys.astype(np.int32)
            
            for pts, score in zip(polys, valid_scores):
                if pts.shape[0] == 4:
                    textline = TextLine(pts=pts, confidence=float(score))
                    if textline.area > 16:
                        textlines.append(textline)
        
        # 6. Process Mask
        pad_h_half = pad_h // 2
        pad_w_half = pad_w // 2
        
        mask_cropped = mask_squeezed
        if pad_h_half > 0:
            mask_cropped = mask_cropped[:-pad_h_half, :]
        if pad_w_half > 0:
            mask_cropped = mask_cropped[:, :-pad_w_half]
            
        raw_mask = cv2.resize(mask_cropped, (im_w, im_h), interpolation=cv2.INTER_LINEAR)
        raw_mask = np.clip(raw_mask * 255, 0, 255).astype(np.uint8)
        
        # 7. Merge and Sort
        if merge_lines:
            blocks = merge_textlines(textlines, im_w, im_h)
        else:
            blocks = [TextBlock(lines=[line]) for line in textlines]
            
        # Sort blocks
        blocks = sort_blocks_by_reading_order(blocks, img=image_rgb)
        
        return DetectionResult(blocks=blocks, mask=raw_mask, raw_lines=textlines)

def expand_coordinates(coords, image_width, image_height, expand_ratio=0, 
                       expand_top=0, expand_bottom=0, expand_left=0, expand_right=0):
    """
    Expand coordinates by ratio (global or directional).
    Ratios are in percentage (0-100).
    """
    if expand_ratio == 0 and expand_top == 0 and expand_bottom == 0 and expand_left == 0 and expand_right == 0:
        return coords
        
    expanded = []
    for x1, y1, x2, y2 in coords:
        width = x2 - x1
        height = y2 - y1
        
        if width <= 0 or height <= 0:
            expanded.append((x1, y1, x2, y2))
            continue
            
        # Global expansion
        delta_w = int(width * expand_ratio / 100 / 2) # Divide by 2 because it's applied to both sides
        delta_h = int(height * expand_ratio / 100 / 2)
        
        # Directional expansion
        d_top = int(height * expand_top / 100)
        d_bottom = int(height * expand_bottom / 100)
        d_left = int(width * expand_left / 100)
        d_right = int(width * expand_right / 100)
        
        new_x1 = max(0, x1 - delta_w - d_left)
        new_y1 = max(0, y1 - delta_h - d_top)
        new_x2 = min(image_width, x2 + delta_w + d_right)
        new_y2 = min(image_height, y2 + delta_h + d_bottom)
        
        expanded.append((new_x1, new_y1, new_x2, new_y2))
    return expanded

# Singleton instance
detector = SaberDetector()
