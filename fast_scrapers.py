import html
import os
import re
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import bs4
import gdown
import requests
import urllib3
from PIL import Image

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def clean_filename(name):
    if not name:
        return "Unnamed"
    name = urllib.parse.unquote(str(name))
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = name.strip('. ')
    return name if name else "Unnamed_Folder"


class BaseFastScraper:
    source_name = "Base"
    base_url = ""

    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
        }

    def _abs_url(self, url):
        if not url:
            return ""
        return urllib.parse.urljoin(self.base_url.rstrip('/') + '/', str(url).strip())

    def _request(self, method, url, **kwargs):
        headers = kwargs.pop("headers", None) or self.headers
        timeout = kwargs.pop("timeout", 20)
        return self.session.request(method, url, headers=headers, timeout=timeout, verify=False, **kwargs)

    def _download_and_convert_image(self, img_url, save_path, referer):
        headers = {
            "Referer": referer,
            "User-Agent": self.headers["User-Agent"],
            "Origin": urllib.parse.urlparse(referer).scheme + "://" + urllib.parse.urlparse(referer).netloc,
        }
        response = requests.get(img_url, headers=headers, timeout=20, verify=False, stream=True)
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

    def _download_cover_common(self, cover_url, manga_url, save_dir, stretch_kl=False):
        if not cover_url:
            return None
        os.makedirs(save_dir, exist_ok=True)
        ext = os.path.splitext(urllib.parse.urlparse(cover_url).path)[1] or '.jpg'
        if ext.lower() not in ['.jpg', '.jpeg', '.png', '.webp']:
            ext = '.jpg'
        save_path = os.path.join(save_dir, f"cover{ext}")

        headers = self.headers.copy()
        headers["Referer"] = manga_url
        resp = self._request("GET", cover_url, headers=headers)
        resp.raise_for_status()

        image = Image.open(BytesIO(resp.content))
        if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
            background = Image.new('RGB', image.size, (255, 255, 255))
            if image.mode == 'P':
                image = image.convert('RGBA')
            background.paste(image, mask=image.split()[-1])
            image = background
        else:
            image = image.convert('RGB')

        if stretch_kl:
            target_width = image.width
            target_height = int(target_width * 1.414)
            image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)

        image.save(save_path, 'JPEG', quality=95)
        return save_path

    def _chapter_dir(self, manga_save_path, chapter_title):
        chapter_dir_name = clean_filename(chapter_title)
        chapter_dir = Path(manga_save_path) / chapter_dir_name / chapter_dir_name
        chapter_dir.mkdir(parents=True, exist_ok=True)
        return chapter_dir

    def search(self, keyword):
        raise NotImplementedError

    def get_chapters(self, manga_url):
        raise NotImplementedError

    def download_chapter(self, manga_save_path, chapter_title, chapter_url, progress_callback=None, cancel_check=None, dl_url=None):
        raise NotImplementedError

    def download_manga_cover(self, manga_url, save_dir):
        raise NotImplementedError


