"""
Property-Based Tests for ChapterService

Feature: database-restructure
Properties 12-16: Chapter Management Properties

**Validates: Requirements 4.1-4.6, 5.1-5.3**

This module tests the following properties:
- Property 12: Chapter Data Separation - metadata in chapters table, content in chapter_contents
- Property 13: Chapter Index Sequencing - sequential chapter indices from 1 to N
- Property 14: Translation Status Tracking - is_translated flag and translated_at timestamp
- Property 15: Chapter Cascade Deletion - deleting chapter deletes its content
- Property 16: Restructure Mapping JSON Round-Trip - source_chapter_ids JSON serialization
"""
import os
import sys
import json
import tempfile
import uuid
import pytest
from hypothesis import given, strategies as st, settings, assume, HealthCheck
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
# Test Data Generators (Strategies)
# ============================================================================

# Title generator - non-empty text
title_strategy = st.text(
    min_size=1,
    max_size=200,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S'))
).filter(lambda x: len(x.strip()) >= 1 and '\x00' not in x)

# Content generator - text of various sizes
content_strategy = st.text(
    min_size=1,
    max_size=5000,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S', 'Z'))
).filter(lambda x: len(x.strip()) >= 1 and '\x00' not in x)

# Summary generator
summary_strategy = st.text(
    max_size=1000,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S', 'Z'))
).filter(lambda x: '\x00' not in x)

# Word count generator
word_count_strategy = st.integers(min_value=0, max_value=100000)

# Chapter index generator
chapter_index_strategy = st.integers(min_value=1, max_value=1000)

# Number of chapters generator (for sequencing tests)
num_chapters_strategy = st.integers(min_value=1, max_value=20)

# Source chapter IDs generator - list of positive integers
source_chapter_ids_strategy = st.lists(
    st.integers(min_value=1, max_value=10000),
    min_size=1,
    max_size=20
)

# Filename generator
filename_strategy = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(whitelist_categories=('L', 'N'))
).filter(lambda x: len(x.strip()) >= 1)


# ============================================================================
# Service Classes - Standalone implementations for testing
# ============================================================================

class StandaloneChapterService:
    """Standalone ChapterService for testing, mirrors app.py implementation."""
    
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
            # 创建章节元数据
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
            
            # 创建章节内容
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
            # 更新章节元数据
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
            
            # 更新章节内容
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

    
    def list_chapters(self, book_id: int, include_content: bool = False) -> List[Dict]:
        """列出书籍的所有章节"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            if include_content:
                results = conn.execute(
                    text(
                        """
                        SELECT c.*, cc.content, cc.content_zh
                        FROM chapters c
                        LEFT JOIN chapter_contents cc ON c.id = cc.chapter_id
                        WHERE c.book_id = :book_id
                        ORDER BY c.chapter_index
                        """
                    ),
                    {"book_id": book_id}
                ).mappings().all()
            else:
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
    
    def delete_chapter(self, chapter_id: int) -> bool:
        """删除章节（级联删除内容）"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM chapters WHERE id = :chapter_id"),
                {"chapter_id": chapter_id}
            )
        return True
    
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


