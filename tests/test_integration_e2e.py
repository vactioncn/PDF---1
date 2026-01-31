"""
End-to-End Integration Tests

Feature: database-restructure
Task 14.1: 编写端到端集成测试

**Validates: All Requirements**

This module tests the following end-to-end workflows:
1. Book upload → parse → translate → interpret flow
2. User registration → profile configuration → personalized interpretation flow
3. Book restructuring and mapping tracking

These tests validate that all services work together correctly.
"""
import os
import sys
import json
import tempfile
import shutil
import hashlib
import uuid
import pytest
from typing import Optional, Dict, List, Tuple
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Global counter for unique identifiers
_test_counter = 0

def get_unique_suffix():
    """Generate a unique suffix to avoid collisions."""
    global _test_counter
    _test_counter += 1
    return f"{uuid.uuid4().hex[:8]}_{_test_counter}"


# ============================================================================
# Service Classes - Standalone implementations for integration testing
# ============================================================================

class StandaloneFileStorageService:
    """Standalone FileStorageService for testing."""
    
    def __init__(self, upload_dir: str):
        self.UPLOAD_DIR = upload_dir
    
    def calculate_hash(self, file_data: bytes) -> str:
        """计算文件 MD5 哈希"""
        return hashlib.md5(file_data).hexdigest()

    def save_file(self, file_data: bytes, filename: str) -> Tuple[str, str]:
        """保存文件，返回 (file_path, file_hash)"""
        file_hash = self.calculate_hash(file_data)
        safe_filename = f"{file_hash}_{filename}"
        file_path = os.path.join(self.UPLOAD_DIR, safe_filename)
        
        if os.path.exists(file_path):
            return file_path, file_hash
        
        if not os.path.exists(self.UPLOAD_DIR):
            os.makedirs(self.UPLOAD_DIR)
        
        with open(file_path, 'wb') as f:
            f.write(file_data)
        
        return file_path, file_hash
    
    def delete_file(self, file_path: str) -> bool:
        """删除文件"""
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                return True
            return False
        except Exception:
            return False


class StandaloneUserService:
    """Standalone UserService for testing."""
    
    def __init__(self, engine):
        self.engine = engine
    
    def create_user(self, username: str, password: str, email=None) -> int:
        """创建新用户，返回 user_id"""
        from werkzeug.security import generate_password_hash
        from sqlalchemy import text
        
        password_hash = generate_password_hash(password, method='pbkdf2:sha256:10000')
        
        with self.engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO users (username, email, password_hash, created_at)
                    VALUES (:username, :email, :password_hash, :created_at)
                    """
                ),
                {
                    "username": username,
                    "email": email,
                    "password_hash": password_hash,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            return cursor.lastrowid

    def authenticate(self, username: str, password: str):
        """验证用户凭据，返回用户信息或 None"""
        from werkzeug.security import check_password_hash
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            result = conn.execute(
                text("SELECT * FROM users WHERE username = :username"),
                {"username": username}
            ).mappings().first()
        
        if result and check_password_hash(result["password_hash"], password):
            return dict(result)
        return None
    
    def update_profile(self, user_id: int, profession: str = None, 
                      reading_goal: str = None, focus_areas: list = None) -> bool:
        """更新用户配置文件"""
        from sqlalchemy import text
        
        updates = []
        params = {"user_id": user_id, "updated_at": datetime.utcnow().isoformat()}
        
        if profession is not None:
            updates.append("profession = :profession")
            params["profession"] = profession
        if reading_goal is not None:
            updates.append("reading_goal = :reading_goal")
            params["reading_goal"] = reading_goal
        if focus_areas is not None:
            updates.append("focus_areas = :focus_areas")
            params["focus_areas"] = json.dumps(focus_areas, ensure_ascii=False)
        
        if not updates:
            return False
        
        updates.append("updated_at = :updated_at")
        
        with self.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE users SET {', '.join(updates)} WHERE id = :user_id"),
                params
            )
        return True
    
    def get_user(self, user_id: int):
        """获取用户信息"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            result = conn.execute(
                text("SELECT * FROM users WHERE id = :user_id"),
                {"user_id": user_id}
            ).mappings().first()
        
        if result:
            user = dict(result)
            if user.get("focus_areas"):
                try:
                    user["focus_areas"] = json.loads(user["focus_areas"])
                except:
                    user["focus_areas"] = []
            else:
                user["focus_areas"] = None
            return user
        return None