class KlMangaFastScraper(BaseFastScraper):
    source_name = "KlManga"

    def __init__(self, base_domain="klmanga.voto"):
        self.base_domain = base_domain.replace('https://', '').replace('http://', '').strip('/')
        self.base_url = f"https://{self.base_domain}"
        super().__init__()

    def _fix_url(self, url):
        if url:
            return re.sub(r'https?://(?:www\.)?klmanga\.[a-z]+', self.base_url, url)
        return url

    def search(self, keyword):
        url = f"{self.base_url}/?s={urllib.parse.quote(keyword)}"
        res = self._request("GET", url)
        res.raise_for_status()
        html_text = res.text
        pattern = r'<a[^>]+href=[\'"]([^\'"]+)[\'"][^>]*class=[\'"][^\'"]*thumb\s+d-block\s+mb-3[^\'"]*[\'"][^>]*>.*?<img[^>]+alt=[\'"]([^\'"]*)[\'"]'
        matches = re.findall(pattern, html_text, re.DOTALL | re.IGNORECASE)

        mangas = []
        for href, alt_text in matches:
            href = self._fix_url(href)
            title = clean_filename(alt_text) if alt_text and alt_text.strip() and alt_text != '...' else clean_filename(href.strip('/').split('/')[-1]).replace('_raw_free', '').replace('-raw-free', '')
            mangas.append({"title": title, "url": href, "source": self.source_name, "chapter_count": 0})

        for item in mangas:
            try:
                chapters = self.get_chapters(item["url"])
                item["chapter_count"] = len(chapters)
            except Exception:
                pass
        return mangas

    def get_chapters_and_cover(self, manga_url):
        manga_url = self._fix_url(manga_url)
        res = self._request("GET", manga_url)
        res.raise_for_status()
        html_text = res.text

        cover_url = None
        cover_match = re.search(r'class=[\'"][^\'"]*main-thumb[^\'"]*[\'"][^>]*>.*?<img[^>]+src=[\'"]([^\'"]+)[\'"]', html_text, re.DOTALL | re.IGNORECASE)
        if cover_match:
            cover_url = self._fix_url(cover_match.group(1))

        chapters = []
        chapter_box_start = html_text.find('chapter-box')
        if chapter_box_start != -1:
            chapter_box_html = html_text[chapter_box_start:chapter_box_start + 50000]
            a_matches = re.findall(r'<a[^>]+href=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</a>', chapter_box_html, re.DOTALL | re.IGNORECASE)
            for href, inner_text in a_matches:
                title_text = re.sub(r'<[^>]+>', '', inner_text).strip()
                title_text = re.sub(r'\s+New$', '', title_text, flags=re.IGNORECASE).strip()
                if title_text and ('chapter' in href.lower() or '第' in title_text or re.search(r'\d+', title_text)):
                    chapters.append({"title": clean_filename(title_text), "url": self._fix_url(href), "source": self.source_name})
        else:
            a_matches = re.findall(r'<a[^>]+href=[\'"]([^\'"]+)[\'"][^>]*class=[\'"][^\'"]*d-inline-flex[^\'"]*[\'"][^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL)
            for href, inner_text in a_matches:
                title_text = re.sub(r'<[^>]+>', '', inner_text).strip()
                if title_text:
                    chapters.append({"title": clean_filename(title_text), "url": self._fix_url(href), "source": self.source_name})

        return cover_url, chapters

    def get_chapters(self, manga_url):
        _, chapters = self.get_chapters_and_cover(manga_url)
        return chapters

    def download_chapter(self, manga_save_path, chapter_title, chapter_url, progress_callback=None, cancel_check=None, dl_url=None):
        chapter_url = self._fix_url(chapter_url)
        chapter_dir = self._chapter_dir(manga_save_path, chapter_title)
        if progress_callback:
            progress_callback(0, 100, f"正在加载页面: {chapter_title}")

        res = self._request("GET", chapter_url, headers={**self.headers, "Referer": self.base_url})
        res.raise_for_status()
        html_text = res.text

        nonce_match = re.search(r'"nonce_a":"([^"]+)"', html_text)
        ajax_match = re.search(r'"ajax_url":"([^"]+)"', html_text)
        reading_chapter_match = re.search(r'reading_chapter:\s*(\d+)', html_text)
        p_match = re.search(r'p:\s*(\d+),', html_text)
        chapter_id_match = re.search(r"chapter_id:\s*'([^']+)'", html_text)
        if not all([nonce_match, ajax_match]):
            return False, None

        nonce_a = nonce_match.group(1)
        ajax_url = ajax_match.group(1).replace('\\/', '/')
        if reading_chapter_match:
            payload = {"nonce_a": nonce_a, "action": "z_do_ajax", "_action": "decode_images", "reading_chapter": reading_chapter_match.group(1)}
        elif all([p_match, chapter_id_match]):
            payload = {"nonce_a": nonce_a, "action": "z_do_ajax", "_action": "decode_images_g", "p": p_match.group(1), "img_index": 0, "chapter_id": chapter_id_match.group(1), "content": ""}
        else:
            return False, None

        api_headers = {**self.headers, "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Referer": chapter_url}
        all_image_urls = []
        seen_image_urls = set()

        def collect_urls(mes_html):
            matches = re.findall(r'<img[^>]+(?:src|data-src)=[\'"]([^\'"]+)[\'"]', mes_html or '')
            for img_url in matches:
                if img_url and img_url not in seen_image_urls:
                    seen_image_urls.add(img_url)
                    all_image_urls.append(img_url)

        def fetch_once():
            img_index = 0
            content_html = ""
            stagnation_rounds = 0
            for _ in range(400):
                if cancel_check and cancel_check():
                    return
                req_payload = payload.copy()
                req_payload["img_index"] = img_index
                req_payload["content"] = content_html
                post_res = self._request("POST", ajax_url, headers=api_headers, data=req_payload)
                res_data = post_res.json()
                mes_html = res_data.get("mes", "")
                before = len(all_image_urls)
                collect_urls(mes_html)
                if mes_html:
                    content_html += mes_html
                stagnation_rounds = stagnation_rounds + 1 if len(all_image_urls) == before else 0
                if str(res_data.get("going")) != "1":
                    break
                try:
                    next_index = int(res_data.get("img_index", img_index))
                except Exception:
                    next_index = img_index + 1
                img_index = next_index if next_index != img_index else img_index + 1
                if stagnation_rounds >= 3:
                    break

        stable_rounds = 0
        for _ in range(5):
            before_count = len(all_image_urls)
            fetch_once()
            stable_rounds = stable_rounds + 1 if len(all_image_urls) == before_count else 0
            if stable_rounds >= 2:
                break

        if not all_image_urls:
            return False, None

        max_workers = min(5, len(all_image_urls))

        def download_single(idx, img_url):
            file_path = chapter_dir / f"{idx:03d}.jpg"
            if file_path.exists() and file_path.stat().st_size > 0:
                return True
            self._download_and_convert_image(img_url, str(file_path), chapter_url)
            try:
                split_image_in_place(str(file_path))
            except Exception:
                pass
            return True

        succeeded = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(download_single, idx, img_url) for idx, img_url in enumerate(all_image_urls, 1)]
            for future in as_completed(futures):
                if cancel_check and cancel_check():
                    return False, None
                if future.result():
                    succeeded += 1
                if progress_callback:
                    progress_callback(succeeded, len(all_image_urls), f"下载图片: {succeeded}/{len(all_image_urls)}")

        return (succeeded > 0), str(chapter_dir) if succeeded > 0 else None

    def download_manga_cover(self, manga_url, save_dir):
        cover_url, _ = self.get_chapters_and_cover(manga_url)
        return self._download_cover_common(cover_url, manga_url, save_dir, stretch_kl=True)


