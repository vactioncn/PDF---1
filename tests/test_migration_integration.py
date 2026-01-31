"""
Migration Integration Tests

Feature: database-restructure
Task 14.2: 编写迁移集成测试

**Validates: Requirements 9.1-9.6, 10.1-10.3**

This module tests the complete migration workflow with realistic data scenarios:
1. Migration with existing books, chapters, and interpretations
2. Data integrity after migration
3. Settings preservation
4. Migration idempotency with realistic data

These tests validate that migration works correctly with realistic data scenarios.
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
# Standalone Service Classes for Integration Testing
# ============================================================================

class StandaloneMigrationService:
    """
    Standalone MigrationService for testing.
    Mirrors the app.py MigrationService implementation.
    """
    
    def __init__(self, engine, uploads_dir: str):
        self.engine = engine
        self.uploads_dir = uploads_dir

    def run_migration(self) -> Dict:
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
def test_db_with_old_schema():
    """
    Create a temporary database with the OLD schema structure.
    This simulates a database before migration.
    """
    from sqlalchemy import create_engine, text
    
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    engine = create_engine(f'sqlite:///{db_path}')
    
    with engine.begin() as conn:
        # Create settings table (preserved during migration)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))

        # Create books table with OLD schema (missing new fields)
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
        
        # Create chapters table (new structure)
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
        
        # Create chapter_contents table (new structure)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chapter_contents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL UNIQUE,
                content TEXT,
                content_zh TEXT,
                FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
            )
        """))
        
        # Create chapter_summaries table (OLD structure for migration testing)
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

        # Create interpretations table with OLD schema
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
def migration_service(test_db_with_old_schema, temp_upload_dir):
    """Create a MigrationService instance for testing."""
    return StandaloneMigrationService(test_db_with_old_schema, temp_upload_dir)


@pytest.fixture(scope="function")
def settings_service(test_db_with_old_schema):
    """Create a SettingsService instance for testing."""
    return StandaloneSettingsService(test_db_with_old_schema)


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


def create_old_interpretation(conn, chapter_title: str, result_json: str,
                              user_profession: str = None, reading_goal: str = None,
                              focus: str = None, density: str = None,
                              chapter_text: str = None, master_prompt: str = None) -> int:
    """Create an interpretation record without book_id (old structure)."""
    from sqlalchemy import text
    
    cursor = conn.execute(
        text(
            """
            INSERT INTO interpretations (chapter_title, result_json, user_profession,
                                        reading_goal, focus, density, chapter_text,
                                        master_prompt, created_at)
            VALUES (:chapter_title, :result_json, :user_profession,
                   :reading_goal, :focus, :density, :chapter_text,
                   :master_prompt, :created_at)
            """
        ),
        {
            "chapter_title": chapter_title,
            "result_json": result_json,
            "user_profession": user_profession,
            "reading_goal": reading_goal,
            "focus": focus,
            "density": density,
            "chapter_text": chapter_text,
            "master_prompt": master_prompt,
            "created_at": datetime.utcnow().isoformat(),
        },
    )
    return cursor.lastrowid


# ============================================================================
# Test Class 1: Migration with Existing Data
# **Validates: Requirements 9.1-9.4**
# ============================================================================

