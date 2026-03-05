import logging
import time
import requests
import json
import re
import os
import sys
from openai import OpenAI
from .openai_helpers import create_openai_client
from . import config as constants

logger = logging.getLogger("SaberTranslator")

# --- 自定义异常 ---
class TranslationParseException(Exception):
    """批量翻译响应解析失败异常，触发重试"""
    pass

# --- RPM Limiting Globals for Translation ---
_translation_rpm_last_reset_time_container = [0]
_translation_rpm_request_count_container = [0]
# ------------------------------------------

def _enforce_rpm_limit(rpm_limit: int, service_name: str, last_reset_time_ref: list, request_count_ref: list):
    """
    执行rpm（每分钟请求数）限制检查和等待。
    使用列表作为引用类型来修改外部的 last_reset_time 和 request_count。
    """
    if rpm_limit <= 0:
        return # 无限制

    current_time = time.time()

    # 检查是否需要重置窗口
    if current_time - last_reset_time_ref[0] >= 60:
        logger.info(f"rpm: {service_name} - 1分钟窗口已过，重置计数器和时间。")
        last_reset_time_ref[0] = current_time
        request_count_ref[0] = 0

    # 检查是否达到rpm限制
    if request_count_ref[0] >= rpm_limit:
        time_to_wait = 60 - (current_time - last_reset_time_ref[0])
        if time_to_wait > 0:
            logger.info(f"rpm: {service_name} - 已达到每分钟 {rpm_limit} 次请求上限。将等待 {time_to_wait:.2f} 秒...")
            time.sleep(time_to_wait)
            # 等待结束后，这是一个新的窗口
            last_reset_time_ref[0] = time.time() # 更新为当前时间
            request_count_ref[0] = 0
        else:
            # 理论上不应该到这里，因为上面的窗口重置逻辑会处理
            logger.info(f"rpm: {service_name} - 窗口已过但计数未重置，立即重置。")
            last_reset_time_ref[0] = current_time
            request_count_ref[0] = 0
    
    # 如果是窗口内的第一次请求，设置窗口开始时间
    if request_count_ref[0] == 0 and last_reset_time_ref[0] == 0: 
        last_reset_time_ref[0] = current_time
        logger.info(f"rpm: {service_name} - 启动新的1分钟请求窗口。")

    request_count_ref[0] += 1
    logger.debug(f"rpm: {service_name} - 当前窗口请求计数: {request_count_ref[0]}/{rpm_limit if rpm_limit > 0 else '无限制'}")

def _safely_extract_from_json(json_str, field_name):
    """
    安全地从JSON字符串中提取特定字段，处理各种异常情况。
    """
    # 尝试直接解析
    try:
        data = json.loads(json_str)
        if field_name in data:
            return data[field_name]
    except (json.JSONDecodeError, TypeError, KeyError):
        pass

    # 解析失败，尝试使用正则表达式提取
    try:
        # 匹配 "field_name": "内容" 或 "field_name":"内容" 的模式
        pattern = r'"' + re.escape(field_name) + r'"\s*:\s*"(.+?)"'
        # 多行模式，使用DOTALL
        match = re.search(pattern, json_str, re.DOTALL)
        if match:
            # 反转义提取的文本
            extracted = match.group(1)
            # 处理转义字符
            extracted = extracted.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
            return extracted
    except Exception:
        pass

    # 如果依然失败，尝试清理明显的JSON结构，仅保留文本内容
    try:
        # 删除常见JSON结构字符
        cleaned = re.sub(r'[{}"\[\]]', '', json_str)
        # 删除字段名和冒号
        cleaned = re.sub(fr'{field_name}\s*:', '', cleaned)
        # 删除多余空白
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned
    except Exception:
        # 所有方法都失败，返回原始文本
        return json_str

def _assemble_batch_prompt(text_list, prompt_template=None):
    """
    将多个文本拼装成一个 Prompt，使用 <|n|> 标记。
    """
    prompt = "请将以下日文文本翻译成中文，保持原有语调和情感。\n"
    prompt += "回复格式必须严格遵守：每行以 <|n|> 开头，后跟翻译结果，n代表文本编号。\n"
    prompt += "例如：\n<|0|>你好\n<|1|>世界\n\n待翻译文本：\n"
    
    for i, text in enumerate(text_list):
        prompt += f"<|{i}|>{text}\n"
        
    return prompt