class NicoMangaFastScraper(BaseFastScraper):
    source_name = "NicoManga"
    base_url = "https://nicomanga.com"

    def search(self, keyword):
        search_url = f"{self.base_url}/manga-list.html?n={urllib.parse.quote(keyword)}"
        res = self._request("GET", search_url)
        res.raise_for_status()
        soup = bs4.BeautifulSoup(res.text, 'html.parser')
        mangas = []
        for el in soup.select('.manga-card a.manga-title'):
            href = self._abs_url(el.get('href', ''))
            title = clean_filename(el.get('title') or el.get_text(" ", strip=True))
            mangas.append({"title": title, "url": href, "source": self.source_name, "chapter_count": 0})

        for item in mangas:
            try:
                item["chapter_count"] = len(self.get_chapters(item["url"]))
            except Exception:
                pass
        return mangas

    def get_chapters_and_cover(self, manga_url):
        res = self._request("GET", manga_url)
        res.raise_for_status()
        soup = bs4.BeautifulSoup(res.text, 'html.parser')

        cover_url = None
        img_el = soup.select_one('.manga-cover-wrapper img, meta[property="og:image"]')
        if img_el:
            cover_url = img_el.get('src') or img_el.get('content')

        chapters = []
        for el in soup.select('a.chapter-grid-item'):
            href = self._abs_url(el.get('href', ''))
            title = el.get('title') or el.get_text(" ", strip=True)
            if href:
                chapters.append({"title": clean_filename(title), "url": href, "source": self.source_name})

        slug_match = re.search(r"var\s+sLugs='([^']+)'", res.text)
        if slug_match:
            list_url = f"{self.base_url}/app/manga/controllers/cont.Listchapter.php"
            list_res = self._request("GET", list_url, params={"slug": slug_match.group(1)}, headers={**self.headers, "Referer": manga_url, "X-Requested-With": "XMLHttpRequest"})
            if list_res.ok:
                list_soup = bs4.BeautifulSoup(list_res.text, 'html.parser')
                full_chapters = []
                for el in list_soup.select('a'):
                    href = self._abs_url(el.get('href', ''))
                    title = el.get('title') or el.get_text(" ", strip=True)
                    if href and 'chapter' in href.lower():
                        full_chapters.append({"title": clean_filename(title), "url": href, "source": self.source_name})
                if full_chapters:
                    chapters = full_chapters

        return cover_url, chapters

    def get_chapters(self, manga_url):
        _, chapters = self.get_chapters_and_cover(manga_url)
        return chapters

    def download_chapter(self, manga_save_path, chapter_title, chapter_url, progress_callback=None, cancel_check=None, dl_url=None):
        chapter_dir = self._chapter_dir(manga_save_path, chapter_title)
        if progress_callback:
            progress_callback(10, 100, "打开章节界面...")

        res = self._request("GET", chapter_url)
        res.raise_for_status()
        soup = bs4.BeautifulSoup(res.text, 'html.parser')
        dl_btn = soup.select_one('#download_chapter_btn')
        if not dl_btn or not dl_btn.has_attr('href'):
            return False, None

        dl_page_url = self._abs_url(dl_btn['href'])
        dl_res = self._request("GET", dl_page_url, headers={**self.headers, "Referer": chapter_url})
        dl_res.raise_for_status()

        slug = None
        chapter = None
        dl_soup = bs4.BeautifulSoup(dl_res.text, 'html.parser')
        for script in dl_soup.find_all('script'):
            script_text = script.string or script.get_text("\n", strip=False)
            if 'const slug' in script_text:
                slug_match = re.search(r'const slug\s*=\s*"([^"]+)";', script_text)
                chapter_match = re.search(r'const chapter\s*=\s*([^;]+);', script_text)
                if slug_match and chapter_match:
                    slug = slug_match.group(1)
                    chapter = chapter_match.group(1).strip()
                    break
        if not slug or not chapter:
            return False, None

        payload = {"slug": slug, "chapter": chapter}
        try:
            payload["chapter"] = float(chapter) if '.' in chapter else int(chapter)
        except Exception:
            pass

        api_headers = {**self.headers, "Content-Type": "application/json", "Origin": self.base_url, "Referer": dl_page_url}
        api_res = self._request("POST", f"{self.base_url}/download/get-images.php", headers=api_headers, json=payload)
        data = api_res.json()
        img_urls = data.get('images', []) if data.get('success') else []
        if not img_urls:
            return False, None

        def download_single(idx, img_url):
            if cancel_check and cancel_check():
                return False
            file_name = chapter_dir / f"{idx:03d}.jpg"
            if file_name.exists() and file_name.stat().st_size > 0:
                return True
            local_session = requests.Session()
            dl_headers = {**self.headers, "Referer": self.base_url}
            img_res = local_session.get(img_url, headers=dl_headers, verify=False, timeout=20)
            if img_res.status_code != 200:
                proxy_url = f"https://img.nicomanga.com/?url={urllib.parse.quote(img_url)}"
                img_res = local_session.get(proxy_url, headers=dl_headers, verify=False, timeout=20)
            img_res.raise_for_status()
            image = Image.open(BytesIO(img_res.content))
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
            image.save(file_name, 'JPEG', quality=95)
            return True

        done = 0
        with ThreadPoolExecutor(max_workers=min(5, len(img_urls))) as executor:
            futures = [executor.submit(download_single, idx, img_url) for idx, img_url in enumerate(img_urls, 1)]
            for future in as_completed(futures):
                ok = future.result()
                if ok:
                    done += 1
                if progress_callback:
                    progress_callback(done, len(img_urls), f"下载图片: {done}/{len(img_urls)}")
        return (done > 0), str(chapter_dir) if done > 0 else None

    def download_manga_cover(self, manga_url, save_dir):
        cover_url, _ = self.get_chapters_and_cover(manga_url)
        return self._download_cover_common(cover_url, manga_url, save_dir)


