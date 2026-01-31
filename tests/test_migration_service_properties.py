"""
Property-Based Tests for MigrationService

Feature: database-restructure
Properties 24, 25, 26: Migration Properties

**Validates: Requirements 9.1-9.6, 10.2, 10.3**

This module tests the following properties:
- Property 24: Migration Data Preservation - all data preserved after migration
- Property 25: Migration Idempotency - running migration twice produces same result
- Property 26: Settings Functionality Preservation - settings work correctly after migration
"""
import os
import sys
import tempfile
import shutil
import uuid
import json
import pytest
from hypothesis import given, strategies as st, settings, assume, HealthCheck
from typing import Optional, Dict, List, Tuple, Any
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Global counter for unique values
_test_counter = 0

def get_unique_suffix():
    """Generate a unique suffix to avoid collisions."""
    global _test_counter
    _test_counter += 1
    return f"{uuid.uuid4().hex[:8]}_{_test_counter}"


# ============================================================================
# Test Data Generators (Strategies)
# ============================================================================

# Settings key-value generator
settings_key_strategy = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P'))
).filter(lambda x: len(x.strip()) >= 1 and x.strip() == x)

settings_value_strategy = st.text(
    min_size=0,
    max_size=1000
)

# Book data generator for migration testing
book_filename_strategy = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(whitelist_categories=('L', 'N'))
).filter(lambda x: len(x.strip()) >= 1)

# Chapter data generator
chapter_title_strategy = st.text(
    min_size=1,
    max_size=200,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'Z'))
).filter(lambda x: len(x.strip()) >= 1)

chapter_content_strategy = st.text(
    min_size=1,
    max_size=5000
)

# Word count generator
word_count_strategy = st.integers(min_value=0, max_value=100000)


# ============================================================================
# Standalone Service Classes for Testing
# ============================================================================