def _parse_batch_response(response_text, expected_count):
    """
    解析 LLM 返回的 <|n|> 格式文本。
    """
    results = {}
    
    # 尝试匹配 <|n|>content
    pattern = r"<\|(\d+)\|>(.*?)(?=(?:<\|\d+\|>|$))"
    matches = re.findall(pattern, response_text, re.DOTALL)
    
    for idx_str, content in matches:
        try:
            idx = int(idx_str)
            results[idx] = content.strip()
        except ValueError:
            pass
            
    # 如果解析失败或者数量不对，尝试备用解析 (简单按行)
    if len(results) < expected_count:
        logger.warning(f"批量翻译解析数量不足 ({len(results)}/{expected_count})，尝试备用解析...")
        lines = [l.strip() for l in response_text.split('\n') if l.strip()]
        # 简单的启发式：如果行数接近，直接按顺序
        if len(lines) == expected_count:
            for i, line in enumerate(lines):
                # 去除可能的标记
                clean_line = re.sub(r"^<\|\d+\|>", "", line).strip()
                results[i] = clean_line
                
    # 填充缺失的索引
    final_list = []
    for i in range(expected_count):
        final_list.append(results.get(i, "【翻译失败】"))
        
    return final_list

def translate_batch_text(text_list, target_language, model_provider, 
                          api_key=None, model_name=None, prompt_content=None, 
                          use_json_format=False, custom_base_url=None,
                          rpm_limit_translation: int = constants.DEFAULT_rpm_TRANSLATION,
                          max_retries: int = constants.DEFAULT_TRANSLATION_MAX_RETRIES):
    """
    批量翻译文本列表。
    """
    if not text_list:
        return []
        
    # 过滤空文本，记录原始索引
    valid_indices = []
    valid_texts = []
    for i, text in enumerate(text_list):
        if text and text.strip():
            valid_indices.append(i)
            valid_texts.append(text)
            
    if not valid_texts:
        return [""] * len(text_list)

    # 组装 Prompt
    batch_prompt = _assemble_batch_prompt(valid_texts)
    
    # 复用 translate_single_text 的核心调用逻辑，但只调用一次
    # 这里我们简化处理，直接构造单次请求，因为 translate_single_text 主要是为单个设计的
    # 为了复用 retry 和 rpm 逻辑，我们可以稍微重构一下，或者直接在这里写
    
    logger.info(f"开始批量翻译 {len(valid_texts)} 条文本 (服务商: {model_provider})...")
    
    # 临时复用单条翻译函数来发送整个大 Prompt
    # 注意：这里我们实际上是发送了一个"单条"请求，内容是聚合后的
    # 但我们需要确保 translate_single_text 不会对其进行额外的 JSON 解析干扰（除非我们明确想要 JSON）
    # 这里的 batch_prompt 已经是构造好的用户消息
    
    # 构造一个新的 prompt_content (system prompt) 来强调格式
    system_prompt = prompt_content or constants.DEFAULT_PROMPT
    system_prompt += "\n重要：请严格按照 <|n|> 格式返回，不要包含其他解释性文字。"
    
    # 临时调用 translate_single_text，但我们需要它返回 raw content 而不是 JSON extracted
    # 实际上 translate_single_text 内部如果不传 use_json_format=True 就返回 raw
    # 但我们传过去的 batch_prompt 是整个 list，而 translate_single_text 只是把它当做 user content
    # 这正是我们想要的。
    
    raw_response = translate_single_text(
        batch_prompt, 
        target_language, 
        model_provider, 
        api_key, 
        model_name, 
        prompt_content=system_prompt, # 使用增强的 system prompt
        use_json_format=False, # 强制关闭 JSON 解析，我们自己解析 <|n|>
        custom_base_url=custom_base_url,
        rpm_limit_translation=rpm_limit_translation,
        max_retries=max_retries
    )
    
    # 解析结果
    # 注意：raw_response 是 LLM 返回的整个大字符串
    parsed_results_list = _parse_batch_response(raw_response, len(valid_texts))
    
    # 还原到原始列表长度
    final_output = [""] * len(text_list)
    for i, valid_idx in enumerate(valid_indices):
        final_output[valid_idx] = parsed_results_list[i]
        
    return final_output

