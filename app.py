import ast
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import re
import string
import sys
import tempfile
import textwrap
import time
import unicodedata
import urllib.parse
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional

import fitz
import requests
from PyPDF2 import PdfReader
from ebooklib import epub
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from openai import OpenAI, OpenAIError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv

# 尝试导入豆包SDK
try:
    from volcenginesdkarkruntime import Ark
    ARK_SDK_AVAILABLE = True
except ImportError:
    ARK_SDK_AVAILABLE = False
    print("⚠️ volcenginesdkarkruntime 未安装，将使用 requests 方式调用API", flush=True)

# 添加 protocols 模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import websockets
    from protocols import (
        EventType,
        MsgType,
        finish_connection,
        finish_session,
        receive_message,
        start_connection,
        start_session,
        wait_for_event,
    )
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False


load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__, static_folder=".")
    CORS(app)

    database_url = os.environ.get(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db"),
    )
    engine = create_engine(database_url, future=True)

    deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "sk-2a870e378cb94696ab3a957a84ee5514")
    deepseek_base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
    chunk_token_limit = 2500
    responses_chunk_limit = 1500
    
    # 火山引擎播客配置
    volcengine_tts_access_key = os.environ.get("VOLCENGINE_TTS_ACCESS_KEY", "")
    volcengine_tts_secret_key = os.environ.get("VOLCENGINE_TTS_SECRET_KEY", "")
    volcengine_tts_app_id = os.environ.get("VOLCENGINE_TTS_APP_ID", "")
    volcengine_tts_resource_id = os.environ.get("VOLCENGINE_TTS_RESOURCE_ID", "volc.service_type.10050")

    def normalize_heading_for_match(text: str) -> str:
        if not text:
            return ""
        normalized = []
        for ch in text.lower():
            if ch.isalnum() or (ord(ch) >= 0x4e00 and ord(ch) <= 0x9fff):
                normalized.append(ch)
        return "".join(normalized)

    def truncate_content_by_child_titles(content: str, child_titles: List[str]) -> str:
        if not content or not child_titles:
            return content
        normalized_children = [normalize_heading_for_match(title) for title in child_titles if title]
        normalized_children = [title for title in normalized_children if title]
        if not normalized_children:
            return content
        lines = content.splitlines()
        cut_index: Optional[int] = None
        for idx, line in enumerate(lines):
            line_norm = normalize_heading_for_match(line)
            if not line_norm:
                continue
            if any(
                line_norm.startswith(title)
                or title.startswith(line_norm)
                for title in normalized_children
            ):
                cut_index = idx
                break
        if cut_index is None or cut_index == 0:
            return content
        truncated = "\n".join(lines[:cut_index]).strip()
        return truncated

    def column_exists(conn, table: str, column: str) -> bool:
        result = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(row[1] == column for row in result)

    def add_column_if_missing(conn, table: str, column: str, definition: str) -> None:
        if not column_exists(conn, table, column):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {definition}"))

    def init_db() -> None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            )

            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS interpretations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chapter_title TEXT NOT NULL,
                        user_profession TEXT NOT NULL,
                        reading_goal TEXT NOT NULL,
                        focus TEXT NOT NULL,
                        density TEXT NOT NULL,
                        chapter_text TEXT NOT NULL,
                        master_prompt TEXT NOT NULL,
                        result_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
            )

            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS books (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        chapter_count INTEGER NOT NULL,
                        total_word_count INTEGER NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
            )

            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS chapter_summaries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        book_id INTEGER,
                        chapter_title TEXT NOT NULL,
                        chapter_content TEXT NOT NULL,
                        chapter_title_zh TEXT,
                        chapter_content_zh TEXT,
                        summary TEXT NOT NULL,
                        word_count INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
                    )
                    """
                )
            )

            info = conn.execute(text("PRAGMA table_info(chapter_summaries)")).fetchall()
            existing_columns = [row[1] for row in info]
            required_columns = [
                "id",
                "book_id",
                "chapter_title",
                "chapter_content",
                "chapter_title_zh",
                "chapter_content_zh",
                "summary",
                "word_count",
                "created_at",
            ]

            if not all(col in existing_columns for col in required_columns):
                conn.execute(text("ALTER TABLE chapter_summaries RENAME TO chapter_summaries_backup"))
                conn.execute(
                    text(
                        """
                        CREATE TABLE chapter_summaries (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            book_id INTEGER,
                            chapter_title TEXT NOT NULL,
                            chapter_content TEXT NOT NULL,
                            chapter_title_zh TEXT,
                            chapter_content_zh TEXT,
                            summary TEXT NOT NULL,
                            word_count INTEGER NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
                        )
                        """
                    )
                )

                copy_columns = [
                    col
                    for col in [
                        "id",
                        "chapter_title",
                        "chapter_content",
                        "summary",
                        "word_count",
                        "created_at",
                    ]
                    if col in existing_columns
                ]
                if copy_columns:
                    cols = ", ".join(copy_columns)
                    conn.execute(
                        text(
                            f"INSERT INTO chapter_summaries ({cols}) "
                            f"SELECT {cols} FROM chapter_summaries_backup"
                        )
                    )
                conn.execute(text("DROP TABLE chapter_summaries_backup"))

    init_db()

    def store_setting(key: str, value: str) -> None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (:key, :value, :updated_at)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """
                ),
                {"key": key, "value": value, "updated_at": datetime.utcnow().isoformat()},
            )

    def load_setting(key: str, default: str = "") -> str:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT value FROM settings WHERE key = :key
                    """
                ),
                {"key": key},
            ).scalar_one_or_none()
        return result if result is not None else default

    def store_interpretation(
        payload: Dict[str, str],
        master_prompt: str,
        result: Dict[str, Any],
    ) -> int:
        with engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO interpretations (
                        chapter_title,
                        user_profession,
                        reading_goal,
                        focus,
                        density,
                        chapter_text,
                        master_prompt,
                        result_json,
                        created_at
                    )
                    VALUES (
                        :chapter_title,
                        :user_profession,
                        :reading_goal,
                        :focus,
                        :density,
                        :chapter_text,
                        :master_prompt,
                        :result_json,
                        :created_at
                    )
                    """
                ),
                {
                    "chapter_title": payload["chapterTitle"],
                    "user_profession": payload["userProfession"],
                    "reading_goal": payload["readingGoal"],
                    "focus": payload["focus"],
                    "density": payload["density"],
                    "chapter_text": payload["chapterText"],
                    "master_prompt": master_prompt,
                    "result_json": json.dumps(result, ensure_ascii=False),
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def store_book(filename: str, chapter_count: int, total_word_count: int) -> int:
        """存储书籍信息，返回书籍ID"""
        with engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO books (filename, chapter_count, total_word_count, created_at)
                    VALUES (:filename, :chapter_count, :total_word_count, :created_at)
                    """
                ),
                {
                    "filename": filename,
                    "chapter_count": chapter_count,
                    "total_word_count": total_word_count,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def check_chapter_exists(chapter_title: str, chapter_content: str) -> Optional[int]:
        """检查章节是否已存在，返回记录ID或None"""
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT id FROM chapter_summaries
                    WHERE chapter_title = :title AND chapter_content = :content
                    LIMIT 1
                    """
                ),
                {"title": chapter_title, "content": chapter_content},
            ).scalar_one_or_none()
        return result

    def store_chapter_summary(
        chapter_title: str,
        chapter_content: str,
        summary: str,
        word_count: int,
        book_id: Optional[int] = None,
        chapter_title_zh: Optional[str] = None,
        chapter_content_zh: Optional[str] = None,
    ) -> int:
        existing_id = check_chapter_exists(chapter_title, chapter_content)
        if existing_id is not None:
            return existing_id

        with engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO chapter_summaries (
                        book_id,
                        chapter_title,
                        chapter_content,
                        chapter_title_zh,
                        chapter_content_zh,
                        summary,
                        word_count,
                        created_at
                    )
                    VALUES (:book_id, :title, :content, :title_zh, :content_zh, :summary, :word_count, :created_at)
                    """
                ),
                {
                    "book_id": book_id,
                    "title": chapter_title,
                    "content": chapter_content,
                    "title_zh": chapter_title_zh,
                    "content_zh": chapter_content_zh,
                    "summary": summary,
                    "word_count": word_count,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def ensure_chinese_translation(
        original: str,
        existing_translation: Optional[str],
        field_label: str,
        api_key: str,
    ) -> str:
        translation = (existing_translation or "").strip()
        if translation and is_chinese_text(translation, threshold=0.2):
            return translation

        if not original or not original.strip():
            return ""

        if is_chinese_text(original):
            return original.strip()

        try:
            translated = call_doubao_translate(original, api_key)
            translated_clean = translated.strip() if translated else ""
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise RuntimeError(f"{field_label}翻译失败: {exc}")

        if not translated_clean:
            raise RuntimeError(f"{field_label}翻译失败：翻译结果为空")
        if translated_clean == original.strip():
            raise RuntimeError(f"{field_label}翻译失败：翻译结果与原文相同")
        if not is_chinese_text(translated_clean, threshold=0.2):
            raise RuntimeError(f"{field_label}翻译失败：翻译结果非中文")

        return translated_clean

    def generate_summary_for_entry(
        title_for_summary: str,
        content_for_summary: str,
        api_key: str,
    ) -> str:
        if not content_for_summary or not content_for_summary.strip():
            raise RuntimeError("章节内容为空，无法生成概要")

        try:
            summary = call_doubao_summary(title_for_summary, content_for_summary, api_key)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise RuntimeError(f"生成概要失败: {exc}")

        summary_clean = summary.strip() if summary else ""
        if not summary_clean:
            raise RuntimeError("生成概要失败：概要为空")
        return summary_clean

    def process_chapter_entry(
        entry: Dict[str, Any],
        api_key: str,
        book_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        title = (entry.get("title") or "").strip()
        content = entry.get("content") or ""

        if not title:
            raise RuntimeError("章节标题为空，无法入库")
        if not content or not content.strip():
            raise RuntimeError(f"章节《{title}》内容为空，无法入库")

        title_zh = (entry.get("title_zh") or "").strip()
        content_zh = entry.get("content_zh") or ""
        summary = (entry.get("summary") or "").strip()

        if not is_chinese_text(title):
            title_zh = ensure_chinese_translation(title, title_zh, "标题", api_key)
        elif not title_zh:
            title_zh = title

        if not is_chinese_text(content):
            content_zh = ensure_chinese_translation(content, content_zh, "正文内容", api_key)
        elif not content_zh:
            content_zh = content

        # 计算字数：优先使用中文内容，如果没有中文则使用原文
        content_for_count = content_zh if content_zh and content_zh.strip() else content
        normalized = "".join(ch for ch in content_for_count if not ch.isspace())
        word_count = len(normalized)

        summary_source_title = title_zh or title
        summary_source_content = content_zh or content
        if not summary:
            summary = generate_summary_for_entry(summary_source_title, summary_source_content, api_key)

        record_id = store_chapter_summary(
            title,
            content,
            summary,
            word_count,
            book_id=book_id,
            chapter_title_zh=title_zh,
            chapter_content_zh=content_zh,
        )

        processed = dict(entry)
        processed.update(
            {
                "title_zh": title_zh,
                "content_zh": content_zh,
                "summary": summary,
                "summary_id": record_id,
                "skipped": False,
                "word_count": word_count,
            }
        )
        return processed

    def parse_pdf_document(upload: io.BytesIO) -> List[Dict[str, Any]]:
        upload.seek(0)
        pdf_bytes = upload.read()

        def normalize_title(raw: Any) -> str:
            if raw is None:
                return ""

            if isinstance(raw, bytes):
                for encoding in ("utf-16", "utf-8", "gb18030", "latin-1"):
                    try:
                        return raw.decode(encoding).strip()
                    except UnicodeDecodeError:
                        continue
                return raw.decode("utf-8", errors="ignore").strip()

            text = str(raw).strip()
            text = unicodedata.normalize("NFKC", text)
            if text.startswith("b'") or text.startswith('b"'):
                try:
                    literal = ast.literal_eval(text)
                except (SyntaxError, ValueError):
                    literal = None
                if isinstance(literal, bytes):
                    return normalize_title(literal)

            if text.startswith(("þÿ", "ÿþ")):  # mis-decoded UTF-16
                encoded = text.encode("latin-1", errors="ignore")
                if len(encoded) % 2 != 0:
                    encoded = encoded[:-1]
                try:
                    text = encoded.decode("utf-16", errors="ignore")
                except UnicodeDecodeError:
                    pass

            if "\x00" in text:
                text = text.replace("\x00", "")

            try:
                encoded = text.encode("latin-1", errors="ignore")
                if len(encoded) >= 2 and encoded[:2] in {b"\xfe\xff", b"\xff\xfe"}:
                    text = encoded.decode("utf-16", errors="ignore")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass

            if text.startswith("\ufeff"):
                text = text.lstrip("\ufeff")
            text = "".join(
                ch for ch in text if unicodedata.category(ch) not in {"Cf", "Co", "Cs"}
            )
            return text

        def is_garbled(text: str) -> bool:
            cleaned = "".join(ch for ch in text if ch not in string.whitespace)
            if not cleaned:
                return True

            valid = 0
            for ch in cleaned:
                code = ord(ch)
                if ("\u4e00" <= ch <= "\u9fff") or ("\u3400" <= ch <= "\u4dbf"):
                    valid += 1
                elif ch.isalnum() or ch in "·—–-，。、《》：；？！" or ch in string.punctuation:
                    valid += 1
            return valid / len(cleaned) < 0.45

        heading_regex = re.compile(
            r"^第[\s〇零一二三四五六七八九十百千万两\dIVXLCDM]+[章节篇卷部节部分回目]?"
        )
        bare_heading_regex = re.compile(
            r"^第[〇零一二三四五六七八九十百千万两\dIVXLCDM]+(章|节|篇|卷|部|部分|回|目)$"
        )

        def guess_heading_from_page(page_index: int) -> str:
            if page_index < 0 or page_index >= doc.page_count:
                return ""
            page = doc.load_page(page_index)

            raw_text = page.get_text("text", flags=fitz.TEXT_PRESERVE_LIGATURES)
            for line in raw_text.splitlines():
                candidate = normalize_title(line).strip()
                if not candidate:
                    continue
                if len(candidate) > 120:
                    continue
                if heading_regex.match(candidate) and not is_garbled(candidate):
                    return candidate

            blocks = page.get_text("blocks")
            blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
            for block in blocks:
                if len(block) < 5:
                    continue
                text = normalize_title(block[4])
                candidate = text.strip().replace("\n", "")
                if not candidate:
                    continue
                candidate_clean = "".join(ch for ch in candidate if ch not in string.whitespace)
                if not candidate_clean:
                    continue
                if len(candidate_clean) > 80:
                    continue
                if is_garbled(candidate_clean):
                    continue
                return candidate.strip()
            return ""

        def title_metrics(text: str) -> Dict[str, float]:
            cleaned = "".join(ch for ch in text if ch not in string.whitespace)
            if not cleaned:
                return {"length": 0.0, "ch_ratio": 0.0, "weird_ratio": 1.0}

            chinese = sum(1 for ch in cleaned if ("\u4e00" <= ch <= "\u9fff") or ("\u3400" <= ch <= "\u4dbf"))
            allowed_ascii = sum(
                1
                for ch in cleaned
                if ch.isalnum() or ch in "·—–-，。、《》：；？！" or ch in string.punctuation
            )
            length = float(len(cleaned))
            weird = max(length - chinese - allowed_ascii, 0.0)

            return {
                "length": length,
                "ch_ratio": chinese / length,
                "weird_ratio": weird / length,
            }

        def choose_better_title(current: str, fallback: str) -> str:
            fallback = fallback.strip()
            if not fallback:
                return current

            metrics_current = title_metrics(current)
            metrics_fallback = title_metrics(fallback)

            if metrics_current["length"] == 0:
                return fallback

            improvements = 0
            if metrics_fallback["length"] >= metrics_current["length"] + 2:
                improvements += 1
            if metrics_fallback["ch_ratio"] > metrics_current["ch_ratio"] + 0.15:
                improvements += 1
            if metrics_fallback["weird_ratio"] + 0.1 < metrics_current["weird_ratio"]:
                improvements += 1
            if metrics_current["length"] <= 4 and metrics_fallback["length"] > metrics_current["length"]:
                improvements += 1

            return fallback if improvements >= 1 else current

        numeral_allowed = re.compile(r"^[〇零一二三四五六七八九十百千万两\dIVXLCDM]+$")

        def int_to_cn(num: int) -> str:
            digits = "零一二三四五六七八九"
            units = ["", "十", "百", "千"]

            if num <= 0:
                return ""
            if num < 10:
                return digits[num]
            if num < 20:
                return "十" if num == 10 else "十" + digits[num % 10]

            result = ""
            chars = list(str(num))
            length = len(chars)
            for idx, ch in enumerate(chars):
                digit = int(ch)
                unit_idx = length - idx - 1
                if digit == 0:
                    if result and not result.endswith("零") and unit_idx > 0:
                        result += "零"
                else:
                    result += digits[digit] + units[unit_idx]
            result = result.rstrip("零")
            result = result.replace("零零", "零")
            if result.startswith("一十"):
                result = result[1:]
            return result

        def extract_outline_entries(reader: PdfReader) -> List[Tuple[int, str, int]]:
            try:
                outline = reader.outline  # PyPDF2 >= 3.0.0
            except AttributeError:
                outline = reader.getOutlines()  # type: ignore[attr-defined]

            entries: List[Tuple[int, str, int]] = []

            def walk(items: Any, level: int) -> None:
                for item in items or []:
                    if isinstance(item, list):
                        walk(item, level + 1)
                        continue

                    raw_title = getattr(item, "title", None) or getattr(item, "title_", None) or str(item)
                    title = normalize_title(raw_title)
                    if not title:
                        continue

                    try:
                        page_number = reader.get_destination_page_number(item)
                    except Exception:  # pylint: disable=broad-except
                        continue

                    entries.append((level, title, page_number + 1))

            walk(outline, 1)
            return entries

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        reader = PdfReader(io.BytesIO(pdf_bytes))

        try:
            toc_entries = extract_outline_entries(reader)
            if not toc_entries:
                toc_entries = doc.get_toc(simple=True)

            normalized_entries: List[Tuple[int, str, int]] = []
            for level, title, page_number in toc_entries:
                normalized_title = normalize_title(title)
                if is_garbled(normalized_title):
                    fallback = guess_heading_from_page(page_number - 1)
                    if fallback:
                        normalized_title = fallback
                normalized_entries.append((level, normalized_title, page_number))
            raw_toc = normalized_entries

            # 使用大模型清洗目录
            api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
            if not api_key:
                raise ValueError("缺少 DOUBAO_API_KEY，无法清洗目录。")
            
            try:
                cleaned_toc = call_doubao_clean_toc(raw_toc, api_key)
                if not cleaned_toc:
                    # 如果清洗后目录为空，使用原始目录
                    cleaned_toc = raw_toc
            except Exception as exc:
                # 如果大模型清洗失败，记录错误但继续使用原始目录
                print(f"大模型清洗目录失败: {exc}", flush=True)
                cleaned_toc = raw_toc

            if not cleaned_toc:
                raise ValueError("未在 PDF 中检测到有效目录。")

            # 保留原来的 should_exclude_chapter 函数定义（虽然不再使用，但保留以防需要）
            def should_exclude_chapter(title: str) -> bool:
                """判断章节标题是否应该被排除（非正文内容）"""
                if not title or not title.strip():
                    return True
                
                title_lower = title.strip().lower()
                title_normalized = title.strip()
                
                # 检查是否是正文章节（有"第X章"等格式），如果是则保留
                is_main_chapter = bool(
                    re.search(r"^第[〇零一二三四五六七八九十百千万两\dIVXLCDM]+[章节篇卷部部分回目]", title_normalized)
                    or re.search(r"^(chapter|part|section|volume)\s*\d+", title_lower, re.IGNORECASE)
                )
                
                # 如果明确是正文章节，需要检查是否包含排除词
                if is_main_chapter:
                    # 即使有"第X章"，但如果标题明确是排除内容（如"第X章 前言"、"第X章 目录"），也要排除
                    exclusion_in_chapter_patterns = [
                        r"第.*?[章节].*?(前言|preface|foreword|introduction)",
                        r"第.*?[章节].*?(目录|contents)",
                        r"第.*?[章节].*?(附录|appendix)",
                        r"第.*?[章节].*?(索引|index)",
                        r"第.*?[章节].*?(参考文献|references|bibliography)",
                        r"第.*?[章节].*?(注释|尾注|脚注|notes)",
                        r"第.*?[章节].*?(序)$",
                    ]
                    for pattern in exclusion_in_chapter_patterns:
                        if re.search(pattern, title_normalized, re.IGNORECASE):
                            return True
                    # 如果是正文章节且不包含排除词，保留
                    return False
                
                # 必须完全匹配的前缀（用于精确匹配非正文内容）
                exact_prefixes = [
                    "版权", "copyright", "版权页", "版权信息",
                    "出版信息", "印刷信息", "出版说明", "印刷说明",
                    "推荐序", "编辑序", "再版序", "各版本序", "版本序",
                    "作者自序", "自序",
                    "前言", "introduction", "intro", "foreword", "preface",
                    "译者序", "翻译者前言", "翻译者后记",
                    "目录", "contents", "table of contents",
                    "致谢", "acknowledgment", "acknowledgments", "thanks",
                    "广告", "advertisement",
                    "注释", "尾注", "脚注",
                    "参考文献", "references", "bibliography", "works cited",
                    "索引", "index", "索引表",
                    "附录", "appendix", "appendices",
                    "作者简介", "作者介绍", "about the author", "author bio",
                    "译者简介", "译者介绍", "about the translator", "translator bio",
                ]
                
                # 检查是否以排除前缀开头
                for prefix in exact_prefixes:
                    if title_normalized.startswith(prefix) or title_lower.startswith(prefix.lower()):
                        return True
                
                # 使用正则表达式匹配排除模式（不匹配正文章节）
                exclusion_patterns = [
                    r"^(版权|copyright)",
                    r"^(出版|印刷|publication|printing)",
                    r"^推荐序|^.*推荐序$",
                    r"^编辑序|^.*编辑序$",
                    r"^再版序|^.*再版序$",
                    r"^(作者)?自序$",
                    r"^前言$|^preface$|^foreword$|^introduction$",
                    r"^译者序|^.*译者序$",
                    r"^翻译者.*(前言|后记)",
                    r"^目录$|^contents$|^table of contents$",
                    r"^致谢$|^thanks$|^acknowledgment",
                    r"^广告$|^advertisement$",
                    r".*其他作品.*",
                    r"^注释$|^尾注$|^脚注$|^notes$|^footnotes$",
                    r"^参考文献$|^references$|^bibliography$",
                    r"^索引$|^index$",
                    r"^附录$|^appendix$",
                    r".*(作者|译者).*(简介|介绍|bio|biography)$",
                    r"^about.*(author|translator)",
                ]
                
                for pattern in exclusion_patterns:
                    if re.search(pattern, title_normalized, re.IGNORECASE | re.UNICODE):
                        return True
                
                return False

            results: List[Dict[str, Any]] = []
            level_counters: Dict[int, int] = {}
            chapter_markers = ["章", "节", "篇", "卷", "部", "部分", "回", "目"]

            default_marker = {1: "章", 2: "章", 3: "节", 4: "节"}

            for index, (level, title, start_page) in enumerate(cleaned_toc):
                for deeper in [lvl for lvl in level_counters if lvl > level]:
                    level_counters.pop(deeper, None)
                current_index = level_counters.get(level, 0) + 1
                level_counters[level] = current_index

                stripped = title.strip()
                normalized_flag = False
                intrinsic_marker = next((marker for marker in chapter_markers if marker in title), None)
                if stripped.startswith("第"):
                    match = re.match(
                        r"^第(?P<num>[^\s章节篇卷部部分回目]{1,24})(?P<mark>章|节|篇|卷|部|部分|回|目)?",
                        stripped,
                    )
                    if match:
                        num_segment = match.group("num")
                        mark = match.group("mark") or intrinsic_marker or default_marker.get(level, "章")
                        tail = stripped[match.end() :].lstrip()
                        if not numeral_allowed.fullmatch(num_segment):
                            cn_numeral = int_to_cn(current_index)
                            stripped = f"第{cn_numeral}{mark} {tail}".strip()
                            normalized_flag = True
                        elif match.group("mark") is None:
                            stripped = f"第{num_segment}{mark} {tail}".strip()
                            normalized_flag = True
                if not normalized_flag:
                    for marker in chapter_markers:
                        if marker in stripped and stripped.startswith("第"):
                            prefix, suffix = stripped.split(marker, 1)
                            numeral_section = prefix[1:].strip()
                            if not numeral_allowed.fullmatch(numeral_section):
                                cn_numeral = int_to_cn(current_index)
                                stripped = f"第{cn_numeral}{marker}{suffix}"
                            break

                fallback_title = guess_heading_from_page(start_page - 1)
                if bare_heading_regex.fullmatch(stripped) or is_garbled(stripped.split(maxsplit=1)[-1]):
                    if fallback_title:
                        stripped = fallback_title.strip()
                elif fallback_title:
                    stripped = choose_better_title(stripped, fallback_title)

                start_idx = max(start_page - 1, 0)
                end_idx = doc.page_count - 1
                for next_entry in cleaned_toc[index + 1 :]:
                    if next_entry[0] <= level:
                        end_idx = max(next_entry[2] - 2, start_idx)
                        break

                page_text: List[str] = []
                for page_id in range(start_idx, end_idx + 1):
                    page = doc.load_page(page_id)
                    extracted = page.get_text("text")
                    if extracted:
                        page_text.append(extracted.strip())

                content = "\n".join(part for part in page_text if part).strip()
                normalized = "".join(ch for ch in content if not ch.isspace())
                word_count = len(normalized)

                results.append(
                    {
                        "title": stripped,
                        "content": content,
                        "word_count": word_count,
                    }
                )

            return results
        finally:
            doc.close()

    def build_generation_prompt(prompt_parts: Dict[str, str], payload: Dict[str, str]) -> str:
        """根据用户提供的提示词部分构建完整的生成提示词"""
        # 将 density 映射到 explanation_density 格式
        density_map = {
            "20% 主干": "20% 主干",
            "50% 核心": "50% 核心",
            "70% 深度": "70% 深度",
        }
        explanation_density = density_map.get(payload.get("density", "50% 核心"), "50% 核心")
        
        # 构建输入数据
        chapter_summary = payload.get("chapterSummary", payload.get("chapterTitle", ""))
        chapter_fulltext = payload.get("chapterText", "")
        
        input_data_json = json.dumps({
            "user_profile": {
                "profession": payload.get("userProfession", ""),
                "reading_goal": payload.get("readingGoal", ""),
                "focus_preference": payload.get("focus", ""),
                "explanation_density": explanation_density,
            },
            "chapter_summary": chapter_summary,
            "chapter_fulltext": chapter_fulltext,
        }, ensure_ascii=False, indent=2)
        
        # 组合用户提供的提示词部分，并替换占位符
        intro_prompt = prompt_parts.get("intro_prompt", "").strip()
        body_prompt = prompt_parts.get("body_prompt", "").strip()
        quiz_prompt = prompt_parts.get("quiz_prompt", "").strip()
        question_prompt = prompt_parts.get("question_prompt", "").strip()
        
        # 替换占位符
        placeholders = {
            "{chapter_fulltext}": chapter_fulltext,
            "{chapter_summary}": chapter_summary,
            "{chapter_title}": payload.get("chapterTitle", ""),
        }
        
        for placeholder, value in placeholders.items():
            if placeholder in body_prompt:
                body_prompt = body_prompt.replace(placeholder, value)
            if placeholder in intro_prompt:
                intro_prompt = intro_prompt.replace(placeholder, value)
            if placeholder in quiz_prompt:
                quiz_prompt = quiz_prompt.replace(placeholder, value)
            if placeholder in question_prompt:
                question_prompt = question_prompt.replace(placeholder, value)
        
        # 组合完整的提示词
        combined_prompt = ""
        if intro_prompt:
            combined_prompt += f"{intro_prompt}\n\n"
        if body_prompt:
            combined_prompt += f"{body_prompt}\n\n"
        if quiz_prompt:
            combined_prompt += f"{quiz_prompt}\n\n"
        if question_prompt:
            combined_prompt += f"{question_prompt}\n\n"
        
        # 构建最终提示词
        return textwrap.dedent(
            f"""
            {combined_prompt.strip()}

            请严格按照上述要求，基于以下输入生成解读内容：

            {input_data_json}

            【输出要求】
            1. 直接输出有效的 JSON 格式，不要有任何额外的说明文字、前缀或后缀
            2. 不要使用 markdown 代码块标记（如 ```json 或 ```）
            3. 输出的第一个字符必须是 {{，最后一个字符必须是 }}

            【输出格式】
            必须是一个有效的 JSON 对象，包含以下字段：
            {{
              "personalized_intro": "个性化导读摘要（字符串）",
              "interpretation": "正文讲解（字符串）",
              "summary_and_application": "现实生活中的举一反三应用（字符串）",
              "powerful_questions": "一个最强有力的思考问题（字符串）",
              "quiz": [
                {{
                  "question": "题目内容",
                  "options": ["选项A", "选项B", "选项C", "选项D"],
                  "answer": "正确答案",
                  "explanation": "解析说明"
                }}
              ]
            }}

            现在请直接输出 JSON，不要有任何其他内容：
            """
        ).strip()

    def _strip_deepseek_reasoning(content: str) -> str:
        """移除DeepSeek R1返回中的<think>推理和多余前缀，只保留最终译文"""
        if not content:
            return ""
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.IGNORECASE | re.DOTALL)
        # DeepSeek可能在最终回答前加上"Answer:"或"最终答案:"等提示
        for marker in ["Answer:", "Final Answer:", "最终答案：", "最终答案:"]:
            if marker in cleaned:
                cleaned = cleaned.split(marker, 1)[-1]
        return cleaned.strip()

    def _extract_thinking_and_json(content: str) -> tuple[str, str]:
        """提取豆包深度思考模型的推理过程和JSON部分
        
        返回: (thinking_process, json_content)
        """
        if not content:
            return "", ""
        
        thinking_process = ""
        json_content = content
        
        # 提取推理过程（可能存在的格式）
        # 格式1: <thinking>...</thinking>
        thinking_match = re.search(r'<thinking>(.*?)</thinking>', content, re.IGNORECASE | re.DOTALL)
        if thinking_match:
            thinking_process = thinking_match.group(1).strip()
            json_content = re.sub(r'<thinking>.*?</thinking>', '', json_content, flags=re.IGNORECASE | re.DOTALL)
        
        # 格式2: <reasoning>...</reasoning>
        reasoning_match = re.search(r'<reasoning>(.*?)</reasoning>', content, re.IGNORECASE | re.DOTALL)
        if reasoning_match:
            if thinking_process:
                thinking_process += "\n\n---\n\n"
            thinking_process += reasoning_match.group(1).strip()
            json_content = re.sub(r'<reasoning>.*?</reasoning>', '', json_content, flags=re.IGNORECASE | re.DOTALL)
        
        # 格式3: 豆包可能使用其他格式，如 ```thinking ... ``` 或 [思考过程] ... [/思考过程]
        thinking_code_match = re.search(r'```thinking\s*(.*?)```', content, re.IGNORECASE | re.DOTALL)
        if thinking_code_match:
            if thinking_process:
                thinking_process += "\n\n---\n\n"
            thinking_process += thinking_code_match.group(1).strip()
            json_content = re.sub(r'```thinking\s*.*?```', '', json_content, flags=re.IGNORECASE | re.DOTALL)
            print(f"✅ 使用格式3提取到思考过程（长度: {len(thinking_code_match.group(1))} 字符）", flush=True)
        
        # 格式4: 豆包可能直接在内容前面输出思考过程，没有标签（尝试提取JSON之前的所有内容）
        if not thinking_process:
            # 查找第一个 { 的位置
            json_start = content.find('{')
            if json_start > 100:  # 如果JSON之前有大量内容，可能是思考过程
                potential_thinking = content[:json_start].strip()
                # 检查是否包含思考相关的关键词
                thinking_keywords = ['思考', '分析', '理解', '需求', '用户', '章节', '内容', '组织', '选择', '设计']
                if any(keyword in potential_thinking for keyword in thinking_keywords):
                    thinking_process = potential_thinking
                    json_content = content[json_start:]
                    print(f"✅ 使用格式4提取到思考过程（长度: {len(thinking_process)} 字符）", flush=True)
        
        # 清理JSON部分
        # 移除可能存在的markdown代码块标记
        json_content = re.sub(r'```json\s*', '', json_content, flags=re.IGNORECASE)
        json_content = re.sub(r'```\s*$', '', json_content, flags=re.MULTILINE)
        json_content = re.sub(r'^```\s*', '', json_content, flags=re.MULTILINE)
        
        # 移除常见的非JSON前缀
        for marker in [
            "以下是", "以下是JSON", "JSON格式如下", "JSON:", "json:",
            "Answer:", "回答:", "输出:", "结果:", "生成结果:",
            "根据要求，", "按照要求，", "以下是解读结果："
        ]:
            if json_content.strip().startswith(marker):
                json_content = json_content.replace(marker, "", 1).strip()
        
        # 移除可能的说明文字（在JSON之前）
        lines = json_content.split('\n')
        json_start_idx = -1
        for i, line in enumerate(lines):
            if line.strip().startswith('{'):
                json_start_idx = i
                break
        if json_start_idx > 0:
            json_content = '\n'.join(lines[json_start_idx:])
        
        return thinking_process.strip(), json_content.strip()
    
    def _clean_doubao_response(content: str) -> str:
        """清理豆包深度思考模型的返回内容，提取JSON部分（保留向后兼容）"""
        _, json_content = _extract_thinking_and_json(content)
        return json_content

    def _fix_json_control_chars(json_str: str) -> str:
        """修复JSON字符串中的控制字符问题"""
        if not json_str:
            return json_str
        
        # 方法1: 使用json.loads的strict=False模式（但这需要修改解析方式）
        # 方法2: 手动转义字符串中的控制字符
        
        # 尝试找到JSON对象的结构，然后修复字符串值中的控制字符
        try:
            # 使用正则表达式找到所有字符串值，并转义其中的控制字符
            # 这个正则匹配JSON字符串值（在引号内的内容）
            def escape_control_chars(match):
                # 获取匹配的字符串内容（不包括引号）
                full_match = match.group(0)
                quote_char = match.group(1)  # 引号字符 " 或 '
                string_content = match.group(2)  # 字符串内容
                
                # 转义控制字符（保留已转义的字符）
                # 替换未转义的换行符、制表符等
                fixed_content = string_content
                
                # 转义换行符（但保留已转义的\n）
                fixed_content = re.sub(r'(?<!\\)\n', '\\n', fixed_content)
                # 转义回车符
                fixed_content = re.sub(r'(?<!\\)\r', '\\r', fixed_content)
                # 转义制表符
                fixed_content = re.sub(r'(?<!\\)\t', '\\t', fixed_content)
                # 转义其他控制字符（ASCII 0-31，除了已处理的）
                # 但要注意不要破坏已转义的字符
                
                return quote_char + fixed_content + quote_char
            
            # 匹配JSON字符串（处理转义的引号和未转义的引号）
            # 这是一个简化的方法：先尝试解析，如果失败则修复
            return json_str
            
        except Exception:
            return json_str
    
    def _fix_json_string(json_str: str) -> str:
        """修复JSON字符串中的控制字符和格式问题
        
        逐字符扫描JSON字符串：
        1. 在字符串值内部：转义所有未转义的控制字符（ASCII 0-31）
        2. 在字符串外部：只保留允许的空白字符（\n, \r, \t, 空格），移除其他控制字符
        """
        if not json_str:
            return json_str
        
        result = []
        in_string = False
        escape_next = False
        i = 0
        
        # 允许的空白字符（在字符串外部）
        allowed_whitespace = {'\n', '\r', '\t', ' '}
        
        while i < len(json_str):
            char = json_str[i]
            char_code = ord(char)
            
            # 处理转义字符
            if escape_next:
                # 当前字符是转义后的，直接添加（不进行控制字符检查）
                result.append(char)
                escape_next = False
                i += 1
                continue
            
            # 遇到反斜杠，下一个字符是转义的
            if char == '\\':
                result.append(char)
                escape_next = True
                i += 1
                continue
            
            # 遇到双引号，切换字符串状态
            if char == '"':
                in_string = not in_string
                result.append(char)
                i += 1
                continue
            
            # 在字符串内部：转义控制字符
            if in_string:
                # ASCII控制字符（0-31）需要转义
                if char_code < 32:
                    if char == '\n':
                        result.append('\\n')
                    elif char == '\r':
                        result.append('\\r')
                    elif char == '\t':
                        result.append('\\t')
                    elif char == '\b':
                        result.append('\\b')
                    elif char == '\f':
                        result.append('\\f')
                    elif char == '\x00':
                        result.append('\\u0000')
                    else:
                        # 其他控制字符使用Unicode转义
                        result.append(f'\\u{char_code:04x}')
                else:
                    result.append(char)
            else:
                # 在字符串外部：只保留允许的空白字符，移除其他控制字符
                if char_code < 32:
                    if char in allowed_whitespace:
                        # 保留允许的空白字符
                        result.append(char)
                    else:
                        # 移除其他控制字符（不添加到结果中）
                        pass  # 跳过这个字符
                else:
                    result.append(char)
            
            i += 1
        
        return ''.join(result)

    def _add_debug_info_to_result(result: Dict[str, Any], debug_info: Dict[str, Any], content_text: str) -> Dict[str, Any]:
        """为结果添加调试信息和思考过程"""
        print(f"\n🔍 _add_debug_info_to_result 开始处理", flush=True)
        print(f"   content_text 长度: {len(content_text) if content_text else 0} 字符", flush=True)
        print(f"   content_text 前200字符: {content_text[:200] if content_text else 'N/A'}...", flush=True)
        
        # 提取思考过程
        thinking_process, _ = _extract_thinking_and_json(content_text)
        print(f"   第一次提取结果: thinking_process 长度={len(thinking_process) if thinking_process else 0}", flush=True)
        
        # 如果没有检测到思考过程，但JSON之前有内容，也尝试添加
        if not thinking_process:
            json_start = content_text.find('{')
            print(f"   未检测到思考过程，检查JSON位置: {json_start}", flush=True)
            if json_start > 100:
                potential_thinking = content_text[:json_start].strip()
                print(f"   JSON之前的内容长度: {len(potential_thinking)} 字符", flush=True)
                if len(potential_thinking) > 200:
                    thinking_process = potential_thinking
                    print(f"   ✅ 使用JSON之前的内容作为思考过程（长度: {len(thinking_process)} 字符）", flush=True)
        
        # 如果没有检测到推理过程，尝试从原始内容中提取（可能格式不同）
        if not thinking_process:
            print(f"   尝试使用正则表达式提取思考过程...", flush=True)
            raw_thinking_patterns = [
                r'思考[：:]\s*(.*?)(?=\n\n|\n\{|$)',
                r'推理[：:]\s*(.*?)(?=\n\n|\n\{|$)',
                r'分析[：:]\s*(.*?)(?=\n\n|\n\{|$)',
            ]
            for pattern in raw_thinking_patterns:
                match = re.search(pattern, content_text, re.IGNORECASE | re.DOTALL)
                if match:
                    raw_thinking = match.group(1).strip()
                    if len(raw_thinking) > 50:
                        thinking_process = raw_thinking
                        print(f"   ✅ 使用正则表达式提取到思考过程（长度: {len(thinking_process)} 字符）", flush=True)
                        break
        
        # 添加思考过程
        if thinking_process:
            result["_thinking_process"] = thinking_process
            print(f"✅ 思考过程已添加到返回结果中（长度: {len(thinking_process)} 字符）", flush=True)
            print(f"   思考过程前500字符: {thinking_process[:500]}...", flush=True)
        else:
            print("⚠️ 未检测到思考过程", flush=True)
            print(f"   content_text 中是否包含 '<thinking>': {'<thinking>' in content_text if content_text else False}", flush=True)
            print(f"   content_text 中是否包含 '思考': {'思考' in content_text if content_text else False}", flush=True)
        
        # 添加调试信息
        result["_debug_info"] = debug_info
        print(f"✅ 调试信息已添加到返回结果中: model={debug_info.get('model')}", flush=True)
        print(f"🔍 _add_debug_info_to_result 处理完成\n", flush=True)
        
        return result

    def call_llm(prompt_parts: Dict[str, str], payload: Dict[str, str]) -> Dict[str, Any]:
        """使用豆包深度思考模型进行解读生成"""
        api_key = os.environ.get("DOUBAO_API_KEY") or os.environ.get("ARK_API_KEY") or load_setting("doubao_api_key", "") or load_setting("ark_api_key", "")
        if not api_key:
            raise RuntimeError("缺少 DOUBAO_API_KEY 或 ARK_API_KEY，请在环境变量中配置豆包密钥。")

        # 豆包深度思考模型（根据官方示例，使用 doubao-seed-1-6-251015）
        model = "doubao-seed-1-6-251015"
        
        # 构建用户提示词（参考豆包示例，不使用系统消息，让模型自然深度思考）
        prompt = build_generation_prompt(prompt_parts, payload)

        # 从payload中读取参数，如果没有则使用默认值
        temperature = float(payload.get("temperature", 0.3))
        max_tokens = int(payload.get("max_tokens", 16000))
        thinking_enabled = payload.get("thinking", "auto")  # "auto" 表示模型自动触发
        
        # 保存调试信息，用于后续添加到返回结果中
        debug_info = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking": "auto (模型自动触发)" if thinking_enabled == "auto" else thinking_enabled,  # 模型会自动触发深度思考，不需要显式传递参数
            "stream": False,
        }
        
        try:
            # 优先使用 SDK 方式调用
            if ARK_SDK_AVAILABLE:
                print(f"✅ 使用 volcenginesdkarkruntime SDK 调用豆包深度思考模型: 模型={model}, 深度思考模式=已启用", flush=True)
                
                # 构建 base_url（默认使用官方地址）
                base_url = "https://ark.cn-beijing.volces.com/api/v3"
                configured_base = (
                    os.environ.get("DOUBAO_API_BASE")
                    or load_setting("doubao_api_base", "")
                ).strip()
                
                if configured_base:
                    configured_base = configured_base.rstrip("/")
                    if configured_base.endswith("/api/v3"):
                        base_url = configured_base
                    elif configured_base.endswith("/api/v3/chat/completions"):
                        base_url = configured_base.replace("/chat/completions", "")
                    elif "/api/v3" not in configured_base:
                        base_url = f"{configured_base}/api/v3"
                    else:
                        base_url = configured_base
                
                print(f"📡 使用 base_url: {base_url}", flush=True)
                print(f"🔑 API Key 长度: {len(api_key) if api_key else 0}", flush=True)
                print(f"⚙️ 参数: temperature={temperature}, max_tokens={max_tokens}, thinking={thinking_enabled}", flush=True)
                
                # 严格按照官方示例代码初始化客户端
                client = Ark(
                    base_url=base_url,
                    api_key=api_key,
                    timeout=1800,  # 30分钟超时，深度思考模型需要较长时间
                )
                
                print(f"📤 发送请求: model={model} (模型会自动触发深度思考)", flush=True)
                
                # 构建API调用参数（参考豆包示例，不使用系统消息，直接传递用户提示词）
                api_params = {
                    "model": model,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                
                # 如果thinking不是"auto"，则传递thinking参数（如果需要显式启用）
                # 注意：深度思考模型通常会自动触发，但为了灵活性，我们支持传递参数
                if thinking_enabled != "auto" and thinking_enabled:
                    if isinstance(thinking_enabled, dict):
                        api_params["thinking"] = thinking_enabled
                    elif thinking_enabled.lower() in ["enabled", "true", "1"]:
                        api_params["thinking"] = {"type": "enabled"}
                
                # 严格按照官方示例代码调用（不需要传递 thinking 参数，模型会自动触发深度思考）
                response = client.chat.completions.create(**api_params)
                
                # 严格按照官方文档和测试结果提取内容
                # 响应结构: response.choices[0].message.content
                # 深度思考内容在: response.choices[0].message.reasoning_content
                choice = response.choices[0]
                message = choice.message
                content_text = message.content
                
                # 确保 content_text 是字符串
                if not isinstance(content_text, str):
                    content_text = str(content_text) if content_text else ""
                
                content_text = content_text.strip()
                
                print(f"✅ 成功提取 content_text，长度: {len(content_text)} 字符", flush=True)
                
                # 深度思考模型返回的思考过程在 message.reasoning_content 中
                # 注意：我们直接保存 reasoning_content，不添加到 content_text 中
                # 因为 _add_debug_info_to_result 会处理思考过程的提取和添加
                reasoning_content = None
                if hasattr(message, 'reasoning_content') and message.reasoning_content:
                    reasoning_content = message.reasoning_content
                    if isinstance(reasoning_content, str) and reasoning_content.strip():
                        print(f"✅ 检测到 reasoning_content（深度思考内容，长度: {len(reasoning_content)} 字符）", flush=True)
                        # 将 reasoning_content 添加到 content_text 前面，用标签包裹，这样后续的 _extract_thinking_and_json 可以提取
                        content_text = f"<thinking>\n{reasoning_content}\n</thinking>\n\n{content_text}"
                    else:
                        print(f"⚠️ reasoning_content 存在但为空", flush=True)
                else:
                    print(f"⚠️ 未找到 reasoning_content 字段", flush=True)
                    # 检查 message 的所有属性，看看是否有其他字段
                    print(f"📋 message 的所有属性: {[attr for attr in dir(message) if not attr.startswith('_')]}", flush=True)
                
                debug_info["endpoint"] = f"SDK (volcenginesdkarkruntime) - {base_url}"
                debug_info["method"] = "SDK"
                debug_info["base_url"] = base_url
                
            else:
                # 降级使用 requests 方式
                print(f"⚠️ 使用 requests 方式调用豆包深度思考模型: 模型={model}", flush=True)
                
                # 构建端点
                configured_base = (
                    os.environ.get("DOUBAO_API_BASE")
                    or load_setting("doubao_api_base", "")
                ).strip()
                
                if configured_base:
                    configured_base = configured_base.rstrip("/")
                    if configured_base.endswith("/chat/completions"):
                        endpoint = configured_base
                    else:
                        endpoint = f"{configured_base}/chat/completions"
                else:
                    endpoint = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                }

                request_payload = {
                    "model": model,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                
                # 如果thinking不是"auto"，则传递thinking参数
                if thinking_enabled != "auto" and thinking_enabled:
                    if isinstance(thinking_enabled, dict):
                        request_payload["thinking"] = thinking_enabled
                    elif thinking_enabled.lower() in ["enabled", "true", "1"]:
                        request_payload["thinking"] = {
                            "type": "enabled"  # 使用正确的 thinking 参数格式
                        }
                
                debug_info["endpoint"] = endpoint
                debug_info["method"] = "requests"
                
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=request_payload,
                    timeout=1800,  # 30分钟超时
                )
                
                response.raise_for_status()
                data = response.json()

                if "choices" not in data or not data["choices"]:
                    raise RuntimeError("豆包接口未返回任何结果。")

                message = data["choices"][0].get("message", {})
                content_text = message.get("content", "").strip()
                
                # 检查是否有 reasoning_content（深度思考内容）
                reasoning_content = message.get("reasoning_content")
                if reasoning_content and isinstance(reasoning_content, str) and reasoning_content.strip():
                    print(f"✅ 检测到 reasoning_content（深度思考内容，长度: {len(reasoning_content)} 字符）", flush=True)
                    # 将 reasoning_content 添加到 content_text 前面，用标签包裹，这样后续的 _extract_thinking_and_json 可以提取
                    content_text = f"<thinking>\n{reasoning_content}\n</thinking>\n\n{content_text}"
                else:
                    print(f"⚠️ 未找到 reasoning_content 字段", flush=True)
                    print(f"📋 message 的键: {list(message.keys())}", flush=True)
            
            # 处理content可能是数组格式的情况
            if isinstance(content_text, list):
                text_content = ""
                for item in content_text:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_content += item.get("text", "")
                content_text = text_content.strip()

            if not content_text:
                raise RuntimeError("豆包接口返回的内容为空。")

            # 记录原始返回内容（用于调试）
            print(f"📝 原始返回内容长度: {len(content_text)} 字符", flush=True)
            print(f"📝 原始返回内容前1000字符:\n{content_text[:1000]}\n", flush=True)
            print(f"📝 原始返回内容后1000字符:\n{content_text[-1000:] if len(content_text) > 1000 else content_text}\n", flush=True)
            
            # 检查是否包含thinking标签
            if '<thinking>' in content_text or '<thinking>' in content_text.lower():
                print(f"✅ 原始内容中包含 <thinking> 标签", flush=True)
            else:
                print(f"⚠️ 原始内容中不包含 <thinking> 标签", flush=True)
            
            # 检查JSON部分的大概位置
            json_start = content_text.find('{')
            if json_start > 0:
                print(f"📊 JSON开始位置: {json_start} 字符，之前的内容长度: {json_start} 字符", flush=True)
                if json_start > 100:
                    print(f"📊 JSON之前的内容可能是思考过程，前500字符:\n{content_text[:min(500, json_start)]}\n", flush=True)
            
            # 提取推理过程和JSON内容
            thinking_process, json_content = _extract_thinking_and_json(content_text)
            
            # 记录推理过程（如果有）
            if thinking_process:
                print(f"✅ 检测到推理过程（长度: {len(thinking_process)} 字符）", flush=True)
                print(f"推理过程完整内容:\n{'='*80}\n{thinking_process}\n{'='*80}\n", flush=True)
            else:
                print("⚠️ 未检测到推理过程，可能模型未返回思考过程", flush=True)
                print(f"⚠️ 原始内容中是否包含 'thinking': {'thinking' in content_text.lower()}", flush=True)
                print(f"⚠️ 原始内容中是否包含 '<': {'<' in content_text}", flush=True)
                print(f"⚠️ 原始内容中是否包含 '思考': {'思考' in content_text}", flush=True)
                
                # 尝试显示JSON之前的所有内容（可能是思考过程但没有标签）
                if json_start > 100:
                    potential_thinking = content_text[:json_start].strip()
                    print(f"⚠️ JSON之前的内容（可能是未标记的思考过程，长度: {len(potential_thinking)} 字符）:\n{potential_thinking[:500]}...\n", flush=True)
            
            # 修复JSON中的控制字符问题
            fixed_content = _fix_json_string(json_content)
            
            # 调试：检查修复前后的差异
            if json_content != fixed_content:
                # 统计修复的控制字符数量
                fixed_chars = 0
                for i, (a, b) in enumerate(zip(json_content, fixed_content)):
                    if a != b:
                        fixed_chars += 1
                        if fixed_chars == 1:  # 只记录第一个
                            char_code = ord(a) if i < len(json_content) else 0
                            print(f"检测到控制字符并已修复（位置: {i}，原字符码: {char_code}，字符: {repr(a)}）", flush=True)
                print(f"共修复 {fixed_chars} 个控制字符", flush=True)
            
            # 验证修复后的JSON是否还有控制字符（在字符串外部）
            has_control_chars = False
            in_str = False
            escape = False
            for i, c in enumerate(fixed_content):
                if escape:
                    escape = False
                    continue
                if c == '\\':
                    escape = True
                    continue
                if c == '"':
                    in_str = not in_str
                    continue
                if not in_str and ord(c) < 32 and c not in '\n\r\t ':
                    if not has_control_chars:
                        print(f"警告：修复后的JSON在字符串外部仍有控制字符（位置: {i}，字符: {repr(c)}，ASCII: {ord(c)}）", flush=True)
                        has_control_chars = True
            
            # 尝试解析 JSON
            try:
                result = json.loads(fixed_content)
                print(f"✅ JSON解析成功！", flush=True)
                
                # 检查interpretation字段的长度
                if "interpretation" in result:
                    interpretation_length = len(result["interpretation"])
                    print(f"📊 interpretation字段长度: {interpretation_length} 字符", flush=True)
                    if interpretation_length < 1000:
                        print(f"⚠️ 警告：interpretation字段过短（{interpretation_length} 字符），应该至少1500-3000字", flush=True)
                        print(f"⚠️ 建议：检查提示词是否明确要求了足够的输出长度", flush=True)
                    elif interpretation_length < 2000:
                        print(f"⚠️ 注意：interpretation字段较短（{interpretation_length} 字符），如果原始内容较长，建议输出更多", flush=True)
                    else:
                        print(f"✅ interpretation字段长度正常（{interpretation_length} 字符）", flush=True)
                
                # 添加调试信息和思考过程
                result = _add_debug_info_to_result(result, debug_info, content_text)
                
                return result
            except json.JSONDecodeError as e:
                # 记录详细的错误信息
                error_pos = getattr(e, 'pos', None)
                error_msg = str(e)
                print(f"JSON解析失败: {error_msg}", flush=True)
                
                if error_pos is not None:
                    print(f"错误位置: {error_pos} (总长度: {len(fixed_content)})", flush=True)
                    
                    # 显示错误位置的上下文
                    context_start = max(0, error_pos - 100)
                    context_end = min(len(fixed_content), error_pos + 100)
                    error_context = fixed_content[context_start:context_end]
                    
                    # 标记错误位置
                    marker_pos = error_pos - context_start
                    marked_context = error_context[:marker_pos] + ">>>ERROR<<<" + error_context[marker_pos:marker_pos+1] + error_context[marker_pos+1:]
                    
                    print(f"错误上下文: {marked_context}", flush=True)
                    
                    # 显示错误字符的详细信息
                    if error_pos < len(fixed_content):
                        error_char = fixed_content[error_pos]
                        char_code = ord(error_char)
                        print(f"错误字符: {repr(error_char)}, ASCII码: {char_code}, Unicode: U+{char_code:04X}", flush=True)
                        
                        # 检查是否是控制字符
                        if char_code < 32:
                            print(f"这是一个控制字符，应该在字符串内部被转义", flush=True)
                            # 尝试再次修复（可能第一次修复有问题）
                            print("尝试重新修复控制字符...", flush=True)
                            re_fixed = _fix_json_string(fixed_content)
                            if re_fixed != fixed_content:
                                print("重新修复后发现变化，尝试解析...", flush=True)
                                try:
                                    result = json.loads(re_fixed)
                                    # 添加调试信息和思考过程
                                    result = _add_debug_info_to_result(result, debug_info, content_text)
                                    return result
                                except:
                                    pass
                    
                    # 显示修复前后的对比
                    if error_pos < len(fixed_content):
                        original_char = fixed_content[error_pos] if error_pos < len(fixed_content) else ''
                        print(f"原始字符: {repr(original_char)}, ASCII码: {ord(original_char) if original_char else 'N/A'}", flush=True)
                
                # 如果错误信息包含 "control character"，尝试更激进的修复
                if "control character" in error_msg.lower() or "control" in error_msg.lower():
                    print("检测到控制字符错误，尝试更激进的修复...", flush=True)
                    # 尝试移除所有控制字符（在字符串外部）
                    aggressive_fixed = []
                    in_str = False
                    escape = False
                    for i, c in enumerate(fixed_content):
                        if escape:
                            aggressive_fixed.append(c)
                            escape = False
                            continue
                        if c == '\\':
                            aggressive_fixed.append(c)
                            escape = True
                            continue
                        if c == '"':
                            in_str = not in_str
                            aggressive_fixed.append(c)
                            continue
                        if not in_str and ord(c) < 32 and c not in '\n\r\t':
                            # 在字符串外部移除控制字符（除了换行、回车、制表符）
                            continue
                        aggressive_fixed.append(c)
                    
                    try:
                        result = json.loads(''.join(aggressive_fixed))
                        print("激进修复成功！", flush=True)
                        # 添加调试信息和思考过程
                        result = _add_debug_info_to_result(result, debug_info, content_text)
                        return result
                    except:
                        pass
                
                raise
            except ValueError as e:
                # 如果直接解析失败，尝试多种方式提取 JSON
                # 注意：re 已经在文件顶部导入，不需要在这里重新导入
                
                # 方法1: 提取第一个完整的JSON对象（从{到匹配的}）
                json_patterns = [
                    r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',  # 简单嵌套
                    r'\{.*?\}',  # 非贪婪匹配
                ]
                
                for pattern in json_patterns:
                    json_match = re.search(pattern, json_content, re.DOTALL)
                    if json_match:
                        try:
                            json_candidate = json_match.group(0)
                            # 修复控制字符后再解析
                            fixed_candidate = _fix_json_string(json_candidate)
                            result = json.loads(fixed_candidate)
                            print(f"成功从内容中提取JSON（使用模式: {pattern}）", flush=True)
                            # 添加调试信息和思考过程
                            result = _add_debug_info_to_result(result, debug_info, content_text)
                            return result
                        except (json.JSONDecodeError, ValueError):
                            continue
                
                # 方法2: 尝试找到最长的JSON-like字符串
                brace_count = 0
                start_idx = -1
                best_match = None
                best_length = 0
                
                for i, char in enumerate(json_content):
                    if char == '{':
                        if brace_count == 0:
                            start_idx = i
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0 and start_idx != -1:
                            candidate = json_content[start_idx:i+1]
                            if len(candidate) > best_length:
                                try:
                                    # 修复控制字符后验证
                                    fixed_candidate = _fix_json_string(candidate)
                                    json.loads(fixed_candidate)  # 验证是否为有效JSON
                                    best_match = fixed_candidate
                                    best_length = len(candidate)
                                except (json.JSONDecodeError, ValueError):
                                    pass
                
                if best_match:
                    try:
                        result = json.loads(best_match)
                        print(f"成功提取最长有效JSON片段（长度: {len(best_match)}）", flush=True)
                        # 添加调试信息和思考过程
                        result = _add_debug_info_to_result(result, debug_info, content_text)
                        return result
                    except (json.JSONDecodeError, ValueError):
                        pass
                
                # 如果所有方法都失败，输出调试信息
                print(f"JSON解析失败。返回内容前500字符: {json_content[:500]}", flush=True)
                print(f"JSON解析错误: {e}", flush=True)
                
                # 尝试修复常见的JSON格式问题
                try:
                    # 移除尾部的多余字符
                    repair_content = json_content.rstrip('.,;。，；\n\r\t ')
                    # 确保以}结尾
                    if not repair_content.rstrip().endswith('}'):
                        # 尝试找到最后一个}
                        last_brace = repair_content.rfind('}')
                        if last_brace > 0:
                            repair_content = repair_content[:last_brace+1]
                    
                    # 再次修复控制字符
                    repair_content = _fix_json_string(repair_content)
                    result = json.loads(repair_content)
                    # 添加调试信息和思考过程
                    result = _add_debug_info_to_result(result, debug_info, content_text)
                    return result
                except (json.JSONDecodeError, ValueError):
                    pass
                
                # 保存原始返回内容到日志，方便调试
                error_detail = f"""
