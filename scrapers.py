# scrapers.py
import os
import re
import time
import zipfile
import urllib.parse
from pathlib import Path
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed # [新增] 引入线程池

# 需要额外安装这两个库: pip install requests pillow
import requests
from PIL import Image
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def clean_filename(name):
    """清理文件名中的非法字符，并处理 Windows 路径限制"""
    if not name: return "Unnamed"
    name = urllib.parse.unquote(name)
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = name.strip('. ')
    return name if name else "Unnamed_Folder"

class BaseScraper:
    source_name = "Base"
    
    def search(self, page, keyword): 
        raise NotImplementedError
        
    def get_chapters(self, page, manga_url): 
        raise NotImplementedError

    def download_chapter(self, context, manga_save_path, chapter_title, chapter_url, progress_callback=None, cancel_check=None, dl_url=None):
        """
        统一接口：由于不同站点下载方式不同（逐图下载 vs ZIP下载），
        具体逻辑下放给各个子类实现，最终需返回 (是否成功, 本地存储路径)
        """
        raise NotImplementedError
    
    def download_manga_cover(self, context, manga_url, save_dir):
        """独立功能：获取并在指定目录保存漫画封面，统一命名为 cover"""
        raise NotImplementedError

# ---------------- KlManga ----------------
class KlMangaScraper(BaseScraper):
    source_name = "KlManga"
    
    def search(self, page, keyword):
        page.goto(f"https://klmanga.talk/?s={keyword}")
        page.wait_for_timeout(2000)
        results = page.locator('a.thumb.d-block.mb-3').all()
        mangas = []
        for el in results:
            href = el.get_attribute('href')
            alt_text = el.locator('img').get_attribute('alt')
            
            if not alt_text or alt_text == '...' or alt_text.strip() == '':
                raw_title = href.strip('/').split('/')[-1]
                title = clean_filename(raw_title).replace('_raw_free', '').replace('-raw-free', '')
            else:
                title = clean_filename(alt_text)
                
            mangas.append({"title": title, "url": href, "source": self.source_name})
        return mangas

    def get_chapters(self, page, manga_url):
            # 【修正1】: 增加 wait_until="domcontentloaded"，并在选定元素后加入硬等待
            page.goto(manga_url, wait_until="domcontentloaded")
            page.wait_for_selector('.chapter-box', timeout=10000)
            
            # 【修正2】: 强制等待 3 秒，让前端 JS 充分渲染出所有章节再抓取
            time.sleep(3) 
            
            chapter_elements = page.locator('.chapter-box a.d-inline-flex').all()
            return [{"title": clean_filename(el.inner_text().strip()), "url": el.get_attribute('href'), "source": self.source_name} for el in chapter_elements]

    def _download_and_convert_image(self, img_url, save_path, referer, user_agent):
        headers = {
            "Referer": referer,
            "User-Agent": user_agent,
            "Origin": urllib.parse.urlparse(referer).scheme + "://" + urllib.parse.urlparse(referer).netloc
        }
        try:
            response = requests.get(img_url, headers=headers, timeout=15, verify=False, stream=True)
            response.raise_for_status()
            image_data = BytesIO(response.content)
            image = Image.open(image_data)
            
            if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
                background = Image.new('RGB', image.size, (255, 255, 255))
                if image.mode == 'P':
                    image = image.convert('RGBA')
                if image.mode == 'RGBA':
                    background.paste(image, mask=image.split()[3])
                else:
                    background.paste(image, mask=image.split()[-1])
                image = background
            else:
                image = image.convert('RGB')
                
            image.save(save_path, 'JPEG', quality=95)
            return True
        except Exception as e:
            print(f"KlManga图片处理失败: {e}")
            return False

    def download_chapter(self, context, manga_save_path, chapter_title, chapter_url, progress_callback=None, cancel_check=None, dl_url=None):
        page = context.new_page()
        user_agent = page.evaluate("navigator.userAgent")
        chapter_dir_name = clean_filename(chapter_title)
        # [修改] 增加一级同名目录，使生肉文件夹与 Trans 文件夹同级
        chapter_dir = Path(manga_save_path) / chapter_dir_name / chapter_dir_name
        chapter_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            if progress_callback: progress_callback(0, 100, f"正在加载页面: {chapter_title}")
            page.goto(chapter_url)
            
            try:
                page.locator('.color-btn.go-open-popup').click(timeout=5000)
            except:
                pass

            if progress_callback: progress_callback(10, 100, "等待图片加载...")
            try:
                page.wait_for_selector('.z_content img', timeout=15000)
                time.sleep(2)
                page.wait_for_function("""
                    () => {
                        const imgs = document.querySelectorAll('.z_content img');
                        if (imgs.length === 0) return false;
                        let loadedCount = 0;
                        for (let img of imgs) {
                            if (img.complete && img.naturalHeight !== 0) loadedCount++;
                        }
                        return loadedCount === imgs.length;
                    }
                """, timeout=20000)
            except:
                pass

            img_urls = []
            max_retries_loop = 3
            downloaded_count = 0

            for attempt in range(max_retries_loop):
                current_img_elements = page.locator('.z_content img').all()
                current_urls = []
                for img in current_img_elements:
                    src = img.get_attribute('src')
                    if src and src not in current_urls:
                        current_urls.append(src)
                
                new_urls = [url for url in current_urls if url not in img_urls]
                if not new_urls: break
                
                start_index = len(img_urls)
                img_urls.extend(new_urls)
                total_imgs = len(img_urls)
                
                max_threads = 5  # 可同时下载的图片数量
                
                with ThreadPoolExecutor(max_workers=max_threads) as executor:
                    futures = {}
                    for local_idx, img_url in enumerate(new_urls):
                        real_index = start_index + local_idx + 1
                        file_name = f"{str(real_index).zfill(3)}.jpg"
                        save_path = chapter_dir / file_name
                        
                        # 提交到线程池
                        future = executor.submit(self._download_and_convert_image, img_url, str(save_path), chapter_url, user_agent)
                        futures[future] = real_index
                        
                    for future in as_completed(futures):
                        # 如果用户点击了取消，立刻停止阻塞，退出循环
                        if cancel_check and cancel_check():
                            break
                            
                        success = future.result()
                        if success:
                            downloaded_count += 1
                        
                        if progress_callback: 
                            progress_callback(downloaded_count, total_imgs, f"图片下载并发中: {downloaded_count}/{total_imgs}")
                
                # 若已取消，直接向外抛出失败以中断后续重试
                if cancel_check and cancel_check():
                    return False, None
                # --- [重写结束] ---
            
            if downloaded_count > 0:
                if progress_callback: progress_callback(100, 100, f"完成: 下载 {downloaded_count} 张图")
                return True, str(chapter_dir)
            return False, None

        except Exception as e:
            print(f"KlManga章节下载错误: {e}")
            if progress_callback: progress_callback(0, 0, f"错误: {str(e)}")
            return False, None
        finally:
            page.close()

    def download_manga_cover(self, context, manga_url, save_dir):
        page = context.new_page()
        try:
            page.goto(manga_url, wait_until="domcontentloaded")
            img_locator = page.locator('.main-thumb img')
            img_locator.wait_for(state="visible", timeout=10000)
            img_url = img_locator.get_attribute('src')
            if not img_url: return None
            
            ext = os.path.splitext(urllib.parse.urlparse(img_url).path)[1] or '.jpg'
            save_path = os.path.join(save_dir, f"cover{ext}")
            
            user_agent = page.evaluate("navigator.userAgent")
            success = self._download_and_convert_image(img_url, save_path, manga_url, user_agent)
            return str(save_path) if success else None
        except Exception as e:
            print(f"KlManga封面获取异常: {e}")
            return None
        finally:
            page.close()

