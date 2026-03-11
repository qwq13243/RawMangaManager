import sqlite3
import threading
import os
from PySide6.QtCore import QStandardPaths

DB_NAME = "manga_manager.db"

class DBHelper:
    def __init__(self, db_name=DB_NAME):
        self.db_path = db_name
        self.lock = threading.Lock()
        # 注意：在多线程环境下访问 SQLite 需要小心，这里简单处理，生产环境建议加锁
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        cursor = self.conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
                            key TEXT PRIMARY KEY, value TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS manga (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            title_zh TEXT, title_jp TEXT, title_romaji TEXT,
                            cover_path TEXT, folder_name TEXT, source_url TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS chapters (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            manga_id INTEGER, 
                            chapter_num REAL, 
                            chapter_str TEXT, 
                            title_original TEXT, 
                            url TEXT, 
                            dl_url TEXT,
                            source_site TEXT,
                            is_downloaded INTEGER DEFAULT 0,
                            local_path TEXT,
                            UNIQUE(manga_id, chapter_str))''')
        try:
            self.conn.cursor().execute("ALTER TABLE chapters ADD COLUMN is_translated INTEGER DEFAULT 0")
            # 新增 read_status 字段
            self.conn.cursor().execute("ALTER TABLE chapters ADD COLUMN read_status INTEGER DEFAULT 0")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

        try:
            self.conn.cursor().execute("ALTER TABLE manga ADD COLUMN is_following INTEGER DEFAULT 1")
            self.conn.cursor().execute("ALTER TABLE manga ADD COLUMN sort_order INTEGER DEFAULT 0")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass 

        try:
            self.conn.cursor().execute("ALTER TABLE manga ADD COLUMN glossary TEXT DEFAULT ''")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

        defaults = {
            'base_dir': os.path.join(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation), "MangaDownloads"),
            'translator_path': '',
            'translator_args': '-input {path}',
            'proxy_enable': '0',
            'proxy_addr': '',
            'headless': '1',
            'max_workers': '1', # 爬虫通常单线程稳定，如需并发需复杂改造
            'retry_count': '3',
            'timeout': '60',
            'reader_window_geometry': '',
            'saber_model_provider': 'siliconflow',
            'saber_api_key': '',
            'saber_base_url': 'https://api.siliconflow.cn/v1',
            'saber_model_name': 'Qwen/Qwen2.5-7B-Instruct',
            # Detection Settings
            'saber_detect_expand_global': '0',
            'saber_detect_expand_top': '0',
            'saber_detect_expand_bottom': '0',
            'saber_detect_expand_left': '0',
            'saber_detect_expand_right': '0',
            'saber_mask_dilate_size': '10',
            'saber_mask_box_expand_ratio': '20'
        }
        for k, v in defaults.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        self.conn.commit()
        
    def update_manga_glossary(self, manga_id, glossary):
        with self.lock:
            self.conn.cursor().execute("UPDATE manga SET glossary=? WHERE id=?", (glossary, manga_id))
            self.conn.commit()

    def get_chapter_by_id(self, chapter_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,))
        return cursor.fetchone()

    def add_local_chapter(self, manga_id, chapter_str, title_original, local_path, source_site="local"):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT COUNT(*), COALESCE(MIN(chapter_num), 0) FROM chapters WHERE manga_id=?", (manga_id,))
            row = cursor.fetchone()
            count = int(row[0] or 0)
            min_num = float(row[1] or 0)
            chapter_num = 1.0 if count == 0 else (min_num - 0.001)

            cursor.execute(
                """INSERT INTO chapters
                   (manga_id, chapter_num, chapter_str, title_original, url, dl_url, source_site, is_downloaded, local_path)
                   VALUES (?, ?, ?, ?, '', '', ?, 1, ?)""",
                (manga_id, chapter_num, chapter_str, title_original, source_site, local_path),
            )
            self.conn.commit()
            return cursor.lastrowid

    def delete_chapter(self, chapter_id):
        with self.lock:
            self.conn.cursor().execute("DELETE FROM chapters WHERE id=?", (chapter_id,))
            self.conn.commit()

    def get_setting(self, key, default=""):
        cursor = self.conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
        res = cursor.fetchone()
        return res[0] if res else default

    def set_setting(self, key, value):
        with self.lock:
            self.conn.cursor().execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
            self.conn.commit()

    def add_manga(self, data):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''INSERT INTO manga (title_zh, title_jp, title_romaji, cover_path, folder_name, source_url)
                              VALUES (?, ?, ?, ?, ?, ?)''', 
                           (data.get('zh',''), data.get('jp',''), data.get('romaji',''), 
                            data.get('cover',''), data.get('folder',''), data.get('url','')))
            self.conn.commit()
            return cursor.lastrowid

    def update_manga_info(self, manga_id, data):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''UPDATE manga SET title_zh=?, title_jp=?, title_romaji=?, cover_path=? WHERE id=?''',
                           (data.get('zh'), data.get('jp'), data.get('romaji'), data.get('cover'), manga_id))
            self.conn.commit()

    def get_all_manga(self):
        cursor = self.conn.cursor()
        # 将查询字段补充完整并按 中文、日文、罗马音 顺序提取
        # 注意：这里我们增加了 is_following 和 sort_order 字段，确保顺序正确
        try:
            cursor.execute("SELECT id, title_zh, title_jp, title_romaji, cover_path, is_following, sort_order, glossary FROM manga ORDER BY sort_order ASC, id DESC")
        except sqlite3.OperationalError:
            try:
                cursor.execute("SELECT id, title_zh, title_jp, title_romaji, cover_path, is_following, sort_order FROM manga ORDER BY sort_order ASC, id DESC")
            except sqlite3.OperationalError:
                cursor.execute("SELECT id, title_zh, title_jp, title_romaji, cover_path FROM manga ORDER BY id DESC")
                res = cursor.fetchall()
                return [(r[0], r[1], r[2], r[3], r[4], 1, 0, "") for r in res]
            res = cursor.fetchall()
            return [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], "") for r in res]
        return cursor.fetchall()

    def update_manga_following(self, manga_id, is_following):
        with self.lock:
            self.conn.cursor().execute("UPDATE manga SET is_following=? WHERE id=?", (is_following, manga_id))
            self.conn.commit()

    def update_manga_order(self, manga_id, sort_order):
        with self.lock:
            self.conn.cursor().execute("UPDATE manga SET sort_order=? WHERE id=?", (sort_order, manga_id))
            self.conn.commit()

    def get_manga_detail(self, manga_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM manga WHERE id=?", (manga_id,))
        return cursor.fetchone()

    def save_chapters(self, manga_id, chapters_data, source_name):
        import re
        with self.lock:
            cursor = self.conn.cursor()
            for cap in chapters_data:
                title = cap.get('title', '')
                if not title: continue
                
                ch_num = 0.0
                ch_str = "Ch.0"
                clean_title = title # 默认使用原标题
                
                # --- 步骤 1: 尝试从标题中提取纯净的章节号 ---
                
                # 模式 A: 匹配各种括号的 "第 X 話/话" (优先级最高)
                # 匹配 【第 11 話】, [第 11 話], (第 11 話), 第 11 話
                match_jp_cn = re.search(r'[【\[(]?第\s*(\d+(?:\.\d+)?)\s*[話话][\])】]?', title)
                
                if match_jp_cn:
                    ch_num = float(match_jp_cn.group(1))
                    ch_str = f"Ch.{match_jp_cn.group(1)}"
                    # 优化：如果匹配成功，尝试清洗标题，只保留 "第 X 話" 部分，去除前面的漫画名
                    # 例如："Manga Name ... [第 11 話]" -> "第 11 話"
                    clean_title = f"第{match_jp_cn.group(1)}話" 
                
                # 模式 B: 匹配英文 "Chapter X", "Ch.X"
                elif re.search(r'(?:Chapter|Ch\.?)\s*(\d+(?:\.\d+)?)', title, re.IGNORECASE):
                    match_en = re.search(r'(?:Chapter|Ch\.?)\s*(\d+(?:\.\d+)?)', title, re.IGNORECASE)
                    ch_num = float(match_en.group(1))
                    ch_str = f"Ch.{match_en.group(1)}"
                    clean_title = f"Chapter {match_en.group(1)}"

                # 模式 C: 匹配末尾数字 (针对没有明确章节标识的)
                elif re.search(r'(\d+(?:\.\d+)?)\s*(?:$|\[|$|\(|Raw|Free)', title):
                    match_end = re.search(r'(\d+(?:\.\d+)?)\s*(?:$|\[|$|\(|Raw|Free)', title)
                    ch_num = float(match_end.group(1))
                    ch_str = f"Ch.{match_end.group(1)}"
                
                # 模式 D: 保底 (仅在以上都失败时使用，容易误判漫画名中的数字)
                else:
                    match_any = re.search(r'(\d+(?:\.\d+)?)', title)
                    if match_any:
                        ch_num = float(match_any.group(1))
                        ch_str = f"Ch.{match_any.group(1)}"
                
                # --- 步骤 2: 执行插入 ---
                try:
                    # 注意：这里我们存入了 clean_title，这样列表显示会更干净
                    cursor.execute('''INSERT OR IGNORE INTO chapters 
                                      (manga_id, chapter_num, chapter_str, title_original, url, dl_url, source_site)
                                      VALUES (?, ?, ?, ?, ?, ?, ?)''',
                                   (manga_id, ch_num, ch_str, clean_title, cap.get('url',''), cap.get('dl_url',''), source_name))
                except Exception as e:
                    print(f"保存章节失败：{e}, 标题：{title}")
            
            self.conn.commit()

    def get_chapters(self, manga_id):
        cursor = self.conn.cursor()
        # 增加返回 chapter_num 字段 (index 9)
        cursor.execute("""SELECT id, chapter_str, title_original, url, is_downloaded, local_path, source_site, is_translated, read_status, chapter_num 
                          FROM chapters WHERE manga_id=? ORDER BY chapter_num DESC""", (manga_id,))
        return cursor.fetchall()

    def mark_chapter_downloaded(self, chapter_id, local_path):
        with self.lock:
            self.conn.cursor().execute("UPDATE chapters SET is_downloaded=1, local_path=? WHERE id=?", (local_path, chapter_id))
            self.conn.commit()

    def mark_chapter_translated(self, chapter_id):
        with self.lock:
            self.conn.cursor().execute("UPDATE chapters SET is_translated=1 WHERE id=?", (chapter_id,))
            self.conn.commit()

    def delete_manga(self, manga_id):
        with self.lock:
            self.conn.cursor().execute("DELETE FROM chapters WHERE manga_id=?", (manga_id,))
            self.conn.cursor().execute("DELETE FROM manga WHERE id=?", (manga_id,))
            self.conn.commit()

    def update_manga_cover(self, manga_id, cover_path):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("UPDATE manga SET cover_path=? WHERE id=?", (cover_path, manga_id))
            self.conn.commit()

    def update_chapter_read_status(self, chapter_id, status):
        with self.lock:
            self.conn.cursor().execute("UPDATE chapters SET read_status=? WHERE id=?", (status, chapter_id))
            self.conn.commit()

db = DBHelper()
