
from core.database import db

# Constants
DEFAULT_rpm_TRANSLATION = 60
DEFAULT_TRANSLATION_MAX_RETRIES = 3
DEFAULT_PROMPT = "你是一个专业的漫画翻译助手。请将外语漫画文本翻译成流畅、自然的中文。保留原文的情感和语调。"
DEFAULT_TRANSLATE_JSON_PROMPT = """你是一个专业的漫画翻译助手。请将外语漫画文本翻译成流畅、自然的中文。
请以 JSON 格式输出，格式如下：
{
    "translated_text": "翻译后的中文文本"
}
"""
BAIDU_TRANSLATE_ENGINE_ID = 'baidu'
YOUDAO_TRANSLATE_ENGINE_ID = 'youdao'
CUSTOM_OPENAI_PROVIDER_ID = 'custom'

PROJECT_TO_BAIDU_TRANSLATE_LANG_MAP = {'zh': 'zh', 'en': 'en', 'ja': 'jp'}
PROJECT_TO_YOUDAO_TRANSLATE_LANG_MAP = {'zh': 'zh-CHS', 'en': 'en', 'ja': 'ja'}
DEFAULT_FILL_COLOR = (255, 255, 255)

# Provider Base URLs (Auto-switch)
PROVIDER_BASE_URLS = {
    'siliconflow': 'https://api.siliconflow.cn/v1',
    'deepseek': 'https://api.deepseek.com/v1',
    'volcano': 'https://ark.cn-beijing.volces.com/api/v3',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/openai/',
    'sakura': 'http://localhost:8080/v1',
    'ollama': 'http://localhost:11434/api',
    'caiyun': 'http://api.interpreter.caiyunai.com/v1'
}

# Rendering Constants
DEFAULT_FONT_RELATIVE_PATH = 'msyh.ttc' # Use Microsoft YaHei as default on Windows
DEFAULT_FONT_SIZE = 20
DEFAULT_TEXT_COLOR = (0, 0, 0)
DEFAULT_STROKE_ENABLED = True
DEFAULT_STROKE_COLOR = (255, 255, 255)
DEFAULT_STROKE_WIDTH = 3
DEFAULT_ROTATION_ANGLE = 0
DEFAULT_TEXT_DIRECTION = 'auto'

class SaberConfig:
    @property
    def model_provider(self):
        return db.get_setting('saber_model_provider', 'siliconflow')
    
    @property
    def api_key(self):
        return db.get_setting('saber_api_key', '')
    
    @property
    def base_url(self):
        # Auto-switch logic: if provider is in known list, use default URL
        # unless it's 'custom', then use stored base_url
        provider = self.model_provider
        if provider == 'custom':
            return db.get_setting('saber_base_url', '')
        # Return default URL for the provider if available, otherwise fallback
        return PROVIDER_BASE_URLS.get(provider, '')
        
    @property
    def model_name(self):
        return db.get_setting('saber_model_name', 'Qwen/Qwen2.5-7B-Instruct')

    @property
    def use_lama(self):
        return db.get_setting('saber_use_lama', '1') == '1'

    # Detection Settings
    @property
    def detect_expand_global(self):
        return int(db.get_setting('saber_detect_expand_global', '0'))
    
    @property
    def detect_expand_top(self):
        return int(db.get_setting('saber_detect_expand_top', '0'))
        
    @property
    def detect_expand_bottom(self):
        return int(db.get_setting('saber_detect_expand_bottom', '0'))
        
    @property
    def detect_expand_left(self):
        return int(db.get_setting('saber_detect_expand_left', '0'))
        
    @property
    def detect_expand_right(self):
        return int(db.get_setting('saber_detect_expand_right', '0'))
        
    @property
    def mask_dilate_size(self):
        return int(db.get_setting('saber_mask_dilate_size', '10'))
        
    @property
    def mask_box_expand_ratio(self):
        return int(db.get_setting('saber_mask_box_expand_ratio', '20'))
        
    @property
    def rpm_limit(self):
        return int(db.get_setting('saber_rpm_limit', str(DEFAULT_rpm_TRANSLATION)))

    @property
    def max_retries(self):
        return int(db.get_setting('saber_max_retries', str(DEFAULT_TRANSLATION_MAX_RETRIES)))
        
    @property
    def translation_mode(self):
        return db.get_setting('saber_translation_mode', 'page')
    
config = SaberConfig()