豆包接口返回的内容不是有效的 JSON 格式。

解析错误: {str(e)}

返回内容长度: {len(json_content)} 字符
返回内容前500字符:
{json_content[:500]}

返回内容后500字符:
{json_content[-500:] if len(json_content) > 500 else json_content}
"""
                print(error_detail, flush=True)
                
                raise RuntimeError(
                    f"豆包接口返回的内容不是有效的 JSON 格式。\n"
                    f"解析错误: {str(e)}\n"
                    f"返回内容前300字符: {json_content[:300]}\n"
                    f"请检查服务器日志获取完整返回内容。"
                )
        except requests.exceptions.RequestException as exc:
            error_msg = f"豆包接口请求失败：{exc}"
            if hasattr(exc, 'response') and exc.response is not None:
                try:
                    error_body = exc.response.json()
                    error_msg += f" - {error_body.get('error', {}).get('message', '')}"
                except:
                    error_msg += f" - {exc.response.text[:200]}"
            raise RuntimeError(error_msg)
        except Exception as exc:
            raise RuntimeError(f"豆包解读生成失败：{exc}")

    @app.route("/test_thinking.html")
    def test_thinking_page():
        """深度思考模型测试页面"""
        return send_from_directory(".", "test_thinking.html")

    @app.route("/test_doubao_thinking.html")
    def test_doubao_thinking_page():
        """豆包深度思考能力纯测试页面"""
        return send_from_directory(".", "test_doubao_thinking.html")

    @app.post("/api/test/doubao-thinking")
    def test_doubao_thinking_endpoint():
        """纯测试接口：直接调用豆包深度思考模型，无额外逻辑"""
        payload = request.get_json() or {}
        prompt = payload.get("prompt", "")
        if not prompt:
            return jsonify({"error": "缺少 prompt 字段"}), 400

        api_key = os.environ.get("DOUBAO_API_KEY") or os.environ.get("ARK_API_KEY") or load_setting("doubao_api_key", "") or load_setting("ark_api_key", "")
        if not api_key:
            return jsonify({"error": "缺少 DOUBAO_API_KEY 或 ARK_API_KEY"}), 400

        model = payload.get("model", "doubao-seed-1-6-251015")
        temperature = float(payload.get("temperature", 0.3))
        max_tokens = int(payload.get("max_tokens", 16000))

        try:
            # 参考豆包官方示例代码，直接调用，不添加任何额外逻辑
            if ARK_SDK_AVAILABLE:
                base_url = "https://ark.cn-beijing.volces.com/api/v3"
                configured_base = (
                    os.environ.get("DOUBAO_API_BASE")
                    or load_setting("doubao_api_base", "")
                ).strip()

                if configured_base:
                    configured_base = configured_base.rstrip("/")
                    if configured_base.endswith("/api/v3"):
                        base_url = configured_base
                    elif configured_base.endswith("/api/v3/chat/completions"):
                        base_url = configured_base.replace("/chat/completions", "")
                    elif "/api/v3" not in configured_base:
                        base_url = f"{configured_base}/api/v3"
                    else:
                        base_url = configured_base

                client = Ark(
                    base_url=base_url,
                    api_key=api_key,
                    timeout=1800,
                )

                # 严格按照官方示例代码调用
                completion = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                # 提取结果
                choice = completion.choices[0]
                message = choice.message
                content = message.content or ""

                # 检查是否有 reasoning_content（思维链）
                reasoning_content = None
                if hasattr(message, 'reasoning_content') and message.reasoning_content:
                    reasoning_content = message.reasoning_content

                return jsonify({
                    "reasoning_content": reasoning_content,
                    "content": content,
                })

            else:
                # 降级使用 requests 方式
                endpoint = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
                configured_base = (
                    os.environ.get("DOUBAO_API_BASE")
                    or load_setting("doubao_api_base", "")
                ).strip()

                if configured_base:
                    configured_base = configured_base.rstrip("/")
                    if configured_base.endswith("/chat/completions"):
                        endpoint = configured_base
                    else:
                        endpoint = f"{configured_base}/chat/completions"

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                }

                request_payload = {
                    "model": model,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }

                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=request_payload,
                    timeout=1800,
                )

                response.raise_for_status()
                data = response.json()

                if "choices" not in data or not data["choices"]:
                    return jsonify({"error": "豆包接口未返回任何结果"}), 500

                message = data["choices"][0].get("message", {})
                content = message.get("content", "").strip()
                reasoning_content = message.get("reasoning_content")

                return jsonify({
                    "reasoning_content": reasoning_content,
                    "content": content,
                })

        except Exception as exc:
            print(f"❌ 调用豆包深度思考模型失败: {exc}", flush=True)
            return jsonify({"error": f"调用失败: {str(exc)}"}), 500

    @app.route("/parser_test_page.html")
    def serve_parser_page():
        return send_from_directory(app.static_folder, "parser_test_page.html")

    @app.route("/aigen_test_page.html")
    def serve_aigen_page():
        return send_from_directory(app.static_folder, "aigen_test_page.html")

    @app.route("/book_restructure_page.html")
    def serve_book_restructure_page():
        return send_from_directory(app.static_folder, "book_restructure_page.html")

    @app.route("/admin.html")
    def serve_admin_page():
        return send_from_directory(app.static_folder, "admin.html")

    @app.route("/admin_books.html")
    def serve_admin_books_page():
        return send_from_directory(app.static_folder, "admin_books.html")

    @app.route("/")
    @app.route("/index.html")
    def serve_index_page():
        """首页"""
        return send_from_directory(".", "index.html")

    def clean_toc_only(upload: io.BytesIO) -> Dict[str, Any]:
        """仅清洗目录，返回清洗前后的目录对比"""
        upload.seek(0)
        pdf_bytes = upload.read()

        def normalize_title(raw: Any) -> str:
            if raw is None:
                return ""
            if isinstance(raw, bytes):
                for encoding in ("utf-16", "utf-8", "gb18030", "latin-1"):
                    try:
                        return raw.decode(encoding).strip()
                    except UnicodeDecodeError:
                        continue
                return raw.decode("utf-8", errors="ignore").strip()
            text = str(raw).strip()
            text = unicodedata.normalize("NFKC", text)
            if text.startswith("b'") or text.startswith('b"'):
                try:
                    literal = ast.literal_eval(text)
                except (SyntaxError, ValueError):
                    literal = None
                if isinstance(literal, bytes):
                    return normalize_title(literal)
            if text.startswith(("þÿ", "ÿþ")):
                encoded = text.encode("latin-1", errors="ignore")
                if len(encoded) % 2 != 0:
                    encoded = encoded[:-1]
                try:
                    text = encoded.decode("utf-16", errors="ignore")
                except UnicodeDecodeError:
                    pass
            if "\x00" in text:
                text = text.replace("\x00", "")
            try:
                encoded = text.encode("latin-1", errors="ignore")
                if len(encoded) >= 2 and encoded[:2] in {b"\xfe\xff", b"\xff\xfe"}:
                    text = encoded.decode("utf-16", errors="ignore")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            if text.startswith("\ufeff"):
                text = text.lstrip("\ufeff")
            text = "".join(
                ch for ch in text if unicodedata.category(ch) not in {"Cf", "Co", "Cs"}
            )
            return text

        def is_garbled(text: str) -> bool:
            cleaned = "".join(ch for ch in text if ch not in string.whitespace)
            if not cleaned:
                return True
            valid = 0
            for ch in cleaned:
                if ("\u4e00" <= ch <= "\u9fff") or ("\u3400" <= ch <= "\u4dbf"):
                    valid += 1
                elif ch.isalnum() or ch in "·—–-，。、《》：；？！""'\"()/\\&" or ch in string.punctuation:
                    valid += 1
            return valid / len(cleaned) < 0.45

        heading_regex = re.compile(
            r"^第[\s〇零一二三四五六七八九十百千万两\dIVXLCDM]+[章节篇卷部节部分回目]?"
        )

        def guess_heading_from_page(page_index: int, doc: fitz.Document) -> str:
            if page_index < 0 or page_index >= doc.page_count:
                return ""
            page = doc.load_page(page_index)
            raw_text = page.get_text("text", flags=fitz.TEXT_PRESERVE_LIGATURES)
            for line in raw_text.splitlines():
                candidate = normalize_title(line).strip()
                if not candidate or len(candidate) > 120:
                    continue
                if heading_regex.match(candidate) and not is_garbled(candidate):
                    return candidate

            # 如果第一轮没找到，尝试从blocks中提取
            blocks = page.get_text("blocks")
            blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
            for block in blocks:
                if len(block) < 5:
                    continue
                text = normalize_title(block[4])
                candidate = text.strip().replace("\n", "")
                if not candidate:
                    continue
                candidate_clean = "".join(ch for ch in candidate if ch not in string.whitespace)
                if not candidate_clean or len(candidate_clean) > 80:
                    continue
                if is_garbled(candidate_clean):
                    continue
                if heading_regex.match(candidate):
                    return candidate.strip()
            return ""

        def title_metrics(text: str) -> Dict[str, float]:
            cleaned = "".join(ch for ch in text if ch not in string.whitespace)
            if not cleaned:
                return {"length": 0.0, "ch_ratio": 0.0, "weird_ratio": 1.0}
            chinese = sum(1 for ch in cleaned if ("\u4e00" <= ch <= "\u9fff") or ("\u3400" <= ch <= "\u4dbf"))
            allowed_ascii = sum(
                1
                for ch in cleaned
                if ch.isalnum() or ch in "·—–-，。、《》：；？！""'\"()/\\&" or ch in string.punctuation
            )
            length = float(len(cleaned))
            weird = max(length - chinese - allowed_ascii, 0.0)
            return {
                "length": length,
                "ch_ratio": chinese / length if length > 0 else 0.0,
                "weird_ratio": weird / length if length > 0 else 1.0,
            }

        def choose_better_title(current: str, fallback: str) -> str:
            fallback = fallback.strip()
            if not fallback:
                return current
            metrics_current = title_metrics(current)
            metrics_fallback = title_metrics(fallback)
            if metrics_current["length"] == 0:
                return fallback
            improvements = 0
            if metrics_fallback["length"] >= metrics_current["length"] + 2:
                improvements += 1
            if metrics_fallback["ch_ratio"] > metrics_current["ch_ratio"] + 0.15:
                improvements += 1
            if metrics_fallback["weird_ratio"] + 0.1 < metrics_current["weird_ratio"]:
                improvements += 1
            if metrics_current["length"] <= 4 and metrics_fallback["length"] > metrics_current["length"]:
                improvements += 1
            return fallback if improvements >= 1 else current

        numeral_allowed = re.compile(r"^[〇零一二三四五六七八九十百千万两\dIVXLCDM]+$")
        chapter_markers = ["章", "节", "篇", "卷", "部", "部分", "回", "目"]
        default_marker = {1: "章", 2: "章", 3: "节", 4: "节"}

        def int_to_cn(num: int) -> str:
            digits = "零一二三四五六七八九"
            units = ["", "十", "百", "千"]
            if num <= 0:
                return ""
            if num < 10:
                return digits[num]
            if num < 20:
                return "十" if num == 10 else "十" + digits[num % 10]
            result = ""
            chars = list(str(num))
            length = len(chars)
            for idx, ch in enumerate(chars):
                digit = int(ch)
                unit_idx = length - idx - 1
                if digit == 0:
                    if result and not result.endswith("零") and unit_idx > 0:
                        result += "零"
                else:
                    result += digits[digit] + units[unit_idx]
            result = result.rstrip("零")
            result = result.replace("零零", "零")
            if result.startswith("一十"):
                result = result[1:]
            return result

        def fix_garbled_chapter_title(title: str, level: int, level_counters: Dict[int, int]) -> str:
            """尝试修复乱码的章节标题"""
            stripped = title.strip()
            if not stripped.startswith("第"):
                return stripped

            # 尝试匹配"第X章"格式
            match = re.match(
                r"^第(?P<num>[^\s章节篇卷部部分回目]{1,24})(?P<mark>章|节|篇|卷|部|部分|回|目)?",
                stripped,
            )
            if match:
                num_segment = match.group("num")
                mark = match.group("mark")
                tail = stripped[match.end() :].lstrip()

                # 如果数字部分是乱码，尝试用当前索引替换
                if not numeral_allowed.fullmatch(num_segment):
                    current_index = level_counters.get(level, 0) + 1
                    cn_numeral = int_to_cn(current_index)
                    intrinsic_marker = next((m for m in chapter_markers if m in stripped), None)
                    mark = mark or intrinsic_marker or default_marker.get(level, "章")
                    # 如果tail也是乱码，尝试清理
                    if tail and is_garbled(tail):
                        # 保留tail中可能有效的部分（中文和标点）
                        cleaned_tail = "".join(
                            ch for ch in tail
                            if ("\u4e00" <= ch <= "\u9fff") or ("\u3400" <= ch <= "\u4dbf")
                            or ch in "·—–-，。、《》：；？！""'\"()/\\&" or ch.isalnum()
                        )
                        if cleaned_tail and not is_garbled(cleaned_tail):
                            tail = cleaned_tail
                        else:
                            tail = ""  # 如果清理后仍然是乱码，清空tail
                    return f"第{cn_numeral}{mark} {tail}".strip() if tail else f"第{cn_numeral}{mark}"
                elif not mark:
                    # 如果数字正常但没有标记，添加标记
                    intrinsic_marker = next((m for m in chapter_markers if m in stripped), None)
                    mark = intrinsic_marker or default_marker.get(level, "章")
                    return f"第{num_segment}{mark} {tail}".strip() if tail else f"第{num_segment}{mark}"

            return stripped

        def extract_outline_entries(reader: PdfReader) -> List[Tuple[int, str, int]]:
            try:
                outline = reader.outline
            except AttributeError:
                outline = reader.getOutlines()  # type: ignore[attr-defined]
            entries: List[Tuple[int, str, int]] = []

            def walk(items: Any, level: int) -> None:
                for item in items or []:
                    if isinstance(item, list):
                        walk(item, level + 1)
                        continue
                    raw_title = getattr(item, "title", None) or getattr(item, "title_", None) or str(item)
                    title = normalize_title(raw_title)
                    if not title:
                        continue
                    try:
                        page_number = reader.get_destination_page_number(item)
                    except Exception:  # pylint: disable=broad-except
                        continue
                    entries.append((level, title, page_number + 1))
            walk(outline, 1)
            return entries

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        reader = PdfReader(io.BytesIO(pdf_bytes))

        try:
            toc_entries = extract_outline_entries(reader)
            if not toc_entries:
                toc_entries = doc.get_toc(simple=True)

            normalized_entries: List[Tuple[int, str, int]] = []
            level_counters: Dict[int, int] = {}
            
            for level, title, page_number in toc_entries:
                # 更新层级计数器
                for deeper in [lvl for lvl in level_counters if lvl > level]:
                    level_counters.pop(deeper, None)
                current_index = level_counters.get(level, 0) + 1
                level_counters[level] = current_index
                
                normalized_title = normalize_title(title)
                
                # 如果标题是乱码，尝试修复
                if is_garbled(normalized_title):
                    # 首先尝试从页面中提取标题
                    fallback = guess_heading_from_page(page_number - 1, doc)
                    if fallback and not is_garbled(fallback):
                        normalized_title = choose_better_title(normalized_title, fallback)
                    else:
                        # 如果无法从页面提取，尝试修复章节号
                        fixed_title = fix_garbled_chapter_title(normalized_title, level, level_counters)
                        if fixed_title != normalized_title:
                            normalized_title = fixed_title
                        # 如果修复后仍然是乱码，尝试从页面提取（即使可能也是乱码）
                        elif fallback:
                            normalized_title = fallback
                else:
                    # 即使不是乱码，也尝试从页面提取更好的标题
                    fallback = guess_heading_from_page(page_number - 1, doc)
                    if fallback and not is_garbled(fallback):
                        normalized_title = choose_better_title(normalized_title, fallback)
                
                normalized_entries.append((level, normalized_title, page_number))
            raw_toc = normalized_entries

            # 使用大模型清洗目录
            api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
            if not api_key:
                raise ValueError("缺少 DOUBAO_API_KEY，无法清洗目录。")

            cleaned_toc = raw_toc
            try:
                cleaned_toc = call_doubao_clean_toc(raw_toc, api_key)
                if not cleaned_toc:
                    cleaned_toc = raw_toc
            except Exception as exc:
                print(f"大模型清洗目录失败: {exc}", flush=True)
                cleaned_toc = raw_toc

            # 格式化目录用于显示
            raw_toc_formatted = [
                {"level": level, "title": title, "page": page}
                for level, title, page in raw_toc
            ]
            cleaned_toc_formatted = [
                {"level": level, "title": title, "page": page}
                for level, title, page in cleaned_toc
            ]

            return {
                "raw_toc": raw_toc_formatted,
                "cleaned_toc": cleaned_toc_formatted,
                "raw_count": len(raw_toc),
                "cleaned_count": len(cleaned_toc),
            }
        finally:
            doc.close()

    def extract_epub_toc_direct(upload: io.BytesIO) -> List[Tuple[int, str, int]]:
        """直接解析EPUB ZIP文件，提取完整目录结构（绕过ebooklib）"""
        upload.seek(0)
        epub_bytes = upload.read()
        
        toc_entries: List[Tuple[int, str, int]] = []
        
        try:
            with zipfile.ZipFile(io.BytesIO(epub_bytes), 'r') as zip_file:
                # 读取OPF文件获取spine信息（用于页码映射）
                opf_files = [f for f in zip_file.namelist() if f.endswith('.opf')]
                spine_map = {}  # href -> spine_index
                if opf_files:
                    try:
                        opf_content = zip_file.read(opf_files[0]).decode('utf-8', errors='ignore')
                        opf_soup = BeautifulSoup(opf_content, 'xml')
                        # 查找spine
                        spine = opf_soup.find('spine')
                        if spine:
                            itemrefs = spine.find_all('itemref')
                            for idx, itemref in enumerate(itemrefs):
                                idref = itemref.get('idref', '')
                                # 查找对应的item
                                item = opf_soup.find('item', {'id': idref})
                                if item:
                                    href = item.get('href', '')
                                    if href:
                                        # 规范化路径
                                        import urllib.parse
                                        href = urllib.parse.unquote(href)
                                        spine_map[href] = idx + 1
                                        # 也存储文件名部分
                                        href_filename = os.path.basename(href)
                                        if href_filename:
                                            spine_map[href_filename] = idx + 1
                        print(f"从OPF提取了{len(spine_map)}个spine映射", flush=True)
                        # 调试：输出前几个spine映射
                        sample_items = list(spine_map.items())[:5]
                        for href, idx in sample_items:
                            print(f"  spine映射示例: {href} -> {idx}", flush=True)
                    except Exception as exc:
                        print(f"解析OPF文件失败: {exc}", flush=True)
                
                # 查找NCX文件（EPUB 2格式）
                ncx_files = [f for f in zip_file.namelist() if f.endswith('.ncx')]
                # 查找NAV文件（EPUB 3格式）
                nav_files = [f for f in zip_file.namelist() if 'nav.xhtml' in f.lower() or 'toc.xhtml' in f.lower()]
                
                print(f"直接解析EPUB: 找到{len(ncx_files)}个NCX文件, {len(nav_files)}个NAV文件", flush=True)
                
                # 优先使用NCX文件
                if ncx_files:
                    ncx_path = ncx_files[0]
                    print(f"使用NCX文件: {ncx_path}", flush=True)
                    try:
                        ncx_content = zip_file.read(ncx_path).decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(ncx_content, 'xml')
                        
                        # 查找navMap
                        nav_map = soup.find('navMap')
                        if nav_map:
                            def parse_nav_point_direct(nav_point, level: int = 1):
                                nav_label = nav_point.find('navLabel')
                                if nav_label:
                                    text_elem = nav_label.find('text')
                                    if text_elem:
                                        title = text_elem.get_text().strip()
                                        if title:
                                            # 获取页码（从content/src映射到spine）
                                            page_num = len(toc_entries) + 1
                                            content_elem = nav_point.find('content')
                                            if content_elem and content_elem.get('src'):
                                                src = content_elem.get('src')
                                                import urllib.parse
                                                # 移除锚点
                                                src = src.split('#')[0]
                                                src = urllib.parse.unquote(src)
                                                src_filename = os.path.basename(src)
                                                
                                                # 规范化路径（统一路径分隔符）
                                                def normalize_path(path: str) -> str:
                                                    """规范化路径用于匹配"""
                                                    path = path.replace('\\', '/')
                                                    # 移除前导斜杠
                                                    if path.startswith('/'):
                                                        path = path[1:]
                                                    return path
                                                
                                                src_normalized = normalize_path(src)
                                                
                                                # 尝试多种匹配方式
                                                matched = False
                                                # 1. 精确匹配
                                                if src in spine_map:
                                                    page_num = spine_map[src]
                                                    matched = True
                                                # 2. 规范化路径匹配
                                                elif not matched:
                                                    for href, idx in spine_map.items():
                                                        href_normalized = normalize_path(href)
                                                        if src_normalized == href_normalized or src_normalized.endswith(href_normalized) or href_normalized.endswith(src_normalized):
                                                            page_num = idx
                                                            matched = True
                                                            print(f"    路径匹配成功: {src} <-> {href} -> {idx}", flush=True)
                                                            break
                                                # 3. 文件名匹配
                                                if not matched and src_filename in spine_map:
                                                    page_num = spine_map[src_filename]
                                                    matched = True
                                                # 4. 使用playOrder作为后备
                                                if not matched:
                                                    play_order = nav_point.get('playOrder', '1')
                                                    try:
                                                        play_order_int = int(play_order)
                                                        # playOrder通常是1-based，但需要验证是否在有效范围内
                                                        if play_order_int > 0 and play_order_int <= len(spine_map):
                                                            page_num = play_order_int
                                                            matched = True
                                                            print(f"    使用playOrder: {play_order} -> {page_num}", flush=True)
                                                    except ValueError:
                                                        pass
                                                
                                                if not matched:
                                                    print(f"    警告: 无法映射路径 {src}，使用默认值 {page_num}", flush=True)
                                            
                                            # 如果不是中文，尝试翻译（在extract_epub_toc_direct中不翻译，留给clean_epub_toc_only统一处理）
                                            toc_entries.append((level, title, page_num))
                                            print(f"  提取目录项 (层级{level}): {title[:60]} (page: {page_num})", flush=True)
                                
                                # 递归处理子节点
                                children = nav_point.find_all('navPoint', recursive=False)
                                if children:
                                    print(f"    发现{len(children)}个子节点 (层级{level}->{level+1})", flush=True)
                                    for child in children:
                                        parse_nav_point_direct(child, level + 1)
                            
                            # 处理所有顶级navPoint
                            top_level = nav_map.find_all('navPoint', recursive=False)
                            print(f"找到{len(top_level)}个顶级navPoint", flush=True)
                            for nav_point in top_level:
                                parse_nav_point_direct(nav_point, 1)
                    except Exception as exc:
                        print(f"解析NCX文件失败: {exc}", flush=True)
                        import traceback
                        traceback.print_exc()
                
                # 如果没有NCX或NCX解析失败，尝试NAV文件（EPUB 3）
                if not toc_entries and nav_files:
                    nav_path = nav_files[0]
                    print(f"使用NAV文件: {nav_path}", flush=True)
                    try:
                        nav_content = zip_file.read(nav_path).decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(nav_content, 'html.parser')
                        
                        # 查找导航区域（通常有epub:type="toc"）
                        nav_elem = soup.find('nav', {'epub:type': 'toc'}) or soup.find('nav')
                        if nav_elem:
                            def parse_nav_li_direct(li_elem, level: int = 1):
                                link = li_elem.find('a')
                                if link:
                                    title = link.get_text().strip()
                                    href = link.get('href', '')
                                    if title:
                                        page_num = len(toc_entries) + 1
                                        # 尝试从href中提取信息并映射到spine
                                        if href:
                                            import urllib.parse
                                            # 移除锚点
                                            href_path = href.split('#')[0]
                                            href_path = urllib.parse.unquote(href_path)
                                            href_filename = os.path.basename(href_path)
                                            
                                            # 规范化路径
                                            def normalize_path(path: str) -> str:
                                                """规范化路径用于匹配"""
                                                path = path.replace('\\', '/')
                                                if path.startswith('/'):
                                                    path = path[1:]
                                                return path
                                            
                                            href_normalized = normalize_path(href_path)
                                            
                                            # 尝试多种匹配方式
                                            matched = False
                                            # 1. 精确匹配
                                            if href_path in spine_map:
                                                page_num = spine_map[href_path]
                                                matched = True
                                            # 2. 规范化路径匹配
                                            elif not matched:
                                                for href_key, idx in spine_map.items():
                                                    href_key_normalized = normalize_path(href_key)
                                                    if href_normalized == href_key_normalized or href_normalized.endswith(href_key_normalized) or href_key_normalized.endswith(href_normalized):
                                                        page_num = idx
                                                        matched = True
                                                        print(f"    路径匹配成功: {href_path} <-> {href_key} -> {idx}", flush=True)
                                                        break
                                            # 3. 文件名匹配
                                            if not matched and href_filename in spine_map:
                                                page_num = spine_map[href_filename]
                                                matched = True
                                            
                                            if not matched:
                                                print(f"    警告: 无法映射路径 {href_path}，使用默认值 {page_num}", flush=True)
                                        
                                        toc_entries.append((level, title, page_num))
                                        print(f"  提取NAV目录项 (层级{level}): {title[:60]} (page: {page_num})", flush=True)
                                
                                # 递归处理子节点
                                sub_list = li_elem.find(['ol', 'ul'], recursive=False)
                                if sub_list:
                                    child_lis = sub_list.find_all('li', recursive=False)
                                    if child_lis:
                                        print(f"    发现{len(child_lis)}个NAV子节点 (层级{level}->{level+1})", flush=True)
                                        for child_li in child_lis:
                                            parse_nav_li_direct(child_li, level + 1)
                            
                            # 处理顶级li元素
                            top_level_lis = nav_elem.find_all('li', recursive=False)
                            print(f"找到{len(top_level_lis)}个顶级li", flush=True)
                            for li_elem in top_level_lis:
                                parse_nav_li_direct(li_elem, 1)
                    except Exception as exc:
                        print(f"解析NAV文件失败: {exc}", flush=True)
                        import traceback
                        traceback.print_exc()
                
                print(f"直接解析EPUB完成，提取了{len(toc_entries)}个目录条目", flush=True)
                
        except Exception as exc:
            print(f"直接解析EPUB ZIP文件失败: {exc}", flush=True)
            import traceback
            traceback.print_exc()
        
        return toc_entries

    def clean_epub_toc_only(upload: io.BytesIO, skip_llm: bool = False) -> Dict[str, Any]:
        """仅清洗EPUB目录，返回清洗前后的目录对比"""
        upload.seek(0)
        epub_bytes = upload.read()
        
        # 优先尝试直接解析EPUB ZIP文件（更可靠）
        print("尝试直接解析EPUB ZIP文件...", flush=True)
        direct_toc = extract_epub_toc_direct(io.BytesIO(epub_bytes))
        
        if direct_toc and len(direct_toc) >= 10:
            print(f"直接解析成功，提取了{len(direct_toc)}个目录条目，使用直接解析结果", flush=True)
            raw_toc = direct_toc
            # 应用normalize_title处理
            def normalize_title(raw: Any) -> str:
                if raw is None:
                    return ""
                if isinstance(raw, bytes):
                    for encoding in ("utf-16", "utf-8", "gb18030", "latin-1"):
                        try:
                            return raw.decode(encoding).strip()
                        except UnicodeDecodeError:
                            continue
                    return raw.decode("utf-8", errors="ignore").strip()
                text = str(raw).strip()
                text = unicodedata.normalize("NFKC", text)
                if text.startswith("b'") or text.startswith('b"'):
                    try:
                        literal = ast.literal_eval(text)
                    except (SyntaxError, ValueError):
                        literal = None
                    if isinstance(literal, bytes):
                        return normalize_title(literal)
                if text.startswith(("þÿ", "ÿþ")):
                    encoded = text.encode("latin-1", errors="ignore")
                    if len(encoded) % 2 != 0:
                        encoded = encoded[:-1]
                    try:
                        text = encoded.decode("utf-16", errors="ignore")
                    except UnicodeDecodeError:
                        pass
                if "\x00" in text:
                    text = text.replace("\x00", "")
                try:
                    encoded = text.encode("latin-1", errors="ignore")
                    if len(encoded) >= 2 and encoded[:2] in {b"\xfe\xff", b"\xff\xfe"}:
                        text = encoded.decode("utf-16", errors="ignore")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                if text.startswith("\ufeff"):
                    text = text.lstrip("\ufeff")
                text = "".join(
                    ch for ch in text if unicodedata.category(ch) not in {"Cf", "Co", "Cs"}
                )
                return text
            
            # 规范化标题（不翻译，翻译在用户点击翻译按钮时进行）
            normalized_toc = []
            for level, title, page in raw_toc:
                normalized_title = normalize_title(title)
                normalized_toc.append((level, normalized_title, page))
            raw_toc = normalized_toc
            
            # 格式化用于返回
            raw_toc_formatted = [
                {"level": level, "title": title, "page": page}
                for level, title, page in raw_toc
            ]
            
            # 使用大模型清洗目录（如果未跳过）
            if skip_llm:
                cleaned_toc = raw_toc
            else:
                api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
                if not api_key:
                    raise ValueError("缺少 DOUBAO_API_KEY，无法清洗目录。")
                
                cleaned_toc = raw_toc
                try:
                    cleaned_toc = call_doubao_clean_toc(raw_toc, api_key)
                    if not cleaned_toc:
                        cleaned_toc = raw_toc
                except Exception as exc:
                    print(f"大模型清洗EPUB目录失败: {exc}", flush=True)
                    cleaned_toc = raw_toc
            
            cleaned_toc_formatted = [
                {"level": level, "title": title, "page": page}
                for level, title, page in cleaned_toc
            ]
            
            return {
                "raw_toc": raw_toc_formatted,
                "cleaned_toc": cleaned_toc_formatted,
                "raw_count": len(raw_toc),
                "cleaned_count": len(cleaned_toc),
                "ncx_count": 0,  # 直接解析不区分来源
                "book_toc_count": len(raw_toc),
            }
        
        # 如果直接解析失败或条目太少，回退到ebooklib方法
        print(f"直接解析结果不足（{len(direct_toc) if direct_toc else 0}个条目），回退到ebooklib方法", flush=True)
        # epub.read_epub() 在某些操作中需要文件路径，所以使用临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix='.epub') as tmp_file:
            tmp_file.write(epub_bytes)
            tmp_file_path = tmp_file.name
        try:
            book = epub.read_epub(tmp_file_path)
        finally:
            # 确保临时文件被删除
            try:
                os.unlink(tmp_file_path)
            except OSError:
                pass

        def normalize_title(raw: Any) -> str:
            if raw is None:
                return ""
            if isinstance(raw, bytes):
                for encoding in ("utf-16", "utf-8", "gb18030", "latin-1"):
                    try:
                        return raw.decode(encoding).strip()
                    except UnicodeDecodeError:
                        continue
                return raw.decode("utf-8", errors="ignore").strip()
            text = str(raw).strip()
            text = unicodedata.normalize("NFKC", text)
            if text.startswith("b'") or text.startswith('b"'):
                try:
                    literal = ast.literal_eval(text)
                except (SyntaxError, ValueError):
                    literal = None
                if isinstance(literal, bytes):
                    return normalize_title(literal)
            if text.startswith(("þÿ", "ÿþ")):
                encoded = text.encode("latin-1", errors="ignore")
                if len(encoded) % 2 != 0:
                    encoded = encoded[:-1]
                try:
                    text = encoded.decode("utf-16", errors="ignore")
                except UnicodeDecodeError:
                    pass
            if "\x00" in text:
                text = text.replace("\x00", "")
            try:
                encoded = text.encode("latin-1", errors="ignore")
                if len(encoded) >= 2 and encoded[:2] in {b"\xfe\xff", b"\xff\xfe"}:
                    text = encoded.decode("utf-16", errors="ignore")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            if text.startswith("\ufeff"):
                text = text.lstrip("\ufeff")
            text = "".join(
                ch for ch in text if unicodedata.category(ch) not in {"Cf", "Co", "Cs"}
            )
            return text

        def is_garbled(text: str) -> bool:
            cleaned = "".join(ch for ch in text if ch not in string.whitespace)
            if not cleaned:
                return True
            valid = 0
            for ch in cleaned:
                if ("\u4e00" <= ch <= "\u9fff") or ("\u3400" <= ch <= "\u4dbf"):
                    valid += 1
                elif ch.isalnum() or ch in "·—–-，。、《》：；？！""'\"()/\\&" or ch in string.punctuation:
                    valid += 1
            return valid / len(cleaned) < 0.45 if len(cleaned) > 0 else True

        # 提取EPUB目录
        toc_entries: List[Tuple[int, str, int]] = []
        ncx_entries: List[Tuple[int, str, int]] = []
        spine_items = list(book.spine)
        
        # 创建spine_id到索引的映射（用于快速查找）
        spine_id_to_index = {spine_id: idx + 1 for idx, (spine_id, _) in enumerate(spine_items)}
        
        # 首先尝试从NCX或NAV中提取目录（通常更完整）
        toc_ncx = None
        for item in book.get_items():
            item_name = item.get_name() if hasattr(item, 'get_name') else ""
            # 检查是否是导航文件（NCX或NAV）
            if item_name and ("nav" in item_name.lower() or "toc" in item_name.lower() or "ncx" in item_name.lower()):
                toc_ncx = item
                break
            # 或者通过文件扩展名判断
            if item_name and any(ext in item_name.lower() for ext in ['.ncx', '.nav', '.xhtml']):
                # 进一步检查内容是否包含导航结构
                try:
                    content = item.get_content().decode("utf-8", errors="ignore")
                    if "navPoint" in content or "navMap" in content or "<nav" in content.lower():
                        toc_ncx = item
                        break
                except Exception:
                    pass
        
        # 如果找到NCX/NAV，优先使用它
        if toc_ncx is not None:
            try:
                ncx_content = toc_ncx.get_content().decode("utf-8", errors="ignore")
                soup = BeautifulSoup(ncx_content, "xml")
                
                # 查找navMap（NCX格式）或nav（EPUB 3格式）
                nav_map = soup.find("navMap")
                if not nav_map:
                    nav_map = soup.find("nav", {"epub:type": "toc"})
                if not nav_map:
                    nav_map = soup.find("nav")
                
                if nav_map:
                    # 先找到所有顶级导航点
                    if nav_map.name == "navMap":
                        top_level_points = nav_map.find_all("navPoint", recursive=False)
                        # 调试：检查所有navPoint（包括嵌套的）
                        all_nav_points = nav_map.find_all("navPoint", recursive=True)
                        print(f"从NCX/NAV提取目录，找到{len(top_level_points)}个顶级导航点，总共{len(all_nav_points)}个navPoint", flush=True)
                        if len(all_nav_points) > len(top_level_points):
                            print(f"  发现嵌套的navPoint: {len(all_nav_points) - len(top_level_points)}个", flush=True)
                    else:
                        top_level_points = nav_map.find_all("li", recursive=False)
                        all_li_points = nav_map.find_all("li", recursive=True)
                        print(f"从NCX/NAV提取目录，找到{len(top_level_points)}个顶级li，总共{len(all_li_points)}个li", flush=True)
                        if len(all_li_points) > len(top_level_points):
                            print(f"  发现嵌套的li: {len(all_li_points) - len(top_level_points)}个", flush=True)
                    
                    def parse_nav_point(nav_point, level: int = 1):
                        if nav_point.name == "navPoint":
                            # NCX格式
                            label = nav_point.find("navLabel")
                            if label:
                                text = label.find("text")
                                if text:
                                    title = normalize_title(text.get_text())
                                    if title:
                                        # 获取页码（使用playOrder作为索引）
                                        play_order = nav_point.get("playOrder", "0")
                                        try:
                                            page_num = int(play_order)
                                        except ValueError:
                                            page_num = len(ncx_entries) + 1
                                        
                                        # 尝试从content中找到spine索引
                                        content_elem = nav_point.find("content")
                                        if content_elem and content_elem.get("src"):
                                            src = content_elem.get("src")
                                            import urllib.parse
                                            try:
                                                # 移除锚点并解码
                                                parsed_src = urllib.parse.unquote(src.split('#')[0])
                                                
                                                # 规范化路径
                                                def normalize_path(path: str) -> str:
                                                    path = path.replace('\\', '/')
                                                    if path.startswith('/'):
                                                        path = path[1:]
                                                    return path
                                                
                                                parsed_src_normalized = normalize_path(parsed_src)
                                                parsed_src_filename = os.path.basename(parsed_src)
                                                
                                                # 尝试多种匹配方式
                                                matched = False
                                                for spine_id, idx in spine_id_to_index.items():
                                                    item = next((item for item in book.get_items() if item.get_id() == spine_id), None)
                                                    if item:
                                                        item_name = item.get_name() if hasattr(item, 'get_name') else ""
                                                        if item_name:
                                                            item_name_normalized = normalize_path(item_name)
                                                            # 精确匹配、规范化路径匹配或文件名匹配
                                                            if (parsed_src in item_name or item_name in parsed_src or
                                                                parsed_src_normalized == item_name_normalized or
                                                                parsed_src_normalized.endswith(item_name_normalized) or
                                                                item_name_normalized.endswith(parsed_src_normalized) or
                                                                parsed_src_filename in item_name or item_name.endswith(parsed_src_filename)):
                                                                page_num = idx
                                                                matched = True
                                                                print(f"    路径匹配成功: {parsed_src} <-> {item_name} -> {idx}", flush=True)
                                                                break
                                                if not matched:
                                                    print(f"    警告: 无法映射路径 {parsed_src}，使用playOrder {page_num}", flush=True)
                                            except Exception as exc:
                                                print(f"    路径匹配异常: {exc}", flush=True)
                                        
                                        # 不翻译，翻译在用户点击翻译按钮时进行
                                        ncx_entries.append((level, title, page_num))
                                        print(f"  提取NCX目录项 (层级{level}): {title[:60]} (page: {page_num})", flush=True)
                            
                            # 递归处理子节点（只查找直接子节点，避免重复）
                            children = nav_point.find_all("navPoint", recursive=False)
                            if children:
                                print(f"    发现{len(children)}个子节点 (层级{level}->{level+1})", flush=True)
                                for idx, child in enumerate(children):
                                    print(f"      处理NCX子节点 {idx+1}/{len(children)}", flush=True)
                                    parse_nav_point(child, level + 1)
                            else:
                                print(f"    无子节点 (层级{level}): {title[:50] if 'title' in locals() else 'N/A'}", flush=True)
                        elif nav_point.name == "li":
                            # EPUB 3 NAV格式
                            link = nav_point.find("a")
                            if link:
                                title = normalize_title(link.get_text())
                                href = link.get("href", "")
                                if title:
                                    page_num = len(ncx_entries) + 1
                                    if href:
                                        import urllib.parse
                                        try:
                                            # 移除锚点并解码
                                            parsed_href = urllib.parse.unquote(href.split('#')[0])
                                            
                                            # 规范化路径
                                            def normalize_path(path: str) -> str:
                                                path = path.replace('\\', '/')
                                                if path.startswith('/'):
                                                    path = path[1:]
                                                return path
                                            
                                            parsed_href_normalized = normalize_path(parsed_href)
                                            parsed_href_filename = os.path.basename(parsed_href)
                                            
                                            # 尝试多种匹配方式
                                            matched = False
                                            for spine_id, idx in spine_id_to_index.items():
                                                item = next((item for item in book.get_items() if item.get_id() == spine_id), None)
                                                if item:
                                                    item_name = item.get_name() if hasattr(item, 'get_name') else ""
                                                    if item_name:
                                                        item_name_normalized = normalize_path(item_name)
                                                        # 精确匹配、规范化路径匹配或文件名匹配
                                                        if (parsed_href in item_name or item_name in parsed_href or
                                                            parsed_href_normalized == item_name_normalized or
                                                            parsed_href_normalized.endswith(item_name_normalized) or
                                                            item_name_normalized.endswith(parsed_href_normalized) or
                                                            parsed_href_filename in item_name or item_name.endswith(parsed_href_filename)):
                                                            page_num = idx
                                                            matched = True
                                                            print(f"    路径匹配成功: {parsed_href} <-> {item_name} -> {idx}", flush=True)
                                                            break
                                            if not matched:
                                                print(f"    警告: 无法映射路径 {parsed_href}，使用默认值 {page_num}", flush=True)
                                        except Exception as exc:
                                            print(f"    路径匹配异常: {exc}", flush=True)
                                    
                                    # 不翻译，翻译在用户点击翻译按钮时进行
                                    ncx_entries.append((level, title, page_num))
                                    print(f"  提取NAV目录项 (层级{level}): {title[:60]} (page: {page_num})", flush=True)
                            
                            # 递归处理子节点（ol或ul）
                            sub_list = nav_point.find(["ol", "ul"], recursive=False)
                            if sub_list:
                                child_lis = sub_list.find_all("li", recursive=False)
                                if child_lis:
                                    print(f"    发现{len(child_lis)}个NAV子节点 (层级{level}->{level+1})", flush=True)
                                    for idx, child_li in enumerate(child_lis):
                                        print(f"      处理NAV子节点 {idx+1}/{len(child_lis)}", flush=True)
                                        parse_nav_point(child_li, level + 1)
                            else:
                                print(f"    无NAV子节点 (层级{level}): {title[:50] if 'title' in locals() else 'N/A'}", flush=True)
                    
                    # 处理顶级导航点
                    for nav_point in top_level_points:
                        parse_nav_point(nav_point, 1)
                    
                    print(f"从NCX/NAV提取了{len(ncx_entries)}个目录条目", flush=True)
                    
                    # 如果NCX提取成功，先保存结果，但继续尝试book.toc（如果NCX条目太少）
                    if ncx_entries:
                        print(f"从NCX/NAV提取了{len(ncx_entries)}个目录条目", flush=True)
                        if len(ncx_entries) < 20:
                            print(f"警告: NCX提取的条目较少({len(ncx_entries)})，将继续尝试book.toc", flush=True)
            except Exception as exc:
                print(f"从NCX提取目录失败: {exc}", flush=True)
                import traceback
                traceback.print_exc()
        
        # 尝试使用book.toc（ebooklib提供的标准方法），无论NCX是否成功
        book_toc_entries: List[Tuple[int, str, int]] = []
        try:
            book_toc = book.toc
            if book_toc:
                print(f"使用book.toc提取目录，共{len(book_toc)}个顶级条目", flush=True)
                # 调试：打印所有条目的结构
                if book_toc:
                    print(f"book.toc 完整结构分析:", flush=True)
                    for idx, toc_item in enumerate(book_toc):
                        print(f"条目 {idx+1}: 类型={type(toc_item)}", flush=True)
                        if isinstance(toc_item, tuple):
                            print(f"  是tuple，长度: {len(toc_item)}", flush=True)
                            if len(toc_item) >= 1:
                                section = toc_item[0]
                                print(f"  section类型: {type(section)}", flush=True)
                                if hasattr(section, 'title'):
                                    print(f"  section.title: {section.title[:60]}", flush=True)
                                if hasattr(section, 'href'):
                                    print(f"  section.href: {section.href[:60]}", flush=True)
                            if len(toc_item) >= 2:
                                children = toc_item[1]
                                print(f"  children类型: {type(children)}", flush=True)
                                if hasattr(children, '__len__'):
                                    print(f"  children长度: {len(children)}", flush=True)
                                    if len(children) > 0:
                                        print(f"  第一个child类型: {type(children[0])}, 内容: {children[0]}", flush=True)
                                else:
                                    print(f"  children不是序列类型", flush=True)
                        else:
                            print(f"  不是tuple，直接内容: {toc_item}", flush=True)
                
                def parse_toc_item(toc_item, level: int = 1):
                    if isinstance(toc_item, tuple):
                        # 格式: (Section(title, href), [children])
                        if len(toc_item) >= 1:
                            section = toc_item[0]
                            children = toc_item[1] if len(toc_item) >= 2 else []
                        else:
                            return
                        
                        title = section.title if hasattr(section, 'title') else str(section)
                        href = section.href if hasattr(section, 'href') else ""
                        
                        # 调试：打印子节点信息
                        if children:
                            print(f"  发现子节点 (层级{level}): {title[:50]}, 子节点数: {len(children) if hasattr(children, '__len__') else 'N/A'}", flush=True)
                            if hasattr(children, '__len__') and len(children) > 0:
                                print(f"    第一个子节点类型: {type(children[0])}, 内容: {children[0]}", flush=True)
                        else:
                            print(f"  无子节点 (层级{level}): {title[:50]}", flush=True)
                        
                        # 尝试从href中找到对应的spine索引
                        page_num = len(book_toc_entries) + 1  # 默认使用当前索引
                        if href:
                            # 尝试多种匹配方式
                            for spine_id, idx in spine_id_to_index.items():
                                if spine_id in href or href in spine_id or (spine_id and href.endswith(spine_id)):
                                    page_num = idx
                                    break
                            # 如果还是没找到，尝试从href中提取文件名
                            if page_num == len(book_toc_entries) + 1:
                                import urllib.parse
                                try:
                                    parsed_href = urllib.parse.unquote(href.split('#')[0])
                                    for spine_id, idx in spine_id_to_index.items():
                                        item = next((item for item in book.get_items() if item.get_id() == spine_id), None)
                                        if item:
                                            item_name = item.get_name() if hasattr(item, 'get_name') else ""
                                            if item_name and (parsed_href in item_name or item_name in parsed_href):
                                                page_num = idx
                                                break
                                except Exception:
                                    pass
                        
                        title_clean = normalize_title(title)
                        # 即使可能是乱码也先保留，后续可以修复
                        # 不翻译，翻译在用户点击翻译按钮时进行
                        if title_clean:
                            if not is_garbled(title_clean):
                                book_toc_entries.append((level, title_clean, page_num))
                                print(f"  提取目录项 (层级{level}): {title_clean[:50]} (page: {page_num})", flush=True)
                            else:
                                # 乱码也保留，但标记
                                book_toc_entries.append((level, title_clean, page_num))
                                print(f"  提取目录项 (层级{level}, 可能乱码): {title_clean[:50]} (page: {page_num})", flush=True)
                        
                        # 递归处理子节点
                        if children:
                            if isinstance(children, (list, tuple)):
                                print(f"  开始递归处理 {len(children)} 个子节点 (层级{level}->{level+1})", flush=True)
                                for idx, child in enumerate(children):
                                    print(f"    处理子节点 {idx+1}/{len(children)}", flush=True)
                                    parse_toc_item(child, level + 1)
                            elif hasattr(children, '__iter__'):
                                # 尝试迭代
                                children_list = list(children)
                                print(f"  开始递归处理 {len(children_list)} 个子节点 (层级{level}->{level+1})", flush=True)
                                for idx, child in enumerate(children_list):
                                    print(f"    处理子节点 {idx+1}/{len(children_list)}", flush=True)
                                    parse_toc_item(child, level + 1)
                            else:
                                print(f"  警告: children不是list/tuple/iterable类型: {type(children)}, 值: {children}", flush=True)
                    else:
                        # 如果不是tuple，尝试直接处理
                        print(f"  警告: toc_item不是tuple类型: {type(toc_item)}, 内容: {toc_item}", flush=True)
                
                for toc_item in book_toc:
                    parse_toc_item(toc_item, 1)
                
                print(f"从book.toc提取了{len(book_toc_entries)}个目录条目", flush=True)
                
                # 如果book.toc提取的条目很少，可能是只提取了顶级条目，需要检查是否有子节点
                if len(book_toc_entries) < 10:
                    print(f"警告: book.toc只提取了{len(book_toc_entries)}个条目，可能不完整", flush=True)
        except Exception as exc:
            print(f"使用book.toc提取目录失败: {exc}", flush=True)
        
        # 合并策略：选择更完整的目录
        toc_entries: List[Tuple[int, str, int]] = []
        if ncx_entries and book_toc_entries:
            # 两者都有结果，选择条目更多的
            if len(book_toc_entries) > len(ncx_entries):
                print(f"book.toc条目({len(book_toc_entries)})多于NCX({len(ncx_entries)})，使用book.toc", flush=True)
                # 使用book.toc，但可以尝试补充NCX中独有的条目
                existing_titles = {title for _, title, _ in book_toc_entries}
                for entry in ncx_entries:
                    if entry[1] not in existing_titles:
                        book_toc_entries.append(entry)
                        existing_titles.add(entry[1])
                toc_entries = book_toc_entries
                print(f"合并后共{len(toc_entries)}个目录条目", flush=True)
            elif len(ncx_entries) > len(book_toc_entries):
                print(f"NCX条目({len(ncx_entries)})多于book.toc({len(book_toc_entries)})，使用NCX", flush=True)
                toc_entries = ncx_entries
            else:
                # 条目数相同，使用book.toc（通常更完整，包含所有层级）
                print(f"NCX和book.toc条目数相同({len(ncx_entries)})，使用book.toc", flush=True)
                toc_entries = book_toc_entries
        elif ncx_entries:
            # 只有NCX有结果
            print(f"使用NCX/NAV目录，共{len(ncx_entries)}个条目", flush=True)
            toc_entries = ncx_entries
        elif book_toc_entries:
            # 只有book.toc有结果
            print(f"使用book.toc目录，共{len(book_toc_entries)}个条目", flush=True)
            toc_entries = book_toc_entries
        
        # 如果提取的条目太少（少于20个），打印严重警告
        if toc_entries and len(toc_entries) < 20:
            print(f"⚠️  警告: 提取的目录条目过少({len(toc_entries)})，可能不完整！", flush=True)
            print(f"   请检查NCX和book.toc的递归处理是否正确", flush=True)
            print(f"   NCX条目数: {len(ncx_entries)}, book.toc条目数: {len(book_toc_entries)}", flush=True)
        
        # 如果仍然没有目录，从spine中提取HTML章节作为最后手段
        if not toc_entries:
            print("尝试从spine中提取HTML章节作为目录", flush=True)
            # 从spine中提取HTML章节
            chapter_index = 0
            # 创建spine_id到item的映射
            items_by_id = {item.get_id(): item for item in book.get_items()}
            for spine_id, _ in spine_items:
                item = items_by_id.get(spine_id)
                if item:
                    # 检查是否是HTML/XHTML文件（通过文件名或直接尝试解析）
                    item_name = item.get_name() if hasattr(item, 'get_name') else ""
                    is_html = False
                    if item_name:
                        is_html = any(ext in item_name.lower() for ext in ['.html', '.xhtml', '.htm'])
                    else:
                        # 如果没有文件名，直接尝试解析内容
                        is_html = True
                    
                    if is_html:
                        chapter_index += 1
                        # 尝试从HTML内容中提取标题
                        try:
                            content = item.get_content().decode("utf-8", errors="ignore")
                            soup = BeautifulSoup(content, "html.parser")
                            title = ""
                            # 查找h1-h6标签
                            for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                                title = tag.get_text().strip()
                                if title:
                                    break
                            if not title:
                                title = f"章节 {chapter_index}"
                            level = 1  # 默认层级
                            toc_entries.append((level, normalize_title(title), chapter_index))
                        except Exception as exc:
                            print(f"从HTML提取章节标题失败: {exc}", flush=True)
                            title = f"章节 {chapter_index}"
                            level = 1
                            toc_entries.append((level, normalize_title(title), chapter_index))

        if not toc_entries:
            raise ValueError("未在 EPUB 中检测到有效目录。")

        print(f"最终提取的目录条目数: {len(toc_entries)}", flush=True)
        if toc_entries:
            print(f"目录示例 (前3条):", flush=True)
            for i, (level, title, page) in enumerate(toc_entries[:3]):
                print(f"  [{i+1}] 层级{level}: {title[:60]} (page: {page})", flush=True)

        raw_toc = toc_entries

        # 使用大模型清洗目录（如果未跳过）
        if skip_llm:
            cleaned_toc = raw_toc
        else:
            api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
            if not api_key:
                raise ValueError("缺少 DOUBAO_API_KEY，无法清洗目录。")

            cleaned_toc = raw_toc
            try:
                cleaned_toc = call_doubao_clean_toc(raw_toc, api_key)
                if not cleaned_toc:
                    cleaned_toc = raw_toc
            except Exception as exc:
                print(f"大模型清洗EPUB目录失败: {exc}", flush=True)
                cleaned_toc = raw_toc

        # 格式化目录用于显示
        raw_toc_formatted = [
            {"level": level, "title": title, "page": page}
            for level, title, page in raw_toc
        ]
        cleaned_toc_formatted = [
            {"level": level, "title": title, "page": page}
            for level, title, page in cleaned_toc
        ]

        return {
            "raw_toc": raw_toc_formatted,
            "cleaned_toc": cleaned_toc_formatted,
            "raw_count": len(raw_toc),
            "cleaned_count": len(cleaned_toc),
            "ncx_count": len(ncx_entries),
            "book_toc_count": len(book_toc_entries),
        }

    def extract_epub_content_from_toc(upload: io.BytesIO, cleaned_toc: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """基于清洗后的EPUB目录提取内容和统计"""
        upload.seek(0)
        epub_bytes = upload.read()
        # epub.read_epub() 在某些操作中需要文件路径，所以使用临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix='.epub') as tmp_file:
            tmp_file.write(epub_bytes)
            tmp_file_path = tmp_file.name
        try:
            book = epub.read_epub(tmp_file_path)
        finally:
            # 确保临时文件被删除
            try:
                os.unlink(tmp_file_path)
            except OSError:
                pass

        # 创建章节ID到内容的映射（按spine顺序）
        spine_items = list(book.spine)
        chapter_map: Dict[int, str] = {}
        chapter_map_by_title: Dict[str, str] = {}  # 标题到内容的映射
        
        # 创建spine_id到item的映射
        items_by_id = {item.get_id(): item for item in book.get_items()}
        
        for idx, (spine_id, _) in enumerate(spine_items):
            item = items_by_id.get(spine_id)
            if item:
                try:
                    # 对于EPUB，spine中的项通常是HTML/XHTML文件，直接尝试解析
                    # 检查文件名（可选）
                    item_name = item.get_name() if hasattr(item, 'get_name') else ""
                    # 跳过明显不是文本内容的文件（如图片、CSS等）
                    skip_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.css', '.js', '.woff', '.ttf', '.otf']
                    should_skip = False
                    if item_name:
                        should_skip = any(item_name.lower().endswith(ext) for ext in skip_extensions)
                    
                    if not should_skip:
                        # 尝试提取内容
                        try:
                            content_bytes = item.get_content()
                            if content_bytes:
                                content = content_bytes.decode("utf-8", errors="ignore")
                                # 检查是否包含HTML标签
                                if "<" in content and ">" in content:
                                    soup = BeautifulSoup(content, "html.parser")
                                    # 提取文本内容
                                    text = soup.get_text(separator="\n", strip=True)
                                    if text:  # 只有非空文本才保存
                                        chapter_map[idx + 1] = text
                                        
                                        # 尝试提取标题，建立标题到内容的映射
                                        # 方法1: 从HTML标签中提取（按优先级）
                                        title_from_content = ""
                                        for tag_name in ["h1", "h2", "h3", "h4", "h5", "h6", "title"]:
                                            tags = soup.find_all(tag_name)
                                            if tags:
                                                # 取第一个非空标题
                                                for tag in tags:
                                                    candidate = tag.get_text().strip()
                                                    if candidate and len(candidate) < 200:  # 标题不应该太长
                                                        title_from_content = candidate
                                                        break
                                                if title_from_content:
                                                    break
                                        
                                        # 方法2: 如果HTML标签中没有找到，尝试从文本的第一行提取
                                        if not title_from_content:
                                            lines = text.split('\n')
                                            for line in lines[:5]:  # 检查前5行
                                                line = line.strip()
                                                if line and len(line) < 200 and len(line) > 3:
                                                    # 跳过明显不是标题的行（如纯数字、纯标点等）
                                                    if not line.replace('.', '').replace('-', '').isdigit():
                                                        title_from_content = line
                                                        break
                                        
                                        # 将提取的标题添加到映射中（支持多个变体）
                                        if title_from_content:
                                            # 原始标题
                                            chapter_map_by_title[title_from_content] = text
                                            # 去除前后空格的变体
                                            title_trimmed = title_from_content.strip()
                                            if title_trimmed != title_from_content:
                                                chapter_map_by_title[title_trimmed] = text
                                            # 去除常见前缀/后缀的变体（如"Chapter 1: "）
                                            import re
                                            title_cleaned = re.sub(r'^(chapter|part|section)\s+\d+[:\s]*', '', title_trimmed, flags=re.IGNORECASE).strip()
                                            if title_cleaned and title_cleaned != title_trimmed:
                                                chapter_map_by_title[title_cleaned] = text
                                else:
                                    # 纯文本内容
                                    text = content.strip()
                                    if text:
                                        chapter_map[idx + 1] = text
                                        # 尝试从第一行提取标题
                                        lines = text.split('\n')
                                        for line in lines[:5]:  # 检查前5行
                                            line = line.strip()
                                            if line and len(line) < 200 and len(line) > 3:
                                                # 跳过明显不是标题的行
                                                if not line.replace('.', '').replace('-', '').isdigit():
                                                    chapter_map_by_title[line] = text
                                                    # 去除常见前缀的变体
                                                    import re
                                                    title_cleaned = re.sub(r'^(chapter|part|section)\s+\d+[:\s]*', '', line, flags=re.IGNORECASE).strip()
                                                    if title_cleaned and title_cleaned != line:
                                                        chapter_map_by_title[title_cleaned] = text
                                                    break
                        except UnicodeDecodeError:
                            # 如果UTF-8解码失败，尝试其他编码
                            try:
                                content = item.get_content().decode("gbk", errors="ignore")
                                if content.strip():
                                    chapter_map[idx + 1] = content.strip()
                            except Exception:
                                pass
                except Exception as exc:
                    print(f"提取章节内容失败 (spine_id={spine_id}, idx={idx}, name={item_name}): {exc}", flush=True)
                    chapter_map[idx + 1] = ""
        
        # 调试信息：检查内容提取情况
        print(f"EPUB内容提取统计: 共{len(spine_items)}个spine项, 成功提取{len([v for v in chapter_map.values() if v])}个章节内容", flush=True)
        if chapter_map:
            sample_idx = min(1, len(chapter_map))
            sample_content = chapter_map.get(sample_idx, "")
            if sample_content:
                print(f"示例章节内容 (索引{sample_idx}): 前100字符 = {sample_content[:100]}", flush=True)
            else:
                print(f"警告: 索引{sample_idx}的章节内容为空", flush=True)

        # 转换格式
        toc_entries = [
            (item["level"], item["title"], item["page"])
            for item in cleaned_toc
        ]

        results: List[Dict[str, Any]] = []
        level_counters: Dict[int, int] = {}
        chapter_markers = ["章", "节", "篇", "卷", "部", "部分", "回", "目"]
        default_marker = {1: "章", 2: "章", 3: "节", 4: "节"}

        numeral_allowed = re.compile(r"^[〇零一二三四五六七八九十百千万两\dIVXLCDM]+$")

        def int_to_cn(num: int) -> str:
            digits = "零一二三四五六七八九"
            units = ["", "十", "百", "千"]
            if num <= 0:
                return ""
            if num < 10:
                return digits[num]
            if num < 20:
                return "十" if num == 10 else "十" + digits[num % 10]
            result = ""
            chars = list(str(num))
            length = len(chars)
            for idx, ch in enumerate(chars):
                digit = int(ch)
                unit_idx = length - idx - 1
                if digit == 0:
                    if result and not result.endswith("零") and unit_idx > 0:
                        result += "零"
                else:
                    result += digits[digit] + units[unit_idx]
            result = result.rstrip("零")
            result = result.replace("零零", "零")
            if result.startswith("一十"):
                result = result[1:]
            return result

        for index, (level, title, page_number) in enumerate(toc_entries):
            for deeper in [lvl for lvl in level_counters if lvl > level]:
                level_counters.pop(deeper, None)
            current_index = level_counters.get(level, 0) + 1
            level_counters[level] = current_index

            stripped = title.strip()
            intrinsic_marker = next((marker for marker in chapter_markers if marker in title), None)
            if stripped.startswith("第"):
                match = re.match(
                    r"^第(?P<num>[^\s章节篇卷部部分回目]{1,24})(?P<mark>章|节|篇|卷|部|部分|回|目)?",
                    stripped,
                )
                if match:
                    num_segment = match.group("num")
                    mark = match.group("mark") or intrinsic_marker or default_marker.get(level, "章")
                    tail = stripped[match.end() :].lstrip()
                    if not numeral_allowed.fullmatch(num_segment):
                        cn_numeral = int_to_cn(current_index)
                        stripped = f"第{cn_numeral}{mark} {tail}".strip()
                    elif match.group("mark") is None:
                        stripped = f"第{num_segment}{mark} {tail}".strip()

            # 规范化标题用于匹配（去除标点、空格，转小写）
            def normalize_for_match(text: str) -> str:
                """规范化文本用于匹配：去除标点、空格，转小写"""
                if not text:
                    return ""
                # 转小写
                text = text.lower()
                # 去除标点符号和空格
                import string
                text = ''.join(ch for ch in text if ch.isalnum() or ch.isspace())
                # 去除多余空格
                text = ' '.join(text.split())
                return text
            
            normalized_title = normalize_for_match(title)

            # 收集直接子章节标题，用于后续截断父章节内容
            child_titles: List[str] = []
            for lookahead in range(index + 1, len(toc_entries)):
                next_level, next_title, _ = toc_entries[lookahead]
                if next_level <= level:
                    break
                if next_level == level + 1:
                    child_titles.append(cleaned_toc[lookahead]["title"])

            # 获取章节内容 - 使用多种策略匹配
            # 注意：对于英文书籍，标题匹配通常比页码匹配更可靠
            content = ""
            
            # 策略1: 通过规范化标题匹配（精确匹配）- 优先使用，因为更可靠
            for title_key, text in chapter_map_by_title.items():
                normalized_key = normalize_for_match(title_key)
                if normalized_title and normalized_key == normalized_title:
                    content = text
                    print(f"策略1成功：精确标题匹配 (标题: {title[:50]}, 匹配到: {title_key[:50]})", flush=True)
                    break
            
            # 策略2: 通过规范化标题部分匹配（模糊匹配）
            if not content:
                # 提取标题的关键词（去除常见词）
                title_words = set(normalized_title.split())
                common_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from'}
                title_words = title_words - common_words
                
                best_match = None
                best_score = 0
                for title_key, text in chapter_map_by_title.items():
                    normalized_key = normalize_for_match(title_key)
                    key_words = set(normalized_key.split())
                    key_words = key_words - common_words
                    
                    # 计算匹配度（共同关键词数量）
                    if title_words and key_words:
                        common = title_words & key_words
                        score = len(common) / max(len(title_words), len(key_words))
                        if score > best_score and score > 0.3:  # 至少30%匹配
                            best_score = score
                            best_match = text
                
                if best_match:
                    content = best_match
                    print(f"策略2成功：模糊标题匹配 (标题: {title[:50]}, 匹配度: {best_score:.2f})", flush=True)
            
            # 策略3: 使用page_number作为spine索引（1-based）- 作为后备
            # 注意：page_number可能来自NCX/NAV，可能不准确，所以放在标题匹配之后
            if not content:
                if page_number > 0 and page_number <= len(chapter_map):
                    candidate = chapter_map.get(page_number, "")
                    # 验证内容是否有效（非空且长度合理）
                    if candidate and len(candidate.strip()) > 50:
                        content = candidate
                        print(f"策略3成功：通过页码匹配 (标题: {title[:50]}, page: {page_number})", flush=True)
            
            # 策略4: 在内容中搜索标题关键词
            if not content:
                # 提取标题的主要关键词（至少3个字符的词）
                title_keywords = [w for w in normalized_title.split() if len(w) >= 3]
                if title_keywords:
                    for idx, text in chapter_map.items():
                        if text:
                            normalized_text = normalize_for_match(text[:1000])  # 只检查前1000字符
                            # 检查是否包含至少一个关键词
                            if any(kw in normalized_text for kw in title_keywords):
                                content = text
                                print(f"策略4成功：内容中搜索到标题关键词 (标题: {title[:50]}, spine_index: {idx})", flush=True)
                                break
            
            # 策略5: 按顺序从spine中提取（目录顺序通常和spine顺序一致）- 最后的后备方案
            if not content:
                # 使用目录索引作为spine索引（从1开始）
                spine_index = index + 1
                if spine_index <= len(chapter_map):
                    candidate = chapter_map.get(spine_index, "")
                    if candidate and len(candidate.strip()) > 50:
                        content = candidate
                        print(f"策略5成功：按顺序匹配 (标题: {title[:50]}, spine_index: {spine_index})", flush=True)
            
            # 调试信息
            if not content:
                print(f"警告：未找到章节内容 (标题: {title}, page: {page_number}, index: {index})", flush=True)
                # 输出可用的章节映射信息用于调试
                if chapter_map:
                    print(f"  可用章节数量: {len(chapter_map)}, 示例索引: {list(chapter_map.keys())[:5]}", flush=True)
                if chapter_map_by_title:
                    print(f"  可用标题数量: {len(chapter_map_by_title)}, 示例标题: {list(chapter_map_by_title.keys())[:3]}", flush=True)
            
            # 如果存在子章节，截断父章节内容，避免重复统计
            if content and child_titles:
                truncated = truncate_content_by_child_titles(content, child_titles)
                if truncated != content:
                    print(
                        f"截断父章节内容（EPUB）: {title[:50]} -> 原长度{len(content)}，截断后{len(truncated)}",
                        flush=True,
                    )
                    content = truncated

            # 保存原始标题和内容
            original_title = stripped
            original_content = content
            
            # 暂时禁用自动翻译，改为手动翻译
            # 如果是中文，中文字段就是原文；如果不是中文，中文字段先设为空，等用户点击翻译按钮时再翻译
            if is_chinese_text(original_title):
                title_zh = original_title
            else:
                title_zh = ""  # 非中文标题，等待手动翻译
            
            if content:
                if is_chinese_text(original_content):
                    content_zh = original_content
                else:
                    content_zh = ""  # 非中文内容，等待手动翻译
            else:
                content_zh = ""

            # 使用原文统计字数（用于显示）
            normalized = "".join(ch for ch in original_content if not ch.isspace())
            word_count = len(normalized)

            results.append(
                {
                    "title": original_title,
                    "content": original_content,
                    "title_zh": title_zh,
                    "content_zh": content_zh,
                    "word_count": word_count,
                }
            )

        return results

    def extract_content_from_toc(upload: io.BytesIO, cleaned_toc: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """基于清洗后的目录提取内容和统计"""
        upload.seek(0)
        pdf_bytes = upload.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        # 转换格式
        toc_entries = [
            (item["level"], item["title"], item["page"])
            for item in cleaned_toc
        ]

        results: List[Dict[str, Any]] = []
        level_counters: Dict[int, int] = {}
        chapter_markers = ["章", "节", "篇", "卷", "部", "部分", "回", "目"]
        default_marker = {1: "章", 2: "章", 3: "节", 4: "节"}

        numeral_allowed = re.compile(r"^[〇零一二三四五六七八九十百千万两\dIVXLCDM]+$")

        def int_to_cn(num: int) -> str:
            digits = "零一二三四五六七八九"
            units = ["", "十", "百", "千"]
            if num <= 0:
                return ""
            if num < 10:
                return digits[num]
            if num < 20:
                return "十" if num == 10 else "十" + digits[num % 10]
            result = ""
            chars = list(str(num))
            length = len(chars)
            for idx, ch in enumerate(chars):
                digit = int(ch)
                unit_idx = length - idx - 1
                if digit == 0:
                    if result and not result.endswith("零") and unit_idx > 0:
                        result += "零"
                else:
                    result += digits[digit] + units[unit_idx]
            result = result.rstrip("零")
            result = result.replace("零零", "零")
            if result.startswith("一十"):
                result = result[1:]
            return result

        for index, (level, title, start_page) in enumerate(toc_entries):
            for deeper in [lvl for lvl in level_counters if lvl > level]:
                level_counters.pop(deeper, None)
            current_index = level_counters.get(level, 0) + 1
            level_counters[level] = current_index

            stripped = title.strip()
            intrinsic_marker = next((marker for marker in chapter_markers if marker in title), None)
            if stripped.startswith("第"):
                match = re.match(
                    r"^第(?P<num>[^\s章节篇卷部部分回目]{1,24})(?P<mark>章|节|篇|卷|部|部分|回|目)?",
                    stripped,
                )
                if match:
                    num_segment = match.group("num")
                    mark = match.group("mark") or intrinsic_marker or default_marker.get(level, "章")
                    tail = stripped[match.end() :].lstrip()
                    if not numeral_allowed.fullmatch(num_segment):
                        cn_numeral = int_to_cn(current_index)
                        stripped = f"第{cn_numeral}{mark} {tail}".strip()
                    elif match.group("mark") is None:
                        stripped = f"第{num_segment}{mark} {tail}".strip()

            start_idx = max(start_page - 1, 0)
            end_idx = doc.page_count - 1
            for next_entry in toc_entries[index + 1 :]:
                if next_entry[0] <= level:
                    end_idx = max(next_entry[2] - 2, start_idx)
                    break

            page_text: List[str] = []
            for page_id in range(start_idx, end_idx + 1):
                page = doc.load_page(page_id)
                extracted = page.get_text("text")
                if extracted:
                    page_text.append(extracted.strip())

            content = "\n".join(part for part in page_text if part).strip()
            
            # 收集直接子章节标题，用于截断父章节内容
            child_titles: List[str] = []
            for lookahead in range(index + 1, len(toc_entries)):
                next_level, next_title, _ = toc_entries[lookahead]
                if next_level <= level:
                    break
                if next_level == level + 1:
                    child_titles.append(cleaned_toc[lookahead]["title"])

            if content and child_titles:
                truncated = truncate_content_by_child_titles(content, child_titles)
                if truncated != content:
                    print(
                        f"截断父章节内容（PDF）: {title[:50]} -> 原长度{len(content)}，截断后{len(truncated)}",
                        flush=True,
                    )
                    content = truncated

            # 保存原始标题和内容
            original_title = stripped
            original_content = content
            
            # 暂时禁用自动翻译，改为手动翻译
            # 如果是中文，中文字段就是原文；如果不是中文，中文字段先设为空，等用户点击翻译按钮时再翻译
            if is_chinese_text(original_title):
                title_zh = original_title
            else:
                title_zh = ""  # 非中文标题，等待手动翻译
            
            if content:
                if is_chinese_text(original_content):
                    content_zh = original_content
                else:
                    content_zh = ""  # 非中文内容，等待手动翻译
            else:
                content_zh = ""

            # 使用原文统计字数（用于显示）
            normalized = "".join(ch for ch in original_content if not ch.isspace())
            word_count = len(normalized)

            results.append(
                {
                    "title": original_title,
                    "content": original_content,
                    "title_zh": title_zh,
                    "content_zh": content_zh,
                    "word_count": word_count,
                }
            )

        doc.close()
        return results

    def detect_file_type(filename: str, file_bytes: io.BytesIO) -> str:
        """检测文件类型"""
        filename_lower = filename.lower()
        if filename_lower.endswith(".pdf"):
            return "pdf"
        elif filename_lower.endswith(".epub"):
            return "epub"
        else:
            # 通过文件头检测
            file_bytes.seek(0)
            header = file_bytes.read(4)
            file_bytes.seek(0)
            if header.startswith(b"%PDF"):
                return "pdf"
            elif header.startswith(b"PK\x03\x04"):  # ZIP格式（EPUB是ZIP格式）
                # 检查是否是EPUB
                try:
                    import zipfile
                    with zipfile.ZipFile(file_bytes, "r") as zip_file:
                        if "META-INF/container.xml" in zip_file.namelist():
                            return "epub"
                except Exception:
                    pass
            return "unknown"

    @app.post("/api/parse/clean_toc")
    def clean_toc_endpoint():
        """清洗目录接口（支持PDF和EPUB）"""
        if "file" not in request.files:
            return jsonify({"error": "缺少文件上传"}), 400

        file_storage = request.files["file"]
        if file_storage.filename == "":
            return jsonify({"error": "未选择文件"}), 400

        try:
            file_bytes = io.BytesIO(file_storage.read())
            file_type = detect_file_type(file_storage.filename, file_bytes)
            
            if file_type == "pdf":
                result = clean_toc_only(file_bytes)
            elif file_type == "epub":
                result = clean_epub_toc_only(file_bytes)
            else:
                return jsonify({"error": f"不支持的文件格式：{file_storage.filename}，仅支持PDF和EPUB"}), 400
            
            return jsonify({**result, "filename": file_storage.filename, "file_type": file_type})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return jsonify({"error": f"清洗目录失败：{exc}"}), 500

    @app.post("/api/parse/debug_toc")
    def debug_toc_endpoint():
        """调试：仅提取原始目录，不进行LLM清洗（支持PDF和EPUB）"""
        if "file" not in request.files:
            return jsonify({"error": "缺少文件上传"}), 400

        file_storage = request.files["file"]
        if file_storage.filename == "":
            return jsonify({"error": "未选择文件"}), 400

        try:
            file_bytes = io.BytesIO(file_storage.read())
            file_type = detect_file_type(file_storage.filename, file_bytes)
            
            if file_type == "epub":
                # 对于EPUB，调用clean_epub_toc_only获取原始目录和统计信息（跳过LLM清洗）
                result = clean_epub_toc_only(file_bytes, skip_llm=True)
                raw_toc = result.get("raw_toc", [])
                ncx_count = result.get("ncx_count", 0)
                book_toc_count = result.get("book_toc_count", 0)
                debug_info = f"EPUB原始目录提取完成（跳过LLM清洗）\nNCX条目数: {ncx_count}\nbook.toc条目数: {book_toc_count}\n最终条目数: {len(raw_toc)}"
            elif file_type == "pdf":
                # 对于PDF，提取原始TOC
                result = clean_toc_only(file_bytes)
                raw_toc = result.get("raw_toc", [])
                ncx_count = 0
                book_toc_count = len(raw_toc)
                debug_info = f"PDF原始目录提取完成\n最终条目数: {len(raw_toc)}"
            else:
                return jsonify({"error": f"不支持的文件格式：{file_storage.filename}，仅支持PDF和EPUB"}), 400
            
            return jsonify({
                "raw_toc": raw_toc,
                "filename": file_storage.filename,
                "file_type": file_type,
                "ncx_count": ncx_count,
                "book_toc_count": book_toc_count,
                "debug_info": debug_info
            })
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:  # pylint: disable=broad-exception-caught
            import traceback
            return jsonify({"error": f"提取原始目录失败：{exc}", "traceback": traceback.format_exc()}), 500

    @app.post("/api/parse/extract")
    def extract_content_endpoint():
        """提取内容接口（支持PDF和EPUB）"""
        # 尝试从JSON或FormData中获取cleaned_toc
        cleaned_toc = None
        file_type = None
        if request.is_json:
            payload = request.get_json() or {}
            cleaned_toc = payload.get("cleaned_toc")
            file_type = payload.get("file_type")
        else:
            # 从FormData中获取
            cleaned_toc_str = request.form.get("cleaned_toc")
            if cleaned_toc_str:
                try:
                    cleaned_toc = json.loads(cleaned_toc_str)
                except json.JSONDecodeError:
                    return jsonify({"error": "cleaned_toc 格式错误"}), 400
            file_type = request.form.get("file_type")

        if not isinstance(cleaned_toc, list):
            return jsonify({"error": "缺少 cleaned_toc 字段或格式错误"}), 400

        # 验证清洗后的目录格式
        validated_cleaned_toc = []
        for item in cleaned_toc:
            if isinstance(item, dict) and "level" in item and "title" in item and "page" in item:
                validated_cleaned_toc.append(item)
            else:
                # 如果格式不正确，尝试转换
                print(f"警告：目录项格式不正确，尝试转换：{item}", flush=True)
                if isinstance(item, (list, tuple)) and len(item) >= 3:
                    validated_cleaned_toc.append({
                        "level": item[0],
                        "title": item[1],
                        "page": item[2]
                    })
        
        if not validated_cleaned_toc:
            return jsonify({"error": "清洗后的目录格式不正确或为空"}), 400
        
        # 调试日志：确认使用的是清洗后的目录
        print(f"提取内容：使用清洗后的目录，共 {len(validated_cleaned_toc)} 个条目", flush=True)
        if validated_cleaned_toc:
            print(f"清洗后目录示例：{validated_cleaned_toc[0]}", flush=True)
        
        cleaned_toc = validated_cleaned_toc

        if "file" not in request.files:
            return jsonify({"error": "缺少文件上传"}), 400

        file_storage = request.files["file"]
        if file_storage.filename == "":
            return jsonify({"error": "未选择文件"}), 400

        try:
            file_bytes = io.BytesIO(file_storage.read())
            # 如果没有提供file_type，自动检测
            if not file_type:
                file_type = detect_file_type(file_storage.filename, file_bytes)
            
            if file_type == "pdf":
                results = extract_content_from_toc(file_bytes, cleaned_toc)
            elif file_type == "epub":
                results = extract_epub_content_from_toc(file_bytes, cleaned_toc)
            else:
                return jsonify({"error": f"不支持的文件格式：{file_storage.filename}，仅支持PDF和EPUB"}), 400
            
            return jsonify({"entries": results, "filename": file_storage.filename, "file_type": file_type})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return jsonify({"error": f"提取内容失败：{exc}"}), 500

    @app.post("/api/parse")
    def parse_endpoint():
        if "file" not in request.files:
            return jsonify({"error": "缺少文件上传"}), 400

        file_storage = request.files["file"]
        if file_storage.filename == "":
            return jsonify({"error": "未选择文件"}), 400

        try:
            file_bytes = io.BytesIO(file_storage.read())
            results = parse_pdf_document(file_bytes)
            return jsonify({"entries": results, "filename": file_storage.filename})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return jsonify({"error": f"解析失败：{exc}"}), 500

    @app.get("/api/settings/prompt_parts")
    def get_prompt_parts():
        """获取分解后的提示词各部分"""
        try:
            # 默认提示词各部分
            default_intro_prompt = """【一、个性化导读摘要】