def translate_single_text(text, target_language, model_provider, 
                          api_key=None, model_name=None, prompt_content=None, 
                          use_json_format=False, custom_base_url=None,
                          rpm_limit_translation: int = constants.DEFAULT_rpm_TRANSLATION,
                          max_retries: int = constants.DEFAULT_TRANSLATION_MAX_RETRIES):
    """
    使用指定的大模型翻译单段文本。
    """
    if not text or not text.strip():
        return ""

    if prompt_content is None:
        # 根据是否使用 JSON 格式选择默认提示词
        if use_json_format:
            prompt_content = constants.DEFAULT_TRANSLATE_JSON_PROMPT
        else:
            prompt_content = constants.DEFAULT_PROMPT
    elif use_json_format and '"translated_text"' not in prompt_content:
        # 如果用户传入了自定义提示词但不是JSON格式，给出警告
        logger.warning("期望JSON格式输出，但提供的翻译提示词可能不是JSON格式。")


    logger.info(f"开始翻译文本: '{text[:30]}...' (服务商: {model_provider}, RPM: {rpm_limit_translation if rpm_limit_translation > 0 else '无'}, 重试: {max_retries})")
    
    # Pre-check API Key for common issues
    if api_key:
        api_key = api_key.strip()
        if not api_key.isascii():
            logger.error("API Key contains non-ASCII characters. Please check your input.")
            return "翻译失败: API Key contains non-ASCII characters"

    retry_count = 0
    translated_text = "【翻译失败】请检查日志"

    # --- RPM Enforcement ---
    _enforce_rpm_limit(
        rpm_limit_translation,
        f"Translation ({model_provider})",
        _translation_rpm_last_reset_time_container,
        _translation_rpm_request_count_container
    )
    # ---------------------

    while retry_count < max_retries:
        try:
            # 优先使用传入的 base_url (来自 config.base_url 的自动切换逻辑)
            # 如果没有传入，则使用硬编码默认值作为后备
            
            if model_provider == 'siliconflow':
                # SiliconFlow (硅基流动) 使用 OpenAI 兼容 API
                if not api_key:
                    raise ValueError("SiliconFlow需要API Key")
                base_url = custom_base_url or "https://api.siliconflow.cn/v1"
                client = create_openai_client(api_key=api_key, base_url=base_url)
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": prompt_content},
                        {"role": "user", "content": text},
                    ]
                )
                translated_text = response.choices[0].message.content.strip()
                
            elif model_provider == 'deepseek':
                # DeepSeek 也使用 OpenAI 兼容 API
                if not api_key:
                    raise ValueError("DeepSeek需要API Key")
                base_url = custom_base_url or "https://api.deepseek.com/v1"
                client = create_openai_client(api_key=api_key, base_url=base_url)
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": prompt_content},
                        {"role": "user", "content": text},
                    ]
                )
                translated_text = response.choices[0].message.content.strip()
                
            elif model_provider == 'volcano':
                # 火山引擎，也使用 OpenAI 兼容 API
                if not api_key: raise ValueError("火山引擎需要 API Key")
                base_url = custom_base_url or "https://ark.cn-beijing.volces.com/api/v3"
                client = create_openai_client(api_key=api_key, base_url=base_url)
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": prompt_content},
                        {"role": "user", "content": text},
                    ]
                )
                translated_text = response.choices[0].message.content.strip()

            elif model_provider == 'caiyun':
                if not api_key: raise ValueError("彩云小译需要 API Key")
                base_url = custom_base_url or "http://api.interpreter.caiyunai.com/v1"
                # Remove trailing slash if exists to avoid double slash issues, 
                # but requests usually handles it well. 
                # Caiyun endpoint is /translator
                if base_url.endswith("/"): base_url = base_url[:-1]
                url = f"{base_url}/translator"
                
                # 确定翻译方向，默认为 auto2zh（自动检测源语言翻译到中文）
                trans_type = "auto2zh"
                if target_language == 'en':
                    trans_type = "zh2en"
                elif target_language == 'ja':
                    trans_type = "zh2ja"
                # 也可以基于源语言确定翻译方向
                if 'japan' in str(model_name) or 'ja' in str(model_name):
                    trans_type = "ja2zh"
                elif 'en' in str(model_name):
                    trans_type = "en2zh"
                
                headers = {
                    "Content-Type": "application/json",
                    "X-Authorization": f"token {api_key}"
                }
                payload = {
                    "source": [text],
                    "trans_type": trans_type,
                    "request_id": f"comic_translator_{int(time.time())}",
                    "detect": True,
                    "media": "text"
                }
                
                response = requests.post(url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()
                if "target" in result and len(result["target"]) > 0:
                    translated_text = result["target"][0].strip()
                else:
                    raise ValueError(f"彩云小译返回格式错误: {result}")

            elif model_provider == 'sakura':
                base_url = custom_base_url or "http://localhost:8080/v1"
                if base_url.endswith("/"): base_url = base_url[:-1]
                url = f"{base_url}/chat/completions"
                
                headers = {"Content-Type": "application/json"}
                sakura_prompt = "你是一个轻小说翻译模型，可以流畅通顺地以日本轻小说的风格将日文翻译成简体中文，并联系上下文正确使用人称代词，不擅自添加原文中没有的代词。"
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": sakura_prompt},
                        {"role": "user", "content": f"将下面的日文文本翻译成中文：{text}"}
                    ]
                }
                response = requests.post(url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()
                choices = result.get('choices', [])
                if not choices:
                    raise ValueError("Sakura 返回空 choices")
                translated_text = choices[0]['message']['content'].strip()

            elif model_provider == 'ollama':
                base_url = custom_base_url or "http://localhost:11434/api"
                if base_url.endswith("/"): base_url = base_url[:-1]
                url = f"{base_url}/chat"
                
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": prompt_content},
                        {"role": "user", "content": text}
                    ],
                    "stream": False
                }
                response = requests.post(url, json=payload)
                response.raise_for_status()
                result = response.json()
                if "message" in result and "content" in result["message"]:
                    translated_text = result["message"]["content"].strip()
                else:
                    raise ValueError(f"Ollama返回格式错误: {result}")
                    
            elif model_provider == constants.BAIDU_TRANSLATE_ENGINE_ID:
                # 百度翻译API - 暂未实现接口
                raise NotImplementedError("百度翻译接口尚未移植")
            
            elif model_provider == constants.YOUDAO_TRANSLATE_ENGINE_ID:
                # 有道翻译API - 暂未实现接口
                raise NotImplementedError("有道翻译接口尚未移植")

            elif model_provider.lower() == 'gemini':
                if not api_key:
                    raise ValueError("Gemini 需要 API Key")
                if not model_name:
                    raise ValueError("Gemini 需要模型名称 (例如 gemini-1.5-flash-latest)")

                client = create_openai_client(
                    api_key=api_key,
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
                )
                
                gemini_messages = []
                if prompt_content:
                    gemini_messages.append({"role": "system", "content": prompt_content})
                gemini_messages.append({"role": "user", "content": text}) 

                response = client.chat.completions.create(
                    model=model_name,
                    messages=gemini_messages,
                )
                translated_text = response.choices[0].message.content.strip()

            elif model_provider == constants.CUSTOM_OPENAI_PROVIDER_ID:
                # Custom uses passed base_url (which comes from config.base_url which checks DB for 'custom')
                if not api_key:
                    raise ValueError("自定义 OpenAI 兼容服务需要 API Key")
                
                # Note: custom_base_url is already resolved in pipeline.py/config.py, 
                # but if it's somehow empty here, we raise error
                if not custom_base_url:
                    raise ValueError("自定义 OpenAI 兼容服务需要 Base URL")

                logger.info(f"使用自定义 OpenAI 兼容服务: Base URL='{custom_base_url}', Model='{model_name}'")
                client = create_openai_client(api_key=api_key, base_url=custom_base_url)
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": prompt_content},
                        {"role": "user", "content": text},
                    ],
                )
                translated_text = response.choices[0].message.content.strip()
            else:
                raise ValueError(f"不支持的翻译提供商: {model_provider}")

            # 解析JSON格式（如果需要）
            if use_json_format:
                translated_text = _safely_extract_from_json(translated_text, "translated_text")

            return translated_text

        except Exception as e:
            retry_count += 1
            # If checking connection (max_retries=0), raise immediately or return error
            if max_retries <= 0:
                # Special case for test_connection
                return f"翻译失败: {str(e)}"
                
            logger.error(f"翻译失败 (尝试 {retry_count}/{max_retries}): {e}")
            if retry_count >= max_retries:
                logger.error("达到最大重试次数，翻译失败。")
                return f"翻译失败: {str(e)}"
            time.sleep(1) # 重试前等待

    return translated_text

def test_connection(model_provider, api_key=None, model_name=None, base_url=None):
    """
    测试翻译服务连接。
    """
    logger.info(f"Testing connection for {model_provider}...")
    try:
        # Use a simple "Hello" translation task
        text = "Hello"
        
        # Call translate_single_text with max_retries=0 to fail fast
        # Note: base_url here might be explicit custom url or None (if auto-switch)
        # But translate_single_text expects 'custom_base_url' argument
        
        # If we are testing 'custom' provider, base_url is required.
        # If testing others, base_url might be None (will use default in translate_single_text)
        
        result = translate_single_text(
            text, 
            target_language='zh', 
            model_provider=model_provider, 
            api_key=api_key, 
            model_name=model_name,
            custom_base_url=base_url,
            max_retries=1, # At least 1 attempt
            rpm_limit_translation=0 # Ignore RPM for test
        )
        
        if "翻译失败" in result:
            return False, result
            
        return True, "连接成功！"
        
    except Exception as e:
        return False, str(e)
