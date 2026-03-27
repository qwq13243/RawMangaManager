import os
import re
from PySide6.QtCore import QThread, Signal, Qt
from core.database import db
from core.scrapers_registry import SCRAPERS
from core.utils import split_image_in_place
from fast_scrapers import clean_filename, RawkumaFastScraper

class WorkerThread(QThread):
    finished_signal = Signal(object)
    error_signal = Signal(str)
    progress_signal = Signal(str)

    def __init__(self, task_type, kwargs):
        super().__init__()
        self.task_type = task_type
        self.kwargs = kwargs
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            if self.task_type == "search":
                source_key = self.kwargs.get("source", "rawkuma")
                scraper = SCRAPERS.get(source_key)
                if not scraper:
                    self.error_signal.emit(f"未知的源: {source_key}")
                    return
                
                self.progress_signal.emit(f"正在 {source_key} 搜索: {self.kwargs['keyword']}...")
                res = scraper.search(self.kwargs["keyword"])
                
                for item in res:
                    if self.is_cancelled:
                        break
                    try:
                        chapters = scraper.get_chapters(item["url"])
                        item["chapters_raw"] = chapters
                        item["chapter_count"] = len(chapters)
                        
                        nums =[]
                        for cap in chapters:
                            match = re.search(r'(\d+(?:\.\d+)?)', cap.get("title", ""))
                            if match: nums.append(float(match.group(1)))
                        
                        if nums:
                            min_num = int(min(nums)) if min(nums).is_integer() else min(nums)
                            max_num = int(max(nums)) if max(nums).is_integer() else max(nums)
                            item["chapter_range"] = f"{min_num}-{max_num}"
                        else:
                            item["chapter_range"] = f"共{len(chapters)}章"
                    except Exception:
                        item["chapters_raw"] = []
                        item["chapter_count"] = 0
                        item["chapter_range"] = "无章节"
                        
                    self.finished_signal.emit({"type": "search_item", "item": item, "source": source_key})
                
                self.finished_signal.emit({"type": "search_done", "source": source_key})
                
            elif self.task_type == "download_cover":
                source_key = self.kwargs.get("source")
                manga_url = self.kwargs.get("url")
                save_dir = self.kwargs.get("save_dir")
                manga_id = self.kwargs.get("manga_id")
                
                os.makedirs(save_dir, exist_ok=True)
                scraper = SCRAPERS.get(source_key)
                if scraper:
                    cover_path = scraper.download_manga_cover(manga_url, save_dir)
                    if cover_path and os.path.exists(cover_path):
                        if source_key == "klmanga":
                            from PySide6.QtGui import QImage
                            img = QImage(cover_path)
                            if not img.isNull():
                                target_width = img.width()
                                target_height = int(target_width * 1.414)
                                scaled_img = img.scaled(target_width, target_height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
                                scaled_img.save(cover_path)

                        db.update_manga_cover(manga_id, cover_path)
                        self.finished_signal.emit({"type": "cover_downloaded", "manga_id": manga_id})
            
            elif self.task_type == "download":
                chapter_list = self.kwargs["chapters"]
                
                for idx, cap_item in enumerate(chapter_list):
                    if self.is_cancelled:
                        self.progress_signal.emit("[中断] 下载任务已被用户取消")
                        break
                    
                    if isinstance(cap_item, tuple) and len(cap_item) == 3:
                        cap, base_dir, manga_title = cap_item
                        prefix = f"[{manga_title}] "
                    else:
                        cap = cap_item
                        base_dir = self.kwargs["base_dir"]
                        prefix = ""

                    cap_id = cap[0]
                    ch_str = cap[1]
                    orig_title = cap[2]
                    chapter_url = cap[3]
                    is_dl = cap[4]
                    local_path = cap[5]
                    source_site = cap[6]
                    is_trans = cap[7]
                    read_status = cap[8]
                    
                    cap_record = db.get_chapter_by_id(cap_id)
                    dl_url = cap_record['dl_url'] if cap_record else ""
                    
                    if is_dl == 1 and local_path and os.path.exists(local_path):
                        self.progress_signal.emit(f"{prefix}[跳过] {ch_str} 已存在")
                        continue
                    
                    if not chapter_url:
                        self.progress_signal.emit(f"{prefix}[错误] {ch_str} 无有效链接")
                        continue

                    scraper = SCRAPERS.get(source_site, RawkumaFastScraper())
                    safe_title = clean_filename(orig_title)
                    # 修复双重目录问题：scraper 内部会再次拼接 safe_title，所以这里只传 base_dir
                    save_dir = base_dir 
                    
                    self.progress_signal.emit(f"{prefix}[开始] {ch_str} - {source_site}")
                    
                    success, saved_path = scraper.download_chapter(
                        manga_save_path=save_dir,
                        chapter_title=safe_title,
                        chapter_url=chapter_url,
                        progress_callback=lambda curr, total, msg: self.progress_signal.emit(f"{prefix}[{ch_str}] {msg}"),
                        cancel_check=lambda: self.is_cancelled,
                        dl_url=dl_url
                    )
                    
                    if success and saved_path:
                        import glob
                        imgs = glob.glob(os.path.join(saved_path, "*.*"))
                        valid_imgs =[f for f in imgs if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
                        if len(valid_imgs) == 1:
                            self.progress_signal.emit(f"[{ch_str}] 检测到单张图，正在进行分割...")
                            split_image_in_place(valid_imgs[0])
                        
                        db.mark_chapter_downloaded(cap_id, saved_path)
                        self.finished_signal.emit({"type": "progress", "id": cap_id})
                        self.progress_signal.emit(f"[成功] {ch_str} 保存至: {saved_path}")

                self.finished_signal.emit({"type": "done"})
            
            elif self.task_type == "get_chapters":
                source_key = self.kwargs.get("source", "rawkuma")
                url = self.kwargs.get("url", "")
                title = self.kwargs.get("title", "")
                if title:
                    self.progress_signal.emit(f"正在检查: {title}")
                scraper = SCRAPERS.get(source_key)
                if not scraper:
                    self.error_signal.emit(f"未知的源: {source_key}")
                    return
                if not url:
                    self.error_signal.emit("缺少漫画链接，无法更新章节")
                    return
                chs = scraper.get_chapters(url)
                self.finished_signal.emit({"type": "chapters", "data": chs, "source": source_key})
        except Exception as e:
            if not self.is_cancelled:
                import traceback
                self.error_signal.emit(f"临界错误: {str(e)}\n{traceback.format_exc()}")
class BatchUpdateWorker(QThread):
    progress_signal = Signal(str)
    finished_signal = Signal()
    refresh_signal = Signal()  # 新增信号

    def __init__(self):
        super().__init__()
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            mangas = db.get_all_manga()
            for m in mangas:
                if self.is_cancelled:
                    break
                
                # is_following check
                if len(m) > 5 and m[5] == 0:
                    continue

                m_id = m[0]
                detail = db.get_manga_detail(m_id)
                url = detail['source_url']
                if not url: continue
                
                source = "rawkuma" 
                if "nicomanga" in url: source = "nicomanga"
                elif "klmanga" in url: source = "klmanga"
                
                title = detail['title_zh'] or detail['title_jp'] or detail['title_romaji']
                self.progress_signal.emit(f"正在检查: {title}")
                
                scraper = SCRAPERS.get(source)
                if scraper:
                    try:
                        chs = scraper.get_chapters(url)
                        if chs:
                            db.save_chapters(m_id, chs, source)
                            latest_text = None
                            try:
                                rows = db.get_chapters(m_id)
                                if rows:
                                    latest = rows[0]
                                    latest_text = latest[1]
                                    if latest[2] and latest[2] != latest[1]:
                                        latest_text = f"{latest[1]} - {latest[2]}"
                            except Exception:
                                latest_text = None

                            if latest_text:
                                self.progress_signal.emit(f"检查完成: {title}，目前最新章节为 {latest_text}")
                            else:
                                self.progress_signal.emit(f"检查完成: {title}，但未获取到最新章节信息")
                            # 逐本完成即刻发送更新信号
                            self.refresh_signal.emit()
                    except:
                        pass
        except Exception as e:
            # 记录错误但不中断
            self.progress_signal.emit(f"更新检查发生错误: {str(e)}")
        self.finished_signal.emit()

class ServerCheckWorker(QThread):
    status_signal = Signal(bool)

    def run(self):
        # We are using local module now, so it is always "alive"
        self.status_signal.emit(True)