目标：

结合用户偏好（职业 / 阅读目的 / 关注点）+ 章节摘要，为用户生成一段 150–300 字的"个性化导读"。

要求：

清楚告诉用户：

这一节在讲什么；

和他（例如 CEO）有什么关系；

对他的阅读目标（如：提升管理技能）有什么价值；

为什么值得读。

语言口语化，像一个朋友耐心地说：

"如果一句话讲明白，这一节其实是在说……"

不要机械重复章节摘要，要重写。"""

            default_body_prompt = """【二、正文讲解（依照用户选择的密度）】

请用艾思奇 + 费曼的风格，把原文重新讲解出来。
讲解内容密度取决于 user_profile.explanation_density：

20% 主干：

只讲主线结论 + 最核心的推理；

控制要点约 3–4 个。

50% 核心：

在主干基础上补充关键例子 & 重要细节；

控制要点约 5–7 个。

70% 深度：

覆盖重要分支观点和细节；

控制要点约 8–12 个，但仍比原文更精炼。

讲解结构建议如下（强制）：

整体框架

2–4 句讲清楚这一节的主旨与作者要回答的问题。

分点讲解

以 3–10 个要点展开（数量依照密度）；

每个要点必须包含：

这个观点讲的是什么；

作者为什么这么说（推理链）；