class StandaloneBookService:
    """Standalone BookService for testing."""
    
    def __init__(self, engine):
        self.engine = engine
    
    def create_book(self, filename: str, source_type: str = 'upload',
                   language: str = 'zh') -> int:
        """创建书籍记录，返回 book_id"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO books (filename, source_type, language, 
                                      status, chapter_count, total_word_count, created_at)
                    VALUES (:filename, :source_type, :language,
                            'parsing', 0, 0, :created_at)
                    """
                ),
                {
                    "filename": filename,
                    "source_type": source_type,
                    "language": language,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            return cursor.lastrowid


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def test_db():
    """Create a temporary database for testing with all required tables."""
    from sqlalchemy import create_engine, text
    
    # Create a temporary database file
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    # Create engine
    engine = create_engine(f'sqlite:///{db_path}')
    
    # Initialize database schema
    with engine.begin() as conn:
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
        
        # Create chapter_contents table with cascade delete
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
        
        # Enable foreign keys for SQLite
        conn.execute(text("PRAGMA foreign_keys = ON"))
    
    yield engine
    
    # Cleanup
    engine.dispose()
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture(scope="function")
def chapter_service(test_db):
    """Get ChapterService instance for testing."""
    return StandaloneChapterService(test_db)


@pytest.fixture(scope="function")
def book_service(test_db):
    """Get BookService instance for testing."""
    return StandaloneBookService(test_db)


# ============================================================================
# Property 12: Chapter Data Separation
# **Validates: Requirements 4.1, 4.2**
# ============================================================================

class TestChapterDataSeparationProperty:
    """
    Property-based tests for chapter data separation.
    
    Property 12: Chapter Data Separation
    *For any* chapter, the metadata (title, title_zh, summary, word_count, 
    is_translated) SHALL be stored in the chapters table, AND the content 
    (content, content_zh) SHALL be stored in the chapter_contents table 
    with a foreign key reference.
    
    **Validates: Requirements 4.1, 4.2**
    """

    @given(
        title=title_strategy,
        content=content_strategy,
        word_count=word_count_strategy,
        summary=summary_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_metadata_stored_in_chapters_table(self, test_db, chapter_service, 
                                                book_service, title, content, 
                                                word_count, summary):
        """
        Property: Chapter metadata SHALL be stored in the chapters table.
        
        **Validates: Requirements 4.1**
        
        For any chapter, title, title_zh, summary, word_count, is_translated
        must be stored in the chapters table.
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create chapter
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content,
            word_count=word_count,
            summary=summary
        )
        
        # Query chapters table directly
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT * FROM chapters WHERE id = :chapter_id"),
                {"chapter_id": chapter_id}
            ).mappings().first()
        
        # Verify metadata is in chapters table
        assert result is not None, "Chapter should exist in chapters table"
        assert result['title'] == title, "Title should be stored in chapters table"
        assert result['word_count'] == word_count, "Word count should be stored in chapters table"
        assert result['summary'] == summary, "Summary should be stored in chapters table"
        assert result['is_translated'] in (0, 1), "is_translated should be 0 or 1"


    @given(
        title=title_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_content_stored_in_chapter_contents_table(self, test_db, chapter_service,
                                                       book_service, title, content):
        """
        Property: Chapter content SHALL be stored in the chapter_contents table.
        
        **Validates: Requirements 4.2**
        
        For any chapter, content and content_zh must be stored in the 
        chapter_contents table with a foreign key reference.
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create chapter
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content
        )
        
        # Query chapter_contents table directly
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT * FROM chapter_contents WHERE chapter_id = :chapter_id"),
                {"chapter_id": chapter_id}
            ).mappings().first()
        
        # Verify content is in chapter_contents table
        assert result is not None, "Content should exist in chapter_contents table"
        assert result['content'] == content, "Content should be stored in chapter_contents table"
        assert result['chapter_id'] == chapter_id, "Foreign key reference should be correct"

    @given(
        title=title_strategy,
        title_zh=title_strategy,
        content=content_strategy,
        content_zh=content_strategy,
        summary=summary_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_translated_chapter_data_separation(self, test_db, chapter_service,
                                                 book_service, title, title_zh,
                                                 content, content_zh, summary):
        """
        Property: Translated chapter data SHALL be properly separated.
        
        **Validates: Requirements 4.1, 4.2**
        
        For any translated chapter, title_zh should be in chapters table,
        content_zh should be in chapter_contents table.
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create chapter with translation
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content,
            title_zh=title_zh,
            content_zh=content_zh,
            summary=summary
        )
        
        # Query both tables
        with test_db.begin() as conn:
            chapter_result = conn.execute(
                text("SELECT * FROM chapters WHERE id = :chapter_id"),
                {"chapter_id": chapter_id}
            ).mappings().first()
            
            content_result = conn.execute(
                text("SELECT * FROM chapter_contents WHERE chapter_id = :chapter_id"),
                {"chapter_id": chapter_id}
            ).mappings().first()
        
        # Verify separation
        assert chapter_result['title_zh'] == title_zh, "title_zh should be in chapters table"
        assert content_result['content_zh'] == content_zh, "content_zh should be in chapter_contents table"


# ============================================================================
# Property 13: Chapter Index Sequencing
# **Validates: Requirements 4.3**
# ============================================================================

class TestChapterIndexSequencingProperty:
    """
    Property-based tests for chapter index sequencing.
    
    Property 13: Chapter Index Sequencing
    *For any* book with N chapters, the chapter_index values SHALL be 
    sequential integers from 1 to N with no gaps or duplicates.
    
    **Validates: Requirements 4.3**
    """

    @given(num_chapters=num_chapters_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_sequential_chapter_indices(self, test_db, chapter_service,
                                        book_service, num_chapters):
        """
        Property: Chapter indices SHALL be sequential from 1 to N.
        
        **Validates: Requirements 4.3**
        
        For any book with N chapters created with sequential indices,
        the indices should be 1, 2, 3, ..., N with no gaps.
        """
        # Create a book
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create N chapters with sequential indices
        for i in range(1, num_chapters + 1):
            chapter_service.create_chapter(
                book_id=book_id,
                chapter_index=i,
                title=f"Chapter {i}",
                content=f"Content for chapter {i}"
            )
        
        # Get all chapters
        chapters = chapter_service.list_chapters(book_id)
        
        # Verify count
        assert len(chapters) == num_chapters, f"Expected {num_chapters} chapters, got {len(chapters)}"
        
        # Verify sequential indices
        indices = [ch['chapter_index'] for ch in chapters]
        expected_indices = list(range(1, num_chapters + 1))
        
        assert sorted(indices) == expected_indices, (
            f"Indices should be sequential from 1 to {num_chapters}, got {sorted(indices)}"
        )


    @given(num_chapters=num_chapters_strategy)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_no_duplicate_indices(self, test_db, chapter_service,
                                  book_service, num_chapters):
        """
        Property: Chapter indices SHALL have no duplicates.
        
        **Validates: Requirements 4.3**
        
        For any book, each chapter_index value should be unique.
        """
        # Create a book
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create N chapters with sequential indices
        for i in range(1, num_chapters + 1):
            chapter_service.create_chapter(
                book_id=book_id,
                chapter_index=i,
                title=f"Chapter {i}",
                content=f"Content for chapter {i}"
            )
        
        # Get all chapters
        chapters = chapter_service.list_chapters(book_id)
        indices = [ch['chapter_index'] for ch in chapters]
        
        # Verify no duplicates
        assert len(indices) == len(set(indices)), (
            f"Found duplicate indices: {indices}"
        )

    @given(num_chapters=num_chapters_strategy)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_chapters_ordered_by_index(self, test_db, chapter_service,
                                       book_service, num_chapters):
        """
        Property: Chapters SHALL be returned ordered by chapter_index.
        
        **Validates: Requirements 4.3**
        
        For any book, list_chapters should return chapters in order.
        """
        # Create a book
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create chapters in reverse order to test ordering
        for i in range(num_chapters, 0, -1):
            chapter_service.create_chapter(
                book_id=book_id,
                chapter_index=i,
                title=f"Chapter {i}",
                content=f"Content for chapter {i}"
            )
        
        # Get all chapters
        chapters = chapter_service.list_chapters(book_id)
        indices = [ch['chapter_index'] for ch in chapters]
        
        # Verify ordering
        assert indices == sorted(indices), (
            f"Chapters should be ordered by index, got {indices}"
        )


# ============================================================================
# Property 14: Translation Status Tracking
# **Validates: Requirements 4.4, 4.5**
# ============================================================================

class TestTranslationStatusTrackingProperty:
    """
    Property-based tests for translation status tracking.
    
    Property 14: Translation Status Tracking
    *For any* chapter, is_translated SHALL be 0 or 1, AND when is_translated 
    changes from 0 to 1, translated_at SHALL be set to a non-null timestamp.
    
    **Validates: Requirements 4.4, 4.5**
    """

    @given(
        title=title_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_untranslated_chapter_status(self, test_db, chapter_service,
                                          book_service, title, content):
        """
        Property: Untranslated chapters SHALL have is_translated=0.
        
        **Validates: Requirements 4.4**
        
        For any chapter created without translation, is_translated should be 0.
        """
        # Create a book
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create chapter without translation
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content
        )
        
        # Get chapter
        chapter = chapter_service.get_chapter(chapter_id)
        
        # Verify is_translated is 0
        assert chapter['is_translated'] == 0, (
            f"Untranslated chapter should have is_translated=0, got {chapter['is_translated']}"
        )
        # translated_at should be None for untranslated chapters
        assert chapter['translated_at'] is None, (
            f"Untranslated chapter should have translated_at=None"
        )


    @given(
        title=title_strategy,
        title_zh=title_strategy,
        content=content_strategy,
        content_zh=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_translated_chapter_status(self, test_db, chapter_service,
                                        book_service, title, title_zh,
                                        content, content_zh):
        """
        Property: Translated chapters SHALL have is_translated=1 and non-null translated_at.
        
        **Validates: Requirements 4.4, 4.5**
        
        For any chapter created with translation, is_translated should be 1
        and translated_at should be set.
        """
        # Create a book
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create chapter with translation
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content,
            title_zh=title_zh,
            content_zh=content_zh
        )
        
        # Get chapter
        chapter = chapter_service.get_chapter(chapter_id)
        
        # Verify is_translated is 1
        assert chapter['is_translated'] == 1, (
            f"Translated chapter should have is_translated=1, got {chapter['is_translated']}"
        )
        # translated_at should be set
        assert chapter['translated_at'] is not None, (
            f"Translated chapter should have non-null translated_at"
        )

    @given(
        title=title_strategy,
        title_zh=title_strategy,
        content=content_strategy,
        content_zh=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_update_translation_sets_timestamp(self, test_db, chapter_service,
                                                book_service, title, title_zh,
                                                content, content_zh):
        """
        Property: Updating translation SHALL set translated_at timestamp.
        
        **Validates: Requirements 4.5**
        
        When is_translated changes from 0 to 1 via update_translation,
        translated_at should be set to a non-null timestamp.
        """
        # Create a book
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create untranslated chapter
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content
        )
        
        # Verify initially untranslated
        chapter_before = chapter_service.get_chapter(chapter_id)
        assert chapter_before['is_translated'] == 0
        assert chapter_before['translated_at'] is None
        
        # Update translation
        chapter_service.update_translation(
            chapter_id=chapter_id,
            title_zh=title_zh,
            content_zh=content_zh
        )
        
        # Verify translation status updated
        chapter_after = chapter_service.get_chapter(chapter_id)
        assert chapter_after['is_translated'] == 1, (
            f"After update, is_translated should be 1"
        )
        assert chapter_after['translated_at'] is not None, (
            f"After update, translated_at should be set"
        )


    @given(
        title=title_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_is_translated_only_0_or_1(self, test_db, chapter_service,
                                       book_service, title, content):
        """
        Property: is_translated SHALL only be 0 or 1.
        
        **Validates: Requirements 4.4**
        
        For any chapter, is_translated must be either 0 or 1.
        """
        from sqlalchemy import text
        
        # Create a book
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create chapter
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content
        )
        
        # Query directly to check raw value
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT is_translated FROM chapters WHERE id = :chapter_id"),
                {"chapter_id": chapter_id}
            ).scalar()
        
        # Verify is_translated is 0 or 1
        assert result in (0, 1), (
            f"is_translated should be 0 or 1, got {result}"
        )


# ============================================================================
# Property 15: Chapter Cascade Deletion
# **Validates: Requirements 4.6**
# ============================================================================

class TestChapterCascadeDeletionProperty:
    """
    Property-based tests for chapter cascade deletion.
    
    Property 15: Chapter Cascade Deletion
    *For any* chapter, when deleted, its corresponding chapter_contents 
    record SHALL also be deleted.
    
    **Validates: Requirements 4.6**
    """

    @given(
        title=title_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_chapter_content_deleted_with_chapter(self, test_db, chapter_service,
                                                   book_service, title, content):
        """
        Property: Deleting a chapter SHALL delete its content record.
        
        **Validates: Requirements 4.6**
        
        For any chapter, when deleted, the corresponding chapter_contents
        record must also be deleted.
        """
        from sqlalchemy import text
        
        # Create a book
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create chapter
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content
        )
        
        # Verify content exists before deletion
        with test_db.begin() as conn:
            content_before = conn.execute(
                text("SELECT * FROM chapter_contents WHERE chapter_id = :chapter_id"),
                {"chapter_id": chapter_id}
            ).mappings().first()
        
        assert content_before is not None, "Content should exist before deletion"
        
        # Delete chapter
        chapter_service.delete_chapter(chapter_id)
        
        # Verify content is deleted
        with test_db.begin() as conn:
            content_after = conn.execute(
                text("SELECT * FROM chapter_contents WHERE chapter_id = :chapter_id"),
                {"chapter_id": chapter_id}
            ).mappings().first()
        
        assert content_after is None, (
            f"Content should be deleted when chapter is deleted"
        )


    @given(
        title=title_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_chapter_record_deleted(self, test_db, chapter_service,
                                    book_service, title, content):
        """
        Property: Chapter record SHALL be deleted.
        
        **Validates: Requirements 4.6**
        
        For any chapter deletion, the chapter record itself must be removed.
        """
        from sqlalchemy import text
        
        # Create a book
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create chapter
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content
        )
        
        # Verify chapter exists before deletion
        chapter_before = chapter_service.get_chapter(chapter_id)
        assert chapter_before is not None, "Chapter should exist before deletion"
        
        # Delete chapter
        chapter_service.delete_chapter(chapter_id)
        
        # Verify chapter is deleted
        chapter_after = chapter_service.get_chapter(chapter_id)
        assert chapter_after is None, (
            f"Chapter should be deleted but still exists"
        )

    @given(num_chapters=num_chapters_strategy)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_delete_one_chapter_preserves_others(self, test_db, chapter_service,
                                                  book_service, num_chapters):
        """
        Property: Deleting one chapter SHALL NOT affect other chapters.
        
        **Validates: Requirements 4.6**
        
        For any book with multiple chapters, deleting one chapter should
        not affect the others.
        """
        assume(num_chapters >= 2)  # Need at least 2 chapters
        
        # Create a book
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create multiple chapters
        chapter_ids = []
        for i in range(1, num_chapters + 1):
            chapter_id = chapter_service.create_chapter(
                book_id=book_id,
                chapter_index=i,
                title=f"Chapter {i}",
                content=f"Content for chapter {i}"
            )
            chapter_ids.append(chapter_id)
        
        # Delete the first chapter
        chapter_service.delete_chapter(chapter_ids[0])
        
        # Verify other chapters still exist
        remaining_chapters = chapter_service.list_chapters(book_id)
        assert len(remaining_chapters) == num_chapters - 1, (
            f"Expected {num_chapters - 1} chapters after deletion, got {len(remaining_chapters)}"
        )
        
        # Verify deleted chapter is gone
        deleted_chapter = chapter_service.get_chapter(chapter_ids[0])
        assert deleted_chapter is None, "Deleted chapter should not exist"
        
        # Verify other chapters still have their content
        for chapter_id in chapter_ids[1:]:
            chapter = chapter_service.get_chapter(chapter_id, include_content=True)
            assert chapter is not None, f"Chapter {chapter_id} should still exist"
            assert chapter.get('content') is not None, f"Chapter {chapter_id} should have content"


# ============================================================================
# Property 16: Restructure Mapping JSON Round-Trip
# **Validates: Requirements 5.1, 5.2, 5.3**
# ============================================================================

class TestRestructureMappingJsonRoundTripProperty:
    """
    Property-based tests for restructure mapping JSON round-trip.
    
    Property 16: Restructure Mapping JSON Round-Trip
    *For any* list of source chapter IDs, storing them as source_chapter_ids 
    and then retrieving and parsing SHALL return the same list.
    
    **Validates: Requirements 5.1, 5.2, 5.3**
    """

    @given(source_chapter_ids=source_chapter_ids_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_source_chapter_ids_round_trip(self, test_db, chapter_service,
                                            book_service, source_chapter_ids):
        """
        Property: source_chapter_ids SHALL round-trip through JSON correctly.
        
        **Validates: Requirements 5.1, 5.2, 5.3**
        
        For any list of source chapter IDs, storing and retrieving should
        return the exact same list.
        """
        # Create source book
        source_filename = f"source_book_{get_unique_suffix()}.pdf"
        source_book_id = book_service.create_book(filename=source_filename)
        
        # Create new (restructured) book
        new_filename = f"new_book_{get_unique_suffix()}.pdf"
        new_book_id = book_service.create_book(
            filename=new_filename,
            source_type='restructured'
        )
        
        # Create a chapter in the new book
        new_chapter_id = chapter_service.create_chapter(
            book_id=new_book_id,
            chapter_index=1,
            title="Restructured Chapter",
            content="Combined content"
        )
        
        # Create mapping
        chapter_service.create_mapping(
            new_book_id=new_book_id,
            new_chapter_id=new_chapter_id,
            source_book_id=source_book_id,
            source_chapter_ids=source_chapter_ids
        )
        
        # Retrieve mapping
        mapping = chapter_service.get_source_chapters(new_chapter_id)
        
        # Verify round-trip
        assert mapping is not None, "Mapping should exist"
        assert mapping['source_chapter_ids'] == source_chapter_ids, (
            f"source_chapter_ids should round-trip correctly. "
            f"Expected {source_chapter_ids}, got {mapping['source_chapter_ids']}"
        )


    @given(
        source_chapter_ids=source_chapter_ids_strategy,
        filename=filename_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_mapping_stores_all_fields(self, test_db, chapter_service,
                                        book_service, source_chapter_ids, filename):
        """
        Property: Mapping SHALL store all required fields.
        
        **Validates: Requirements 5.1, 5.2**
        
        For any mapping, new_book_id, new_chapter_id, source_book_id,
        and source_chapter_ids should all be stored correctly.
        """
        from sqlalchemy import text
        
        # Create source book
        source_filename = f"source_{filename}_{get_unique_suffix()}.pdf"
        source_book_id = book_service.create_book(filename=source_filename)
        
        # Create new book
        new_filename = f"new_{filename}_{get_unique_suffix()}.pdf"
        new_book_id = book_service.create_book(
            filename=new_filename,
            source_type='restructured'
        )
        
        # Create chapter
        new_chapter_id = chapter_service.create_chapter(
            book_id=new_book_id,
            chapter_index=1,
            title="Test Chapter",
            content="Test content"
        )
        
        # Create mapping
        mapping_id = chapter_service.create_mapping(
            new_book_id=new_book_id,
            new_chapter_id=new_chapter_id,
            source_book_id=source_book_id,
            source_chapter_ids=source_chapter_ids
        )
        
        # Query directly to verify storage
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT * FROM chapter_mappings WHERE id = :mapping_id"),
                {"mapping_id": mapping_id}
            ).mappings().first()
        
        assert result is not None, "Mapping should exist"
        assert result['new_book_id'] == new_book_id
        assert result['new_chapter_id'] == new_chapter_id
        assert result['source_book_id'] == source_book_id
        
        # Verify JSON storage
        stored_ids = json.loads(result['source_chapter_ids'])
        assert stored_ids == source_chapter_ids

    @given(source_chapter_ids=source_chapter_ids_strategy)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_mapping_json_is_valid_array(self, test_db, chapter_service,
                                          book_service, source_chapter_ids):
        """
        Property: source_chapter_ids SHALL be stored as valid JSON array.
        
        **Validates: Requirements 5.2**
        
        For any source_chapter_ids, the stored value should be a valid
        JSON array that can be parsed.
        """
        from sqlalchemy import text
        
        # Create books and chapter
        source_book_id = book_service.create_book(
            filename=f"source_{get_unique_suffix()}.pdf"
        )
        new_book_id = book_service.create_book(
            filename=f"new_{get_unique_suffix()}.pdf",
            source_type='restructured'
        )
        new_chapter_id = chapter_service.create_chapter(
            book_id=new_book_id,
            chapter_index=1,
            title="Test",
            content="Content"
        )
        
        # Create mapping
        mapping_id = chapter_service.create_mapping(
            new_book_id=new_book_id,
            new_chapter_id=new_chapter_id,
            source_book_id=source_book_id,
            source_chapter_ids=source_chapter_ids
        )
        
        # Query raw JSON
        with test_db.begin() as conn:
            raw_json = conn.execute(
                text("SELECT source_chapter_ids FROM chapter_mappings WHERE id = :id"),
                {"id": mapping_id}
            ).scalar()
        
        # Verify it's valid JSON
        try:
            parsed = json.loads(raw_json)
            assert isinstance(parsed, list), "Should be a JSON array"
            assert all(isinstance(x, int) for x in parsed), "All elements should be integers"
        except json.JSONDecodeError as e:
            pytest.fail(f"source_chapter_ids is not valid JSON: {e}")


    @given(source_chapter_ids=source_chapter_ids_strategy)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_get_source_chapters_returns_parsed_list(self, test_db, chapter_service,
                                                      book_service, source_chapter_ids):
        """
        Property: get_source_chapters SHALL return parsed list, not JSON string.
        
        **Validates: Requirements 5.3**
        
        When querying a restructured chapter, the source_chapter_ids should
        be returned as a Python list, not a JSON string.
        """
        # Create books and chapter
        source_book_id = book_service.create_book(
            filename=f"source_{get_unique_suffix()}.pdf"
        )
        new_book_id = book_service.create_book(
            filename=f"new_{get_unique_suffix()}.pdf",
            source_type='restructured'
        )
        new_chapter_id = chapter_service.create_chapter(
            book_id=new_book_id,
            chapter_index=1,
            title="Test",
            content="Content"
        )
        
        # Create mapping
        chapter_service.create_mapping(
            new_book_id=new_book_id,
            new_chapter_id=new_chapter_id,
            source_book_id=source_book_id,
            source_chapter_ids=source_chapter_ids
        )
        
        # Get source chapters
        mapping = chapter_service.get_source_chapters(new_chapter_id)
        
        # Verify it's a list, not a string
        assert mapping is not None
        assert isinstance(mapping['source_chapter_ids'], list), (
            f"source_chapter_ids should be a list, got {type(mapping['source_chapter_ids'])}"
        )
        assert mapping['source_chapter_ids'] == source_chapter_ids


# ============================================================================
# Additional Edge Case Tests
# ============================================================================

class TestChapterServiceEdgeCases:
    """Additional edge case tests for ChapterService."""

    @given(
        title=title_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_get_chapter_with_content_includes_both_tables(self, test_db, 
                                                           chapter_service,
                                                           book_service,
                                                           title, content):
        """
        Test that get_chapter with include_content=True returns data from both tables.
        
        **Validates: Requirements 4.1, 4.2**
        """
        # Create book and chapter
        book_id = book_service.create_book(filename=f"book_{get_unique_suffix()}.pdf")
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content,
            word_count=len(content)
        )
        
        # Get chapter with content
        chapter = chapter_service.get_chapter(chapter_id, include_content=True)
        
        # Verify both metadata and content are present
        assert chapter is not None
        assert chapter['title'] == title  # From chapters table
        assert chapter['content'] == content  # From chapter_contents table
        assert chapter['word_count'] == len(content)  # From chapters table

    @given(
        title=title_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_get_chapter_without_content_excludes_content(self, test_db,
                                                          chapter_service,
                                                          book_service,
                                                          title, content):
        """
        Test that get_chapter with include_content=False excludes content fields.
        
        **Validates: Requirements 4.1, 4.2**
        """
        # Create book and chapter
        book_id = book_service.create_book(filename=f"book_{get_unique_suffix()}.pdf")
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title=title,
            content=content
        )
        
        # Get chapter without content
        chapter = chapter_service.get_chapter(chapter_id, include_content=False)
        
        # Verify metadata is present but content is not
        assert chapter is not None
        assert chapter['title'] == title
        assert 'content' not in chapter or chapter.get('content') is None