class StandaloneMigrationService:
    """
    Standalone MigrationService for testing.
    Mirrors the app.py MigrationService implementation.
    """
    
    def __init__(self, engine, uploads_dir: str):
        self.engine = engine
        self.uploads_dir = uploads_dir
    
    def run_migration(self) -> Dict[str, Any]:
        """执行数据迁移，返回迁移结果统计"""
        results = {
            "books_migrated": 0,
            "chapters_migrated": 0,
            "interpretations_migrated": 0,
            "interpretations_unmatched": [],
            "errors": []
        }
        
        try:
            results["books_migrated"] = self.migrate_books()
            results["chapters_migrated"] = self.migrate_chapters()
            migrated, unmatched = self.migrate_interpretations()
            results["interpretations_migrated"] = migrated
            results["interpretations_unmatched"] = unmatched
            self.create_upload_directory()
        except Exception as e:
            results["errors"].append(str(e))
        
        return results
    
    def migrate_books(self) -> int:
        """迁移书籍数据，返回迁移数量"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE books SET 
                        source_type = COALESCE(source_type, 'upload'),
                        language = COALESCE(language, 'zh'),
                        status = COALESCE(status, 'ready')
                    WHERE source_type IS NULL OR language IS NULL OR status IS NULL
                    """
                )
            )
            return result.rowcount

    def migrate_chapters(self) -> int:
        """迁移章节数据（从 chapter_summaries 拆分到 chapters 和 chapter_contents），返回迁移数量"""
        from sqlalchemy import text
        
        migrated_count = 0
        
        with self.engine.begin() as conn:
            # 检查 chapter_summaries 表是否存在
            tables = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='chapter_summaries'")
            ).fetchall()
            
            if not tables:
                return 0
            
            # 获取所有 chapter_summaries 记录
            old_chapters = conn.execute(
                text("SELECT * FROM chapter_summaries ORDER BY book_id, id")
            ).mappings().all()
            
            # 按 book_id 分组，计算 chapter_index
            book_chapter_counts = {}
            
            for old_chapter in old_chapters:
                book_id = old_chapter.get("book_id")
                
                # 检查是否已迁移（通过 title 和 book_id 匹配）
                existing = conn.execute(
                    text(
                        """
                        SELECT id FROM chapters 
                        WHERE book_id = :book_id AND title = :title
                        LIMIT 1
                        """
                    ),
                    {"book_id": book_id, "title": old_chapter["chapter_title"]}
                ).scalar_one_or_none()
                
                if existing:
                    continue  # 已迁移，跳过
                
                # 计算 chapter_index
                if book_id not in book_chapter_counts:
                    max_index = conn.execute(
                        text("SELECT MAX(chapter_index) FROM chapters WHERE book_id = :book_id"),
                        {"book_id": book_id}
                    ).scalar() or 0
                    book_chapter_counts[book_id] = max_index
                
                book_chapter_counts[book_id] += 1
                chapter_index = book_chapter_counts[book_id]
                
                # 判断是否已翻译
                has_translation = bool(old_chapter.get("chapter_title_zh") and 
                                      old_chapter.get("chapter_content_zh"))

                # 创建新章节记录
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
                        "title": old_chapter["chapter_title"],
                        "title_zh": old_chapter.get("chapter_title_zh"),
                        "summary": old_chapter.get("summary", ""),
                        "word_count": old_chapter.get("word_count", 0),
                        "is_translated": 1 if has_translation else 0,
                        "created_at": old_chapter.get("created_at", datetime.utcnow().isoformat()),
                        "translated_at": old_chapter.get("created_at") if has_translation else None,
                    },
                )
                new_chapter_id = cursor.lastrowid
                
                # 创建章节内容记录
                conn.execute(
                    text(
                        """
                        INSERT INTO chapter_contents (chapter_id, content, content_zh)
                        VALUES (:chapter_id, :content, :content_zh)
                        """
                    ),
                    {
                        "chapter_id": new_chapter_id,
                        "content": old_chapter.get("chapter_content", ""),
                        "content_zh": old_chapter.get("chapter_content_zh"),
                    },
                )
                
                migrated_count += 1
        
        return migrated_count

    def migrate_interpretations(self) -> Tuple[int, List[int]]:
        """迁移解读数据，返回 (成功数量, 未匹配的解读ID列表)"""
        from sqlalchemy import text
        
        migrated_count = 0
        unmatched_ids = []
        
        with self.engine.begin() as conn:
            # 获取所有没有 book_id 的解读
            old_interpretations = conn.execute(
                text("SELECT * FROM interpretations WHERE book_id IS NULL")
            ).mappings().all()
            
            for old_interp in old_interpretations:
                chapter_title = old_interp.get("chapter_title", "")
                
                # 尝试通过 chapter_title 匹配书籍
                match = conn.execute(
                    text(
                        """
                        SELECT c.book_id, c.id as chapter_id
                        FROM chapters c
                        WHERE c.title = :title OR c.title_zh = :title
                        LIMIT 1
                        """
                    ),
                    {"title": chapter_title}
                ).mappings().first()
                
                if not match:
                    # 在 chapter_summaries 表中查找
                    match = conn.execute(
                        text(
                            """
                            SELECT book_id FROM chapter_summaries
                            WHERE chapter_title = :title OR chapter_title_zh = :title
                            LIMIT 1
                            """
                        ),
                        {"title": chapter_title}
                    ).mappings().first()
                
                if match:
                    book_id = match.get("book_id")
                    chapter_id = match.get("chapter_id")
                    
                    conn.execute(
                        text(
                            """
                            UPDATE interpretations SET 
                                book_id = :book_id,
                                chapter_id = :chapter_id
                            WHERE id = :interp_id
                            """
                        ),
                        {
                            "book_id": book_id,
                            "chapter_id": chapter_id,
                            "interp_id": old_interp["id"]
                        }
                    )
                    migrated_count += 1
                else:
                    unmatched_ids.append(old_interp["id"])
        
        return migrated_count, unmatched_ids
    
    def create_upload_directory(self) -> bool:
        """创建 uploads 目录"""
        if not os.path.exists(self.uploads_dir):
            os.makedirs(self.uploads_dir)
            return True
        return False