一个通俗例子或比喻；

避免原文照搬。

小结

3–5 句话总结：

这一节的骨架是什么？

哪些是容易忽略的误解？

如果只能记两句话，该记什么？"""

            default_quiz_prompt = """【四、知识点选择题（3–5 题）】

从你的讲解内容中挑选 3–5 个关键知识点，设计成选择题。

每题格式：

第 X 题
题干：……
A. ……
B. ……
C. ……
D. ……
正确答案：X
解析：用 1–3 句话解释为什么。

要求：

题目必须基于你的讲解（不是原文，也不是扩展内容）；

选项要有迷惑性但不恶意；

重点考核概念理解与逻辑关系。"""

            default_question_prompt = """【五、一个最强有力的思考问题】

结合：

用户职业（根据 user_profile.profession）

阅读目的（根据 user_profile.reading_goal）

内容核心思想

提出 一个 极具力量的开放式问题。

特征：

指向现实，而不是抽象哲学；

帮助用户重新审视自己的行为模式、习惯或决策；

要具体到能让他立即产生自我反思。

避免：

"你有什么收获？"这类泛泛之问；

同时提出多个问题。

示例模板（仅示意）：

"在你最近做的某个关键管理决策里，这一节提到的 X 思想，你最忽略的是哪一部分？" """

            return jsonify({
                "intro_prompt": load_setting("prompt_intro", default=default_intro_prompt),
                "body_prompt": load_setting("prompt_body", default=default_body_prompt),
                "quiz_prompt": load_setting("prompt_quiz", default=default_quiz_prompt),
                "question_prompt": load_setting("prompt_question", default=default_question_prompt),
            })
        except SQLAlchemyError as exc:
            return jsonify({"error": f"读取提示词失败：{exc}"}), 500

    @app.post("/api/settings/prompt_parts")
    def save_prompt_parts():
        """保存分解后的提示词各部分"""
        payload = request.get_json() or {}
        try:
            if "intro_prompt" in payload:
                store_setting("prompt_intro", payload["intro_prompt"])
            if "body_prompt" in payload:
                store_setting("prompt_body", payload["body_prompt"])
            if "quiz_prompt" in payload:
                store_setting("prompt_quiz", payload["quiz_prompt"])
            if "question_prompt" in payload:
                store_setting("prompt_question", payload["question_prompt"])
            return jsonify({"status": "ok"})
        except SQLAlchemyError as exc:
            return jsonify({"error": f"保存失败：{exc}"}), 500

    def get_master_prompt():
        """获取完整的主提示词（兼容旧接口）"""
        try:
            # 基础提示词
            base_prompt = """你是"阅读一本通"的 AI 讲书助手。你的任务不是总结，而是把内容讲懂：用朴实、清晰、逻辑顺的方式，像一位耐心的讲书老师，把书讲得让用户听懂、用得上。