# ---------------- NicoManga ----------------
class NicoMangaScraper(BaseScraper):
    source_name = "NicoManga"
    
    def search(self, page, keyword):
        search_url = f"https://nicomanga.com/manga-list.html?n={urllib.parse.quote(keyword)}"
        page.goto(search_url, timeout=60000)
        if page.locator('text="No Manga Found"').is_visible(): return []
        
        result_elements = page.locator('.manga-card a.manga-title').all()
        mangas = []
        for el in result_elements:
            href = "https://nicomanga.com/" + el.get_attribute('href').lstrip('/')
            raw_title = el.get_attribute('title') or el.inner_text().strip()
            mangas.append({"title": clean_filename(raw_title), "url": href, "source": self.source_name})
        return mangas

    def get_chapters(self, page, manga_url):
        # 【修正1】: 增加 wait_until="domcontentloaded"
        page.goto(manga_url, timeout=60000, wait_until="domcontentloaded")
        try:
            page.wait_for_selector('.chapter-grid-container', timeout=30000)
            
            # 【修正2】: 先稍微等待让展开按钮真正被加载进DOM
            time.sleep(2)
            show_all_btn = page.locator('.show-all-chapters-btn')
            
            # 【修正3】: 更稳定的按钮检测与点击，避免 is_visible 瞬发失效
            if show_all_btn.count() > 0:
                try:
                    show_all_btn.wait_for(state="visible", timeout=3000)
                    show_all_btn.click()
                    time.sleep(2)  # 点击后给予展开所有DOM的时间
                except:
                    pass
            chapter_elements = page.locator('a.chapter-grid-item').all()
        except:
            return []
            
        chapters = []
        for el in chapter_elements:
            href = "https://nicomanga.com/" + el.get_attribute('href').lstrip('/')
            title = el.get_attribute('title') or el.inner_text().strip()
            chapters.append({"title": clean_filename(title), "url": href, "source": self.source_name})
        return chapters

    def download_chapter(self, context, manga_save_path, chapter_title, chapter_url, progress_callback=None, cancel_check=None, dl_url=None):
        page = context.new_page()
        chapter_dir_name = clean_filename(chapter_title)
        # [修改] 增加一级同名目录，使生肉文件夹与 Trans 文件夹同级
        chapter_dir = Path(manga_save_path) / chapter_dir_name / chapter_dir_name
        chapter_dir.mkdir(parents=True, exist_ok=True)
        dl_page = None
        
        try:
            if progress_callback: progress_callback(10, 100, f"打开章节界面...")
            page.goto(chapter_url, timeout=60000)
            
            # 处理 R-18 弹窗
            try:
                page.locator('#age_warning_modal').wait_for(state="visible", timeout=4000)
                page.reload(timeout=60000)
                time.sleep(2)
            except: pass

            page.locator('#download_chapter_btn').wait_for(state="visible", timeout=30000)
            with context.expect_page() as new_page_info:
                page.locator('#download_chapter_btn').click()
            dl_page = new_page_info.value
            
            if progress_callback: progress_callback(30, 100, "等待获取下载权限(倒计时)...")
            dl_page.wait_for_selector('#downloadBtn.ready', timeout=40000)
            
            if progress_callback: progress_callback(60, 100, "正在下载ZIP...")
            with dl_page.expect_download(timeout=120000) as download_info:
                dl_page.locator('#downloadBtn').click()
            download = download_info.value
            
            zip_path = chapter_dir / f"{chapter_dir_name}.zip"
            download.save_as(str(zip_path))
            
            if progress_callback: progress_callback(85, 100, "正在解压清理文件...")
            extract_success = False
            for attempt in range(3):
                try:
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.extractall(chapter_dir)
                    extract_success = True
                    break
                except Exception as zip_err:
                    time.sleep(2)
            
            if extract_success:
                try: os.remove(zip_path)
                except: pass
                if progress_callback: progress_callback(100, 100, "完成")
                return True, str(chapter_dir)
            return False, None

        except Exception as e:
            print(f"NicoManga下载阶段发生异常: {e}")
            if progress_callback: progress_callback(0, 0, f"错误: {str(e)}")
            return False, None
        finally:
            if dl_page and not dl_page.is_closed(): dl_page.close()
            if not page.is_closed(): page.close()

    def download_manga_cover(self, context, manga_url, save_dir):
        page = context.new_page()
        try:
            page.goto(manga_url, wait_until="domcontentloaded")
            img_locator = page.locator('.manga-cover-wrapper img')
            img_locator.wait_for(state="visible", timeout=10000)
            img_url = img_locator.get_attribute('src')
            if not img_url: return None
            
            ext = os.path.splitext(urllib.parse.urlparse(img_url).path)[1] or '.jpg'
            if ext.lower() not in['.jpg', '.jpeg', '.png', '.webp']: ext = '.jpg'
            save_path = os.path.join(save_dir, f"cover{ext}")
            
            headers = {"User-Agent": page.evaluate("navigator.userAgent"), "Referer": manga_url}
            resp = requests.get(img_url, headers=headers, timeout=15, verify=False)
            if resp.status_code == 200:
                with open(save_path, 'wb') as f:
                    f.write(resp.content)
                return str(save_path)
            return None
        except Exception as e:
            print(f"NicoManga封面获取异常: {e}")
            return None
        finally:
            page.close()