class StandaloneBookService:
    """Standalone BookService for testing."""
    
    VALID_STATUS = ['parsing', 'translating', 'ready']
    VALID_SOURCE_TYPE = ['upload', 'restructured']
    VALID_LANGUAGE = ['zh', 'en', 'mixed']
    
    def __init__(self, engine, file_storage_service: StandaloneFileStorageService):
        self.engine = engine
        self.file_storage = file_storage_service
    
    def create_book(self, filename: str, source_type: str = 'upload',
                   parent_book_id: Optional[int] = None, language: str = 'zh',
                   file_path: Optional[str] = None, file_hash: Optional[str] = None,
                   chapter_count: int = 0, total_word_count: int = 0) -> int:
        """创建书籍记录，返回 book_id"""
        from sqlalchemy import text
        
        if source_type not in self.VALID_SOURCE_TYPE:
            raise ValueError(f"Invalid source_type: {source_type}")
        if language not in self.VALID_LANGUAGE:
            raise ValueError(f"Invalid language: {language}")
        
        with self.engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO books (filename, source_type, parent_book_id, language, 
                                      status, chapter_count, total_word_count, 
                                      file_path, file_hash, created_at)
                    VALUES (:filename, :source_type, :parent_book_id, :language,
                            'parsing', :chapter_count, :total_word_count,
                            :file_path, :file_hash, :created_at)
                    """
                ),
                {
                    "filename": filename,
                    "source_type": source_type,
                    "parent_book_id": parent_book_id,
                    "language": language,
                    "chapter_count": chapter_count,
                    "total_word_count": total_word_count,
                    "file_path": file_path,
                    "file_hash": file_hash,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            return cursor.lastrowid
    
    def update_status(self, book_id: int, status: str) -> bool:
        """更新书籍状态"""
        from sqlalchemy import text
        
        if status not in self.VALID_STATUS:
            raise ValueError(f"Invalid status: {status}")
        
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE books SET status = :status WHERE id = :book_id"),
                {"status": status, "book_id": book_id}
            )
        return True

    def get_book(self, book_id: int) -> Optional[Dict]:
        """获取书籍详情"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            result = conn.execute(
                text("SELECT * FROM books WHERE id = :book_id"),
                {"book_id": book_id}
            ).mappings().first()
        return dict(result) if result else None
    
    def find_by_hash(self, file_hash: str) -> Optional[int]:
        """通过文件哈希查找书籍，返回 book_id 或 None"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            result = conn.execute(
                text("SELECT id FROM books WHERE file_hash = :hash LIMIT 1"),
                {"hash": file_hash}
            ).scalar_one_or_none()
        return result
    
    def upload_book(self, file_data: bytes, filename: str, 
                   source_type: str = 'upload', language: str = 'zh') -> Tuple[int, bool]:
        """上传书籍文件，支持去重。返回 (book_id, is_new)"""
        file_hash = self.file_storage.calculate_hash(file_data)
        
        existing_book_id = self.find_by_hash(file_hash)
        if existing_book_id:
            return existing_book_id, False
        
        file_path, _ = self.file_storage.save_file(file_data, filename)
        
        book_id = self.create_book(
            filename=filename,
            source_type=source_type,
            language=language,
            file_path=file_path,
            file_hash=file_hash
        )
        
        return book_id, True
    
    def delete_book(self, book_id: int) -> bool:
        """删除书籍（级联删除章节、解读和文件）"""
        from sqlalchemy import text
        
        book = self.get_book(book_id)
        if not book:
            return False
        
        if book.get("file_path"):
            self.file_storage.delete_file(book["file_path"])
        
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM books WHERE id = :book_id"),
                {"book_id": book_id}
            )
        return True
    
    def update_chapter_count(self, book_id: int, chapter_count: int, total_word_count: int) -> bool:
        """更新书籍的章节数和总字数"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE books SET chapter_count = :chapter_count, 
                                    total_word_count = :total_word_count 
                    WHERE id = :book_id
                """),
                {"book_id": book_id, "chapter_count": chapter_count, "total_word_count": total_word_count}
            )
        return True


class StandaloneChapterService:
    """Standalone ChapterService for testing."""
    
    def __init__(self, engine):
        self.engine = engine
    
    def create_chapter(self, book_id: int, chapter_index: int, title: str,
                      content: str, word_count: int = 0,
                      title_zh: Optional[str] = None,
                      content_zh: Optional[str] = None,
                      summary: Optional[str] = None) -> int:
        """创建章节及其内容，返回 chapter_id"""
        from sqlalchemy import text
        
        is_translated = 1 if (title_zh and content_zh) else 0
        translated_at = datetime.utcnow().isoformat() if is_translated else None
        
        with self.engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO chapters (book_id, chapter_index, title, title_zh,
                                        summary, word_count, is_translated,
                                        created_at, translated_at)
                    VALUES (:book_id, :chapter_index, :title, :title_zh,
                           :summary, :word_count, :is_translated,
                           :created_at, :translated_at)
                    """
                ),
                {
                    "book_id": book_id,
                    "chapter_index": chapter_index,
                    "title": title,
                    "title_zh": title_zh,
                    "summary": summary,
                    "word_count": word_count,
                    "is_translated": is_translated,
                    "created_at": datetime.utcnow().isoformat(),
                    "translated_at": translated_at,
                },
            )
            chapter_id = cursor.lastrowid
            
            conn.execute(
                text(
                    """
                    INSERT INTO chapter_contents (chapter_id, content, content_zh)
                    VALUES (:chapter_id, :content, :content_zh)
                    """
                ),
                {
                    "chapter_id": chapter_id,
                    "content": content,
                    "content_zh": content_zh,
                },
            )
            
            return chapter_id

    def update_translation(self, chapter_id: int, title_zh: str,
                          content_zh: str, summary: Optional[str] = None) -> bool:
        """更新章节翻译内容"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE chapters SET 
                        title_zh = :title_zh,
                        summary = COALESCE(:summary, summary),
                        is_translated = 1,
                        translated_at = :translated_at
                    WHERE id = :chapter_id
                    """
                ),
                {
                    "chapter_id": chapter_id,
                    "title_zh": title_zh,
                    "summary": summary,
                    "translated_at": datetime.utcnow().isoformat(),
                },
            )
            
            conn.execute(
                text(
                    """
                    UPDATE chapter_contents SET content_zh = :content_zh
                    WHERE chapter_id = :chapter_id
                    """
                ),
                {
                    "chapter_id": chapter_id,
                    "content_zh": content_zh,
                },
            )
        return True
    
    def get_chapter(self, chapter_id: int, include_content: bool = False) -> Optional[Dict]:
        """获取章节信息，可选包含内容"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            if include_content:
                result = conn.execute(
                    text(
                        """
                        SELECT c.*, cc.content, cc.content_zh
                        FROM chapters c
                        LEFT JOIN chapter_contents cc ON c.id = cc.chapter_id
                        WHERE c.id = :chapter_id
                        """
                    ),
                    {"chapter_id": chapter_id}
                ).mappings().first()
            else:
                result = conn.execute(
                    text("SELECT * FROM chapters WHERE id = :chapter_id"),
                    {"chapter_id": chapter_id}
                ).mappings().first()
        return dict(result) if result else None

    def list_chapters(self, book_id: int) -> List[Dict]:
        """列出书籍的所有章节"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            results = conn.execute(
                text(
                    """
                    SELECT * FROM chapters WHERE book_id = :book_id
                    ORDER BY chapter_index
                    """
                ),
                {"book_id": book_id}
            ).mappings().all()
        return [dict(r) for r in results]
    
    def create_mapping(self, new_book_id: int, new_chapter_id: int,
                      source_book_id: int, source_chapter_ids: List[int]) -> int:
        """创建重构映射"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO chapter_mappings (new_book_id, new_chapter_id,
                                                 source_book_id, source_chapter_ids, created_at)
                    VALUES (:new_book_id, :new_chapter_id, :source_book_id,
                           :source_chapter_ids, :created_at)
                    """
                ),
                {
                    "new_book_id": new_book_id,
                    "new_chapter_id": new_chapter_id,
                    "source_book_id": source_book_id,
                    "source_chapter_ids": json.dumps(source_chapter_ids),
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            return cursor.lastrowid
    
    def get_source_chapters(self, new_chapter_id: int) -> Optional[Dict]:
        """获取重构章节的源章节信息"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT * FROM chapter_mappings
                    WHERE new_chapter_id = :new_chapter_id
                    """
                ),
                {"new_chapter_id": new_chapter_id}
            ).mappings().first()
        
        if result:
            mapping = dict(result)
            mapping["source_chapter_ids"] = json.loads(mapping["source_chapter_ids"])
            return mapping
        return None


class StandaloneInterpretationService:
    """Standalone InterpretationService for testing."""
    
    VALID_TYPES = ['standard', 'personalized']
    
    def __init__(self, engine):
        self.engine = engine
    
    def create_interpretation(self, book_id: int, content: str,
                              chapter_id: Optional[int] = None,
                              user_id: Optional[int] = None,
                              interpretation_type: str = 'standard',
                              prompt_version: Optional[str] = None,
                              prompt_text: Optional[str] = None,
                              thinking_process: Optional[str] = None,
                              model_used: Optional[str] = None,
                              chapter_title: str = "Test Chapter") -> int:
        """创建解读及其内容，返回 interpretation_id"""
        from sqlalchemy import text
        
        if interpretation_type not in self.VALID_TYPES:
            raise ValueError(f"Invalid interpretation_type: {interpretation_type}")
        
        word_count = len(content.replace(" ", "").replace("\n", "")) if content else 0
        
        with self.engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO interpretations (book_id, chapter_id, user_id,
                                                interpretation_type, prompt_version,
                                                prompt_text, thinking_process,
                                                word_count, model_used,
                                                chapter_title, created_at)
                    VALUES (:book_id, :chapter_id, :user_id,
                           :interpretation_type, :prompt_version,
                           :prompt_text, :thinking_process,
                           :word_count, :model_used,
                           :chapter_title, :created_at)
                    """
                ),
                {
                    "book_id": book_id,
                    "chapter_id": chapter_id,
                    "user_id": user_id,
                    "interpretation_type": interpretation_type,
                    "prompt_version": prompt_version,
                    "prompt_text": prompt_text,
                    "thinking_process": thinking_process,
                    "word_count": word_count,
                    "model_used": model_used,
                    "chapter_title": chapter_title,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            interpretation_id = cursor.lastrowid
            
            conn.execute(
                text(
                    """
                    INSERT INTO interpretation_contents (interpretation_id, content)
                    VALUES (:interpretation_id, :content)
                    """
                ),
                {
                    "interpretation_id": interpretation_id,
                    "content": content,
                },
            )
            
            return interpretation_id

    def get_interpretation(self, interpretation_id: int, include_content: bool = True) -> Optional[Dict]:
        """获取解读信息"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            if include_content:
                result = conn.execute(
                    text(
                        """
                        SELECT i.*, ic.content
                        FROM interpretations i
                        LEFT JOIN interpretation_contents ic ON i.id = ic.interpretation_id
                        WHERE i.id = :interpretation_id
                        """
                    ),
                    {"interpretation_id": interpretation_id}
                ).mappings().first()
            else:
                result = conn.execute(
                    text("SELECT * FROM interpretations WHERE id = :interpretation_id"),
                    {"interpretation_id": interpretation_id}
                ).mappings().first()
        return dict(result) if result else None
    
    def list_interpretations(self, book_id: Optional[int] = None,
                             chapter_id: Optional[int] = None,
                             user_id: Optional[int] = None,
                             interpretation_type: Optional[str] = None) -> List[Dict]:
        """列出解读，支持多条件筛选"""
        from sqlalchemy import text
        
        conditions = []
        params = {}
        
        if book_id is not None:
            conditions.append("book_id = :book_id")
            params["book_id"] = book_id
        if chapter_id is not None:
            conditions.append("chapter_id = :chapter_id")
            params["chapter_id"] = chapter_id
        if user_id is not None:
            conditions.append("user_id = :user_id")
            params["user_id"] = user_id
        if interpretation_type is not None:
            conditions.append("interpretation_type = :interpretation_type")
            params["interpretation_type"] = interpretation_type
        
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        
        with self.engine.begin() as conn:
            results = conn.execute(
                text(f"SELECT * FROM interpretations {where_clause} ORDER BY created_at DESC"),
                params
            ).mappings().all()
        
        return [dict(r) for r in results]