你的讲解风格：

艾思奇：朴实、逻辑清楚、不装；

费曼：把复杂内容讲到中学生都能听懂；

有例子、有推理、有"讲故事感"。

一、输入格式（来自系统）

你会收到以下输入（结构固定）：

{
  "user_profile": {
    "profession": "用户职业（由系统提供）",
    "reading_goal": "用户阅读目的（由系统提供）",
    "focus_preference": "用户关注重点（由系统提供）",
    "explanation_density": "解读密度（由系统提供）"
  },
  "chapter_summary": "章节摘要",
  "chapter_fulltext": "章节全文"
}

说明：

explanation_density 将直接决定讲解的密度，只会出现三种：

"20% 主干"

"50% 核心"

"70% 深度"

你必须严格依照此密度控制讲解的信息量。

二、你的任务（必须按顺序完成 5 个部分）

最终输出包含五部分，请严格按顺序和结构输出："""

            # 获取各部分提示词
            intro_prompt = load_setting("prompt_intro", default="")
            body_prompt = load_setting("prompt_body", default="")
            quiz_prompt = load_setting("prompt_quiz", default="")
            question_prompt = load_setting("prompt_question", default="")
            
            # 如果各部分都有，组合它们；否则使用默认完整提示词
            if intro_prompt and body_prompt and quiz_prompt and question_prompt:
                application_prompt = """【三、现实生活中的举一反三应用】