# ---------------- Rawkuma ----------------
class RawkumaScraper(BaseScraper):
    source_name = "Rawkuma"
    
    def search(self, page, keyword):
        search_url = f"https://rawkuma.net/library/?search_term={urllib.parse.quote(keyword)}"
        page.goto(search_url, timeout=60000)
        try:
            page.wait_for_selector('#search-results', timeout=30000)
        except: 
            return[]
        
        result_elements = page.locator('#search-results a.text-base.font-medium').all()
        return[{"title": clean_filename(el.inner_text().strip()), "url": el.get_attribute('href'), "source": self.source_name} for el in result_elements]

    def get_chapters(self, page, manga_url):
        page.goto(manga_url, timeout=60000, wait_until="domcontentloaded")
        try:
            chapters_tab = page.locator('button[data-key="chapters"]')
            chapters_tab.wait_for(state="visible", timeout=15000)
            chapters_tab.click()
            time.sleep(2)
            page.wait_for_selector('#chapter-list div[data-chapter-number]', timeout=30000)
        except:
            return[]
            
        chapter_rows = page.locator('#chapter-list div[data-chapter-number]').all()
        chapters =[]
        for el in chapter_rows:
            try:
                title = clean_filename(el.locator('.font-medium').inner_text().strip())
                # 获取阅读页链接作为主要 URL (应对 fallback)
                read_url_locator = el.locator('a').first
                read_url = read_url_locator.get_attribute('href') if read_url_locator.count() > 0 else ""
                
                # 获取 GDrive 直链存入 dl_url
                dl_url_locator = el.locator('a[href*="drive.google.com"]')
                dl_url = dl_url_locator.get_attribute('href') if dl_url_locator.count() > 0 else ""
                
                if title and read_url:
                    chapters.append({"title": title, "url": read_url, "dl_url": dl_url, "source": self.source_name})
            except: pass
        return chapters

    def _download_and_convert_image(self, img_url, save_path, referer, user_agent):
        headers = {
            "Referer": referer,
            "User-Agent": user_agent,
            "Origin": urllib.parse.urlparse(referer).scheme + "://" + urllib.parse.urlparse(referer).netloc
        }
        try:
            response = requests.get(img_url, headers=headers, timeout=15, verify=False, stream=True)
            response.raise_for_status()
            image_data = BytesIO(response.content)
            image = Image.open(image_data)
            
            if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
                background = Image.new('RGB', image.size, (255, 255, 255))
                if image.mode == 'P':
                    image = image.convert('RGBA')
                if image.mode == 'RGBA':
                    background.paste(image, mask=image.split()[3])
                else:
                    background.paste(image, mask=image.split()[-1])
                image = background
            else:
                image = image.convert('RGB')
                
            image.save(save_path, 'JPEG', quality=95)
            return True
        except Exception as e:
            print(f"Rawkuma图片处理失败: {e}")
            return False

    def download_chapter(self, context, manga_save_path, chapter_title, chapter_url, progress_callback=None, cancel_check=None, dl_url=None):
        page = context.new_page()
        user_agent = page.evaluate("navigator.userAgent")
        chapter_dir_name = clean_filename(chapter_title)
        # [修改] 增加一级同名目录，使生肉文件夹与 Trans 文件夹同级
        chapter_dir = Path(manga_save_path) / chapter_dir_name / chapter_dir_name
        chapter_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            target_dl_url = dl_url if dl_url else chapter_url
            
            # --- 阶段 1: 尝试 Google Drive ZIP 下载 ---
            if "drive.google.com" in target_dl_url:
                if progress_callback: progress_callback(30, 100, f"尝试 Google Drive 下载...")
                try:
                    with page.expect_download(timeout=5000) as download_info:
                        page.evaluate(f"window.location.href = '{target_dl_url}';")
                    
                    download = download_info.value
                    zip_path = chapter_dir / f"{chapter_dir_name}.zip"
                    download.save_as(str(zip_path))
                    
                    # 检查是否为有效 ZIP，规避超限时下载到 HTML 错误页的情况
                    if zipfile.is_zipfile(zip_path):
                        if progress_callback: progress_callback(85, 100, "解压并清理 ZIP...")
                        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                            zip_ref.extractall(chapter_dir)
                        try: os.remove(zip_path)
                        except: pass
                        if progress_callback: progress_callback(100, 100, "完成")
                        return True, str(chapter_dir)
                    else:
                        try: os.remove(zip_path)
                        except: pass
                        raise Exception("下载的文件非ZIP格式(通常为达到下载配额)")
                except Exception as e:
                    if progress_callback: progress_callback(40, 100, "Google Drive 下载限额或失败，切换至网页图片抓取...")
            
            # --- 阶段 2: 降级回网页源图片抓取 ---
            target_read_url = chapter_url if "rawkuma.net" in chapter_url else None
            if not target_read_url:
                if progress_callback: progress_callback(0, 0, "缺少该章节阅读页 URL，无法切换下载方式")
                return False, None
                
            if progress_callback: progress_callback(50, 100, f"正在加载章节阅读页...")
            page.goto(target_read_url, timeout=60000, wait_until="domcontentloaded")
            
            try:
                page.wait_for_selector('section[data-image-data] img', timeout=15000)
            except: pass
            time.sleep(2)
            
            img_elements = page.locator('section[data-image-data] img').all()
            if not img_elements:
                img_elements = page.locator('section.w-full.flex-col img').all()
                
            img_urls =[]
            for img in img_elements:
                src = img.get_attribute('src')
                if src and src not in img_urls:
                    img_urls.append(src)
            
            if not img_urls:
                if progress_callback: progress_callback(0, 0, "网页解析失败，未能获取到任何图片")
                return False, None

            total_imgs = len(img_urls)
            downloaded_count = 0
            
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {}
                for idx, img_url in enumerate(img_urls):
                    file_name = f"{str(idx + 1).zfill(3)}.jpg"
                    save_path = chapter_dir / file_name
                    future = executor.submit(self._download_and_convert_image, img_url, str(save_path), target_read_url, user_agent)
                    futures[future] = idx
                    
                for future in as_completed(futures):
                    if cancel_check and cancel_check():
                        break
                    if future.result():
                        downloaded_count += 1
                    if progress_callback: 
                        progress_callback(downloaded_count, total_imgs, f"并发下载网页图片: {downloaded_count}/{total_imgs}")

            if cancel_check and cancel_check():
                return False, None

            if downloaded_count > 0:
                if progress_callback: progress_callback(100, 100, f"完成: 下载 {downloaded_count} 张图")
                return True, str(chapter_dir)
            return False, None

        except Exception as e:
            print(f"Rawkuma下载异常: {e}")
            if progress_callback: progress_callback(0, 0, f"错误: {str(e)}")
            return False, None
        finally:
            if not page.is_closed(): page.close()

    def download_manga_cover(self, context, manga_url, save_dir):
        page = context.new_page()
        try:
            page.goto(manga_url, wait_until="domcontentloaded")
            img_locator = page.locator('img.wp-post-image').first
            img_locator.wait_for(state="visible", timeout=10000)
            img_url = img_locator.get_attribute('src')
            if not img_url: return None
            
            ext = os.path.splitext(urllib.parse.urlparse(img_url).path)[1] or '.jpg'
            if ext.lower() not in['.jpg', '.jpeg', '.png', '.webp']: ext = '.jpg'
            save_path = os.path.join(save_dir, f"cover{ext}")
            
            headers = {"User-Agent": page.evaluate("navigator.userAgent"), "Referer": manga_url}
            resp = requests.get(img_url, headers=headers, timeout=15, verify=False)
            if resp.status_code == 200:
                with open(save_path, 'wb') as f:
                    f.write(resp.content)
                return str(save_path)
            return None
        except Exception as e:
            print(f"Rawkuma封面获取异常: {e}")
            return None
        finally:
            page.close()