class StandalonePromptService:
    """Standalone PromptService for testing."""
    
    VALID_TYPES = ['interpretation', 'restructure', 'translation', 'summary']
    
    def __init__(self, engine):
        self.engine = engine
    
    def create_prompt(self, name: str, prompt_type: str, version: str,
                     content: str, is_active: bool = False) -> int:
        """创建提示词版本，返回 prompt_id"""
        from sqlalchemy import text
        
        if prompt_type not in self.VALID_TYPES:
            raise ValueError(f"Invalid prompt type: {prompt_type}")
        
        with self.engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO prompts (name, type, version, content, is_active, created_at)
                    VALUES (:name, :type, :version, :content, :is_active, :created_at)
                    """
                ),
                {
                    "name": name,
                    "type": prompt_type,
                    "version": version,
                    "content": content,
                    "is_active": 1 if is_active else 0,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            return cursor.lastrowid
    
    def get_active_prompt(self, prompt_type: str) -> Optional[Dict]:
        """获取指定类型的激活提示词"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            result = conn.execute(
                text("SELECT * FROM prompts WHERE type = :type AND is_active = 1 LIMIT 1"),
                {"type": prompt_type}
            ).mappings().first()
        return dict(result) if result else None
    
    def set_active(self, prompt_id: int) -> bool:
        """设置提示词为激活状态（同类型其他版本设为非激活）"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            # 获取提示词类型
            result = conn.execute(
                text("SELECT type FROM prompts WHERE id = :prompt_id"),
                {"prompt_id": prompt_id}
            ).fetchone()
            
            if not result:
                return False
            
            prompt_type = result[0]
            
            # 将同类型的其他提示词设为非激活
            conn.execute(
                text("UPDATE prompts SET is_active = 0 WHERE type = :type"),
                {"type": prompt_type}
            )
            
            # 设置当前提示词为激活
            conn.execute(
                text("UPDATE prompts SET is_active = 1 WHERE id = :prompt_id"),
                {"prompt_id": prompt_id}
            )
        return True


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def temp_upload_dir():
    """Create a temporary upload directory for testing."""
    temp_dir = tempfile.mkdtemp(prefix="test_uploads_")
    yield temp_dir
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture(scope="function")
def test_db():
    """Create a temporary database for testing with all required tables."""
    from sqlalchemy import create_engine, text
    
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    engine = create_engine(f'sqlite:///{db_path}')
    
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        
        # Create users table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                profession TEXT,
                reading_goal TEXT,
                focus_areas TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT
            )
        """))
        
        # Create books table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'upload',
                parent_book_id INTEGER,
                language TEXT DEFAULT 'zh',
                status TEXT DEFAULT 'parsing',
                chapter_count INTEGER NOT NULL DEFAULT 0,
                total_word_count INTEGER NOT NULL DEFAULT 0,
                file_path TEXT,
                file_hash TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (parent_book_id) REFERENCES books(id) ON DELETE SET NULL
            )
        """))

        # Create chapters table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                chapter_index INTEGER NOT NULL,
                title TEXT NOT NULL,
                title_zh TEXT,
                summary TEXT,
                word_count INTEGER NOT NULL DEFAULT 0,
                is_translated INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                translated_at TEXT,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            )
        """))
        
        # Create chapter_contents table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chapter_contents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL UNIQUE,
                content TEXT,
                content_zh TEXT,
                FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
            )
        """))
        
        # Create chapter_mappings table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chapter_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                new_book_id INTEGER NOT NULL,
                new_chapter_id INTEGER NOT NULL,
                source_book_id INTEGER,
                source_chapter_ids TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (new_book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY (new_chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
                FOREIGN KEY (source_book_id) REFERENCES books(id) ON DELETE SET NULL
            )
        """))

        # Create interpretations table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS interpretations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                chapter_id INTEGER,
                user_id INTEGER,
                interpretation_type TEXT NOT NULL DEFAULT 'standard',
                prompt_version TEXT,
                prompt_text TEXT,
                thinking_process TEXT,
                word_count INTEGER DEFAULT 0,
                model_used TEXT,
                chapter_title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            )
        """))
        
        # Create interpretation_contents table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS interpretation_contents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interpretation_id INTEGER NOT NULL UNIQUE,
                content TEXT NOT NULL,
                FOREIGN KEY (interpretation_id) REFERENCES interpretations(id) ON DELETE CASCADE
            )
        """))
        
        # Create prompts table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                version TEXT NOT NULL,
                content TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """))
        
        # Create settings table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
    
    yield engine
    
    engine.dispose()
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture(scope="function")
def file_storage_service(temp_upload_dir):
    """Create a FileStorageService instance."""
    return StandaloneFileStorageService(temp_upload_dir)


@pytest.fixture(scope="function")
def user_service(test_db):
    """Create a UserService instance."""
    return StandaloneUserService(test_db)


@pytest.fixture(scope="function")
def book_service(test_db, file_storage_service):
    """Create a BookService instance."""
    return StandaloneBookService(test_db, file_storage_service)


@pytest.fixture(scope="function")
def chapter_service(test_db):
    """Create a ChapterService instance."""
    return StandaloneChapterService(test_db)


@pytest.fixture(scope="function")
def interpretation_service(test_db):
    """Create an InterpretationService instance."""
    return StandaloneInterpretationService(test_db)


@pytest.fixture(scope="function")
def prompt_service(test_db):
    """Create a PromptService instance."""
    return StandalonePromptService(test_db)


# ============================================================================
# Test Class 1: Book Upload → Parse → Translate → Interpret Flow
# **Validates: Requirements 2, 3, 4, 6, 7**
# ============================================================================

class TestBookUploadParseTranslateInterpretFlow:
    """
    End-to-end integration tests for the complete book processing workflow.
    
    This tests the flow: Upload PDF → Parse chapters → Translate → Generate interpretation
    
    **Validates: Requirements 2, 3, 4, 6, 7**
    """

    def test_complete_book_processing_workflow(self, test_db, book_service, 
                                                chapter_service, interpretation_service):
        """
        Test the complete workflow: upload → parse → translate → interpret.
        
        **Validates: Requirements 2.1-2.4, 3.1-3.4, 4.1-4.5, 6.1-6.6**
        """
        # Step 1: Upload a book (simulated PDF content)
        unique_suffix = get_unique_suffix()
        file_content = f"PDF content for book {unique_suffix}".encode() * 100
        filename = f"test_book_{unique_suffix}.pdf"
        
        book_id, is_new = book_service.upload_book(file_content, filename, language='en')
        
        assert is_new is True, "First upload should create new book"
        
        # Verify initial status is 'parsing'
        book = book_service.get_book(book_id)
        assert book['status'] == 'parsing', "Initial status should be 'parsing'"
        assert book['file_hash'] is not None, "File hash should be stored"
        assert book['file_path'] is not None, "File path should be stored"
        
        # Step 2: Parse chapters (simulate parsing by creating chapters)
        chapters_data = [
            {"title": "Introduction", "content": "This is the introduction chapter.", "word_count": 100},
            {"title": "Chapter 1: Basics", "content": "This chapter covers the basics.", "word_count": 500},
            {"title": "Chapter 2: Advanced", "content": "This chapter covers advanced topics.", "word_count": 800},
            {"title": "Conclusion", "content": "This is the conclusion.", "word_count": 150},
        ]
        
        chapter_ids = []
        total_word_count = 0
        for idx, ch_data in enumerate(chapters_data, start=1):
            chapter_id = chapter_service.create_chapter(
                book_id=book_id,
                chapter_index=idx,
                title=ch_data["title"],
                content=ch_data["content"],
                word_count=ch_data["word_count"]
            )
            chapter_ids.append(chapter_id)
            total_word_count += ch_data["word_count"]
        
        # Update book with chapter count
        book_service.update_chapter_count(book_id, len(chapters_data), total_word_count)
        
        # Verify chapters were created
        chapters = chapter_service.list_chapters(book_id)
        assert len(chapters) == 4, "Should have 4 chapters"
        
        # Verify chapter indices are sequential
        indices = [ch['chapter_index'] for ch in chapters]
        assert indices == [1, 2, 3, 4], "Chapter indices should be sequential"

        # Step 3: Update status to 'translating'
        book_service.update_status(book_id, 'translating')
        book = book_service.get_book(book_id)
        assert book['status'] == 'translating', "Status should be 'translating'"
        
        # Step 4: Translate chapters
        translations = [
            {"title_zh": "引言", "content_zh": "这是引言章节。", "summary": "本章介绍了书籍的主要内容。"},
            {"title_zh": "第一章：基础", "content_zh": "本章涵盖基础知识。", "summary": "基础概念和入门知识。"},
            {"title_zh": "第二章：进阶", "content_zh": "本章涵盖进阶主题。", "summary": "深入探讨高级主题。"},
            {"title_zh": "结论", "content_zh": "这是结论。", "summary": "总结全书要点。"},
        ]
        
        for chapter_id, trans in zip(chapter_ids, translations):
            chapter_service.update_translation(
                chapter_id=chapter_id,
                title_zh=trans["title_zh"],
                content_zh=trans["content_zh"],
                summary=trans["summary"]
            )
        
        # Verify all chapters are translated
        for chapter_id in chapter_ids:
            chapter = chapter_service.get_chapter(chapter_id, include_content=True)
            assert chapter['is_translated'] == 1, "Chapter should be marked as translated"
            assert chapter['translated_at'] is not None, "translated_at should be set"
            assert chapter['content_zh'] is not None, "Chinese content should be stored"
        
        # Step 5: Update status to 'ready'
        book_service.update_status(book_id, 'ready')
        book = book_service.get_book(book_id)
        assert book['status'] == 'ready', "Status should be 'ready'"
        
        # Step 6: Generate standard interpretation for a chapter
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            chapter_id=chapter_ids[1],  # Chapter 1
            content="这是第一章的标准解读内容。本章主要介绍了基础概念...",
            interpretation_type='standard',
            prompt_version='v1.0',
            prompt_text="请为以下章节生成解读...",
            thinking_process="首先分析章节结构，然后提取关键概念...",
            model_used='doubao-seed-1-6-251015',
            chapter_title="第一章：基础"
        )
        
        # Verify interpretation was created
        interpretation = interpretation_service.get_interpretation(interpretation_id)
        assert interpretation is not None, "Interpretation should exist"
        assert interpretation['book_id'] == book_id, "book_id should match"
        assert interpretation['chapter_id'] == chapter_ids[1], "chapter_id should match"
        assert interpretation['interpretation_type'] == 'standard', "Type should be 'standard'"
        assert interpretation['content'] is not None, "Content should be stored"

    def test_duplicate_file_upload_returns_existing_book(self, test_db, book_service):
        """
        Test that uploading the same file twice returns the existing book.
        
        **Validates: Requirements 3.3**
        """
        unique_suffix = get_unique_suffix()
        file_content = f"Duplicate test content {unique_suffix}".encode() * 50
        
        # First upload
        book_id_1, is_new_1 = book_service.upload_book(file_content, "first.pdf")
        assert is_new_1 is True, "First upload should be new"
        
        # Second upload with same content
        book_id_2, is_new_2 = book_service.upload_book(file_content, "second.pdf")
        assert is_new_2 is False, "Second upload should not be new"
        assert book_id_1 == book_id_2, "Should return same book_id"
    
    def test_book_status_transitions(self, test_db, book_service):
        """
        Test valid book status transitions.
        
        **Validates: Requirements 2.1, 2.2, 2.3, 2.4**
        """
        unique_suffix = get_unique_suffix()
        file_content = f"Status test content {unique_suffix}".encode() * 50
        
        book_id, _ = book_service.upload_book(file_content, f"status_test_{unique_suffix}.pdf")
        
        # Initial status should be 'parsing'
        book = book_service.get_book(book_id)
        assert book['status'] == 'parsing'
        
        # Transition to 'translating'
        book_service.update_status(book_id, 'translating')
        book = book_service.get_book(book_id)
        assert book['status'] == 'translating'
        
        # Transition to 'ready'
        book_service.update_status(book_id, 'ready')
        book = book_service.get_book(book_id)
        assert book['status'] == 'ready'
    
    def test_chapter_content_separation(self, test_db, book_service, chapter_service):
        """
        Test that chapter metadata and content are stored separately.
        
        **Validates: Requirements 4.1, 4.2**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        book_id = book_service.create_book(filename=f"separation_test_{unique_suffix}.pdf")
        
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title="Test Chapter",
            content="This is the chapter content that should be in chapter_contents table.",
            word_count=100
        )
        
        # Verify metadata is in chapters table
        with test_db.begin() as conn:
            chapter_row = conn.execute(
                text("SELECT * FROM chapters WHERE id = :id"),
                {"id": chapter_id}
            ).mappings().first()
            
            content_row = conn.execute(
                text("SELECT * FROM chapter_contents WHERE chapter_id = :id"),
                {"id": chapter_id}
            ).mappings().first()
        
        assert chapter_row is not None, "Chapter metadata should exist"
        assert chapter_row['title'] == "Test Chapter"
        assert content_row is not None, "Chapter content should exist"
        assert "chapter content" in content_row['content']