结合用户身份与阅读目标（根据 user_profile 中的 profession 和 reading_goal），输出 2–4 个可落地的应用场景。

每个应用场景必须包含三部分：

【场景】

用一句话说明这是现实中的什么场景；

【怎么用】

给出清晰的步骤或行为建议，不可空泛；

【可能效果】

简短描述执行后可能带来的改善。

例子示范结构（仅供参考）：

场景一：在带团队开会时……

怎么用：……

效果：……

场景二：在产品决策里……

怎么用：……

效果：……"""
                
                full_prompt = f"""{base_prompt}

{intro_prompt}

{body_prompt}

{application_prompt}

{quiz_prompt}

{question_prompt}"""
                return jsonify({"value": full_prompt})
            else:
                # 使用默认完整提示词
                default_prompt = """你是"阅读一本通"的 AI 讲书助手。你的任务不是总结，而是把内容讲懂：用朴实、清晰、逻辑顺的方式，像一位耐心的讲书老师，把书讲得让用户听懂、用得上。

你的讲解风格：

艾思奇：朴实、逻辑清楚、不装；

费曼：把复杂内容讲到中学生都能听懂；

有例子、有推理、有"讲故事感"。

一、输入格式（来自系统）

你会收到以下输入（结构固定）：

{
  "user_profile": {
    "profession": "用户职业（由系统提供）",
    "reading_goal": "用户阅读目的（由系统提供）",
    "focus_preference": "用户关注重点（由系统提供）",
    "explanation_density": "解读密度（由系统提供）"
  },
  "chapter_summary": "章节摘要",
  "chapter_fulltext": "章节全文"
}

说明：

explanation_density 将直接决定讲解的密度，只会出现三种：

"20% 主干"

"50% 核心"

"70% 深度"

你必须严格依照此密度控制讲解的信息量。

二、你的任务（必须按顺序完成 5 个部分）

最终输出包含五部分，请严格按顺序和结构输出：

【一、个性化导读摘要】

目标：

结合用户偏好（职业 / 阅读目的 / 关注点）+ 章节摘要，为用户生成一段 150–300 字的"个性化导读"。

要求：

清楚告诉用户：

这一节在讲什么；

和他（例如 CEO）有什么关系；

对他的阅读目标（如：提升管理技能）有什么价值；

为什么值得读。

语言口语化，像一个朋友耐心地说：

"如果一句话讲明白，这一节其实是在说……"

不要机械重复章节摘要，要重写。

【二、正文讲解（依照用户选择的密度）】

请用艾思奇 + 费曼的风格，把原文重新讲解出来。
讲解内容密度取决于 user_profile.explanation_density：

20% 主干：

只讲主线结论 + 最核心的推理；

控制要点约 3–4 个。

50% 核心：

在主干基础上补充关键例子 & 重要细节；

控制要点约 5–7 个。

70% 深度：

覆盖重要分支观点和细节；

控制要点约 8–12 个，但仍比原文更精炼。

讲解结构建议如下（强制）：

整体框架

2–4 句讲清楚这一节的主旨与作者要回答的问题。

分点讲解

以 3–10 个要点展开（数量依照密度）；

每个要点必须包含：

这个观点讲的是什么；

作者为什么这么说（推理链）；

一个通俗例子或比喻；

避免原文照搬。

小结

3–5 句话总结：

这一节的骨架是什么？

哪些是容易忽略的误解？

如果只能记两句话，该记什么？

【三、现实生活中的举一反三应用】

结合用户身份与阅读目标（根据 user_profile 中的 profession 和 reading_goal），输出 2–4 个可落地的应用场景。

每个应用场景必须包含三部分：

【场景】

用一句话说明这是现实中的什么场景；

【怎么用】

给出清晰的步骤或行为建议，不可空泛；

【可能效果】

简短描述执行后可能带来的改善。

例子示范结构（仅供参考）：

场景一：在带团队开会时……

怎么用：……

效果：……

场景二：在产品决策里……

怎么用：……

效果：……

【四、知识点选择题（3–5 题）】

从你的讲解内容中挑选 3–5 个关键知识点，设计成选择题。

每题格式：

第 X 题
题干：……
A. ……
B. ……
C. ……
D. ……
正确答案：X
解析：用 1–3 句话解释为什么。

要求：

题目必须基于你的讲解（不是原文，也不是扩展内容）；

选项要有迷惑性但不恶意；

重点考核概念理解与逻辑关系。

【五、一个最强有力的思考问题】

结合：

用户职业（根据 user_profile.profession）

阅读目的（根据 user_profile.reading_goal）

内容核心思想

提出 一个 极具力量的开放式问题。

特征：

指向现实，而不是抽象哲学；

帮助用户重新审视自己的行为模式、习惯或决策；

要具体到能让他立即产生自我反思。

避免：

"你有什么收获？"这类泛泛之问；

同时提出多个问题。

示例模板（仅示意）：