class TestMigrationWithExistingData:
    """
    Integration tests for migration with existing books, chapters, and interpretations.
    
    **Validates: Requirements 9.1-9.4**
    """

    def test_migrate_single_book_with_chapters(self, test_db_with_old_schema, 
                                                migration_service):
        """
        Test migration of a single book with multiple chapters.
        
        **Validates: Requirements 9.1, 9.2**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create a book with old schema (missing new fields)
        with test_db_with_old_schema.begin() as conn:
            book_id = create_old_book(
                conn, 
                filename=f"test_book_{unique_suffix}.pdf",
                chapter_count=3,
                total_word_count=1500
            )
            
            # Create chapters in old chapter_summaries table
            for i in range(1, 4):
                create_old_chapter_summary(
                    conn,
                    book_id=book_id,
                    chapter_title=f"Chapter {i}: Test Title {unique_suffix}",
                    chapter_content=f"This is the content of chapter {i}. " * 50,
                    summary=f"Summary of chapter {i}",
                    word_count=500
                )
        
        # Run migration
        result = migration_service.run_migration()
        
        # Verify migration results
        assert result["books_migrated"] == 1, "Should migrate 1 book"
        assert result["chapters_migrated"] == 3, "Should migrate 3 chapters"
        assert len(result["errors"]) == 0, "Should have no errors"
        
        # Verify book has default values
        with test_db_with_old_schema.begin() as conn:
            book = conn.execute(
                text("SELECT * FROM books WHERE id = :id"),
                {"id": book_id}
            ).mappings().first()
            
            assert book["source_type"] == "upload", "source_type should default to 'upload'"
            assert book["language"] == "zh", "language should default to 'zh'"
            assert book["status"] == "ready", "status should default to 'ready'"
            
            # Verify chapters were migrated to new tables
            chapters = conn.execute(
                text("SELECT * FROM chapters WHERE book_id = :book_id ORDER BY chapter_index"),
                {"book_id": book_id}
            ).mappings().all()
            
            assert len(chapters) == 3, "Should have 3 chapters"
            
            # Verify chapter indices are sequential
            for i, chapter in enumerate(chapters, start=1):
                assert chapter["chapter_index"] == i, f"Chapter index should be {i}"
                
                # Verify chapter content exists
                content = conn.execute(
                    text("SELECT * FROM chapter_contents WHERE chapter_id = :id"),
                    {"id": chapter["id"]}
                ).mappings().first()
                
                assert content is not None, "Chapter content should exist"
                assert "content of chapter" in content["content"], "Content should be preserved"

    def test_migrate_book_with_translated_chapters(self, test_db_with_old_schema,
                                                    migration_service):
        """
        Test migration preserves translated chapter data.
        
        **Validates: Requirements 9.2**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create a book with translated chapters
        with test_db_with_old_schema.begin() as conn:
            book_id = create_old_book(
                conn,
                filename=f"translated_book_{unique_suffix}.pdf",
                chapter_count=2,
                total_word_count=1000
            )
            
            # Create translated chapter
            create_old_chapter_summary(
                conn,
                book_id=book_id,
                chapter_title=f"Introduction_{unique_suffix}",
                chapter_content="This is the introduction content in English.",
                chapter_title_zh=f"引言_{unique_suffix}",
                chapter_content_zh="这是英文的引言内容。",
                summary="Introduction summary",
                word_count=500
            )
            
            # Create untranslated chapter
            create_old_chapter_summary(
                conn,
                book_id=book_id,
                chapter_title=f"Chapter 1_{unique_suffix}",
                chapter_content="This is chapter 1 content.",
                summary="Chapter 1 summary",
                word_count=500
            )
        
        # Run migration
        result = migration_service.run_migration()
        
        assert result["chapters_migrated"] == 2, "Should migrate 2 chapters"
        
        # Verify translated chapter
        with test_db_with_old_schema.begin() as conn:
            chapters = conn.execute(
                text("SELECT * FROM chapters WHERE book_id = :book_id ORDER BY chapter_index"),
                {"book_id": book_id}
            ).mappings().all()
            
            # First chapter should be translated
            translated_chapter = chapters[0]
            assert translated_chapter["is_translated"] == 1, "Should be marked as translated"
            assert translated_chapter["title_zh"] is not None, "Should have Chinese title"
            assert translated_chapter["translated_at"] is not None, "Should have translated_at"
            
            # Second chapter should not be translated
            untranslated_chapter = chapters[1]
            assert untranslated_chapter["is_translated"] == 0, "Should not be marked as translated"
            
            # Verify content
            content = conn.execute(
                text("SELECT * FROM chapter_contents WHERE chapter_id = :id"),
                {"id": translated_chapter["id"]}
            ).mappings().first()
            
            assert content["content_zh"] is not None, "Should have Chinese content"

    def test_migrate_interpretations_with_matching_chapters(self, test_db_with_old_schema,
                                                             migration_service):
        """
        Test migration associates interpretations with books by matching chapter_title.
        
        **Validates: Requirements 9.3**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        chapter_title = f"Test Chapter_{unique_suffix}"
        
        # Create book and chapter
        with test_db_with_old_schema.begin() as conn:
            book_id = create_old_book(
                conn,
                filename=f"interp_test_book_{unique_suffix}.pdf"
            )
            
            create_old_chapter_summary(
                conn,
                book_id=book_id,
                chapter_title=chapter_title,
                chapter_content="Chapter content",
                summary="Chapter summary",
                word_count=100
            )
            
            # Create interpretation without book_id (old structure)
            interp_id = create_old_interpretation(
                conn,
                chapter_title=chapter_title,
                result_json='{"summary": "Test interpretation"}',
                user_profession="软件工程师",
                reading_goal="提升技术能力"
            )
        
        # Run migration
        result = migration_service.run_migration()
        
        assert result["interpretations_migrated"] == 1, "Should migrate 1 interpretation"
        assert len(result["interpretations_unmatched"]) == 0, "Should have no unmatched"
        
        # Verify interpretation was associated with book
        with test_db_with_old_schema.begin() as conn:
            interp = conn.execute(
                text("SELECT * FROM interpretations WHERE id = :id"),
                {"id": interp_id}
            ).mappings().first()
            
            assert interp["book_id"] == book_id, "Should be associated with book"
            assert interp["chapter_id"] is not None, "Should be associated with chapter"

    def test_migrate_unmatched_interpretations_logged(self, test_db_with_old_schema,
                                                       migration_service):
        """
        Test that unmatched interpretations are logged for manual review.
        
        **Validates: Requirements 9.4**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create interpretation without matching chapter
        with test_db_with_old_schema.begin() as conn:
            interp_id = create_old_interpretation(
                conn,
                chapter_title=f"NonExistent Chapter_{unique_suffix}",
                result_json='{"summary": "Orphan interpretation"}'
            )
        
        # Run migration
        result = migration_service.run_migration()
        
        assert result["interpretations_migrated"] == 0, "Should not migrate any"
        assert interp_id in result["interpretations_unmatched"], "Should log unmatched ID"
        
        # Verify interpretation still exists but without book_id
        with test_db_with_old_schema.begin() as conn:
            interp = conn.execute(
                text("SELECT * FROM interpretations WHERE id = :id"),
                {"id": interp_id}
            ).mappings().first()
            
            assert interp is not None, "Interpretation should still exist"
            assert interp["book_id"] is None, "book_id should still be NULL"