class StandaloneSettingsService:
    """Standalone settings service for testing."""
    
    def __init__(self, engine):
        self.engine = engine
    
    def store_setting(self, key: str, value: str) -> None:
        """Store a setting key-value pair."""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
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
    
    def load_setting(self, key: str, default: str = "") -> str:
        """Load a setting value by key."""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            result = conn.execute(
                text("SELECT value FROM settings WHERE key = :key"),
                {"key": key},
            ).scalar_one_or_none()
        return result if result is not None else default


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
        # Create settings table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))

        # Create books table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                source_type TEXT,
                parent_book_id INTEGER,
                language TEXT,
                status TEXT,
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
        
        # Create chapter_summaries table (old structure for migration testing)
        conn.execute(text("""
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
        """))

        # Create interpretations table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS interpretations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER,
                chapter_id INTEGER,
                user_id INTEGER,
                interpretation_type TEXT NOT NULL DEFAULT 'standard',
                prompt_version TEXT,
                prompt_text TEXT,
                thinking_process TEXT,
                word_count INTEGER DEFAULT 0,
                model_used TEXT,
                chapter_title TEXT NOT NULL,
                user_profession TEXT,
                reading_goal TEXT,
                focus TEXT,
                density TEXT,
                chapter_text TEXT,
                master_prompt TEXT,
                result_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
            )
        """))
    
    yield engine
    
    engine.dispose()
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture(scope="function")
def migration_service(test_db, temp_upload_dir):
    """Create a MigrationService instance for testing."""
    return StandaloneMigrationService(test_db, temp_upload_dir)


@pytest.fixture(scope="function")
def settings_service(test_db):
    """Create a SettingsService instance for testing."""
    return StandaloneSettingsService(test_db)


# ============================================================================
# Helper Functions
# ============================================================================

def create_old_book(conn, filename: str, chapter_count: int = 0, 
                   total_word_count: int = 0) -> int:
    """Create a book record without new fields (simulating old data)."""
    from sqlalchemy import text
    
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
    return cursor.lastrowid


def create_old_chapter_summary(conn, book_id: int, chapter_title: str, 
                               chapter_content: str, summary: str,
                               word_count: int, chapter_title_zh: str = None,
                               chapter_content_zh: str = None) -> int:
    """Create a chapter_summaries record (old structure)."""
    from sqlalchemy import text
    
    cursor = conn.execute(
        text(
            """
            INSERT INTO chapter_summaries (book_id, chapter_title, chapter_content,
                                          chapter_title_zh, chapter_content_zh,
                                          summary, word_count, created_at)
            VALUES (:book_id, :chapter_title, :chapter_content,
                   :chapter_title_zh, :chapter_content_zh,
                   :summary, :word_count, :created_at)
            """
        ),
        {
            "book_id": book_id,
            "chapter_title": chapter_title,
            "chapter_content": chapter_content,
            "chapter_title_zh": chapter_title_zh,
            "chapter_content_zh": chapter_content_zh,
            "summary": summary,
            "word_count": word_count,
            "created_at": datetime.utcnow().isoformat(),
        },
    )
    return cursor.lastrowid


def get_all_books(conn) -> List[Dict]:
    """Get all books from the database."""
    from sqlalchemy import text
    
    result = conn.execute(text("SELECT * FROM books")).mappings().all()
    return [dict(row) for row in result]


def get_all_chapters(conn) -> List[Dict]:
    """Get all chapters from the database."""
    from sqlalchemy import text
    
    result = conn.execute(text("SELECT * FROM chapters")).mappings().all()
    return [dict(row) for row in result]


def get_all_chapter_contents(conn) -> List[Dict]:
    """Get all chapter_contents from the database."""
    from sqlalchemy import text
    
    result = conn.execute(text("SELECT * FROM chapter_contents")).mappings().all()
    return [dict(row) for row in result]


def get_all_settings(conn) -> List[Dict]:
    """Get all settings from the database."""
    from sqlalchemy import text
    
    result = conn.execute(text("SELECT * FROM settings")).mappings().all()
    return [dict(row) for row in result]


# ============================================================================
# Property 24: Migration Data Preservation
# **Validates: Requirements 9.1, 9.2, 10.3**
# ============================================================================

class TestMigrationDataPreservationProperty:
    """
    Property-based tests for migration data preservation.
    
    Property 24: Migration Data Preservation
    *For any* existing data before migration, after migration:
    (1) all books SHALL exist with new fields having default values,
    (2) all chapter_summaries SHALL be split into chapters and chapter_contents
        with data preserved,
    (3) all settings SHALL be unchanged.
    
    **Validates: Requirements 9.1, 9.2, 10.3**
    """

    @given(
        filename=book_filename_strategy,
        chapter_count=st.integers(min_value=0, max_value=100),
        total_word_count=word_count_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_books_preserved_with_default_values(self, test_db, migration_service,
                                                  filename, chapter_count, 
                                                  total_word_count):
        """
        Property: All books SHALL exist after migration with new fields having
        default values.
        
        **Validates: Requirements 9.1**
        
        For any book created without new fields, after migration the book
        must still exist with source_type='upload', language='zh', status='ready'.
        """
        from sqlalchemy import text
        
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        # Create book without new fields (simulating old data)
        with test_db.begin() as conn:
            book_id = create_old_book(conn, unique_filename, chapter_count, 
                                     total_word_count)
        
        # Run migration
        migration_service.run_migration()
        
        # Verify book still exists with default values
        with test_db.begin() as conn:
            book = conn.execute(
                text("SELECT * FROM books WHERE id = :id"),
                {"id": book_id}
            ).mappings().first()
        
        assert book is not None, "Book should exist after migration"
        assert book["filename"] == unique_filename, "Filename should be preserved"
        assert book["chapter_count"] == chapter_count, "Chapter count should be preserved"
        assert book["total_word_count"] == total_word_count, "Word count should be preserved"
        # New fields should have default values
        assert book["source_type"] == "upload", "source_type should default to 'upload'"
        assert book["language"] == "zh", "language should default to 'zh'"
        assert book["status"] == "ready", "status should default to 'ready'"


    @given(
        filename=book_filename_strategy,
        chapter_title=chapter_title_strategy,
        chapter_content=chapter_content_strategy,
        summary=st.text(min_size=1, max_size=500),
        word_count=word_count_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_chapter_summaries_split_to_new_tables(self, test_db, migration_service,
                                                    filename, chapter_title,
                                                    chapter_content, summary,
                                                    word_count):
        """
        Property: All chapter_summaries SHALL be split into chapters and
        chapter_contents with data preserved.
        
        **Validates: Requirements 9.2**
        
        For any chapter_summaries record, after migration:
        - A corresponding chapters record must exist with metadata
        - A corresponding chapter_contents record must exist with content
        - All data must be preserved correctly
        """
        from sqlalchemy import text
        
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        unique_title = f"{chapter_title}_{get_unique_suffix()}"
        
        # Create book and chapter_summary (old structure)
        with test_db.begin() as conn:
            book_id = create_old_book(conn, unique_filename)
            create_old_chapter_summary(
                conn, book_id, unique_title, chapter_content,
                summary, word_count
            )
        
        # Run migration
        migration_service.run_migration()
        
        # Verify chapter was migrated to new tables
        with test_db.begin() as conn:
            # Check chapters table
            chapter = conn.execute(
                text(
                    """
                    SELECT * FROM chapters 
                    WHERE book_id = :book_id AND title = :title
                    """
                ),
                {"book_id": book_id, "title": unique_title}
            ).mappings().first()
            
            assert chapter is not None, "Chapter should exist in chapters table"
            assert chapter["title"] == unique_title, "Title should be preserved"
            assert chapter["summary"] == summary, "Summary should be preserved"
            assert chapter["word_count"] == word_count, "Word count should be preserved"
            
            # Check chapter_contents table
            content = conn.execute(
                text(
                    """
                    SELECT * FROM chapter_contents 
                    WHERE chapter_id = :chapter_id
                    """
                ),
                {"chapter_id": chapter["id"]}
            ).mappings().first()
            
            assert content is not None, "Chapter content should exist"
            assert content["content"] == chapter_content, "Content should be preserved"


    @given(
        filename=book_filename_strategy,
        chapter_title=chapter_title_strategy,
        chapter_content=chapter_content_strategy,
        chapter_title_zh=chapter_title_strategy,
        chapter_content_zh=chapter_content_strategy,
        summary=st.text(min_size=1, max_size=500),
        word_count=word_count_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_translated_chapters_preserved(self, test_db, migration_service,
                                           filename, chapter_title, chapter_content,
                                           chapter_title_zh, chapter_content_zh,
                                           summary, word_count):
        """
        Property: Translated chapter data SHALL be preserved during migration.
        
        **Validates: Requirements 9.2**
        
        For any chapter_summaries with translation data, after migration:
        - title_zh and content_zh must be preserved
        - is_translated flag must be set to 1
        """
        from sqlalchemy import text
        
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        unique_title = f"{chapter_title}_{get_unique_suffix()}"
        unique_title_zh = f"{chapter_title_zh}_{get_unique_suffix()}"
        
        # Create book and translated chapter_summary
        with test_db.begin() as conn:
            book_id = create_old_book(conn, unique_filename)
            create_old_chapter_summary(
                conn, book_id, unique_title, chapter_content,
                summary, word_count,
                chapter_title_zh=unique_title_zh,
                chapter_content_zh=chapter_content_zh
            )
        
        # Run migration
        migration_service.run_migration()
        
        # Verify translated data was preserved
        with test_db.begin() as conn:
            chapter = conn.execute(
                text(
                    """
                    SELECT * FROM chapters 
                    WHERE book_id = :book_id AND title = :title
                    """
                ),
                {"book_id": book_id, "title": unique_title}
            ).mappings().first()
            
            assert chapter is not None, "Chapter should exist"
            assert chapter["title_zh"] == unique_title_zh, "Chinese title should be preserved"
            assert chapter["is_translated"] == 1, "is_translated should be 1 for translated chapters"
            
            content = conn.execute(
                text("SELECT * FROM chapter_contents WHERE chapter_id = :id"),
                {"id": chapter["id"]}
            ).mappings().first()
            
            assert content["content_zh"] == chapter_content_zh, "Chinese content should be preserved"


    @given(
        key=settings_key_strategy,
        value=settings_value_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_settings_unchanged_after_migration(self, test_db, migration_service,
                                                 settings_service, key, value):
        """
        Property: All settings SHALL be unchanged after migration.
        
        **Validates: Requirements 10.3**
        
        For any settings key-value pair, after migration the setting
        must still exist with the same value.
        """
        unique_key = f"{key}_{get_unique_suffix()}"
        
        # Store setting before migration
        settings_service.store_setting(unique_key, value)
        
        # Run migration
        migration_service.run_migration()
        
        # Verify setting is unchanged
        retrieved_value = settings_service.load_setting(unique_key)
        assert retrieved_value == value, (
            f"Setting value should be unchanged. Expected '{value}', got '{retrieved_value}'"
        )


# ============================================================================
# Property 25: Migration Idempotency
# **Validates: Requirements 9.6**
# ============================================================================

class TestMigrationIdempotencyProperty:
    """
    Property-based tests for migration idempotency.
    
    Property 25: Migration Idempotency
    *For any* database state, running migration twice SHALL produce the
    same final state as running it once.
    
    **Validates: Requirements 9.6**
    """

    @given(
        filename=book_filename_strategy,
        chapter_count=st.integers(min_value=0, max_value=100),
        total_word_count=word_count_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_book_migration_idempotent(self, test_db, migration_service,
                                        filename, chapter_count, total_word_count):
        """
        Property: Running book migration twice SHALL produce the same result.
        
        **Validates: Requirements 9.6**
        
        For any book, running migration multiple times must not change
        the final state after the first migration.
        """
        from sqlalchemy import text
        
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        # Create book without new fields
        with test_db.begin() as conn:
            book_id = create_old_book(conn, unique_filename, chapter_count, 
                                     total_word_count)
        
        # Run migration first time
        result1 = migration_service.run_migration()
        
        # Get state after first migration
        with test_db.begin() as conn:
            book_after_first = conn.execute(
                text("SELECT * FROM books WHERE id = :id"),
                {"id": book_id}
            ).mappings().first()
            book_after_first = dict(book_after_first)
        
        # Run migration second time
        result2 = migration_service.run_migration()
        
        # Get state after second migration
        with test_db.begin() as conn:
            book_after_second = conn.execute(
                text("SELECT * FROM books WHERE id = :id"),
                {"id": book_id}
            ).mappings().first()
            book_after_second = dict(book_after_second)
        
        # Property: state should be identical
        assert book_after_first == book_after_second, (
            "Book state should be identical after running migration twice"
        )
        
        # Second migration should not migrate any books (already done)
        assert result2["books_migrated"] == 0, (
            "Second migration should not migrate any books"
        )


    @given(
        filename=book_filename_strategy,
        chapter_title=chapter_title_strategy,
        chapter_content=chapter_content_strategy,
        summary=st.text(min_size=1, max_size=500),
        word_count=word_count_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_chapter_migration_idempotent(self, test_db, migration_service,
                                           filename, chapter_title, chapter_content,
                                           summary, word_count):
        """
        Property: Running chapter migration twice SHALL produce the same result.
        
        **Validates: Requirements 9.6**
        
        For any chapter_summaries record, running migration multiple times
        must not create duplicate chapters.
        """
        from sqlalchemy import text
        
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        unique_title = f"{chapter_title}_{get_unique_suffix()}"
        
        # Create book and chapter_summary
        with test_db.begin() as conn:
            book_id = create_old_book(conn, unique_filename)
            create_old_chapter_summary(
                conn, book_id, unique_title, chapter_content,
                summary, word_count
            )
        
        # Run migration first time
        result1 = migration_service.run_migration()
        
        # Count chapters after first migration
        with test_db.begin() as conn:
            count_after_first = conn.execute(
                text("SELECT COUNT(*) FROM chapters WHERE book_id = :book_id"),
                {"book_id": book_id}
            ).scalar()
            
            chapters_after_first = conn.execute(
                text("SELECT * FROM chapters WHERE book_id = :book_id ORDER BY id"),
                {"book_id": book_id}
            ).mappings().all()
            chapters_after_first = [dict(c) for c in chapters_after_first]
        
        # Run migration second time
        result2 = migration_service.run_migration()
        
        # Count chapters after second migration
        with test_db.begin() as conn:
            count_after_second = conn.execute(
                text("SELECT COUNT(*) FROM chapters WHERE book_id = :book_id"),
                {"book_id": book_id}
            ).scalar()
            
            chapters_after_second = conn.execute(
                text("SELECT * FROM chapters WHERE book_id = :book_id ORDER BY id"),
                {"book_id": book_id}
            ).mappings().all()
            chapters_after_second = [dict(c) for c in chapters_after_second]
        
        # Property: chapter count should be identical
        assert count_after_first == count_after_second, (
            f"Chapter count should be identical. First: {count_after_first}, "
            f"Second: {count_after_second}"
        )
        
        # Property: chapter data should be identical
        assert chapters_after_first == chapters_after_second, (
            "Chapter data should be identical after running migration twice"
        )
        
        # Second migration should not migrate any chapters
        assert result2["chapters_migrated"] == 0, (
            "Second migration should not migrate any chapters"
        )


    @given(
        filename=book_filename_strategy,
        num_chapters=st.integers(min_value=1, max_value=5)
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_multiple_chapters_migration_idempotent(self, test_db, migration_service,
                                                     filename, num_chapters):
        """
        Property: Running migration on multiple chapters SHALL be idempotent.
        
        **Validates: Requirements 9.6**
        
        For any book with multiple chapters, running migration multiple times
        must produce the same final state.
        """
        from sqlalchemy import text
        
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        # Create book with multiple chapters
        with test_db.begin() as conn:
            book_id = create_old_book(conn, unique_filename)
            
            for i in range(num_chapters):
                create_old_chapter_summary(
                    conn, book_id,
                    chapter_title=f"Chapter_{i}_{get_unique_suffix()}",
                    chapter_content=f"Content for chapter {i}",
                    summary=f"Summary for chapter {i}",
                    word_count=100 * (i + 1)
                )
        
        # Run migration three times
        migration_service.run_migration()
        migration_service.run_migration()
        result3 = migration_service.run_migration()
        
        # Verify final state
        with test_db.begin() as conn:
            chapter_count = conn.execute(
                text("SELECT COUNT(*) FROM chapters WHERE book_id = :book_id"),
                {"book_id": book_id}
            ).scalar()
        
        # Property: should have exactly num_chapters chapters
        assert chapter_count == num_chapters, (
            f"Should have {num_chapters} chapters, got {chapter_count}"
        )
        
        # Third migration should not migrate anything
        assert result3["chapters_migrated"] == 0, (
            "Third migration should not migrate any chapters"
        )


# ============================================================================
# Property 26: Settings Functionality Preservation
# **Validates: Requirements 10.2**
# ============================================================================

class TestSettingsFunctionalityPreservationProperty:
    """
    Property-based tests for settings functionality preservation.
    
    Property 26: Settings Functionality Preservation
    *For any* key-value pair, storing and retrieving from settings SHALL
    work correctly after migration.
    
    **Validates: Requirements 10.2**
    """

    @given(
        key=settings_key_strategy,
        value=settings_value_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_store_and_retrieve_after_migration(self, test_db, migration_service,
                                                 settings_service, key, value):
        """
        Property: Storing and retrieving settings SHALL work after migration.
        
        **Validates: Requirements 10.2**
        
        For any key-value pair, after running migration, storing and
        retrieving the setting must return the same value.
        """
        unique_key = f"{key}_{get_unique_suffix()}"
        
        # Run migration first
        migration_service.run_migration()
        
        # Store setting after migration
        settings_service.store_setting(unique_key, value)
        
        # Retrieve setting
        retrieved_value = settings_service.load_setting(unique_key)
        
        # Property: retrieved value must equal stored value
        assert retrieved_value == value, (
            f"Retrieved value should equal stored value. "
            f"Expected '{value}', got '{retrieved_value}'"
        )


    @given(
        key=settings_key_strategy,
        value1=settings_value_strategy,
        value2=settings_value_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_update_setting_after_migration(self, test_db, migration_service,
                                            settings_service, key, value1, value2):
        """
        Property: Updating settings SHALL work correctly after migration.
        
        **Validates: Requirements 10.2**
        
        For any key and two values, after migration, updating a setting
        must correctly replace the old value with the new value.
        """
        unique_key = f"{key}_{get_unique_suffix()}"
        
        # Run migration
        migration_service.run_migration()
        
        # Store initial value
        settings_service.store_setting(unique_key, value1)
        
        # Update to new value
        settings_service.store_setting(unique_key, value2)
        
        # Retrieve and verify
        retrieved_value = settings_service.load_setting(unique_key)
        
        # Property: should have the updated value
        assert retrieved_value == value2, (
            f"Setting should be updated to new value. "
            f"Expected '{value2}', got '{retrieved_value}'"
        )

    @given(
        key=settings_key_strategy,
        default=settings_value_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_load_nonexistent_setting_returns_default(self, test_db, migration_service,
                                                       settings_service, key, default):
        """
        Property: Loading non-existent setting SHALL return default value.
        
        **Validates: Requirements 10.2**
        
        For any non-existent key, loading the setting with a default
        must return the default value.
        """
        unique_key = f"nonexistent_{key}_{get_unique_suffix()}"
        
        # Run migration
        migration_service.run_migration()
        
        # Load non-existent setting with default
        retrieved_value = settings_service.load_setting(unique_key, default)
        
        # Property: should return the default value
        assert retrieved_value == default, (
            f"Non-existent setting should return default. "
            f"Expected '{default}', got '{retrieved_value}'"
        )

    @given(
        keys_and_values=st.lists(
            st.tuples(settings_key_strategy, settings_value_strategy),
            min_size=1,
            max_size=10
        )
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_multiple_settings_preserved(self, test_db, migration_service,
                                          settings_service, keys_and_values):
        """
        Property: Multiple settings SHALL all be preserved after migration.
        
        **Validates: Requirements 10.2**
        
        For any set of key-value pairs, all settings must be correctly
        stored and retrieved after migration.
        """
        # Make keys unique
        unique_pairs = [
            (f"{k}_{get_unique_suffix()}", v) 
            for k, v in keys_and_values
        ]
        
        # Store all settings before migration
        for key, value in unique_pairs:
            settings_service.store_setting(key, value)
        
        # Run migration
        migration_service.run_migration()
        
        # Verify all settings are preserved
        for key, expected_value in unique_pairs:
            retrieved_value = settings_service.load_setting(key)
            assert retrieved_value == expected_value, (
                f"Setting '{key}' should be preserved. "
                f"Expected '{expected_value}', got '{retrieved_value}'"
            )