class RawkumaFastScraper(BaseFastScraper):
    source_name = "Rawkuma"
    base_url = "https://rawkuma.net"

    def _get_search_nonce(self):
        library_url = f"{self.base_url}/library/"
        res = self._request("GET", library_url, headers={**self.headers, "Referer": library_url})
        res.raise_for_status()
        match = re.search(r'admin-ajax\.php\?nonce=([a-f0-9]+)&#038;action=search', res.text)
        return match.group(1) if match else None

    def _extract_search_results(self, html_text):
        soup = bs4.BeautifulSoup(html_text, 'html.parser')
        mangas = []
        seen_urls = set()
        for el in soup.select('a[href*="/manga/"]'):
            href = self._abs_url(el.get('href', ''))
            if not href or '/manga/' not in href or '/chapter-' in href or href in seen_urls:
                continue
            title_el = el.select_one('h3')
            raw_title = ''
            if title_el:
                raw_title = title_el.get_text(" ", strip=True)
            if not raw_title:
                raw_title = str(el.get('title') or '').strip()
            if not raw_title:
                img_el = el.select_one('img')
                if img_el:
                    raw_title = str(img_el.get('alt') or '').strip()
            if not raw_title:
                raw_title = el.get_text(" ", strip=True)
            seen_urls.add(href)
            mangas.append({"title": clean_filename(raw_title), "url": href, "source": self.source_name, "chapter_count": 0})
        return mangas

    def search(self, keyword):
        library_url = f"{self.base_url}/library/"
        search_res = self._request(
            "POST",
            f"{self.base_url}/wp-admin/admin-ajax.php?action=advanced_search",
            headers={**self.headers, "Referer": f"{library_url}?search_term={urllib.parse.quote(keyword)}", "HX-Request": "true", "X-Requested-With": "XMLHttpRequest"},
            data={"query": keyword},
        )
        search_res.raise_for_status()
        mangas = self._extract_search_results(search_res.text)
        if not mangas:
            nonce = self._get_search_nonce()
            if nonce:
                fallback_res = self._request("POST", f"{self.base_url}/wp-admin/admin-ajax.php?nonce={nonce}&action=search", headers={**self.headers, "Referer": library_url}, data={"query": keyword})
                fallback_res.raise_for_status()
                mangas = self._extract_search_results(fallback_res.text)

        for item in mangas:
            try:
                item["chapter_count"] = len(self.get_chapters(item["url"]))
            except Exception:
                pass
        return mangas

    def get_chapters_and_cover(self, manga_url):
        res = self._request("GET", manga_url)
        res.raise_for_status()
        soup = bs4.BeautifulSoup(res.text, 'html.parser')

        cover_url = None
        img_el = soup.select_one('div[itemprop="image"] img.wp-post-image') or soup.select_one('img.wp-post-image')
        if img_el and img_el.has_attr('src'):
            cover_url = self._abs_url(img_el['src'])
        if not cover_url:
            og_img = soup.select_one('meta[property="og:image"]')
            if og_img and og_img.has_attr('content'):
                cover_url = self._abs_url(og_img['content'])

        chapter_list = soup.select_one('#chapter-list[hx-get]')
        chapter_html = ""
        if chapter_list and chapter_list.has_attr('hx-get'):
            chapter_list_url = self._abs_url(html.unescape(str(chapter_list['hx-get'])).strip())
            ajax_headers = {**self.headers, "Referer": manga_url, "HX-Request": "true", "X-Requested-With": "XMLHttpRequest"}
            chapter_res = self._request("GET", chapter_list_url, headers=ajax_headers)
            chapter_res.raise_for_status()
            chapter_html = chapter_res.text

        chapter_soup = bs4.BeautifulSoup(chapter_html, 'html.parser') if chapter_html else soup
        chapter_rows = chapter_soup.select('#chapter-list div[data-chapter-number], div[data-chapter-number]')
        if not chapter_rows:
            chapter_rows = chapter_soup.select('a[href*="/manga/"][href*="/chapter-"]')

        chapters = []
        seen_urls = set()
        for el in chapter_rows:
            if el.name == 'a':
                chap_a = el
                scope = el
            else:
                chap_a = el.select_one('a[href*="/chapter-"]') or el.select_one('a')
                scope = el
            if not chap_a:
                continue
            read_url = self._abs_url(chap_a.get('href', ''))
            if not read_url or '/chapter-' not in read_url or read_url in seen_urls:
                continue
            seen_urls.add(read_url)
            title_el = scope.select_one('.font-medium') if hasattr(scope, 'select_one') else None
            raw_title = (title_el.get_text(" ", strip=True) if title_el else '') or chap_a.get_text(" ", strip=True) or str(chap_a.get('title') or '')
            dl_url_el = scope.select_one('a[href*="drive.google.com"]') if hasattr(scope, 'select_one') else None
            dl_url = html.unescape(str(dl_url_el.get('href', ''))) if dl_url_el else ""
            chapters.append({"title": clean_filename(raw_title), "url": read_url, "dl_url": dl_url, "source": self.source_name})
        return cover_url, chapters

    def get_chapters(self, manga_url):
        _, chapters = self.get_chapters_and_cover(manga_url)
        return chapters

    def download_chapter(self, manga_save_path, chapter_title, chapter_url, progress_callback=None, cancel_check=None, dl_url=None):
        chapter_dir = self._chapter_dir(manga_save_path, chapter_title)
        target_dl_url = dl_url if dl_url else chapter_url
        if target_dl_url and "drive.google.com" in target_dl_url:
            try:
                file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', target_dl_url) or re.search(r'id=([a-zA-Z0-9_-]+)', target_dl_url)
                if file_id_match:
                    zip_path = os.path.join(str(chapter_dir), "temp_download.zip")
                    gdown.download(id=file_id_match.group(1), output=zip_path, quiet=True)
                    if os.path.exists(zip_path) and zipfile.is_zipfile(zip_path):
                        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                            zip_ref.extractall(str(chapter_dir))
                        try:
                            os.remove(zip_path)
                        except Exception:
                            pass
                        return True, str(chapter_dir)
            except Exception:
                pass

        img_urls = []
        seen = set()

        def normalize_img_url(url):
            if not url:
                return None
            url = url.strip()
            if not url or url.startswith('data:'):
                return None
            if url.startswith('//'):
                return 'https:' + url
            if url.startswith('/'):
                return urllib.parse.urljoin(self.base_url, url)
            return url

        def is_probable_chapter_image(url):
            low = url.lower()
            if not re.search(r'\.(jpg|jpeg|png|webp)(\?|$)', low):
                return False
            if any(x in low for x in ["logo", "avatar", "gravatar", "icon", "banner"]):
                return False
            return any(x in low for x in ["/chapter-", "/chapter/", "/manga/", "/uploads/"]) or bool(re.search(r'/\d+\.(jpg|jpeg|png|webp)(\?|$)', low))

        def add_img_url(url):
            norm = normalize_img_url(url)
            if norm and is_probable_chapter_image(norm) and norm not in seen:
                seen.add(norm)
                img_urls.append(norm)

        def collect_from_html(html_text):
            soup = bs4.BeautifulSoup(html_text, 'html.parser')
            selectors = ['section[data-image-data="1"] img', 'img.ts-main-image', '.reading-content img', '.rdminimal img', 'section[data-image-data] img', 'section.w-full.flex-col img', '#readerarea img', '.entry-content img', 'img.wp-manga-chapter-img']
            for selector in selectors:
                for img in soup.select(selector):
                    add_img_url(img.get('src'))
                    add_img_url(img.get('data-src'))
                    add_img_url(img.get('data-lazy-src'))
                    add_img_url(img.get('data-original'))
            for m in re.findall(r'https?://[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?', html_text, re.IGNORECASE):
                add_img_url(m)

        res = self._request("GET", chapter_url, headers={**self.headers, "Referer": self.base_url})
        res.raise_for_status()
        collect_from_html(res.text)
        if not img_urls:
            return False, None

        def download_single(idx, img_url):
            if cancel_check and cancel_check():
                return False
            save_path = chapter_dir / f"{idx:03d}.jpg"
            if save_path.exists() and save_path.stat().st_size > 0:
                return True
            self._download_and_convert_image(img_url, str(save_path), chapter_url)
            return True

        done = 0
        with ThreadPoolExecutor(max_workers=min(5, len(img_urls))) as executor:
            futures = [executor.submit(download_single, idx, img_url) for idx, img_url in enumerate(img_urls, 1)]
            for future in as_completed(futures):
                if future.result():
                    done += 1
                if progress_callback:
                    progress_callback(done, len(img_urls), f"并发下载网页图片: {done}/{len(img_urls)}")
        return (done > 0), str(chapter_dir) if done > 0 else None

    def download_manga_cover(self, manga_url, save_dir):
        cover_url, _ = self.get_chapters_and_cover(manga_url)
        return self._download_cover_common(cover_url, manga_url, save_dir)


def split_image_in_place(img_path):
    try:
        with Image.open(img_path) as img:
            w, h = img.size
            if h <= w * 1.5:
                return
            page_height_approx = w * 1.414
            num_pages = max(2, round(h / page_height_approx))
            piece_height = h // num_pages
            base_name, ext = os.path.splitext(img_path)
            for i in range(num_pages):
                box = (0, i * piece_height, w, min((i + 1) * piece_height, h))
                piece = img.crop(box)
                piece_path = f"{base_name}_{i + 1:02d}{ext}"
                piece.save(piece_path, quality=95)
        os.remove(img_path)
    except Exception:
        pass


FAST_SCRAPERS = {
    "klmanga": KlMangaFastScraper(),
    "nicomanga": NicoMangaFastScraper(),
    "rawkuma": RawkumaFastScraper(),
}