# ============================================================================
# Test Class 2: Data Integrity After Migration
# **Validates: Requirements 9.1, 9.2**
# ============================================================================

class TestDataIntegrityAfterMigration:
    """
    Integration tests for data integrity verification after migration.
    
    **Validates: Requirements 9.1, 9.2**
    """

    def test_all_book_data_preserved(self, test_db_with_old_schema, migration_service):
        """
        Test that all book data is preserved after migration.
        
        **Validates: Requirements 9.1**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create multiple books with various data
        books_data = [
            {"filename": f"book1_{unique_suffix}.pdf", "chapter_count": 5, "word_count": 2500},
            {"filename": f"book2_{unique_suffix}.pdf", "chapter_count": 10, "word_count": 5000},
            {"filename": f"book3_{unique_suffix}.pdf", "chapter_count": 3, "word_count": 1500},
        ]
        
        book_ids = []
        with test_db_with_old_schema.begin() as conn:
            for data in books_data:
                book_id = create_old_book(
                    conn,
                    filename=data["filename"],
                    chapter_count=data["chapter_count"],
                    total_word_count=data["word_count"]
                )
                book_ids.append(book_id)
        
        # Run migration
        result = migration_service.run_migration()
        
        assert result["books_migrated"] == 3, "Should migrate 3 books"
        
        # Verify all books exist with preserved data
        with test_db_with_old_schema.begin() as conn:
            for i, book_id in enumerate(book_ids):
                book = conn.execute(
                    text("SELECT * FROM books WHERE id = :id"),
                    {"id": book_id}
                ).mappings().first()
                
                assert book is not None, f"Book {book_id} should exist"
                assert book["filename"] == books_data[i]["filename"], "Filename preserved"
                assert book["chapter_count"] == books_data[i]["chapter_count"], "Chapter count preserved"
                assert book["total_word_count"] == books_data[i]["word_count"], "Word count preserved"

    def test_chapter_content_integrity(self, test_db_with_old_schema, migration_service):
        """
        Test that chapter content is correctly split and preserved.
        
        **Validates: Requirements 9.2**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create book with chapters containing specific content
        original_content = "This is the original chapter content with specific text. " * 20
        original_content_zh = "这是原始章节内容，包含特定文本。" * 20
        original_summary = "This is the original summary."
        
        with test_db_with_old_schema.begin() as conn:
            book_id = create_old_book(conn, filename=f"integrity_test_{unique_suffix}.pdf")
            
            create_old_chapter_summary(
                conn,
                book_id=book_id,
                chapter_title=f"Integrity Test Chapter_{unique_suffix}",
                chapter_content=original_content,
                chapter_title_zh=f"完整性测试章节_{unique_suffix}",
                chapter_content_zh=original_content_zh,
                summary=original_summary,
                word_count=len(original_content.split())
            )
        
        # Run migration
        migration_service.run_migration()
        
        # Verify content integrity
        with test_db_with_old_schema.begin() as conn:
            chapter = conn.execute(
                text("SELECT * FROM chapters WHERE book_id = :book_id"),
                {"book_id": book_id}
            ).mappings().first()
            
            assert chapter["summary"] == original_summary, "Summary should be preserved"
            
            content = conn.execute(
                text("SELECT * FROM chapter_contents WHERE chapter_id = :id"),
                {"id": chapter["id"]}
            ).mappings().first()
            
            assert content["content"] == original_content, "Content should be exactly preserved"
            assert content["content_zh"] == original_content_zh, "Chinese content should be preserved"

    def test_multiple_books_with_chapters_integrity(self, test_db_with_old_schema,
                                                     migration_service):
        """
        Test migration integrity with multiple books each having multiple chapters.
        
        **Validates: Requirements 9.1, 9.2**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create 3 books with 4 chapters each
        with test_db_with_old_schema.begin() as conn:
            for book_num in range(1, 4):
                book_id = create_old_book(
                    conn,
                    filename=f"multi_book_{book_num}_{unique_suffix}.pdf",
                    chapter_count=4,
                    total_word_count=2000
                )
                
                for ch_num in range(1, 5):
                    create_old_chapter_summary(
                        conn,
                        book_id=book_id,
                        chapter_title=f"Book{book_num}_Chapter{ch_num}_{unique_suffix}",
                        chapter_content=f"Content for book {book_num}, chapter {ch_num}",
                        summary=f"Summary for book {book_num}, chapter {ch_num}",
                        word_count=500
                    )
        
        # Run migration
        result = migration_service.run_migration()
        
        assert result["books_migrated"] == 3, "Should migrate 3 books"
        assert result["chapters_migrated"] == 12, "Should migrate 12 chapters (3 books * 4 chapters)"
        
        # Verify each book has correct number of chapters
        with test_db_with_old_schema.begin() as conn:
            books = conn.execute(text("SELECT * FROM books")).mappings().all()
            
            for book in books:
                chapters = conn.execute(
                    text("SELECT * FROM chapters WHERE book_id = :book_id"),
                    {"book_id": book["id"]}
                ).mappings().all()
                
                assert len(chapters) == 4, f"Book {book['id']} should have 4 chapters"
                
                # Verify each chapter has content
                for chapter in chapters:
                    content = conn.execute(
                        text("SELECT * FROM chapter_contents WHERE chapter_id = :id"),
                        {"id": chapter["id"]}
                    ).mappings().first()
                    
                    assert content is not None, f"Chapter {chapter['id']} should have content"


# ============================================================================
# Test Class 3: Settings Preservation
# **Validates: Requirements 10.1-10.3**
# ============================================================================

class TestSettingsPreservation:
    """
    Integration tests for settings preservation during migration.
    
    **Validates: Requirements 10.1-10.3**
    """

    def test_existing_settings_preserved(self, test_db_with_old_schema, 
                                          migration_service, settings_service):
        """
        Test that existing settings are not modified during migration.
        
        **Validates: Requirements 10.3**
        """
        unique_suffix = get_unique_suffix()
        
        # Create settings before migration
        settings_data = {
            f"api_key_{unique_suffix}": "sk-test-key-12345",
            f"master_prompt_{unique_suffix}": "You are a helpful assistant...",
            f"model_name_{unique_suffix}": "doubao-seed-1-6-251015",
        }
        
        for key, value in settings_data.items():
            settings_service.store_setting(key, value)
        
        # Run migration
        migration_service.run_migration()
        
        # Verify all settings are unchanged
        for key, expected_value in settings_data.items():
            actual_value = settings_service.load_setting(key)
            assert actual_value == expected_value, f"Setting '{key}' should be preserved"

    def test_settings_functionality_after_migration(self, test_db_with_old_schema,
                                                     migration_service, settings_service):
        """
        Test that settings can be stored and retrieved after migration.
        
        **Validates: Requirements 10.2**
        """
        unique_suffix = get_unique_suffix()
        
        # Run migration first
        migration_service.run_migration()
        
        # Store new settings after migration
        new_settings = {
            f"new_key_1_{unique_suffix}": "new_value_1",
            f"new_key_2_{unique_suffix}": "new_value_2",
        }
        
        for key, value in new_settings.items():
            settings_service.store_setting(key, value)
        
        # Verify settings can be retrieved
        for key, expected_value in new_settings.items():
            actual_value = settings_service.load_setting(key)
            assert actual_value == expected_value, f"New setting '{key}' should work"

    def test_settings_update_after_migration(self, test_db_with_old_schema,
                                              migration_service, settings_service):
        """
        Test that settings can be updated after migration.
        
        **Validates: Requirements 10.2**
        """
        unique_suffix = get_unique_suffix()
        key = f"updatable_setting_{unique_suffix}"
        
        # Store initial value
        settings_service.store_setting(key, "initial_value")
        
        # Run migration
        migration_service.run_migration()
        
        # Update setting
        settings_service.store_setting(key, "updated_value")
        
        # Verify update worked
        actual_value = settings_service.load_setting(key)
        assert actual_value == "updated_value", "Setting should be updated"

    def test_settings_table_structure_preserved(self, test_db_with_old_schema,
                                                 migration_service):
        """
        Test that settings table structure is preserved.
        
        **Validates: Requirements 10.1**
        """
        from sqlalchemy import text
        
        # Run migration
        migration_service.run_migration()
        
        # Verify settings table structure
        with test_db_with_old_schema.begin() as conn:
            # Check table exists
            tables = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
            ).fetchall()
            
            assert len(tables) == 1, "Settings table should exist"
            
            # Check columns
            columns = conn.execute(
                text("PRAGMA table_info(settings)")
            ).fetchall()
            
            column_names = [col[1] for col in columns]
            assert "key" in column_names, "Should have 'key' column"
            assert "value" in column_names, "Should have 'value' column"
            assert "updated_at" in column_names, "Should have 'updated_at' column"


# ============================================================================
# Test Class 4: Migration Idempotency with Realistic Data
# **Validates: Requirements 9.6**
# ============================================================================

class TestMigrationIdempotency:
    """
    Integration tests for migration idempotency with realistic data scenarios.
    
    **Validates: Requirements 9.6**
    """

    def test_migration_idempotent_with_books(self, test_db_with_old_schema,
                                              migration_service):
        """
        Test that running migration multiple times produces same result for books.
        
        **Validates: Requirements 9.6**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create books
        with test_db_with_old_schema.begin() as conn:
            for i in range(3):
                create_old_book(
                    conn,
                    filename=f"idempotent_book_{i}_{unique_suffix}.pdf",
                    chapter_count=i + 1,
                    total_word_count=(i + 1) * 500
                )
        
        # Run migration first time
        result1 = migration_service.run_migration()
        
        # Get state after first migration
        with test_db_with_old_schema.begin() as conn:
            books_after_first = conn.execute(
                text("SELECT * FROM books ORDER BY id")
            ).mappings().all()
            books_after_first = [dict(b) for b in books_after_first]
        
        # Run migration second time
        result2 = migration_service.run_migration()
        
        # Get state after second migration
        with test_db_with_old_schema.begin() as conn:
            books_after_second = conn.execute(
                text("SELECT * FROM books ORDER BY id")
            ).mappings().all()
            books_after_second = [dict(b) for b in books_after_second]
        
        # Verify idempotency
        assert result1["books_migrated"] == 3, "First migration should migrate 3 books"
        assert result2["books_migrated"] == 0, "Second migration should migrate 0 books"
        assert books_after_first == books_after_second, "Book state should be identical"

    def test_migration_idempotent_with_chapters(self, test_db_with_old_schema,
                                                 migration_service):
        """
        Test that running migration multiple times doesn't duplicate chapters.
        
        **Validates: Requirements 9.6**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create book with chapters
        with test_db_with_old_schema.begin() as conn:
            book_id = create_old_book(conn, filename=f"idempotent_chapters_{unique_suffix}.pdf")
            
            for i in range(5):
                create_old_chapter_summary(
                    conn,
                    book_id=book_id,
                    chapter_title=f"Chapter_{i}_{unique_suffix}",
                    chapter_content=f"Content for chapter {i}",
                    summary=f"Summary for chapter {i}",
                    word_count=100
                )
        
        # Run migration three times
        result1 = migration_service.run_migration()
        result2 = migration_service.run_migration()
        result3 = migration_service.run_migration()
        
        # Verify chapter counts
        with test_db_with_old_schema.begin() as conn:
            chapter_count = conn.execute(
                text("SELECT COUNT(*) FROM chapters WHERE book_id = :book_id"),
                {"book_id": book_id}
            ).scalar()
        
        assert result1["chapters_migrated"] == 5, "First migration should migrate 5 chapters"
        assert result2["chapters_migrated"] == 0, "Second migration should migrate 0 chapters"
        assert result3["chapters_migrated"] == 0, "Third migration should migrate 0 chapters"
        assert chapter_count == 5, "Should have exactly 5 chapters"

    def test_migration_idempotent_with_mixed_data(self, test_db_with_old_schema,
                                                   migration_service, settings_service):
        """
        Test migration idempotency with a realistic mixed data scenario.
        
        **Validates: Requirements 9.6**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create realistic data scenario
        with test_db_with_old_schema.begin() as conn:
            # Create multiple books
            book1_id = create_old_book(
                conn,
                filename=f"programming_book_{unique_suffix}.pdf",
                chapter_count=10,
                total_word_count=50000
            )
            
            book2_id = create_old_book(
                conn,
                filename=f"design_patterns_{unique_suffix}.pdf",
                chapter_count=23,
                total_word_count=80000
            )
            
            # Create chapters for book 1
            chapter_titles_book1 = []
            for i in range(1, 11):
                title = f"Programming Chapter {i}_{unique_suffix}"
                chapter_titles_book1.append(title)
                create_old_chapter_summary(
                    conn,
                    book_id=book1_id,
                    chapter_title=title,
                    chapter_content=f"Programming content for chapter {i}. " * 100,
                    chapter_title_zh=f"编程第{i}章_{unique_suffix}",
                    chapter_content_zh=f"第{i}章的编程内容。" * 100,
                    summary=f"Summary of programming chapter {i}",
                    word_count=5000
                )
            
            # Create chapters for book 2 (some translated, some not)
            for i in range(1, 24):
                title = f"Design Pattern {i}_{unique_suffix}"
                if i <= 15:  # First 15 chapters translated
                    create_old_chapter_summary(
                        conn,
                        book_id=book2_id,
                        chapter_title=title,
                        chapter_content=f"Design pattern content {i}. " * 80,
                        chapter_title_zh=f"设计模式{i}_{unique_suffix}",
                        chapter_content_zh=f"设计模式{i}的内容。" * 80,
                        summary=f"Summary of design pattern {i}",
                        word_count=3500
                    )
                else:  # Last 8 chapters not translated
                    create_old_chapter_summary(
                        conn,
                        book_id=book2_id,
                        chapter_title=title,
                        chapter_content=f"Design pattern content {i}. " * 80,
                        summary=f"Summary of design pattern {i}",
                        word_count=3500
                    )
            
            # Create interpretations
            for title in chapter_titles_book1[:5]:  # 5 interpretations for book 1
                create_old_interpretation(
                    conn,
                    chapter_title=title,
                    result_json='{"summary": "Test interpretation"}',
                    user_profession="软件工程师"
                )
        
        # Store settings
        settings_service.store_setting(f"api_key_{unique_suffix}", "test-api-key")
        settings_service.store_setting(f"prompt_{unique_suffix}", "Test prompt content")
        
        # Run migration multiple times
        result1 = migration_service.run_migration()
        result2 = migration_service.run_migration()
        result3 = migration_service.run_migration()
        
        # Verify final state
        with test_db_with_old_schema.begin() as conn:
            total_books = conn.execute(text("SELECT COUNT(*) FROM books")).scalar()
            total_chapters = conn.execute(text("SELECT COUNT(*) FROM chapters")).scalar()
            total_contents = conn.execute(text("SELECT COUNT(*) FROM chapter_contents")).scalar()
            
            # Verify translated chapters
            translated_count = conn.execute(
                text("SELECT COUNT(*) FROM chapters WHERE is_translated = 1")
            ).scalar()
        
        # Assertions
        assert total_books == 2, "Should have 2 books"
        assert total_chapters == 33, "Should have 33 chapters (10 + 23)"
        assert total_contents == 33, "Each chapter should have content"
        assert translated_count == 25, "Should have 25 translated chapters (10 + 15)"
        
        # Verify idempotency
        assert result1["books_migrated"] == 2
        assert result1["chapters_migrated"] == 33
        assert result1["interpretations_migrated"] == 5
        
        assert result2["books_migrated"] == 0
        assert result2["chapters_migrated"] == 0
        assert result2["interpretations_migrated"] == 0
        
        assert result3["books_migrated"] == 0
        assert result3["chapters_migrated"] == 0
        assert result3["interpretations_migrated"] == 0
        
        # Verify settings preserved
        assert settings_service.load_setting(f"api_key_{unique_suffix}") == "test-api-key"
        assert settings_service.load_setting(f"prompt_{unique_suffix}") == "Test prompt content"


