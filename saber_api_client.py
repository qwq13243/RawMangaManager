from PySide6.QtCore import QThread, Signal
import os
import logging
from core.saber.pipeline import SaberPipeline

logger = logging.getLogger("TranslationWorker")

class TranslationWorker(QThread):
    log_signal = Signal(str)
    chapter_finished = Signal(int) # cap_id
    finished_all = Signal()
    
    def __init__(self, tasks):
        super().__init__()
        self.tasks = tasks
        self.is_cancelled = False
        self.pipeline = None

    def run(self):
        # Initialize pipeline in the thread to ensure thread safety with Qt/CUDA if applicable
        if self.pipeline is None:
            self.pipeline = SaberPipeline()
            
        total = len(self.tasks)
        for i, task in enumerate(self.tasks):
            if self.is_cancelled:
                self.log_signal.emit("翻译任务已取消")
                break
                
            cap_id = task['cap_id']
            base_dir = task['base_dir']
            ch_title = task['ch_title']
            glossary = task.get('glossary', '') # 获取术语表
            
            chapter_dir = os.path.join(base_dir, ch_title)
            trans_dir = os.path.join(chapter_dir, f"Trans_{ch_title}")
            os.makedirs(trans_dir, exist_ok=True)
            
            if not os.path.exists(chapter_dir):
                self.log_signal.emit(f"错误：章节目录不存在 {chapter_dir}")
                continue

            try:
                # Find images recursively to handle potential subdirectories (like double-nested chapters)
                valid_exts = ('.png', '.jpg', '.jpeg', '.webp')
                images = []
                trans_dir_abs = os.path.abspath(trans_dir)
                
                for root, dirs, files in os.walk(chapter_dir):
                    # Skip the translation directory
                    if os.path.abspath(root).startswith(trans_dir_abs):
                        continue
                        
                    for f in files:
                        if f.lower().endswith(valid_exts):
                            images.append(os.path.join(root, f))
                
                # Sort by numeric value in filename
                def sort_key(x):
                    import re
                    # Extract all numbers from filename
                    nums = re.findall(r'\d+', os.path.basename(x))
                    if nums:
                        return int(nums[-1]) # Use last number (usually page number)
                    return x
                
                images.sort(key=sort_key)
                
                total_imgs = len(images)
                self.log_signal.emit(f"正在翻译: {ch_title} ({i+1}/{total}) - 共 {total_imgs} 页")
                
                for img_idx, src_path in enumerate(images):
                    if self.is_cancelled: 
                        break
                    
                    img_name = os.path.basename(src_path)
                    dst_path = os.path.join(trans_dir, img_name)
                    
                    # Check if already translated (resume capability)
                    if os.path.exists(dst_path):
                        # self.log_signal.emit(f"  - 跳过: {img_name} (已存在)")
                        continue
                    
                    try:
                        msg = f"  - [{img_idx+1}/{total_imgs}] 处理中: {img_name}"
                        if not self.is_cancelled:
                            self.log_signal.emit(msg)
                        
                        success = self.pipeline.process_image(src_path, dst_path, glossary=glossary)
                        if success:
                            msg = f"  - 完成: {img_name}"
                            if not self.is_cancelled:
                                self.log_signal.emit(msg)
                        else:
                            msg = f"  - 失败: {img_name}"
                            if not self.is_cancelled:
                                self.log_signal.emit(msg)
                    except Exception as e:
                        msg = f"  - 错误: {img_name} - {e}"
                        if not self.is_cancelled:
                            self.log_signal.emit(msg)
                        logger.error(f"Translation error for {img_name}: {e}", exc_info=True)
                
                if not self.is_cancelled:
                    self.chapter_finished.emit(cap_id)
                    
            except Exception as e:
                logger.error(f"Chapter failed {ch_title}: {e}", exc_info=True)
                if not self.is_cancelled:
                    self.log_signal.emit(f"章节处理错误: {e}")
                
        self.finished_all.emit()

    def cancel(self):
        self.is_cancelled = True