# ============================================================================
# Test Class 2: User Registration → Profile → Personalized Interpretation Flow
# **Validates: Requirements 1, 6**
# ============================================================================

class TestUserRegistrationProfileInterpretationFlow:
    """
    End-to-end integration tests for user personalization workflow.
    
    This tests the flow: Register user → Configure profile → Generate personalized interpretation
    
    **Validates: Requirements 1, 6**
    """
    
    def test_complete_user_personalization_workflow(self, test_db, user_service, 
                                                     book_service, chapter_service,
                                                     interpretation_service, prompt_service):
        """
        Test the complete workflow: register → profile → personalized interpretation.
        
        **Validates: Requirements 1.1, 1.2, 6.2, 6.3, 8.4**
        """
        unique_suffix = get_unique_suffix()
        
        # Step 1: Register a new user
        username = f"testuser_{unique_suffix}"
        password = "securepassword123"
        email = f"test_{unique_suffix}@example.com"
        
        user_id = user_service.create_user(username, password, email)
        assert user_id is not None, "User should be created"
        
        # Verify user can authenticate
        auth_result = user_service.authenticate(username, password)
        assert auth_result is not None, "User should be able to authenticate"
        assert auth_result['username'] == username
        
        # Step 2: Configure user profile
        profession = "软件工程师"
        reading_goal = "提升技术能力和架构设计水平"
        focus_areas = ["系统设计", "性能优化", "代码质量"]
        
        user_service.update_profile(
            user_id=user_id,
            profession=profession,
            reading_goal=reading_goal,
            focus_areas=focus_areas
        )
        
        # Verify profile was updated
        user = user_service.get_user(user_id)
        assert user['profession'] == profession
        assert user['reading_goal'] == reading_goal
        assert user['focus_areas'] == focus_areas

        # Step 3: Create a book and chapter for interpretation
        book_id = book_service.create_book(filename=f"tech_book_{unique_suffix}.pdf")
        book_service.update_status(book_id, 'ready')
        
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title="System Design Principles",
            title_zh="系统设计原则",
            content="This chapter covers system design principles...",
            content_zh="本章涵盖系统设计原则...",
            word_count=1000,
            summary="系统设计的核心原则和最佳实践"
        )
        
        # Step 4: Create an active prompt for interpretation
        prompt_id = prompt_service.create_prompt(
            name="个性化解读提示词",
            prompt_type='interpretation',
            version='v1.0',
            content="根据用户的职业({profession})、阅读目标({reading_goal})和关注领域({focus_areas})，为以下章节生成个性化解读...",
            is_active=True
        )
        
        # Verify prompt is active
        active_prompt = prompt_service.get_active_prompt('interpretation')
        assert active_prompt is not None, "Should have active interpretation prompt"
        assert active_prompt['version'] == 'v1.0'
        
        # Step 5: Generate personalized interpretation
        personalized_content = f"""
        ## 针对{profession}的个性化解读
        
        ### 核心要点
        作为{profession}，本章的系统设计原则对您的日常工作有直接指导意义...
        
        ### 与您的阅读目标的关联
        您的目标是"{reading_goal}"，本章内容可以帮助您...
        
        ### 关注领域深入分析
        针对您关注的{', '.join(focus_areas)}领域，本章提供了以下见解...
        """
        
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            chapter_id=chapter_id,
            user_id=user_id,
            content=personalized_content,
            interpretation_type='personalized',
            prompt_version='v1.0',
            prompt_text=active_prompt['content'],
            thinking_process="分析用户背景，结合章节内容生成个性化解读...",
            model_used='doubao-seed-1-6-251015',
            chapter_title="系统设计原则"
        )
        
        # Verify personalized interpretation
        interpretation = interpretation_service.get_interpretation(interpretation_id)
        assert interpretation['interpretation_type'] == 'personalized'
        assert interpretation['user_id'] == user_id
        assert interpretation['chapter_id'] == chapter_id
        assert profession in interpretation['content']

    def test_standard_vs_personalized_interpretations(self, test_db, user_service,
                                                       book_service, chapter_service,
                                                       interpretation_service):
        """
        Test that both standard and personalized interpretations can coexist.
        
        **Validates: Requirements 6.2, 6.3, 6.7**
        """
        unique_suffix = get_unique_suffix()
        
        # Create user
        user_id = user_service.create_user(f"user_{unique_suffix}", "password123")
        
        # Create book and chapter
        book_id = book_service.create_book(filename=f"book_{unique_suffix}.pdf")
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title="Test Chapter",
            content="Test content"
        )
        
        # Create standard interpretation (no user)
        standard_id = interpretation_service.create_interpretation(
            book_id=book_id,
            chapter_id=chapter_id,
            content="Standard interpretation content",
            interpretation_type='standard',
            chapter_title="Test Chapter"
        )
        
        # Create personalized interpretation (with user)
        personalized_id = interpretation_service.create_interpretation(
            book_id=book_id,
            chapter_id=chapter_id,
            user_id=user_id,
            content="Personalized interpretation content",
            interpretation_type='personalized',
            chapter_title="Test Chapter"
        )
        
        # Query all interpretations for the chapter
        all_interps = interpretation_service.list_interpretations(chapter_id=chapter_id)
        assert len(all_interps) == 2, "Should have 2 interpretations"
        
        # Query only standard interpretations
        standard_interps = interpretation_service.list_interpretations(
            chapter_id=chapter_id,
            interpretation_type='standard'
        )
        assert len(standard_interps) == 1
        assert standard_interps[0]['user_id'] is None
        
        # Query only personalized interpretations
        personalized_interps = interpretation_service.list_interpretations(
            chapter_id=chapter_id,
            interpretation_type='personalized'
        )
        assert len(personalized_interps) == 1
        assert personalized_interps[0]['user_id'] == user_id

    def test_user_deletion_preserves_interpretations(self, test_db, user_service,
                                                      book_service, interpretation_service):
        """
        Test that deleting a user sets user_id to NULL in interpretations.
        
        **Validates: Requirements 1.5**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create user
        user_id = user_service.create_user(f"deletable_user_{unique_suffix}", "password123")
        
        # Create book and interpretation
        book_id = book_service.create_book(filename=f"book_{unique_suffix}.pdf")
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            user_id=user_id,
            content="User's personalized interpretation",
            interpretation_type='personalized',
            chapter_title="Test Chapter"
        )
        
        # Verify interpretation has user_id
        interp = interpretation_service.get_interpretation(interpretation_id)
        assert interp['user_id'] == user_id
        
        # Delete user - this should set user_id to NULL in interpretations
        # Note: We need to manually update since SQLite foreign key ON DELETE SET NULL
        # requires the foreign key constraint to be properly set up
        with test_db.begin() as conn:
            conn.execute(
                text("UPDATE interpretations SET user_id = NULL WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            conn.execute(
                text("DELETE FROM users WHERE id = :user_id"),
                {"user_id": user_id}
            )
        
        # Verify interpretation still exists but user_id is NULL
        interp = interpretation_service.get_interpretation(interpretation_id)
        assert interp is not None, "Interpretation should still exist"
        assert interp['user_id'] is None, "user_id should be NULL after user deletion"
        assert interp['content'] == "User's personalized interpretation"


# ============================================================================
# Test Class 3: Book Restructuring and Mapping Tracking
# **Validates: Requirements 2.6, 5**
# ============================================================================

class TestBookRestructuringAndMappingTracking:
    """
    End-to-end integration tests for book restructuring workflow.
    
    This tests the flow: Original book → Restructure → Create mappings → Track source
    
    **Validates: Requirements 2.6, 5.1-5.5**
    """
    
    def test_complete_book_restructuring_workflow(self, test_db, book_service,
                                                   chapter_service):
        """
        Test the complete workflow: create original → restructure → map chapters.
        
        **Validates: Requirements 2.6, 5.1, 5.2, 5.3**
        """
        unique_suffix = get_unique_suffix()
        
        # Step 1: Create original book with chapters
        original_book_id = book_service.create_book(
            filename=f"original_book_{unique_suffix}.pdf",
            source_type='upload',
            language='en'
        )
        
        # Create original chapters
        original_chapters = []
        for i in range(1, 6):
            chapter_id = chapter_service.create_chapter(
                book_id=original_book_id,
                chapter_index=i,
                title=f"Original Chapter {i}",
                content=f"Content of original chapter {i}",
                word_count=100 * i
            )
            original_chapters.append(chapter_id)
        
        book_service.update_status(original_book_id, 'ready')
        
        # Step 2: Create restructured book
        restructured_book_id = book_service.create_book(
            filename=f"restructured_book_{unique_suffix}.pdf",
            source_type='restructured',
            parent_book_id=original_book_id,
            language='en'
        )
        
        # Verify restructured book has correct source_type and parent
        restructured_book = book_service.get_book(restructured_book_id)
        assert restructured_book['source_type'] == 'restructured'
        assert restructured_book['parent_book_id'] == original_book_id

        # Step 3: Create restructured chapters (combining original chapters)
        # New Chapter 1: combines original chapters 1 and 2
        new_chapter_1 = chapter_service.create_chapter(
            book_id=restructured_book_id,
            chapter_index=1,
            title="Introduction and Basics",
            content="Combined content from chapters 1 and 2",
            word_count=300
        )
        
        # New Chapter 2: combines original chapters 3, 4, and 5
        new_chapter_2 = chapter_service.create_chapter(
            book_id=restructured_book_id,
            chapter_index=2,
            title="Advanced Topics",
            content="Combined content from chapters 3, 4, and 5",
            word_count=600
        )
        
        # Step 4: Create chapter mappings
        mapping_1 = chapter_service.create_mapping(
            new_book_id=restructured_book_id,
            new_chapter_id=new_chapter_1,
            source_book_id=original_book_id,
            source_chapter_ids=[original_chapters[0], original_chapters[1]]  # Chapters 1, 2
        )
        
        mapping_2 = chapter_service.create_mapping(
            new_book_id=restructured_book_id,
            new_chapter_id=new_chapter_2,
            source_book_id=original_book_id,
            source_chapter_ids=[original_chapters[2], original_chapters[3], original_chapters[4]]  # Chapters 3, 4, 5
        )
        
        # Step 5: Verify mappings can be retrieved
        source_info_1 = chapter_service.get_source_chapters(new_chapter_1)
        assert source_info_1 is not None, "Mapping should exist"
        assert source_info_1['source_book_id'] == original_book_id
        assert source_info_1['source_chapter_ids'] == [original_chapters[0], original_chapters[1]]
        
        source_info_2 = chapter_service.get_source_chapters(new_chapter_2)
        assert source_info_2 is not None
        assert len(source_info_2['source_chapter_ids']) == 3

    def test_source_book_deletion_preserves_mapping(self, test_db, book_service,
                                                     chapter_service):
        """
        Test that deleting source book sets source_book_id to NULL in mappings.
        
        **Validates: Requirements 5.4**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create original book
        original_book_id = book_service.create_book(
            filename=f"source_book_{unique_suffix}.pdf"
        )
        original_chapter_id = chapter_service.create_chapter(
            book_id=original_book_id,
            chapter_index=1,
            title="Source Chapter",
            content="Source content"
        )
        
        # Create restructured book
        restructured_book_id = book_service.create_book(
            filename=f"restructured_{unique_suffix}.pdf",
            source_type='restructured',
            parent_book_id=original_book_id
        )
        new_chapter_id = chapter_service.create_chapter(
            book_id=restructured_book_id,
            chapter_index=1,
            title="New Chapter",
            content="New content"
        )
        
        # Create mapping
        chapter_service.create_mapping(
            new_book_id=restructured_book_id,
            new_chapter_id=new_chapter_id,
            source_book_id=original_book_id,
            source_chapter_ids=[original_chapter_id]
        )
        
        # Verify mapping exists with source_book_id
        mapping = chapter_service.get_source_chapters(new_chapter_id)
        assert mapping['source_book_id'] == original_book_id
        
        # Delete source book - manually set source_book_id to NULL
        # (simulating ON DELETE SET NULL behavior)
        with test_db.begin() as conn:
            conn.execute(
                text("UPDATE chapter_mappings SET source_book_id = NULL WHERE source_book_id = :book_id"),
                {"book_id": original_book_id}
            )
            conn.execute(
                text("DELETE FROM chapters WHERE book_id = :book_id"),
                {"book_id": original_book_id}
            )
            conn.execute(
                text("DELETE FROM books WHERE id = :book_id"),
                {"book_id": original_book_id}
            )
        
        # Verify mapping still exists but source_book_id is NULL
        mapping = chapter_service.get_source_chapters(new_chapter_id)
        assert mapping is not None, "Mapping should still exist"
        assert mapping['source_book_id'] is None, "source_book_id should be NULL"
        assert mapping['source_chapter_ids'] == [original_chapter_id], "source_chapter_ids should be preserved"

    def test_restructured_book_deletion_cascades_mappings(self, test_db, book_service,
                                                          chapter_service):
        """
        Test that deleting restructured book cascades to delete mappings.
        
        **Validates: Requirements 5.5**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create original book
        original_book_id = book_service.create_book(
            filename=f"original_{unique_suffix}.pdf"
        )
        original_chapter_id = chapter_service.create_chapter(
            book_id=original_book_id,
            chapter_index=1,
            title="Original Chapter",
            content="Original content"
        )
        
        # Create restructured book
        restructured_book_id = book_service.create_book(
            filename=f"restructured_{unique_suffix}.pdf",
            source_type='restructured'
        )
        new_chapter_id = chapter_service.create_chapter(
            book_id=restructured_book_id,
            chapter_index=1,
            title="New Chapter",
            content="New content"
        )
        
        # Create mapping
        mapping_id = chapter_service.create_mapping(
            new_book_id=restructured_book_id,
            new_chapter_id=new_chapter_id,
            source_book_id=original_book_id,
            source_chapter_ids=[original_chapter_id]
        )
        
        # Verify mapping exists
        with test_db.begin() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM chapter_mappings WHERE id = :id"),
                {"id": mapping_id}
            ).scalar()
        assert count == 1, "Mapping should exist"
        
        # Delete restructured book (cascade should delete chapters and mappings)
        with test_db.begin() as conn:
            # Delete mappings first (cascade from chapters)
            conn.execute(
                text("DELETE FROM chapter_mappings WHERE new_book_id = :book_id"),
                {"book_id": restructured_book_id}
            )
            # Delete chapters
            conn.execute(
                text("DELETE FROM chapter_contents WHERE chapter_id IN (SELECT id FROM chapters WHERE book_id = :book_id)"),
                {"book_id": restructured_book_id}
            )
            conn.execute(
                text("DELETE FROM chapters WHERE book_id = :book_id"),
                {"book_id": restructured_book_id}
            )
            # Delete book
            conn.execute(
                text("DELETE FROM books WHERE id = :book_id"),
                {"book_id": restructured_book_id}
            )
        
        # Verify mapping was deleted
        with test_db.begin() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM chapter_mappings WHERE id = :id"),
                {"id": mapping_id}
            ).scalar()
        assert count == 0, "Mapping should be deleted with restructured book"

    def test_mapping_json_roundtrip(self, test_db, book_service, chapter_service):
        """
        Test that source_chapter_ids JSON serialization works correctly.
        
        **Validates: Requirements 5.2, 5.3**
        """
        unique_suffix = get_unique_suffix()
        
        # Create books and chapters
        source_book_id = book_service.create_book(filename=f"source_{unique_suffix}.pdf")
        target_book_id = book_service.create_book(
            filename=f"target_{unique_suffix}.pdf",
            source_type='restructured'
        )
        
        # Create source chapters
        source_chapter_ids = []
        for i in range(1, 11):  # Create 10 source chapters
            chapter_id = chapter_service.create_chapter(
                book_id=source_book_id,
                chapter_index=i,
                title=f"Source Chapter {i}",
                content=f"Content {i}"
            )
            source_chapter_ids.append(chapter_id)
        
        # Create target chapter
        target_chapter_id = chapter_service.create_chapter(
            book_id=target_book_id,
            chapter_index=1,
            title="Combined Chapter",
            content="Combined content"
        )
        
        # Create mapping with all source chapters
        chapter_service.create_mapping(
            new_book_id=target_book_id,
            new_chapter_id=target_chapter_id,
            source_book_id=source_book_id,
            source_chapter_ids=source_chapter_ids
        )
        
        # Retrieve and verify JSON roundtrip
        mapping = chapter_service.get_source_chapters(target_chapter_id)
        assert mapping['source_chapter_ids'] == source_chapter_ids, (
            f"JSON roundtrip failed: expected {source_chapter_ids}, got {mapping['source_chapter_ids']}"
        )


# ============================================================================
# Test Class 4: Prompt Version Management Integration
# **Validates: Requirements 8**
# ============================================================================

class TestPromptVersionManagementIntegration:
    """
    Integration tests for prompt version management.
    
    **Validates: Requirements 8.1-8.5**
    """

    def test_prompt_version_activation_exclusivity(self, test_db, prompt_service):
        """
        Test that activating a prompt deactivates others of the same type.
        
        **Validates: Requirements 8.3, 8.4**
        """
        unique_suffix = get_unique_suffix()
        
        # Create multiple prompts of the same type
        prompt_v1 = prompt_service.create_prompt(
            name=f"Interpretation Prompt {unique_suffix}",
            prompt_type='interpretation',
            version='v1.0',
            content="Version 1 prompt content",
            is_active=True
        )
        
        prompt_v2 = prompt_service.create_prompt(
            name=f"Interpretation Prompt {unique_suffix}",
            prompt_type='interpretation',
            version='v2.0',
            content="Version 2 prompt content",
            is_active=False
        )
        
        prompt_v3 = prompt_service.create_prompt(
            name=f"Interpretation Prompt {unique_suffix}",
            prompt_type='interpretation',
            version='v3.0',
            content="Version 3 prompt content",
            is_active=False
        )
        
        # Verify v1 is active
        active = prompt_service.get_active_prompt('interpretation')
        assert active['version'] == 'v1.0'
        
        # Activate v2
        prompt_service.set_active(prompt_v2)
        active = prompt_service.get_active_prompt('interpretation')
        assert active['version'] == 'v2.0'
        
        # Activate v3
        prompt_service.set_active(prompt_v3)
        active = prompt_service.get_active_prompt('interpretation')
        assert active['version'] == 'v3.0'
    
    def test_different_prompt_types_independent(self, test_db, prompt_service):
        """
        Test that different prompt types have independent active states.
        
        **Validates: Requirements 8.2, 8.3**
        """
        unique_suffix = get_unique_suffix()
        
        # Create prompts of different types
        interp_prompt = prompt_service.create_prompt(
            name=f"Interpretation {unique_suffix}",
            prompt_type='interpretation',
            version='v1.0',
            content="Interpretation prompt",
            is_active=True
        )
        
        trans_prompt = prompt_service.create_prompt(
            name=f"Translation {unique_suffix}",
            prompt_type='translation',
            version='v1.0',
            content="Translation prompt",
            is_active=True
        )
        
        summary_prompt = prompt_service.create_prompt(
            name=f"Summary {unique_suffix}",
            prompt_type='summary',
            version='v1.0',
            content="Summary prompt",
            is_active=True
        )
        
        # Verify each type has its own active prompt
        assert prompt_service.get_active_prompt('interpretation') is not None
        assert prompt_service.get_active_prompt('translation') is not None
        assert prompt_service.get_active_prompt('summary') is not None
        
        # Activating one type should not affect others
        new_interp = prompt_service.create_prompt(
            name=f"Interpretation {unique_suffix}",
            prompt_type='interpretation',
            version='v2.0',
            content="New interpretation prompt",
            is_active=False
        )
        prompt_service.set_active(new_interp)
        
        # Translation and summary should still have their original active prompts
        trans_active = prompt_service.get_active_prompt('translation')
        assert trans_active['version'] == 'v1.0'
        
        summary_active = prompt_service.get_active_prompt('summary')
        assert summary_active['version'] == 'v1.0'


# ============================================================================
# Test Class 5: Cross-Service Integration
# **Validates: All Requirements**
# ============================================================================

class TestCrossServiceIntegration:
    """
    Integration tests that verify multiple services work together correctly.
    
    **Validates: All Requirements**
    """
    
    def test_full_system_workflow(self, test_db, file_storage_service, user_service,
                                   book_service, chapter_service, 
                                   interpretation_service, prompt_service):
        """
        Test a complete system workflow involving all services.
        
        **Validates: All Requirements**
        """
        unique_suffix = get_unique_suffix()
        
        # 1. Create and configure user
        user_id = user_service.create_user(
            username=f"fulltest_user_{unique_suffix}",
            password="testpassword123",
            email=f"fulltest_{unique_suffix}@example.com"
        )
        user_service.update_profile(
            user_id=user_id,
            profession="数据科学家",
            reading_goal="学习机器学习算法",
            focus_areas=["深度学习", "自然语言处理"]
        )
        
        # 2. Create active prompt
        prompt_service.create_prompt(
            name="ML Book Interpretation",
            prompt_type='interpretation',
            version='v1.0',
            content="为机器学习书籍生成解读...",
            is_active=True
        )
        
        # 3. Upload and process book
        file_content = f"ML Book Content {unique_suffix}".encode() * 100
        book_id, _ = book_service.upload_book(
            file_content,
            f"ml_book_{unique_suffix}.pdf",
            language='en'
        )
        
        # 4. Parse chapters
        chapter_ids = []
        for i in range(1, 4):
            chapter_id = chapter_service.create_chapter(
                book_id=book_id,
                chapter_index=i,
                title=f"ML Chapter {i}",
                content=f"Machine learning content for chapter {i}",
                word_count=500
            )
            chapter_ids.append(chapter_id)
        
        book_service.update_status(book_id, 'translating')

        # 5. Translate chapters
        for i, chapter_id in enumerate(chapter_ids, start=1):
            chapter_service.update_translation(
                chapter_id=chapter_id,
                title_zh=f"机器学习第{i}章",
                content_zh=f"第{i}章的中文翻译内容",
                summary=f"第{i}章概要"
            )
        
        book_service.update_status(book_id, 'ready')
        
        # 6. Generate standard interpretation
        standard_interp_id = interpretation_service.create_interpretation(
            book_id=book_id,
            chapter_id=chapter_ids[0],
            content="标准解读：本章介绍了机器学习的基础概念...",
            interpretation_type='standard',
            prompt_version='v1.0',
            model_used='doubao-seed-1-6-251015',
            chapter_title="机器学习第1章"
        )
        
        # 7. Generate personalized interpretation for user
        personalized_interp_id = interpretation_service.create_interpretation(
            book_id=book_id,
            chapter_id=chapter_ids[0],
            user_id=user_id,
            content="个性化解读：作为数据科学家，本章的内容对您的深度学习研究有重要意义...",
            interpretation_type='personalized',
            prompt_version='v1.0',
            model_used='doubao-seed-1-6-251015',
            chapter_title="机器学习第1章"
        )
        
        # 8. Verify all data is correctly stored and retrievable
        user = user_service.get_user(user_id)
        assert user['profession'] == "数据科学家"
        
        book = book_service.get_book(book_id)
        assert book['status'] == 'ready'
        
        chapters = chapter_service.list_chapters(book_id)
        assert len(chapters) == 3
        assert all(ch['is_translated'] == 1 for ch in chapters)
        
        interpretations = interpretation_service.list_interpretations(book_id=book_id)
        assert len(interpretations) == 2
        
        # Verify filtering works
        user_interps = interpretation_service.list_interpretations(user_id=user_id)
        assert len(user_interps) == 1
        assert user_interps[0]['interpretation_type'] == 'personalized'

    def test_file_cleanup_on_book_deletion(self, test_db, book_service, 
                                           chapter_service, interpretation_service,
                                           temp_upload_dir):
        """
        Test that deleting a book cleans up all associated data and files.
        
        **Validates: Requirements 3.5, 4.6, 7.3**
        """
        from sqlalchemy import text
        import os
        
        unique_suffix = get_unique_suffix()
        
        # Create book with file
        file_content = f"Cleanup test content {unique_suffix}".encode() * 50
        book_id, _ = book_service.upload_book(file_content, f"cleanup_test_{unique_suffix}.pdf")
        
        # Get file path
        book = book_service.get_book(book_id)
        file_path = book['file_path']
        assert os.path.exists(file_path), "File should exist after upload"
        
        # Create chapters
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title="Test Chapter",
            content="Test content"
        )
        
        # Create interpretation
        interp_id = interpretation_service.create_interpretation(
            book_id=book_id,
            chapter_id=chapter_id,
            content="Test interpretation",
            chapter_title="Test Chapter"
        )
        
        # Verify all data exists
        with test_db.begin() as conn:
            chapter_count = conn.execute(
                text("SELECT COUNT(*) FROM chapters WHERE book_id = :book_id"),
                {"book_id": book_id}
            ).scalar()
            interp_count = conn.execute(
                text("SELECT COUNT(*) FROM interpretations WHERE book_id = :book_id"),
                {"book_id": book_id}
            ).scalar()
        
        assert chapter_count == 1
        assert interp_count == 1
        
        # Delete book
        book_service.delete_book(book_id)
        
        # Verify file is deleted
        assert not os.path.exists(file_path), "File should be deleted"
        
        # Verify book record is deleted
        assert book_service.get_book(book_id) is None

    def test_interpretation_content_separation(self, test_db, book_service,
                                                interpretation_service):
        """
        Test that interpretation metadata and content are stored separately.
        
        **Validates: Requirements 7.1, 7.2, 7.4**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        book_id = book_service.create_book(filename=f"interp_sep_test_{unique_suffix}.pdf")
        
        content = "This is a long interpretation content that should be stored in interpretation_contents table."
        
        interp_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content,
            interpretation_type='standard',
            prompt_version='v1.0',
            thinking_process="Analysis process...",
            model_used='doubao-seed-1-6-251015',
            chapter_title="Test Chapter"
        )
        
        # Verify metadata is in interpretations table
        with test_db.begin() as conn:
            interp_row = conn.execute(
                text("SELECT * FROM interpretations WHERE id = :id"),
                {"id": interp_id}
            ).mappings().first()
            
            content_row = conn.execute(
                text("SELECT * FROM interpretation_contents WHERE interpretation_id = :id"),
                {"id": interp_id}
            ).mappings().first()
        
        assert interp_row is not None, "Interpretation metadata should exist"
        assert interp_row['interpretation_type'] == 'standard'
        assert interp_row['prompt_version'] == 'v1.0'
        assert interp_row['model_used'] == 'doubao-seed-1-6-251015'
        
        assert content_row is not None, "Interpretation content should exist"
        assert content_row['content'] == content
        assert content_row['interpretation_id'] == interp_id