"在你最近做的某个关键管理决策里，这一节提到的 X 思想，你最忽略的是哪一部分？"
"""
                stored_value = load_setting("master_prompt", default=default_prompt)
                return jsonify({"value": stored_value})
        except SQLAlchemyError as exc:
            return jsonify({"error": f"读取提示词失败：{exc}"}), 500

    @app.get("/api/settings/master_prompt")
    def get_master_prompt_route():
        """获取完整的主提示词（兼容旧接口）"""
        return get_master_prompt()

    @app.post("/api/settings/master_prompt")
    def save_master_prompt():
        payload = request.get_json() or {}
        value = payload.get("value")
        if value is None:
            return jsonify({"error": "缺少 value 字段"}), 400

        try:
            store_setting("master_prompt", value)
            return jsonify({"status": "ok"})
        except SQLAlchemyError as exc:
            return jsonify({"error": f"保存失败：{exc}"}), 500

    @app.post("/api/generate")
    def generate_endpoint():
        payload = request.get_json() or {}
        required_fields = [
            "chapterTitle",
            "chapterText",
            "userProfession",
            "readingGoal",
            "focus",
            "density",
        ]

        missing = [field for field in required_fields if not payload.get(field)]
        if missing:
            return jsonify({"error": f"缺少字段：{', '.join(missing)}"}), 400

        # 从请求中获取用户设置的提示词部分
        intro_prompt = payload.get("intro_prompt", "").strip()
        body_prompt = payload.get("body_prompt", "").strip()
        quiz_prompt = payload.get("quiz_prompt", "").strip()
        question_prompt = payload.get("question_prompt", "").strip()
        
        # 如果用户没有提供提示词部分，从设置中加载
        if not intro_prompt:
            intro_prompt = load_setting("prompt_intro", default="")
        if not body_prompt:
            body_prompt = load_setting("prompt_body", default="")
        if not quiz_prompt:
            quiz_prompt = load_setting("prompt_quiz", default="")
        if not question_prompt:
            question_prompt = load_setting("prompt_question", default="")
        
        # 检查是否有必要的提示词（至少需要正文部分）
        if not body_prompt:
            return jsonify({"error": "缺少正文解读提示词，请在提示词调适界面中设置"}), 400
        
        # 构建提示词部分字典
        prompt_parts = {
            "intro_prompt": intro_prompt,
            "body_prompt": body_prompt,
            "quiz_prompt": quiz_prompt,
            "question_prompt": question_prompt,
        }
        
        try:
            llm_output = call_llm(prompt_parts, payload)
            # 保存时使用组合后的提示词（用于记录）
            combined_prompt = "\n\n".join([v for v in prompt_parts.values() if v])
            record_id = store_interpretation(payload, combined_prompt, llm_output)
            return jsonify({"result": llm_output, "record_id": record_id})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500

    @app.post("/api/generate/part")
    def generate_part_endpoint():
        """单独生成某个部分的解读（用于调试）"""
        payload = request.get_json() or {}
        part = payload.get("part")  # intro, body, quiz, question
        if not part:
            return jsonify({"error": "缺少 part 字段"}), 400
        
        required_fields = ["chapterTitle", "userProfession", "readingGoal", "focus", "density"]
        missing = [field for field in required_fields if not payload.get(field)]
        if missing:
            return jsonify({"error": f"缺少字段：{', '.join(missing)}"}), 400

        # 根据部分类型选择不同的输入内容和提示词
        if part == "intro":
            chapter_content = payload.get("chapterSummary", payload.get("chapterTitle", ""))
            part_prompt = payload.get("intro_prompt", "").strip()
            if not part_prompt:
                part_prompt = load_setting("prompt_intro", default="")
        elif part == "body":
            chapter_content = payload.get("chapterText", "")
            part_prompt = payload.get("body_prompt", "").strip()
            if not part_prompt:
                part_prompt = load_setting("prompt_body", default="")
        elif part == "quiz":
            chapter_content = payload.get("chapterText", "")
            part_prompt = payload.get("quiz_prompt", "").strip()
            if not part_prompt:
                part_prompt = load_setting("prompt_quiz", default="")
        elif part == "question":
            chapter_content = payload.get("chapterText", "")
            part_prompt = payload.get("question_prompt", "").strip()
            if not part_prompt:
                part_prompt = load_setting("prompt_question", default="")
        else:
            return jsonify({"error": f"无效的 part 值: {part}"}), 400

        if not part_prompt:
            return jsonify({"error": f"缺少 {part} 部分的提示词，请在提示词调适界面中设置"}), 400

        # 构建用户画像
        density_map = {
            "20% 主干": "20% 主干",
            "50% 核心": "50% 核心",
            "70% 深度": "70% 深度",
        }
        explanation_density = density_map.get(payload.get("density", "50% 核心"), "50% 核心")
        
        user_profile_json = json.dumps({
            "profession": payload.get("userProfession", ""),
            "reading_goal": payload.get("readingGoal", ""),
            "focus_preference": payload.get("focus", ""),
            "explanation_density": explanation_density,
        }, ensure_ascii=False, indent=2)

        # 根据部分类型构建不同的输入数据
        if part == "intro":
            # 导读部分：使用摘要
            input_data_json = json.dumps({
                "user_profile": {
                    "profession": payload.get("userProfession", ""),
                    "reading_goal": payload.get("readingGoal", ""),
                    "focus_preference": payload.get("focus", ""),
                    "explanation_density": explanation_density,
                },
                "chapter_summary": chapter_content,
                "chapter_fulltext": "",  # 导读不需要全文
            }, ensure_ascii=False, indent=2)
        else:
            # 其他部分：使用正文内容
            input_data_json = json.dumps({
                "user_profile": {
                    "profession": payload.get("userProfession", ""),
                    "reading_goal": payload.get("readingGoal", ""),
                    "focus_preference": payload.get("focus", ""),
                    "explanation_density": explanation_density,
                },
                "chapter_summary": payload.get("chapterSummary", ""),
                "chapter_fulltext": chapter_content,
            }, ensure_ascii=False, indent=2)

        # 构建调用LLM的payload
        llm_payload = {
            "chapterTitle": payload.get("chapterTitle", ""),
            "chapterText": chapter_content,
            "chapterSummary": payload.get("chapterSummary", ""),
            "userProfession": payload.get("userProfession", ""),
            "readingGoal": payload.get("readingGoal", ""),
            "focus": payload.get("focus", ""),
            "density": payload.get("density", ""),
        }

        # 构建提示词部分字典（只包含当前部分）
        prompt_parts = {
            "intro_prompt": "" if part != "intro" else part_prompt,
            "body_prompt": "" if part != "body" else part_prompt,
            "quiz_prompt": "" if part != "quiz" else part_prompt,
            "question_prompt": "" if part != "question" else part_prompt,
        }

        try:
            llm_output = call_llm(prompt_parts, llm_payload)
            
            # 根据部分类型提取对应的输出
            # 如果返回的是单个字段的JSON（如 {"personalized_intro": "..."}），直接提取
            # 如果返回的是完整JSON对象，从中提取对应字段
            if part == "intro":
                result = llm_output.get("personalized_intro") or llm_output.get("result") or llm_output.get("intro") or ""
            elif part == "body":
                result = llm_output.get("interpretation") or llm_output.get("result") or llm_output.get("body") or ""
            elif part == "quiz":
                result = llm_output.get("quiz") or llm_output.get("result") or []
            elif part == "question":
                result = llm_output.get("powerful_questions") or llm_output.get("result") or llm_output.get("question") or ""
            
            # 如果result仍然是字典，可能是返回了整个对象，尝试提取第一个字段的值
            if isinstance(result, dict):
                if part == "quiz" and "quiz" in result:
                    result = result["quiz"]
                elif part == "intro" and "personalized_intro" in result:
                    result = result["personalized_intro"]
                elif part == "body" and "interpretation" in result:
                    result = result["interpretation"]
                elif part == "question" and "powerful_questions" in result:
                    result = result["powerful_questions"]
                elif len(result) == 1:
                    # 如果只有一个字段，提取它的值
                    result = list(result.values())[0]
            
            return jsonify({"result": result, "part": part})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500

    def generate_volcengine_tts_signature(
        access_key: str, secret_key: str, method: str, host: str, path: str, params: Dict[str, Any]
    ) -> str:
        """生成火山引擎TTS API签名（根据火山引擎文档：https://www.volcengine.com/docs/6561/1668014）"""
        # 排除signature参数
        params_for_sign = {k: v for k, v in params.items() if k != "signature"}
        
        # 按参数名排序
        sorted_params = sorted(params_for_sign.items())
        query_string = "&".join([f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted_params])
        
        # 构建待签名字符串：Method + Host + Path + QueryString
        string_to_sign = f"{method}\n{host}\n{path}\n{query_string}"
        
        # 使用HMAC-SHA256签名
        signature = hmac.new(
            secret_key.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        # Base64编码
        return base64.b64encode(signature).decode('utf-8')

    async def call_volcengine_podcast_async(
        text: str,
        access_token: str,
        app_id: str,
        resource_id: str,
        encoding: str = "mp3",
    ) -> Dict[str, Any]:
        """异步调用火山引擎播客 API"""
        if not WEBSOCKETS_AVAILABLE:
            raise RuntimeError("需要安装 websockets: pip3 install websockets")
        
        ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sami/podcasttts"
        APP_KEY = "aGjiRDfUWi"  # SDK 中的固定值
        
        headers = {
            "X-Api-App-Id": app_id,
            "X-Api-App-Key": APP_KEY,
            "X-Api-Access-Key": access_token,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        
        podcast_audio = bytearray()
        audio = bytearray()
        websocket = None
        
        try:
            # 建立 WebSocket 连接
            websocket = await websockets.connect(ENDPOINT, additional_headers=headers)
            
            # 构建请求参数
            req_params = {
                "input_id": f"podcast_{int(time.time())}",
                "input_text": text,
                "action": 0,  # 0: 文本播客
                "use_head_music": False,
                "use_tail_music": False,
                "input_info": {
                    "input_url": "",
                    "return_audio_url": False,
                    "only_nlp_text": False,
                },
                "speaker_info": {"random_order": False},
                "audio_config": {
                    "format": encoding,
                    "sample_rate": 24000,
                    "speech_rate": 0,
                },
            }
            
            session_id = str(uuid.uuid4())
            
            # Start connection
            await start_connection(websocket)
            await wait_for_event(websocket, MsgType.FullServerResponse, EventType.ConnectionStarted)
            
            # Start session
            await start_session(websocket, json.dumps(req_params).encode(), session_id)
            await wait_for_event(websocket, MsgType.FullServerResponse, EventType.SessionStarted)
            
            # Finish session
            await finish_session(websocket, session_id)
            
            # 接收响应
            audio_received = False
            while True:
                msg = await receive_message(websocket)
                
                # 音频数据块
                if msg.type == MsgType.AudioOnlyServer and msg.event == EventType.PodcastRoundResponse:
                    audio.extend(msg.payload)
                    if not audio_received:
                        audio_received = True
                    print(f"收到音频块: {len(msg.payload)} 字节", flush=True)
                
                # 错误信息
                elif msg.type == MsgType.Error:
                    error_msg = msg.payload.decode("utf-8", errors="ignore")
                    # 如果是超时错误但已收到音频，继续处理
                    if "RPCTimeout" in error_msg and audio_received:
                        if audio:
                            podcast_audio.extend(audio)
                            audio.clear()
                        break
                    else:
                        raise RuntimeError(f"服务器错误: {error_msg}")
                
                elif msg.type == MsgType.FullServerResponse:
                    # 播客 round 结束
                    if msg.event == EventType.PodcastRoundEnd:
                        data = json.loads(msg.payload.decode("utf-8"))
                        if data.get("is_error"):
                            raise RuntimeError(f"播客轮次错误: {data}")
                        if audio:
                            podcast_audio.extend(audio)
                            audio.clear()
                    
                    # 播客结束
                    elif msg.event == EventType.PodcastEnd:
                        pass
                
                # 会话结束
                if msg.event == EventType.SessionFinished:
                    break
            
            # 如果还有未处理的音频
            if audio:
                podcast_audio.extend(audio)
            
            if not audio_received and not podcast_audio:
                raise RuntimeError("未收到音频数据")
            
            # 关闭连接
            try:
                await finish_connection(websocket)
                await wait_for_event(websocket, MsgType.FullServerResponse, EventType.ConnectionFinished)
            except:
                pass
            
            if podcast_audio:
                audio_base64 = base64.b64encode(bytes(podcast_audio)).decode("utf-8")
                return {
                    "audio_base64": audio_base64,
                    "format": encoding,
                    "duration": 0,
                }
            else:
                raise RuntimeError("未生成音频数据")
        
        finally:
            if websocket:
                await websocket.close()
    
    def call_volcengine_tts(
        text: str,
        voice_type: str = "BV700_streaming",
        language: str = "zh",
        speed_ratio: float = 1.0,
        volume_ratio: float = 1.0,
        pitch_ratio: float = 1.0,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        app_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """调用火山引擎播客 API生成音频（使用正确的播客 API）"""
        if not access_key:
            access_key = volcengine_tts_access_key
        if not app_id:
            app_id = volcengine_tts_app_id
        
        resource_id = volcengine_tts_resource_id
        
        if not access_key or not app_id:
            raise RuntimeError("缺少火山引擎播客配置：需要 ACCESS_KEY 和 APP_ID")
        
        if not WEBSOCKETS_AVAILABLE:
            raise RuntimeError("需要安装 websockets: pip3 install websockets")
        
        # 使用异步函数
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(
            call_volcengine_podcast_async(
                text=text,
                access_token=access_key,
                app_id=app_id,
                resource_id=resource_id,
                encoding="mp3",
            )
        )

    @app.post("/api/podcast/generate")
    def generate_podcast_endpoint():
        """生成播客音频"""
        payload = request.get_json() or {}
        text = payload.get("text", "").strip()
        voice_type = payload.get("voice_type", "BV700_streaming")
        language = payload.get("language", "zh")
        speed_ratio = float(payload.get("speed_ratio", 1.0))
        volume_ratio = float(payload.get("volume_ratio", 1.0))
        pitch_ratio = float(payload.get("pitch_ratio", 1.0))
        
        # 支持从请求中传入配置
        access_key = payload.get("access_key") or volcengine_tts_access_key
        secret_key = payload.get("secret_key") or volcengine_tts_secret_key
        app_id = payload.get("app_id") or volcengine_tts_app_id
        
        if not text:
            return jsonify({"error": "缺少文本内容"}), 400
        
        try:
            result = call_volcengine_tts(
                text=text,
                voice_type=voice_type,
                language=language,
                speed_ratio=speed_ratio,
                volume_ratio=volume_ratio,
                pitch_ratio=pitch_ratio,
                access_key=access_key,
                secret_key=secret_key,
                app_id=app_id,
            )
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.post("/api/translate")
    def translate_endpoint():
        """翻译API端点，用于测试翻译功能"""
        payload = request.get_json() or {}
        text = payload.get("text", "").strip()
        
        if not text:
            return jsonify({"error": "缺少文本内容"}), 400
        
        api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
        if not api_key:
            return jsonify({"error": "缺少 DOUBAO_API_KEY，请在环境变量或设置中配置豆包密钥。"}), 400
        
        try:
            translated = call_doubao_translate(text, api_key)
            return jsonify({"translated": translated, "result": translated})
        except Exception as exc:
            return jsonify({"error": f"翻译失败: {str(exc)}"}), 500

    @app.post("/api/translate/chapter")
    def translate_chapter_endpoint():
        """翻译单个章节的标题和内容，并生成中文概要"""
        payload = request.get_json() or {}
        title = payload.get("title", "").strip()
        content = payload.get("content", "").strip()
        
        if not title and not content:
            return jsonify({"error": "缺少标题或内容"}), 400
        
        api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
        if not api_key:
            return jsonify({"error": "缺少 DOUBAO_API_KEY，请在环境变量或设置中配置豆包密钥。"}), 400
        
        try:
            title_zh = ""
            content_zh = ""
            
            # 翻译标题
            title_translation_error = None
            if title:
                if is_chinese_text(title):
                    title_zh = title
                    print(f"标题已是中文，无需翻译", flush=True)
                else:
                    try:
                        translated_title = call_doubao_translate(title, api_key)
                        print(f"标题翻译完成，翻译后: {translated_title[:100]}", flush=True)
                        # 验证翻译结果
                        if not translated_title or translated_title.strip() == "":
                            print(f"警告: 标题翻译结果为空", flush=True)
                            title_translation_error = "翻译结果为空"
                            title_zh = ""  # 保持为空，表示翻译失败
                        elif translated_title.strip() == title.strip():
                            print(f"警告: 标题翻译结果与原文相同，可能翻译失败", flush=True)
                            title_translation_error = "翻译结果与原文相同"
                            title_zh = ""  # 保持为空，表示翻译失败
                        elif not is_chinese_text(translated_title, threshold=0.2):
                            print(f"警告: 标题翻译结果非中文，可能翻译失败", flush=True)
                            title_translation_error = "翻译结果非中文"
                            title_zh = ""
                        else:
                            title_zh = translated_title  # 只有翻译成功且结果不同时才设置
                    except Exception as exc:
                         print(f"翻译标题失败: {exc}", flush=True)
                         title_translation_error = str(exc)
                         title_zh = ""  # 翻译失败时保持为空字符串
            
            # 翻译内容（call_doubao_translate会自动处理分段翻译）
            content_translation_error = None
            if content:
                if is_chinese_text(content):
                    content_zh = content
                    print(f"内容已是中文，无需翻译", flush=True)
                else:
                    try:
                        print(f"开始翻译章节内容，长度: {len(content)}字符", flush=True)
                        # call_doubao_translate会自动检测token数量并分段翻译
                        translated = call_doubao_translate(content, api_key)
                        print(f"章节内容翻译完成，翻译后长度: {len(translated)}字符", flush=True)
                        # 验证翻译结果（去除首尾空格后比较）
                        translated_clean = translated.strip() if translated else ""
                        content_clean = content.strip() if content else ""
                        if not translated_clean:
                            print(f"警告: 翻译结果为空", flush=True)
                            content_translation_error = "翻译结果为空"
                            content_zh = ""  # 保持为空，表示翻译失败
                        elif translated_clean == content_clean:
                            print(f"警告: 翻译结果与原文相同（去除空格后），可能翻译失败", flush=True)
                            content_translation_error = "翻译结果与原文相同"
                            content_zh = ""  # 保持为空，表示翻译失败
                        elif not is_chinese_text(translated_clean, threshold=0.2):
                            print(f"警告: 翻译结果非中文，可能翻译失败", flush=True)
                            content_translation_error = "翻译结果非中文"
                            content_zh = ""
                        else:
                            content_zh = translated  # 只有翻译成功且结果不同时才设置
                    except Exception as exc:
                         print(f"翻译内容失败: {exc}", flush=True)
                         content_translation_error = str(exc)
                         content_zh = ""  # 翻译失败时保持为空字符串
            
            # 翻译完成后，基于中文内容生成概要
            summary = ""
            if title_zh and content_zh:
                try:
                    print(f"基于中文内容生成章节概要: {title_zh[:50]}...", flush=True)
                    summary = call_doubao_summary(title_zh, content_zh, api_key)
                    print(f"章节概要生成完成", flush=True)
                except Exception as exc:
                    print(f"生成概要失败: {exc}", flush=True)
                    # 概要生成失败不影响翻译结果
            
            # 构建返回结果
            result = {
                "title_zh": title_zh,
                "content_zh": content_zh,
                "summary": summary,
                "success": True
            }
            # 收集所有翻译错误
            warnings = []
            if title_translation_error:
                warnings.append(f"标题翻译失败: {title_translation_error}")
            if content_translation_error:
                warnings.append(f"内容翻译失败: {content_translation_error}")
            
            # 如果有翻译错误，添加到结果中
            if warnings:
                result["title_translation_error"] = title_translation_error
                result["content_translation_error"] = content_translation_error
                result["warning"] = "; ".join(warnings)
            
            # 验证：如果标题或内容翻译失败，不应该生成概要
            if summary and (not title_zh or not content_zh):
                print(f"警告: 概要已生成，但标题或内容翻译失败，这不应该发生", flush=True)
                result["warning"] = (result.get("warning", "") + "; 警告: 概要生成时翻译状态异常").strip("; ")
            
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": f"翻译失败: {str(exc)}"}), 500

    def is_chinese_text(text: str, threshold: float = 0.3) -> bool:
        """检测文本是否为中文（至少threshold比例的中文字符）"""
        if not text or not text.strip():
            return False
        # 移除空白字符
        cleaned = "".join(ch for ch in text if not ch.isspace())
        if not cleaned:
            return False
        chinese_count = 0
        for ch in cleaned:
            # 检查是否是中文字符（包括CJK统一汉字、扩展A、扩展B等）
            if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf" or "\u20000" <= ch <= "\u2a6df":
                chinese_count += 1
        return (chinese_count / len(cleaned)) >= threshold if len(cleaned) > 0 else False

    def estimate_tokens(text: str) -> int:
        """估算文本的token数量，区分ASCII和非ASCII字符"""
        if not text:
            return 0
        ascii_chars = sum(1 for ch in text if ord(ch) < 128)
        non_ascii_chars = len(text) - ascii_chars
        # ASCII字符假设平均4字符≈1个token，非ASCII（中文）假设1字符≈1个token
        estimated = (ascii_chars / 4.0) + non_ascii_chars
        return int(estimated) + 1
    
    def call_doubao_translate(text: str, api_key: str) -> str:
        """使用豆包翻译模型将文本翻译成中文
        
        模型：Doubao-Seed-Translation
        - 支持28种语言互译
        - 最大输入Token长度：1K tokens
        - 输出长度：最大3K tokens
        - 上下文窗口：4K tokens
        """
        # 尝试翻译模型，优先使用可用的模型
        # 注意：doubao-seed-translation 可能不存在或需要特殊权限
        # 测试发现 doubao-seed-1-6 可用且工作正常
        candidate_models = [
            "deepseek",  # DeepSeek R1翻译
            "doubao-seed-translation-250915",  # 最新翻译模型，优先Responses API
            "doubao-seed-1-6",  # 通用模型，已验证可用
            "doubao-seed-translation",  # 如果用户有权限，尝试专用翻译模型
            "Doubao-Seed-Translation",  # 备用格式
        ]

        MAX_INPUT_TOKENS = 3500
        estimated_tokens = estimate_tokens(text)
        
        # 如果超过限制，需要分段翻译
        if estimated_tokens > MAX_INPUT_TOKENS:
            print(f"文本长度超过限制 (估算{estimated_tokens} tokens > {MAX_INPUT_TOKENS} tokens)，将分段翻译", flush=True)
            return call_doubao_translate_chunked(text, api_key)
        
        # 单次翻译
        system_prompt = "你是一个专业的翻译助手，请将用户提供的文本准确翻译成中文。保持原文的格式和结构，只翻译内容。"
        user_prompt = text

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        
        configured_base = (
            os.environ.get("DOUBAO_API_BASE")
            or load_setting("doubao_api_base", "")
        ).strip()
        candidate_endpoints = []
        if configured_base:
            configured_base = configured_base.rstrip("/")
            if configured_base.endswith("/chat/completions"):
                candidate_endpoints.append(configured_base)
            else:
                candidate_endpoints.append(f"{configured_base}/chat/completions")
        candidate_endpoints.append("https://ark.cn-beijing.volces.com/api/v3/chat/completions")
        
        last_error: Optional[Exception] = None
        last_status_code: Optional[int] = None
        last_error_message: Optional[str] = None
        response_data: Optional[Dict[str, Any]] = None

        # 尝试不同的模型和端点组合
        for model in candidate_models:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            
            for endpoint in candidate_endpoints:
                try:
                    print(f"尝试翻译: 模型={model}, 端点={endpoint}, 文本长度={len(text)}字符 (估算{estimated_tokens} tokens)", flush=True)
                    response = requests.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                        timeout=600,  # 10分钟超时，支持长文本翻译
                    )
                    last_status_code = response.status_code
                    
                    if response.status_code == 200:
                        response_data = response.json()
                        print(f"翻译成功: 模型={model}, 端点={endpoint}", flush=True)
                        break
                    else:
                        # 记录非200状态码的错误信息
                        try:
                            error_body = response.json()
                            last_error_message = error_body.get("error", {}).get("message", response.text[:200])
                        except:
                            last_error_message = response.text[:200]
                        print(f"翻译失败: 模型={model}, 状态码={response.status_code}, 错误={last_error_message}", flush=True)
                        # 如果是404（模型不存在），立即尝试下一个模型，不要等待
                        if response.status_code == 404:
                            print(f"模型 {model} 不存在或无权限，立即尝试下一个模型", flush=True)
                            continue
                except requests.exceptions.RequestException as exc:
                    last_error = exc
                    print(f"请求异常: 模型={model}, 错误={exc}", flush=True)
                    continue
                except Exception as exc:
                    last_error = exc
                    print(f"其他异常: 模型={model}, 错误={exc}", flush=True)
                    continue
            
            if response_data:
                break

        if not response_data:
            error_msg = f"豆包翻译接口请求失败：所有模型名称都尝试失败"
            if last_status_code:
                error_msg += f" (HTTP {last_status_code})"
            if last_error_message:
                error_msg += f": {last_error_message}"
            elif last_error:
                error_msg += f": {last_error}"
            raise Exception(error_msg)

        try:
            choices = response_data.get("choices", [])
            if choices and len(choices) > 0:
                message = choices[0].get("message", {})
                content = message.get("content", "")
                if isinstance(content, list):
                    # 如果content是数组格式
                    text_content = ""
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_content += item.get("text", "")
                    content = text_content
                return content.strip()
            else:
                raise Exception("豆包翻译接口返回格式异常：未找到choices")
        except Exception as exc:
            raise Exception(f"解析豆包翻译响应失败：{exc}")
    
    def call_doubao_translate_chunked(text: str, api_key: str) -> str:
        """分段翻译长文本（超过1K tokens）
        
        将文本分成多个段落，每段不超过1K tokens，分别翻译后合并。
        """
        MAX_INPUT_TOKENS = 3500
        # 保守估计：1 token ≈ 1个非ASCII字符，≈4个ASCII字符
        # 为了安全，将单个chunk限制在3000字符以内
        MAX_CHARS_PER_CHUNK = 3000
        
        # 按段落分割（优先保持段落完整性）
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # 如果当前段落加上新段落不超过限制，合并
            if len(current_chunk) + len(para) + 2 <= MAX_CHARS_PER_CHUNK:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
            else:
                # 如果当前chunk不为空，先保存
                if current_chunk:
                    chunks.append(current_chunk)
                
                # 如果单个段落就超过限制，需要进一步分割
                if len(para) > MAX_CHARS_PER_CHUNK:
                    # 按句子分割
                    import re
                    sentences = re.split(r'([.!?。！？]\s+)', para)
                    current_sentence = ""
                    for i in range(0, len(sentences), 2):
                        sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else "")
                        if len(current_sentence) + len(sentence) <= MAX_CHARS_PER_CHUNK:
                            current_sentence += sentence
                        else:
                            if current_sentence:
                                chunks.append(current_sentence)
                            # 如果单个句子还是太长，强制分割
                            if len(sentence) > MAX_CHARS_PER_CHUNK:
                                for j in range(0, len(sentence), MAX_CHARS_PER_CHUNK):
                                    chunks.append(sentence[j:j+MAX_CHARS_PER_CHUNK])
                                current_sentence = ""
                            else:
                                current_sentence = sentence
                    if current_sentence:
                        current_chunk = current_sentence
                else:
                    current_chunk = para
        
        # 添加最后一个chunk
        if current_chunk:
            chunks.append(current_chunk)
        
        print(f"将文本分成{len(chunks)}段进行翻译，总长度={len(text)}字符，单段上限{MAX_CHARS_PER_CHUNK}字符", flush=True)
        if len(chunks) > 20:
            print("警告：段落数量较多，整体翻译时间可能较长", flush=True)
        
        # 内部函数：单次翻译（不进行分段检查，避免递归）
        def translate_single_chunk(chunk_text: str) -> str:
            """翻译单个chunk（不进行分段检查）"""
            # 尝试翻译模型，优先使用可用的模型
            candidate_models = [
                "deepseek",  # DeepSeek R1
                "doubao-seed-translation-250915",  # 最新翻译模型，优先Responses API
                "doubao-seed-1-6",  # 通用模型，已验证可用
                "doubao-seed-translation",  # 如果用户有权限，尝试专用翻译模型
                "Doubao-Seed-Translation",  # 备用格式
            ]
            deepseek_translate = call_deepseek_translate
            responses_translate = call_doubao_translate_responses
            chunk_limit = chunk_token_limit
            responses_limit = responses_chunk_limit
            system_prompt = "你是一个专业的翻译助手，请将用户提供的文本准确翻译成中文。保持原文的格式和结构，只翻译内容。"
            user_prompt = chunk_text

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            
            configured_base = (
                os.environ.get("DOUBAO_API_BASE")
                or load_setting("doubao_api_base", "")
            ).strip()
            candidate_endpoints = []
            if configured_base:
                configured_base = configured_base.rstrip("/")
                if configured_base.endswith("/chat/completions"):
                    candidate_endpoints.append(configured_base)
                else:
                    candidate_endpoints.append(f"{configured_base}/chat/completions")
            candidate_endpoints.append("https://ark.cn-beijing.volces.com/api/v3/chat/completions")
            
            # 尝试不同的模型和端点组合
            for model in candidate_models:
                if model == "deepseek":
                    try:
                        if estimate_tokens(chunk_text) > chunk_limit:
                            print(
                                f"Chunk文本较长(≈{estimate_tokens(chunk_text)} tokens)，DeepSeek继续分段",
                                flush=True,
                            )
                        translated = deepseek_translate(chunk_text, target_language="zh")
                        return translated
                    except Exception as exc:
                        print(f"DeepSeek翻译chunk失败: {exc}", flush=True)
                        continue
                if model == "doubao-seed-translation-250915":
                    if len(chunk_text) > 0 and estimate_tokens(chunk_text) > responses_limit:
                        print(
                            f"Chunk文本较长(≈{estimate_tokens(chunk_text)} tokens)，继续保持分段",
                            flush=True,
                        )
                    try:
                        print(
                            "使用Responses API翻译chunk (doubao-seed-translation-250915)",
                            flush=True,
                        )
                        source_lang = "zh" if is_chinese_text(chunk_text) else None
                        return call_doubao_translate_responses(
                            chunk_text,
                            api_key,
                            target_language="zh",
                            source_language=source_lang,
                        )
                    except Exception as exc:
                        print(f"Responses翻译chunk失败: {exc}", flush=True)
                        continue

            raise Exception("所有模型和端点都尝试失败")
        
        # 逐段翻译
        translated_chunks = []
        for i, chunk in enumerate(chunks):
            print(f"翻译第 {i+1}/{len(chunks)} 段 (长度: {len(chunk)}字符)", flush=True)
            try:
                translated_chunk = translate_single_chunk(chunk)
                translated_chunks.append(translated_chunk)
            except Exception as exc:
                print(f"第 {i+1} 段翻译失败: {exc}，使用原文", flush=True)
                translated_chunks.append(chunk)  # 翻译失败时使用原文
        
        # 合并翻译结果
        result = "\n\n".join(translated_chunks)
        print(f"分段翻译完成，总长度={len(result)}字符", flush=True)
        return result

    def translate_text_if_needed(text: str, api_key: str) -> str:
        """如果文本不是中文，则翻译成中文"""
        if not text or not text.strip():
            return text
        if is_chinese_text(text):
            return text
        try:
            print(f"检测到非中文文本，开始翻译: {text[:50]}...", flush=True)
            translated = call_doubao_translate(text, api_key)
            print(f"翻译完成: {translated[:50]}...", flush=True)
            return translated
        except Exception as exc:
            print(f"翻译失败: {exc}，使用原文", flush=True)
            return text

    def call_doubao_clean_toc(raw_toc: List[Tuple[int, str, int]], api_key: str) -> List[Tuple[int, str, int]]:
        """使用豆包大模型清洗目录"""
        model = os.environ.get("DOUBAO_MODEL") or load_setting("doubao_model", "doubao-seed-1-6")
        configured_base = (
            os.environ.get("DOUBAO_API_BASE")
            or load_setting("doubao_api_base", "")
        ).strip()
        candidate_endpoints = []
        if configured_base:
            configured_base = configured_base.rstrip("/")
            if configured_base.endswith("/chat/completions"):
                candidate_endpoints.append(configured_base)
            else:
                candidate_endpoints.append(f"{configured_base}/chat/completions")
        candidate_endpoints.append("https://ark.cn-beijing.volces.com/api/v3/chat/completions")

        # 构建原始目录文本（包含页码信息，帮助大模型理解结构）
        toc_text = "\n".join([f"{'  ' * (level - 1)}{title} (第{page}页)" for level, title, page in raw_toc])

        system_prompt = """你现在要处理一份书籍的《原始目录》，输出《清洗后的目录》。

请基于以下原则执行：

① 清洗后的目录是什么？
它只保留书的"正文主体结构"，也就是作者正式展开内容时使用的结构。
正文主体结构通常以"部分 / 篇 / 卷 / 章 / 节"等形式出现，用于组织和表达知识内容。

② 什么必须删除？
请删除所有不属于内容主体结构的条目，包括：
- 与出版相关的内容（出版说明、版本说明、献词、致谢、推荐序、后记等）
- 与教学或辅助功能相关的内容（小结、练习、思考题、复习提要等）
- 各种附属内容（附录、索引、参考文献、作者/译者简介、附加阅读材料）
- 目录页本身或其他不是内容展开的结构

它们不属于知识主体结构，因此不应出现在清洗后的目录中。

③ 你的判断方法
请不依赖正文内容本身，只根据目录条目的名称和出版惯例来判断。
简单判断：
- 凡是用于表达知识、展开故事或组织论述的条目 → 保留；
- 凡是用于包装、说明、辅助、附加的条目 → 删除。

④ 输出方式
只输出清洗后的目录，每行一个条目，保持原有的缩进层级。
如果原始目录中包含页码信息（如"第X页"），请在输出时保留这些页码信息。
不要解释过程，不要添加任何说明文字。"""

        user_prompt = f"原始目录：\n{toc_text}\n\n请输出清洗后的目录："

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        payloads = [
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
                ],
            },
        ]

        last_error: Optional[Exception] = None
        response_data: Optional[Dict[str, Any]] = None

        for endpoint in candidate_endpoints:
            for payload_item in payloads:
                if last_error:
                    time.sleep(0.3)
                try:
                    response = requests.post(
                        endpoint,
                        headers=headers,
                        json=payload_item,
                        timeout=180,
                    )
                    if response.status_code == 404:
                        last_error = RuntimeError(f"豆包接口 404：{endpoint}")
                        continue
                    response.raise_for_status()
                    response_data = response.json()
                    break
                except requests.Timeout as exc:
                    last_error = RuntimeError(f"豆包接口请求超时（超过180秒）：{endpoint}")
                    print(f"目录清洗超时: {exc}", flush=True)
                    continue
                except requests.RequestException as exc:
                    last_error = exc
                    print(f"目录清洗请求异常: {exc}", flush=True)
                    continue
            if response_data is not None:
                break

        if response_data is None:
            error_msg = f"豆包接口请求失败：{last_error}"
            print(f"目录清洗失败: {error_msg}", flush=True)
            raise RuntimeError(error_msg)

        data = response_data
        if isinstance(data, dict) and data.get("code") not in (0, None):
            raise RuntimeError(f"豆包接口返回错误：{data.get('msg', '未知错误')}")

        choices = None
        if isinstance(data, dict):
            if "choices" in data:
                choices = data["choices"]
            else:
                choices = data.get("data", {}).get("choices")
        if not choices:
            raise RuntimeError("豆包接口未返回任何结果。")

        message = choices[0].get("message", {})
        cleaned_text = ""
        if isinstance(message, dict):
            content_items = message.get("content")
            if isinstance(content_items, list):
                texts = [
                    item.get("text", "")
                    for item in content_items
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                cleaned_text = "".join(texts).strip()
            else:
                cleaned_text = str(message.get("content", "")).strip()
        if not cleaned_text:
            raise RuntimeError("豆包接口返回的清洗结果为空。")

        # 解析清洗后的目录，匹配回原始目录的层级和页码
        cleaned_lines = [line for line in cleaned_text.split("\n") if line.strip()]
        cleaned_toc: List[Tuple[int, str, int]] = []
        
        # 创建原始目录的标题映射（用于匹配页码）
        title_to_page = {title.strip(): page for _, title, page in raw_toc}
        title_to_level = {title.strip(): level for level, title, _ in raw_toc}
        # 创建标题到完整条目的映射（用于更好的匹配）
        title_to_entry = {(level, title.strip()): (level, title.strip(), page) for level, title, page in raw_toc}
        
        # 页码提取正则
        page_pattern = re.compile(r"第(\d+)页")
        
        for line in cleaned_lines:
            # 计算缩进层级
            indent = len(line) - len(line.lstrip())
            level = (indent // 2) + 1 if indent > 0 else 1
            original_line = line.strip()
            
            if not original_line:
                continue
            
            # 尝试从清洗后的目录中提取页码（如果大模型保留了）
            page = None
            title = original_line
            page_match = page_pattern.search(original_line)
            if page_match:
                try:
                    page = int(page_match.group(1))
                    # 移除页码信息，只保留标题
                    title = page_pattern.sub("", original_line).strip()
                    title = title.replace("()", "").strip()
                except ValueError:
                    pass
            
            # 如果提取不到页码，尝试通过标题匹配
            if page is None:
                # 先尝试精确匹配
                page = title_to_page.get(title, None)
                orig_level = title_to_level.get(title, level)
                
                # 如果精确匹配失败，尝试模糊匹配
                if page is None:
                    best_match_entry = None
                    best_score = 0
                    for (orig_level_key, orig_title), (orig_l, orig_t, orig_p) in title_to_entry.items():
                        # 计算相似度
                        if title == orig_title:
                            # 完全匹配
                            best_match_entry = (orig_l, orig_t, orig_p)
                            best_score = 1.0
                            break
                        elif title in orig_title or orig_title in title:
                            # 包含关系
                            score = min(len(title), len(orig_title)) / max(len(title), len(orig_title))
                            if score > best_score:
                                best_score = score
                                best_match_entry = (orig_l, orig_t, orig_p)
                        else:
                            # 计算编辑距离相似度（简化版）
                            common_chars = len(set(title) & set(orig_title))
                            total_chars = len(set(title) | set(orig_title))
                            if total_chars > 0:
                                score = common_chars / total_chars
                                if score > best_score and score > 0.5:  # 相似度阈值
                                    best_score = score
                                    best_match_entry = (orig_l, orig_t, orig_p)
                    
                    if best_match_entry:
                        level, title, page = best_match_entry
                    else:
                        # 如果完全匹配不到，使用默认值
                        page = 1
            
            cleaned_toc.append((level, title, page))
        
        return cleaned_toc if cleaned_toc else raw_toc

    def call_doubao_summary(title: str, content: str, api_key: str) -> str:
        model = os.environ.get("DOUBAO_MODEL") or load_setting("doubao_model", "doubao-seed-1-6")
        configured_base = (
            os.environ.get("DOUBAO_API_BASE")
            or load_setting("doubao_api_base", "")
        ).strip()
        candidate_endpoints = []
        if configured_base:
            configured_base = configured_base.rstrip("/")
            if configured_base.endswith("/chat/completions"):
                candidate_endpoints.append(configured_base)
            else:
                candidate_endpoints.append(f"{configured_base}/chat/completions")
        candidate_endpoints.append("https://ark.cn-beijing.volces.com/api/v3/chat/completions")

        system_prompt = (
            "你是一个资深的中文图书编辑，擅长撰写结构化的章节概述。"
            "请输出一个简明扼要的段落，总结该章节讨论的主题、主要观点与关键结论。"
            "禁止编造内容，保持客观准确。输出使用中文。"
        )
        user_prompt = f"章节标题：{title}\n章节全文：{content}"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        
        # 尝试两种格式：简单字符串格式和数组格式
        payloads = [
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
                ],
            },
        ]

        last_error: Optional[Exception] = None
        response_data: Optional[Dict[str, Any]] = None

        for endpoint in candidate_endpoints:
            for payload in payloads:
                if last_error:
                    time.sleep(0.3)
                try:
                    response = requests.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                        timeout=90,
                    )
                    if response.status_code == 404:
                        last_error = RuntimeError(f"豆包接口 404：{endpoint}")
                        continue
                    response.raise_for_status()
                    response_data = response.json()
                    break
                except requests.RequestException as exc:
                    last_error = exc
                    continue
            if response_data is not None:
                break

        if response_data is None:
            raise RuntimeError(f"豆包接口请求失败：{last_error}")

        data = response_data
        if isinstance(data, dict) and data.get("code") not in (0, None):
            raise RuntimeError(f"豆包接口返回错误：{data.get('msg', '未知错误')}")

        choices = None
        if isinstance(data, dict):
            if "choices" in data:
                choices = data["choices"]
            else:
                choices = data.get("data", {}).get("choices")
        if not choices:
            raise RuntimeError("豆包接口未返回任何结果。")

        message = choices[0].get("message", {})
        summary = ""
        if isinstance(message, dict):
            content_items = message.get("content")
            if isinstance(content_items, list):
                texts = [
                    item.get("text", "")
                    for item in content_items
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                summary = "".join(texts).strip()
            else:
                summary = str(message.get("content", "")).strip()
        if not summary:
            raise RuntimeError("豆包接口返回的概述为空。")
        return summary

    @app.post("/api/parse/ingest")
    def parse_ingest_endpoint():
        payload = request.get_json() or {}
        entries = payload.get("entries")
        if not isinstance(entries, list) or not entries:
            return jsonify({"error": "缺少有效的章节列表"}), 400

        api_key_override = (payload.get("doubao_api_key") or "").strip()
        if api_key_override:
            os.environ["DOUBAO_API_KEY"] = api_key_override
            try:
                store_setting("doubao_api_key", api_key_override)
            except SQLAlchemyError:
                pass

        api_base_override = (payload.get("doubao_api_base") or "").strip()
        if api_base_override:
            os.environ["DOUBAO_API_BASE"] = api_base_override
            try:
                store_setting("doubao_api_base", api_base_override)
            except SQLAlchemyError:
                pass

        model_override = (payload.get("doubao_model") or "").strip()
        if model_override:
            os.environ["DOUBAO_MODEL"] = model_override
            try:
                store_setting("doubao_model", model_override)
            except SQLAlchemyError:
                pass

        api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
        if not api_key:
            return jsonify({"error": "缺少 DOUBAO_API_KEY，请在环境变量或设置中配置豆包密钥。"}), 400

        # 创建书籍记录
        filename = payload.get("filename", "未知文件.pdf")
        total_word_count = sum(int(entry.get("word_count") or len(entry.get("content") or "")) for entry in entries)
        book_id = store_book(filename, len(entries), total_word_count)

        results: List[Dict[str, Any]] = []
        try:
            for entry in entries:
                processed = process_chapter_entry(entry, api_key, book_id=book_id)
                results.append(processed)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500

        message = "章节概述已完成入库。"
        return jsonify({"entries": results, "message": message})

    @app.get("/api/admin/books")
    def list_books():
        """查询所有已入库的书籍"""
        try:
            page = int(request.args.get("page", 1))
            per_page = int(request.args.get("per_page", 50))
            offset = (page - 1) * per_page

            with engine.begin() as conn:
                # 获取总数
                total = conn.execute(
                    text("SELECT COUNT(*) FROM books")
                ).scalar_one()

                # 获取分页数据
                results = conn.execute(
                    text(
                        """
                        SELECT id, filename, chapter_count, total_word_count, created_at
                        FROM books
                        ORDER BY created_at DESC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {"limit": per_page, "offset": offset},
                ).fetchall()

                books = [
                    {
                        "id": row[0],
                        "filename": row[1],
                        "chapter_count": row[2],
                        "total_word_count": row[3],
                        "created_at": row[4],
                    }
                    for row in results
                ]

            return jsonify(
                {
                    "books": books,
                    "total": total,
                    "page": page,
                    "per_page": per_page,
                    "total_pages": (total + per_page - 1) // per_page,
                }
            )
        except SQLAlchemyError as exc:
            return jsonify({"error": f"查询失败：{exc}"}), 500

    @app.get("/api/admin/books/<int:book_id>")
    def get_book_details(book_id: int):
        """获取书籍详情（包括章节列表）"""
        try:
            with engine.begin() as conn:
                # 查询书籍信息
                book_row = conn.execute(
                    text(
                        """
                        SELECT id, filename, chapter_count, total_word_count, created_at
                        FROM books
                        WHERE id = :id
                        """
                    ),
                    {"id": book_id},
                ).fetchone()
                if not book_row:
                    return jsonify({"error": "书籍不存在"}), 404

                book = {
                    "id": book_row[0],
                    "filename": book_row[1],
                    "chapter_count": book_row[2],
                    "total_word_count": book_row[3],
                    "created_at": book_row[4],
                }

                # 查询章节列表
                chapters_rows = conn.execute(
                    text(
                        """
                        SELECT id, chapter_title, chapter_title_zh, chapter_content, chapter_content_zh,
                               summary, word_count, created_at
                        FROM chapter_summaries
                        WHERE book_id = :book_id
                        ORDER BY id ASC
                        """
                    ),
                    {"book_id": book_id},
                ).fetchall()

                chapters = [
                    {
                        "id": row[0],
                        "title": row[1],
                        "title_zh": row[2] or row[1],
                        "content": row[3],
                        "content_zh": row[4] or row[3],
                        "summary": row[5],
                        "word_count": row[6],
                        "created_at": row[7],
                    }
                    for row in chapters_rows
                ]

            return jsonify({"book": book, "chapters": chapters})
        except SQLAlchemyError as exc:
            return jsonify({"error": f"查询失败：{exc}"}), 500

    @app.delete("/api/admin/books/<int:book_id>")
    def delete_book(book_id: int):
        """删除指定书籍（会级联删除所有章节）"""
        try:
            with engine.begin() as conn:
                # 先查询书籍信息
                book = conn.execute(
                    text("SELECT filename FROM books WHERE id = :id"),
                    {"id": book_id},
                ).scalar_one_or_none()
                if not book:
                    return jsonify({"error": "书籍不存在"}), 404

                # 获取章节数量
                chapter_count = conn.execute(
                    text("SELECT COUNT(*) FROM chapter_summaries WHERE book_id = :id"),
                    {"id": book_id},
                ).scalar_one()

                # 删除书籍（会级联删除章节）
                result = conn.execute(
                    text("DELETE FROM books WHERE id = :id"),
                    {"id": book_id},
                )
            return jsonify(
                {
                    "status": "ok",
                    "message": f"已删除书籍《{book}》及其 {chapter_count} 个章节",
                }
            )
        except SQLAlchemyError as exc:
            return jsonify({"error": f"删除失败：{exc}"}), 500

    @app.post("/api/admin/books/batch_delete")
    def batch_delete_books():
        """批量删除书籍"""
        payload = request.get_json() or {}
        ids = payload.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "缺少 ids 字段或 ids 为空"}), 400

        # 验证所有ID都是整数
        try:
            ids = [int(id_val) for id_val in ids]
        except (ValueError, TypeError):
            return jsonify({"error": "ids 必须都是整数"}), 400

        try:
            with engine.begin() as conn:
                # 获取要删除的书籍信息
                books = conn.execute(
                    text(
                        """
                        SELECT id, filename FROM books WHERE id IN ({})
                        """.format(",".join([f":id{i}" for i in range(len(ids))]))
                    ),
                    {f"id{i}": id_val for i, id_val in enumerate(ids)},
                ).fetchall()

                # 获取总章节数
                total_chapters = conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM chapter_summaries 
                        WHERE book_id IN ({})
                        """.format(",".join([f":id{i}" for i in range(len(ids))]))
                    ),
                    {f"id{i}": id_val for i, id_val in enumerate(ids)},
                ).scalar_one()

                # 删除书籍（会级联删除章节）
                placeholders = ",".join([f":id{i}" for i in range(len(ids))])
                params = {f"id{i}": id_val for i, id_val in enumerate(ids)}
                result = conn.execute(
                    text(f"DELETE FROM books WHERE id IN ({placeholders})"),
                    params,
                )
            return jsonify(
                {
                    "status": "ok",
                    "message": f"已删除 {result.rowcount} 本书籍及其 {total_chapters} 个章节",
                }
            )
        except SQLAlchemyError as exc:
            return jsonify({"error": f"批量删除失败：{exc}"}), 500

    def call_doubao_translate_responses(
        text: str,
        api_key: str,
        target_language: str = "zh",
        source_language: Optional[str] = None,
    ) -> str:
        """调用Responses API进行翻译"""
        if not text:
            return ""

        # 基础URL处理
        configured_base = (
            os.environ.get("DOUBAO_API_BASE")
            or load_setting("doubao_api_base", "")
        ).strip()
        if configured_base:
            base_url = configured_base.rstrip("/")
            if base_url.endswith("/responses"):
                endpoint = base_url
            elif base_url.endswith("/api/v3"):
                endpoint = f"{base_url}/responses"
            else:
                endpoint = f"{base_url}/api/v3/responses"
        else:
            endpoint = "https://ark.cn-beijing.volces.com/api/v3/responses"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        translation_options: Dict[str, Any] = {"target_language": target_language}
        if source_language is None:
            # 简单推测源语言：如果主要是中文，则标记为中文，否则默认英文
            if is_chinese_text(text):
                source_language = "zh"
            else:
                ascii_chars = sum(1 for ch in text if ord(ch) < 128)
                ratio = ascii_chars / max(len(text), 1)
                source_language = "en" if ratio > 0.6 else "auto"
        translation_options["source_language"] = source_language

        payload = {
            "model": "doubao-seed-translation-250915",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": text,
                            "translation_options": translation_options,
                        }
                    ],
                }
            ],
        }

        print(
            f"调用Responses翻译API: endpoint={endpoint}, target_language={target_language}",
            flush=True,
        )

        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=600)
            if response.status_code != 200:
                try:
                    error_body = response.json()
                    error_msg = (
                        error_body.get("error", {}).get("message")
                        or error_body.get("message")
                        or json.dumps(error_body, ensure_ascii=False)[:200]
                    )
                except Exception:
                    error_msg = response.text[:200]
                raise Exception(
                    f"Responses翻译接口失败 (HTTP {response.status_code}): {error_msg}"
                )

            data = response.json()
            # 解析responses返回结构
            texts: List[str] = []

            if isinstance(data, dict):
                if "output_text" in data:
                    value = data.get("output_text")
                    if isinstance(value, list):
                        texts.extend([str(item) for item in value])
                    else:
                        texts.append(str(value))
                if "output" in data and isinstance(data["output"], list):
                    for message in data["output"]:
                        if not isinstance(message, dict):
                            continue
                        content_list = message.get("content")
                        if isinstance(content_list, list):
                            for content_item in content_list:
                                if (
                                    isinstance(content_item, dict)
                                    and content_item.get("type") in {"output_text", "text"}
                                ):
                                    texts.append(str(content_item.get("text", "")))
                elif "data" in data and isinstance(data["data"], list):
                    for item in data["data"]:
                        if not isinstance(item, dict):
                            continue
                        text_val = item.get("text")
                        if text_val:
                            texts.append(str(text_val))
                        elif "content" in item and isinstance(item["content"], list):
                            for content_item in item["content"]:
                                if (
                                    isinstance(content_item, dict)
                                    and content_item.get("type") == "output_text"
                                ):
                                    texts.append(str(content_item.get("text", "")))

            translated = "".join(texts).strip()
            if not translated:
                # 尝试进一步解析
                print(
                    f"Responses原始返回: {json.dumps(data, ensure_ascii=False)[:500]}",
                    flush=True,
                )
                raise Exception("Responses翻译接口返回内容为空或无法解析")
            return translated
        except Exception as exc:
            raise Exception(f"Responses翻译接口调用失败: {exc}")

    def call_deepseek_translate(
        text: str,
        target_language: str = "zh",
        source_language: Optional[str] = None,
        timeout: int = 600,
    ) -> str:
        """DeepSeek翻译"""
        api_key = deepseek_api_key
        endpoint = f"{deepseek_base_url.rstrip('/')}/chat/completions"

        system_prompt = "你是一个专业的翻译助手，请将用户提供的文本准确翻译成中文。保持原文的格式和结构，只翻译内容。"
        user_prompt = text

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        payload = {
            "model": "deepseek-reasoner",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            raw_content = data["choices"][0]["message"]["content"]
            return _strip_deepseek_reasoning(raw_content)
        except requests.exceptions.RequestException as exc:
            raise Exception(f"DeepSeek翻译请求失败: {exc}")

    def call_deepseek_translate_chunked(
        text: str,
        target_language: str = "zh",
        timeout: int = 600,
    ) -> str:
        """DeepSeek分段翻译"""
        MAX_CHARS_PER_CHUNK = 2800

        candidate_models = [
            "deepseek",  # DeepSeek R1翻译
            "doubao-seed-translation-250915",  # 最新翻译模型，优先Responses API
            "doubao-seed-1-6",  # 通用模型，已验证可用
            "doubao-seed-translation",  # 如果用户有权限，尝试专用翻译模型
            "Doubao-Seed-Translation",  # 备用格式
        ]

        system_prompt = "你是一个专业的翻译助手，请将用户提供的文本准确翻译成中文。保持原文的格式和结构，只翻译内容。"
        user_prompt = text

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {deepseek_api_key}",
        }

        payload = {
            "model": "deepseek-reasoner",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }

        try:
            response = requests.post(
                f"{deepseek_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            raw_content = data["choices"][0]["message"]["content"]
            return _strip_deepseek_reasoning(raw_content)
        except requests.exceptions.RequestException as exc:
            raise Exception(f"DeepSeek分段翻译请求失败: {exc}")

    @app.post("/api/parse/ingest/start")
    def parse_ingest_start_endpoint():
        payload = request.get_json() or {}
        filename = payload.get("filename") or "未知文件.pdf"
        try:
            chapter_count = int(payload.get("chapter_count") or 0)
            total_word_count = int(payload.get("total_word_count") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "chapter_count 或 total_word_count 非法"}), 400

        if chapter_count <= 0:
            return jsonify({"error": "chapter_count 必须大于 0"}), 400

        book_id = store_book(filename, chapter_count, total_word_count)
        return jsonify({"book_id": book_id})

    @app.post("/api/parse/ingest/chapter")
    def parse_ingest_chapter_endpoint():
        payload = request.get_json() or {}
        entry = payload.get("entry")
        if not isinstance(entry, dict):
            return jsonify({"error": "缺少有效的 entry"}), 400

        book_id = payload.get("book_id")
        try:
            book_id_int = int(book_id) if book_id is not None else None
        except (TypeError, ValueError):
            return jsonify({"error": "book_id 非法"}), 400

        api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
        if not api_key:
            return jsonify({"error": "缺少 DOUBAO_API_KEY，请在环境变量或设置中配置豆包密钥。"}), 400

        try:
            processed = process_chapter_entry(entry, api_key, book_id=book_id_int)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500

        response_payload: Dict[str, Any] = {"entry": processed}
        if "index" in payload:
            response_payload["index"] = payload["index"]
        return jsonify(response_payload)

    def calculate_word_count(text: str) -> int:
        """计算字数（去除所有空格）"""
        if not text:
            return 0
        normalized = "".join(ch for ch in text if not ch.isspace())
        return len(normalized)

    def split_into_sentences(text: str) -> List[str]:
        """将文本分割成句子"""
        if not text:
            return []
        # 使用正则表达式分割句子（句号、问号、感叹号等）
        # 保留分隔符
        import re
        sentence_endings = re.compile(r'([。！？.!?；;]+\s*)')
        parts = sentence_endings.split(text)
        sentences = []
        current = ""
        for part in parts:
            if sentence_endings.match(part):
                current += part
                if current.strip():
                    sentences.append(current.strip())
                current = ""
            else:
                current += part
        if current.strip():
            sentences.append(current.strip())
        return [s for s in sentences if s]

    def calculate_sentence_similarity(sent1: str, sent2: str) -> float:
        """计算两个句子的相似度（使用词重叠方法）"""
        if not sent1 or not sent2:
            return 0.0
        
        # 归一化：转小写、去除标点
        def normalize_text(text: str) -> set:
            # 保留中文字符、数字、字母
            normalized = re.sub(r'[^\u4e00-\u9fff\w\s]', '', text.lower())
            # 分词：中文字符逐个，英文单词按空格
            words = set()
            for char in normalized:
                if '\u4e00' <= char <= '\u9fff':
                    words.add(char)
            # 英文单词
            for word in normalized.split():
                if word:
                    words.add(word)
            return words
        
        words1 = normalize_text(sent1)
        words2 = normalize_text(sent2)
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        if union == 0:
            return 0.0
        
        return intersection / union  # Jaccard相似度

    def semantic_segmentation(text: str, similarity_threshold: float = 0.3) -> List[str]:
        """使用TextTiling风格的语义分割，将文本分割成语义片段"""
        sentences = split_into_sentences(text)
        if len(sentences) <= 1:
            return [text] if text else []
        
        # 计算相邻句子之间的相似度
        similarities = []
        for i in range(len(sentences) - 1):
            sim = calculate_sentence_similarity(sentences[i], sentences[i + 1])
            similarities.append(sim)
        
        if not similarities:
            return [text]
        
        # 找到分割点：相似度低于阈值的地方，或相似度明显下降的地方
        # 使用滑动窗口检测相似度下降
        window_size = min(3, len(similarities))
        if window_size < 2:
            return [text]
        
        # 计算局部平均相似度
        local_means = []
        for i in range(len(similarities)):
            start = max(0, i - window_size // 2)
            end = min(len(similarities), i + window_size // 2 + 1)
            local_mean = sum(similarities[start:end]) / (end - start)
            local_means.append(local_mean)
        
        # 找到相似度低谷（分割点）
        split_points = []
        for i in range(1, len(local_means) - 1):
            # 如果当前位置的相似度明显低于前后，则可能是分割点
            if (local_means[i] < similarity_threshold or 
                (local_means[i] < local_means[i-1] * 0.7 and 
                 local_means[i] < local_means[i+1] * 0.7)):
                split_points.append(i + 1)  # 在i和i+1之间分割
        
        # 如果没有找到明显的分割点，尝试更宽松的策略
        if not split_points:
            # 找到相似度最低的几个点
            sorted_indices = sorted(range(len(similarities)), 
                                   key=lambda i: similarities[i])
            # 取相似度最低的20%作为候选分割点
            num_splits = max(1, len(similarities) // 10)
            split_points = sorted([idx + 1 for idx in sorted_indices[:num_splits]])
        
        # 去除相邻太近的分割点（至少间隔2个句子）
        if split_points:
            filtered_splits = [split_points[0]]
            for split in split_points[1:]:
                if split - filtered_splits[-1] >= 2:
                    filtered_splits.append(split)
            split_points = filtered_splits
        
        # 根据分割点组合句子
        segments = []
        start_idx = 0
        for split_idx in split_points:
            segment = "".join(sentences[start_idx:split_idx])
            if segment.strip():
                segments.append(segment.strip())
            start_idx = split_idx
        # 添加最后一段
        if start_idx < len(sentences):
            segment = "".join(sentences[start_idx:])
            if segment.strip():
                segments.append(segment.strip())
        
        return segments if segments else [text]

    def pack_segments_by_word_count(segments: List[str], max_word_count: int) -> List[str]:
        """在语义片段基础上，按字数限制重新打包"""
        if not segments:
            return []
        
        packed = []
        current_chunk = ""
        current_count = 0
        
        for segment in segments:
            seg_count = calculate_word_count(segment)
            
            # 如果单个片段就超过限制，需要进一步分割
            if seg_count > max_word_count:
                # 先将当前chunk保存
                if current_chunk:
                    packed.append(current_chunk)
                    current_chunk = ""
                    current_count = 0
                
                # 对超长片段进行递归分割（按段落或句子）
                # 简单策略：按段落分割
                paragraphs = segment.split("\n\n")
                for para in paragraphs:
                    para_count = calculate_word_count(para)
                    if para_count > max_word_count:
                        # 段落还是太长，按句子分割
                        sentences = split_into_sentences(para)
                        for sent in sentences:
                            sent_count = calculate_word_count(sent)
                            if current_count + sent_count > max_word_count and current_chunk:
                                packed.append(current_chunk)
                                current_chunk = sent
                                current_count = sent_count
                            else:
                                current_chunk += sent
                                current_count += sent_count
                    else:
                        # 段落可以加入当前chunk
                        if current_count + para_count > max_word_count and current_chunk:
                            packed.append(current_chunk)
                            current_chunk = para
                            current_count = para_count
                        else:
                            if current_chunk:
                                current_chunk += "\n\n" + para
                            else:
                                current_chunk = para
                            current_count += para_count
            else:
                # 片段可以加入当前chunk
                if current_count + seg_count > max_word_count and current_chunk:
                    # 当前chunk已满，保存并开始新的
                    packed.append(current_chunk)
                    current_chunk = segment
                    current_count = seg_count
                else:
                    # 加入当前chunk
                    if current_chunk:
                        current_chunk += "\n\n" + segment
                    else:
                        current_chunk = segment
                    current_count += seg_count
        
        # 添加最后的chunk
        if current_chunk:
            packed.append(current_chunk)
        
        return packed

    def split_article_into_segments(
        title: str,
        content: str,
        max_word_count: int = 10000,
        api_key: Optional[str] = None,
        similarity_threshold: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        将文章分割成多个段落，每个段落不超过指定字数，并为每个段落生成标题
        
        输入参数：
        - title: str - 原文章标题
        - content: str - 原文章内容
        - max_word_count: int - 每个段落的最大字数限制（去除空格后，默认10000）
        - api_key: Optional[str] - 用于生成标题的API密钥（如果为None，会尝试从环境变量获取）
        - similarity_threshold: float - 语义分割的相似度阈值（默认0.3）
        
        输出：
        List[Dict[str, Any]] - 分割后的段落列表，每个字典包含：
            - "title": str - 生成的段落标题
            - "content": str - 段落内容
            - "word_count": int - 段落的字数（去除空格后）
        
        示例：
        >>> segments = split_article_into_segments(
        ...     title="如何学习编程",
        ...     content="编程是一门艺术...",
        ...     max_word_count=300
        ... )
        >>> for seg in segments:
        ...     print(f"{seg['title']}: {seg['word_count']}字")
        """
        if not content or not content.strip():
            return []
        
        if not api_key:
            api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
            if not api_key:
                raise ValueError("缺少 DOUBAO_API_KEY，无法生成标题")
        
        actual_word_count = calculate_word_count(content)
        
        # 如果文章字数不超过限制，直接返回（使用原标题）
        if actual_word_count <= max_word_count:
            return [{
                "title": title,
                "content": content,
                "word_count": actual_word_count
            }]
        
        # 第一步：使用语义分割技术切分成话题片段
        print(f"开始语义分割：文章长度 {actual_word_count} 字，最大限制 {max_word_count} 字", flush=True)
        semantic_segments = semantic_segmentation(content, similarity_threshold=similarity_threshold)
        print(f"语义分割完成：共 {len(semantic_segments)} 个话题片段", flush=True)
        
        # 第二步：在话题片段基础上，按字数限制重新打包
        packed_chunks = pack_segments_by_word_count(semantic_segments, max_word_count)
        print(f"打包完成：共 {len(packed_chunks)} 个打包块", flush=True)
        
        # 验证每个chunk的字数
        for idx, chunk in enumerate(packed_chunks):
            chunk_word_count = calculate_word_count(chunk)
            if chunk_word_count > max_word_count:
                print(f"警告：第 {idx+1} 个打包块超过限制 ({chunk_word_count} > {max_word_count})", flush=True)
        
        # 第三步：对每个打包后的chunk调用大模型生成标题
        segments = []
        for idx, chunk in enumerate(packed_chunks):
            chunk_word_count = calculate_word_count(chunk)
            
            # 生成标题的提示词
            title_prompt = f"""请为以下文本片段生成一个简洁、准确的标题。

要求：
1. 标题要能准确概括这段文字的核心内容
2. 标题要简洁明了，一般不超过20字
3. 不要使用"第X部分"、"原标题"这类形式化表述
4. 直接输出标题，不要添加任何说明文字

文本内容：
{chunk}

标题："""
            
            try:
                import requests
                base_url = "https://ark.cn-beijing.volces.com/api/v3"
                endpoint = f"{base_url}/chat/completions"
                
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                
                request_payload = {
                    "model": "doubao-seed-1-6-flash-250828",  # 使用更快的flash模型生成标题
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是一个专业的标题生成助手。请根据文本内容生成简洁准确的标题，直接输出标题文字，不要添加任何说明。"
                        },
                        {
                            "role": "user",
                            "content": title_prompt
                        }
                    ],
                    "temperature": 0.3,
                    "max_tokens": 200,  # 标题不需要太多token
                }
                
                response = requests.post(endpoint, headers=headers, json=request_payload, timeout=300)
                response.raise_for_status()
                data = response.json()
                
                if "choices" not in data or not data["choices"]:
                    raise RuntimeError("API未返回结果")
                
                message = data["choices"][0].get("message", {})
                generated_title = message.get("content", "").strip()
                
                # 清理标题（去除可能的引号、标点等）
                generated_title = re.sub(r'^["\'「」【】\[\]()（）]*|["\'「」【】\[\]()（）]*$', '', generated_title).strip()
                
                if not generated_title:
                    generated_title = f"{title} - 片段{idx+1}"
                
                segments.append({
                    "title": generated_title,
                    "content": chunk,
                    "word_count": chunk_word_count
                })
                
                print(f"第 {idx+1}/{len(packed_chunks)} 个片段标题生成完成: {generated_title} ({chunk_word_count}字)", flush=True)
                
            except Exception as exc:
                print(f"生成标题失败 (片段{idx+1}): {exc}", flush=True)
                # 标题生成失败时使用默认标题
                segments.append({
                    "title": f"{title} - 片段{idx+1}",
                    "content": chunk,
                    "word_count": chunk_word_count
                })
        
        return segments if segments else [{"title": title, "content": content, "word_count": actual_word_count}]

    @app.post("/api/restructure/split-article")
    def split_article_endpoint():
        """
        标准文章分割接口
        
        输入：
        - text: str - 要分割的文本内容（必需）
        - max_length: int - 每段的最大字数限制（必需，去除空格后的字符数）
        - title: str - 原文章标题（可选，如果不提供会使用默认标题）
        
        输出：
        - segments: List[Dict] - 分割后的段落列表，每个字典包含：
            - "title": str - 生成的段落标题
            - "content": str - 段落内容
            - "word_count": int - 段落的字数（去除空格后）
        """
        payload = request.get_json() or {}
        text = payload.get("text", "").strip()
        max_length = payload.get("max_length")
        title = payload.get("title", "").strip() or "未命名文章"

        if not text:
            return jsonify({"error": "缺少 text 字段（文本内容）"}), 400
        
        if max_length is None:
            return jsonify({"error": "缺少 max_length 字段（分段长度）"}), 400
        
        try:
            max_word_count = int(max_length)
            if max_word_count <= 0:
                return jsonify({"error": "max_length 必须大于 0"}), 400
        except (TypeError, ValueError):
            return jsonify({"error": "max_length 必须是有效的整数"}), 400

        api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
        if not api_key:
            return jsonify({"error": "缺少 DOUBAO_API_KEY"}), 400

        try:
            # 调用封装的函数进行文章分割
            segments = split_article_into_segments(
                title=title,
                content=text,
                max_word_count=max_word_count,
                api_key=api_key
            )
            
            if not segments:
                return jsonify({"error": "分割失败，无法生成有效片段"}), 500

            return jsonify({"segments": segments})
            
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            print(f"分割文章出错: {exc}", flush=True)
            return jsonify({"error": f"分割失败: {str(exc)}"}), 500

    @app.post("/api/restructure/split-chapter")
    def split_chapter_endpoint():
        """分割超过1万字的章节（兼容旧接口）"""
        payload = request.get_json() or {}
        title = payload.get("title", "").strip()
        content = payload.get("content", "").strip()
        max_word_count = int(payload.get("max_word_count", 10000))
        word_count = int(payload.get("word_count", 0))

        if not title or not content:
            return jsonify({"error": "缺少标题或内容"}), 400

        api_key = os.environ.get("DOUBAO_API_KEY") or load_setting("doubao_api_key", "")
        if not api_key:
            return jsonify({"error": "缺少 DOUBAO_API_KEY"}), 400

        try:
            # 调用封装的函数进行文章分割
            segments = split_article_into_segments(
                title=title,
                content=content,
                max_word_count=max_word_count,
                api_key=api_key
            )
            
            if not segments:
                return jsonify({"error": "分割失败，无法生成有效片段"}), 500

            return jsonify({"segments": segments})
            
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            print(f"分割章节出错: {exc}", flush=True)
            return jsonify({"error": f"分割失败: {str(exc)}"}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)