# ============================================================================
# Test Class 5: Upload Directory Creation
# **Validates: Requirements 9.5**
# ============================================================================

class TestUploadDirectoryCreation:
    """
    Integration tests for upload directory creation during migration.
    
    **Validates: Requirements 9.5**
    """

    def test_creates_upload_directory_if_not_exists(self, test_db_with_old_schema,
                                                     temp_upload_dir):
        """
        Test that migration creates uploads directory if it doesn't exist.
        
        **Validates: Requirements 9.5**
        """
        # Use a non-existent directory
        new_upload_dir = os.path.join(temp_upload_dir, "new_uploads")
        
        assert not os.path.exists(new_upload_dir), "Directory should not exist initially"
        
        # Create migration service with new directory
        migration_service = StandaloneMigrationService(
            test_db_with_old_schema, 
            new_upload_dir
        )
        
        # Run migration
        migration_service.run_migration()
        
        # Verify directory was created
        assert os.path.exists(new_upload_dir), "Directory should be created"
        assert os.path.isdir(new_upload_dir), "Should be a directory"

    def test_does_not_fail_if_directory_exists(self, test_db_with_old_schema,
                                                temp_upload_dir):
        """
        Test that migration doesn't fail if uploads directory already exists.
        
        **Validates: Requirements 9.5**
        """
        # Directory already exists (from fixture)
        assert os.path.exists(temp_upload_dir), "Directory should exist"
        
        migration_service = StandaloneMigrationService(
            test_db_with_old_schema,
            temp_upload_dir
        )
        
        # Run migration - should not raise
        result = migration_service.run_migration()
        
        assert len(result["errors"]) == 0, "Should have no errors"