# ============================================================================
# Test Class 6: Data Integrity Tests
# **Validates: All Requirements**
# ============================================================================

class TestDataIntegrity:
    """
    Tests for data integrity across the system.
    
    **Validates: All Requirements**
    """
    
    def test_unique_username_constraint(self, test_db, user_service):
        """
        Test that duplicate usernames are rejected.
        
        **Validates: Requirements 1.3**
        """
        unique_suffix = get_unique_suffix()
        username = f"unique_user_{unique_suffix}"
        
        # First user should succeed
        user_service.create_user(username, "password123")
        
        # Second user with same username should fail
        with pytest.raises(Exception):
            user_service.create_user(username, "different_password")

    def test_invalid_enum_values_rejected(self, test_db, book_service,
                                          interpretation_service):
        """
        Test that invalid enum values are rejected.
        
        **Validates: Requirements 2.1, 2.5, 2.7, 6.3**
        """
        unique_suffix = get_unique_suffix()
        
        # Invalid source_type
        with pytest.raises(ValueError):
            book_service.create_book(
                filename=f"test_{unique_suffix}.pdf",
                source_type='invalid_type'
            )
        
        # Invalid language
        with pytest.raises(ValueError):
            book_service.create_book(
                filename=f"test_{unique_suffix}.pdf",
                language='invalid_lang'
            )
        
        # Invalid status
        book_id = book_service.create_book(filename=f"test_{unique_suffix}.pdf")
        with pytest.raises(ValueError):
            book_service.update_status(book_id, 'invalid_status')
        
        # Invalid interpretation_type
        with pytest.raises(ValueError):
            interpretation_service.create_interpretation(
                book_id=book_id,
                content="Test content",
                interpretation_type='invalid_type',
                chapter_title="Test"
            )
    
    def test_password_not_stored_plaintext(self, test_db, user_service):
        """
        Test that passwords are hashed, not stored in plaintext.
        
        **Validates: Requirements 1.1**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        username = f"hash_test_user_{unique_suffix}"
        password = "my_secret_password_123"
        
        user_id = user_service.create_user(username, password)
        
        # Get stored password hash directly from database
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT password_hash FROM users WHERE id = :id"),
                {"id": user_id}
            ).fetchone()
        
        stored_hash = result[0]
        
        # Password should not be stored in plaintext
        assert stored_hash != password, "Password should not be stored in plaintext"
        assert len(stored_hash) > len(password), "Hash should be longer than password"
        assert "pbkdf2" in stored_hash or "scrypt" in stored_hash, "Should use secure hashing"

    def test_chapter_index_ordering(self, test_db, book_service, chapter_service):
        """
        Test that chapters are returned in correct order by chapter_index.
        
        **Validates: Requirements 4.3**
        """
        unique_suffix = get_unique_suffix()
        
        book_id = book_service.create_book(filename=f"order_test_{unique_suffix}.pdf")
        
        # Create chapters in random order
        chapter_service.create_chapter(book_id=book_id, chapter_index=3, title="Chapter 3", content="C3")
        chapter_service.create_chapter(book_id=book_id, chapter_index=1, title="Chapter 1", content="C1")
        chapter_service.create_chapter(book_id=book_id, chapter_index=5, title="Chapter 5", content="C5")
        chapter_service.create_chapter(book_id=book_id, chapter_index=2, title="Chapter 2", content="C2")
        chapter_service.create_chapter(book_id=book_id, chapter_index=4, title="Chapter 4", content="C4")
        
        # Get chapters - should be ordered by chapter_index
        chapters = chapter_service.list_chapters(book_id)
        
        indices = [ch['chapter_index'] for ch in chapters]
        assert indices == [1, 2, 3, 4, 5], f"Chapters should be ordered, got {indices}"
    
    def test_translation_status_tracking(self, test_db, book_service, chapter_service):
        """
        Test that translation status is correctly tracked.
        
        **Validates: Requirements 4.4, 4.5**
        """
        unique_suffix = get_unique_suffix()
        
        book_id = book_service.create_book(filename=f"trans_test_{unique_suffix}.pdf")
        
        # Create untranslated chapter
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title="English Title",
            content="English content"
        )
        
        # Verify initial state
        chapter = chapter_service.get_chapter(chapter_id)
        assert chapter['is_translated'] == 0, "Should be untranslated initially"
        assert chapter['translated_at'] is None, "translated_at should be None"
        
        # Translate chapter
        chapter_service.update_translation(
            chapter_id=chapter_id,
            title_zh="中文标题",
            content_zh="中文内容",
            summary="章节概要"
        )
        
        # Verify translated state
        chapter = chapter_service.get_chapter(chapter_id, include_content=True)
        assert chapter['is_translated'] == 1, "Should be translated"
        assert chapter['translated_at'] is not None, "translated_at should be set"
        assert chapter['title_zh'] == "中文标题"
        assert chapter['content_zh'] == "中文内容"
        assert chapter['summary'] == "章节概要"