# ============================================================================
# Test Class 6: Edge Cases and Error Handling
# **Validates: Requirements 9.1-9.6**
# ============================================================================

class TestMigrationEdgeCases:
    """
    Integration tests for migration edge cases and error handling.
    
    **Validates: Requirements 9.1-9.6**
    """

    def test_migration_with_empty_database(self, test_db_with_old_schema,
                                            migration_service):
        """
        Test migration on an empty database.
        
        **Validates: Requirements 9.6**
        """
        # Run migration on empty database
        result = migration_service.run_migration()
        
        assert result["books_migrated"] == 0
        assert result["chapters_migrated"] == 0
        assert result["interpretations_migrated"] == 0
        assert len(result["interpretations_unmatched"]) == 0
        assert len(result["errors"]) == 0

    def test_migration_with_book_without_chapters(self, test_db_with_old_schema,
                                                   migration_service):
        """
        Test migration of a book that has no chapters.
        
        **Validates: Requirements 9.1**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create book without chapters
        with test_db_with_old_schema.begin() as conn:
            book_id = create_old_book(
                conn,
                filename=f"empty_book_{unique_suffix}.pdf",
                chapter_count=0,
                total_word_count=0
            )
        
        # Run migration
        result = migration_service.run_migration()
        
        assert result["books_migrated"] == 1, "Should migrate the book"
        assert result["chapters_migrated"] == 0, "Should have no chapters to migrate"
        
        # Verify book has default values
        with test_db_with_old_schema.begin() as conn:
            book = conn.execute(
                text("SELECT * FROM books WHERE id = :id"),
                {"id": book_id}
            ).mappings().first()
            
            assert book["source_type"] == "upload"
            assert book["language"] == "zh"
            assert book["status"] == "ready"

    def test_migration_with_special_characters_in_content(self, test_db_with_old_schema,
                                                          migration_service):
        """
        Test migration preserves special characters in content.
        
        **Validates: Requirements 9.2**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Content with special characters
        special_content = """
        This content has special characters:
        - Unicode: 你好世界 🎉 émojis
        - Quotes: "double" and 'single'
        - HTML-like: <tag>content</tag>
        - SQL-like: SELECT * FROM table WHERE id = 1;
        - Newlines and tabs:\t\n\r
        - Backslashes: C:\\path\\to\\file
        """
        
        with test_db_with_old_schema.begin() as conn:
            book_id = create_old_book(conn, filename=f"special_chars_{unique_suffix}.pdf")
            
            create_old_chapter_summary(
                conn,
                book_id=book_id,
                chapter_title=f"Special Chapter_{unique_suffix}",
                chapter_content=special_content,
                summary="Summary with special chars: 你好 <test>",
                word_count=100
            )
        
        # Run migration
        migration_service.run_migration()
        
        # Verify content preserved
        with test_db_with_old_schema.begin() as conn:
            chapter = conn.execute(
                text("SELECT * FROM chapters WHERE book_id = :book_id"),
                {"book_id": book_id}
            ).mappings().first()
            
            content = conn.execute(
                text("SELECT * FROM chapter_contents WHERE chapter_id = :id"),
                {"id": chapter["id"]}
            ).mappings().first()
            
            assert content["content"] == special_content, "Special characters should be preserved"

    def test_migration_with_large_content(self, test_db_with_old_schema,
                                           migration_service):
        """
        Test migration handles large content correctly.
        
        **Validates: Requirements 9.2**
        """
        from sqlalchemy import text
        
        unique_suffix = get_unique_suffix()
        
        # Create large content (simulating a real chapter)
        large_content = "This is a paragraph of text. " * 5000  # ~150KB
        
        with test_db_with_old_schema.begin() as conn:
            book_id = create_old_book(conn, filename=f"large_content_{unique_suffix}.pdf")
            
            create_old_chapter_summary(
                conn,
                book_id=book_id,
                chapter_title=f"Large Chapter_{unique_suffix}",
                chapter_content=large_content,
                summary="Summary of large chapter",
                word_count=len(large_content.split())
            )
        
        # Run migration
        result = migration_service.run_migration()
        
        assert result["chapters_migrated"] == 1
        
        # Verify content preserved
        with test_db_with_old_schema.begin() as conn:
            chapter = conn.execute(
                text("SELECT * FROM chapters WHERE book_id = :book_id"),
                {"book_id": book_id}
            ).mappings().first()
            
            content = conn.execute(
                text("SELECT * FROM chapter_contents WHERE chapter_id = :id"),
                {"id": chapter["id"]}
            ).mappings().first()
            
            assert content["content"] == large_content, "Large content should be preserved"
            assert len(content["content"]) == len(large_content)
