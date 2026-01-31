"""
Property-Based Tests for InterpretationService

Feature: database-restructure
Properties 19-22: Interpretation Management Properties

**Validates: Requirements 6.1-6.7, 7.1-7.4**

This module tests the following properties:
- Property 19: Interpretation Data Separation - metadata in interpretations table, content in interpretation_contents
- Property 20: Interpretation Required and Optional Fields - book_id required, chapter_id and user_id optional
- Property 21: Interpretation Filtering - multi-parameter filtering returns matching records
- Property 22: Interpretation Cascade Deletion - deleting interpretation deletes its content
"""
import os
import sys
import json
import tempfile
import uuid
import pytest
from hypothesis import given, strategies as st, settings, assume, HealthCheck
from typing import Optional, Dict, List
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

# Content generator - text of various sizes
content_strategy = st.text(
    min_size=1,
    max_size=5000,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S', 'Z'))
).filter(lambda x: len(x.strip()) >= 1 and '\x00' not in x)

# Interpretation type generator
interpretation_type_strategy = st.sampled_from(['standard', 'personalized'])

# Optional text generator
optional_text_strategy = st.one_of(st.none(), st.text(
    max_size=500,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S'))
).filter(lambda x: '\x00' not in x))

# Model name generator
model_name_strategy = st.one_of(
    st.none(),
    st.sampled_from(['doubao-seed-1-6-251015', 'doubao-seed-1-6-flash-250828', 'deepseek-chat'])
)

# Filename generator
filename_strategy = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(whitelist_categories=('L', 'N'))
).filter(lambda x: len(x.strip()) >= 1)

# Chapter title generator
chapter_title_strategy = st.text(
    min_size=1,
    max_size=200,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S'))
).filter(lambda x: len(x.strip()) >= 1 and '\x00' not in x)

# Username generator
username_strategy = st.text(
    min_size=3,
    max_size=50,
    alphabet=st.characters(whitelist_categories=('L', 'N'))
).filter(lambda x: len(x.strip()) >= 3)

# Password generator
password_strategy = st.text(
    min_size=8,
    max_size=100,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P'))
).filter(lambda x: len(x.strip()) >= 8)


# ============================================================================
# Service Classes - Standalone implementations for testing
# ============================================================================

class StandaloneInterpretationService:
    """Standalone InterpretationService for testing, mirrors app.py implementation."""
    
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
            # 创建解读元数据
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
            
            # 创建解读内容
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
    
    def delete_interpretation(self, interpretation_id: int) -> bool:
        """删除解读（级联删除内容）"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM interpretations WHERE id = :interpretation_id"),
                {"interpretation_id": interpretation_id}
            )
        return True


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


class StandaloneChapterService:
    """Standalone ChapterService for testing."""
    
    def __init__(self, engine):
        self.engine = engine
    
    def create_chapter(self, book_id: int, chapter_index: int, title: str,
                      content: str, word_count: int = 0) -> int:
        """创建章节及其内容，返回 chapter_id"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO chapters (book_id, chapter_index, title, word_count,
                                        is_translated, created_at)
                    VALUES (:book_id, :chapter_index, :title, :word_count,
                           0, :created_at)
                    """
                ),
                {
                    "book_id": book_id,
                    "chapter_index": chapter_index,
                    "title": title,
                    "word_count": word_count,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            chapter_id = cursor.lastrowid
            
            conn.execute(
                text(
                    """
                    INSERT INTO chapter_contents (chapter_id, content)
                    VALUES (:chapter_id, :content)
                    """
                ),
                {"chapter_id": chapter_id, "content": content},
            )
            
            return chapter_id


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
        # Enable foreign keys for SQLite
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
                user_profession TEXT,
                reading_goal TEXT,
                focus TEXT,
                density TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            )
        """))
        
        # Create interpretation_contents table with cascade delete
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS interpretation_contents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interpretation_id INTEGER NOT NULL UNIQUE,
                content TEXT NOT NULL,
                FOREIGN KEY (interpretation_id) REFERENCES interpretations(id) ON DELETE CASCADE
            )
        """))
    
    yield engine
    
    # Cleanup
    engine.dispose()
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture(scope="function")
def interpretation_service(test_db):
    """Get InterpretationService instance for testing."""
    return StandaloneInterpretationService(test_db)


@pytest.fixture(scope="function")
def book_service(test_db):
    """Get BookService instance for testing."""
    return StandaloneBookService(test_db)


@pytest.fixture(scope="function")
def chapter_service(test_db):
    """Get ChapterService instance for testing."""
    return StandaloneChapterService(test_db)


@pytest.fixture(scope="function")
def user_service(test_db):
    """Get UserService instance for testing."""
    return StandaloneUserService(test_db)


# ============================================================================
# Property 19: Interpretation Data Separation
# **Validates: Requirements 7.1, 7.2, 7.4**
# ============================================================================

class TestInterpretationDataSeparationProperty:
    """
    Property-based tests for interpretation data separation.
    
    Property 19: Interpretation Data Separation
    *For any* interpretation, the metadata SHALL be stored in the interpretations 
    table, AND the content SHALL be stored in the interpretation_contents table 
    with a one-to-one relationship.
    
    **Validates: Requirements 7.1, 7.2, 7.4**
    """

    @given(
        content=content_strategy,
        interpretation_type=interpretation_type_strategy,
        model_used=model_name_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_metadata_stored_in_interpretations_table(self, test_db, interpretation_service,
                                                       book_service, content,
                                                       interpretation_type, model_used):
        """
        Property: Interpretation metadata SHALL be stored in the interpretations table.
        
        **Validates: Requirements 7.1**
        
        For any interpretation, book_id, chapter_id, user_id, interpretation_type,
        prompt_version, thinking_process, word_count, model_used must be stored
        in the interpretations table.
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create interpretation
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content,
            interpretation_type=interpretation_type,
            model_used=model_used,
            chapter_title="Test Chapter"
        )
        
        # Query interpretations table directly
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT * FROM interpretations WHERE id = :interpretation_id"),
                {"interpretation_id": interpretation_id}
            ).mappings().first()
        
        # Verify metadata is in interpretations table
        assert result is not None, "Interpretation should exist in interpretations table"
        assert result['book_id'] == book_id, "book_id should be stored in interpretations table"
        assert result['interpretation_type'] == interpretation_type, "interpretation_type should be stored"
        assert result['model_used'] == model_used, "model_used should be stored"
        assert result['word_count'] is not None, "word_count should be stored"

    @given(content=content_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_content_stored_in_interpretation_contents_table(self, test_db, 
                                                              interpretation_service,
                                                              book_service, content):
        """
        Property: Interpretation content SHALL be stored in the interpretation_contents table.
        
        **Validates: Requirements 7.2**
        
        For any interpretation, the content must be stored in the 
        interpretation_contents table with a foreign key reference.
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create interpretation
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content,
            chapter_title="Test Chapter"
        )
        
        # Query interpretation_contents table directly
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT * FROM interpretation_contents WHERE interpretation_id = :interpretation_id"),
                {"interpretation_id": interpretation_id}
            ).mappings().first()
        
        # Verify content is in interpretation_contents table
        assert result is not None, "Content should exist in interpretation_contents table"
        assert result['content'] == content, "Content should be stored in interpretation_contents table"
        assert result['interpretation_id'] == interpretation_id, "Foreign key reference should be correct"

    @given(content=content_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_one_to_one_relationship(self, test_db, interpretation_service,
                                      book_service, content):
        """
        Property: There SHALL be a one-to-one relationship between interpretation and content.
        
        **Validates: Requirements 7.4**
        
        For any interpretation, there must be exactly one corresponding
        interpretation_contents record.
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create interpretation
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content,
            chapter_title="Test Chapter"
        )
        
        # Count content records for this interpretation
        with test_db.begin() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM interpretation_contents WHERE interpretation_id = :interpretation_id"),
                {"interpretation_id": interpretation_id}
            ).scalar()
        
        # Verify exactly one content record
        assert count == 1, f"Expected exactly 1 content record, got {count}"


# ============================================================================
# Property 20: Interpretation Required and Optional Fields
# **Validates: Requirements 6.1, 6.2**
# ============================================================================

class TestInterpretationRequiredOptionalFieldsProperty:
    """
    Property-based tests for interpretation required and optional fields.
    
    Property 20: Interpretation Required and Optional Fields
    *For any* interpretation, book_id SHALL NOT be NULL, AND chapter_id 
    and user_id MAY be NULL.
    
    **Validates: Requirements 6.1, 6.2**
    """

    @given(content=content_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_book_id_is_required(self, test_db, interpretation_service,
                                  book_service, content):
        """
        Property: book_id SHALL NOT be NULL for any interpretation.
        
        **Validates: Requirements 6.1**
        
        For any interpretation, book_id must be a valid non-null reference.
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create interpretation with book_id
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content,
            chapter_title="Test Chapter"
        )
        
        # Query interpretations table directly
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT book_id FROM interpretations WHERE id = :interpretation_id"),
                {"interpretation_id": interpretation_id}
            ).mappings().first()
        
        # Verify book_id is not NULL
        assert result is not None, "Interpretation should exist"
        assert result['book_id'] is not None, "book_id SHALL NOT be NULL"
        assert result['book_id'] == book_id, "book_id should match the provided value"

    @given(content=content_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_chapter_id_may_be_null(self, test_db, interpretation_service,
                                     book_service, content):
        """
        Property: chapter_id MAY be NULL for whole-book interpretations.
        
        **Validates: Requirements 6.2**
        
        For any interpretation, chapter_id can be NULL (for whole-book interpretations).
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create interpretation without chapter_id
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content,
            chapter_id=None,  # Explicitly NULL
            chapter_title="Whole Book Interpretation"
        )
        
        # Query interpretations table directly
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT chapter_id FROM interpretations WHERE id = :interpretation_id"),
                {"interpretation_id": interpretation_id}
            ).mappings().first()
        
        # Verify chapter_id can be NULL
        assert result is not None, "Interpretation should exist"
        assert result['chapter_id'] is None, "chapter_id MAY be NULL"

    @given(content=content_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_user_id_may_be_null(self, test_db, interpretation_service,
                                  book_service, content):
        """
        Property: user_id MAY be NULL for general interpretations.
        
        **Validates: Requirements 6.2**
        
        For any interpretation, user_id can be NULL (for general interpretations).
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create interpretation without user_id
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content,
            user_id=None,  # Explicitly NULL
            chapter_title="General Interpretation"
        )
        
        # Query interpretations table directly
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT user_id FROM interpretations WHERE id = :interpretation_id"),
                {"interpretation_id": interpretation_id}
            ).mappings().first()
        
        # Verify user_id can be NULL
        assert result is not None, "Interpretation should exist"
        assert result['user_id'] is None, "user_id MAY be NULL"

    @given(
        content=content_strategy,
        username=username_strategy,
        password=password_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_chapter_id_and_user_id_can_be_set(self, test_db, interpretation_service,
                                                book_service, chapter_service,
                                                user_service, content, username, password):
        """
        Property: chapter_id and user_id CAN be set when provided.
        
        **Validates: Requirements 6.1, 6.2**
        
        For any interpretation, chapter_id and user_id can be valid references.
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create a chapter
        chapter_id = chapter_service.create_chapter(
            book_id=book_id,
            chapter_index=1,
            title="Test Chapter",
            content="Chapter content"
        )
        
        # Create a user
        unique_username = f"{username}_{get_unique_suffix()}"
        user_id = user_service.create_user(unique_username, password)
        
        # Create interpretation with both chapter_id and user_id
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content,
            chapter_id=chapter_id,
            user_id=user_id,
            chapter_title="Test Chapter"
        )
        
        # Query interpretations table directly
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT book_id, chapter_id, user_id FROM interpretations WHERE id = :interpretation_id"),
                {"interpretation_id": interpretation_id}
            ).mappings().first()
        
        # Verify all fields are set correctly
        assert result is not None, "Interpretation should exist"
        assert result['book_id'] == book_id, "book_id should be set"
        assert result['chapter_id'] == chapter_id, "chapter_id should be set when provided"
        assert result['user_id'] == user_id, "user_id should be set when provided"


# ============================================================================
# Property 21: Interpretation Filtering
# **Validates: Requirements 6.7**
# ============================================================================

class TestInterpretationFilteringProperty:
    """
    Property-based tests for interpretation filtering.
    
    Property 21: Interpretation Filtering
    *For any* combination of filter parameters (book_id, chapter_id, user_id, 
    interpretation_type), querying interpretations SHALL return only records 
    matching ALL specified parameters.
    
    **Validates: Requirements 6.7**
    """

    @given(
        content1=content_strategy,
        content2=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_filter_by_book_id(self, test_db, interpretation_service,
                                book_service, content1, content2):
        """
        Property: Filtering by book_id SHALL return only interpretations for that book.
        
        **Validates: Requirements 6.7**
        
        For any book_id filter, only interpretations with matching book_id are returned.
        """
        # Create two books
        book_id1 = book_service.create_book(filename=f"book1_{get_unique_suffix()}.pdf")
        book_id2 = book_service.create_book(filename=f"book2_{get_unique_suffix()}.pdf")
        
        # Create interpretations for each book
        interpretation_service.create_interpretation(
            book_id=book_id1, content=content1, chapter_title="Chapter 1"
        )
        interpretation_service.create_interpretation(
            book_id=book_id2, content=content2, chapter_title="Chapter 2"
        )
        
        # Filter by book_id1
        results = interpretation_service.list_interpretations(book_id=book_id1)
        
        # Verify all results match the filter
        assert len(results) >= 1, "Should find at least one interpretation"
        for r in results:
            assert r['book_id'] == book_id1, f"All results should have book_id={book_id1}"

    @given(content=content_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_filter_by_interpretation_type(self, test_db, interpretation_service,
                                            book_service, content):
        """
        Property: Filtering by interpretation_type SHALL return only matching types.
        
        **Validates: Requirements 6.7**
        
        For any interpretation_type filter, only interpretations with matching type are returned.
        """
        # Create a book
        book_id = book_service.create_book(filename=f"book_{get_unique_suffix()}.pdf")
        
        # Create interpretations of different types
        interpretation_service.create_interpretation(
            book_id=book_id, content=content, 
            interpretation_type='standard', chapter_title="Standard"
        )
        interpretation_service.create_interpretation(
            book_id=book_id, content=content,
            interpretation_type='personalized', chapter_title="Personalized"
        )
        
        # Filter by 'standard' type
        results = interpretation_service.list_interpretations(interpretation_type='standard')
        
        # Verify all results match the filter
        for r in results:
            assert r['interpretation_type'] == 'standard', "All results should be 'standard' type"

    @given(
        content=content_strategy,
        username=username_strategy,
        password=password_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_filter_by_user_id(self, test_db, interpretation_service,
                                book_service, user_service, content, username, password):
        """
        Property: Filtering by user_id SHALL return only interpretations for that user.
        
        **Validates: Requirements 6.7**
        
        For any user_id filter, only interpretations with matching user_id are returned.
        """
        # Create a book
        book_id = book_service.create_book(filename=f"book_{get_unique_suffix()}.pdf")
        
        # Create two users
        user_id1 = user_service.create_user(f"user1_{get_unique_suffix()}", password)
        user_id2 = user_service.create_user(f"user2_{get_unique_suffix()}", password)
        
        # Create interpretations for each user
        interpretation_service.create_interpretation(
            book_id=book_id, content=content, user_id=user_id1, chapter_title="User1"
        )
        interpretation_service.create_interpretation(
            book_id=book_id, content=content, user_id=user_id2, chapter_title="User2"
        )
        
        # Filter by user_id1
        results = interpretation_service.list_interpretations(user_id=user_id1)
        
        # Verify all results match the filter
        assert len(results) >= 1, "Should find at least one interpretation"
        for r in results:
            assert r['user_id'] == user_id1, f"All results should have user_id={user_id1}"

    @given(
        content=content_strategy,
        username=username_strategy,
        password=password_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_filter_by_multiple_parameters(self, test_db, interpretation_service,
                                            book_service, user_service, content,
                                            username, password):
        """
        Property: Filtering by multiple parameters SHALL return only records matching ALL.
        
        **Validates: Requirements 6.7**
        
        For any combination of filters, only interpretations matching ALL parameters are returned.
        """
        # Create two books
        book_id1 = book_service.create_book(filename=f"book1_{get_unique_suffix()}.pdf")
        book_id2 = book_service.create_book(filename=f"book2_{get_unique_suffix()}.pdf")
        
        # Create a user
        user_id = user_service.create_user(f"user_{get_unique_suffix()}", password)
        
        # Create interpretations with various combinations
        # book1, standard, user
        interpretation_service.create_interpretation(
            book_id=book_id1, content=content, user_id=user_id,
            interpretation_type='standard', chapter_title="B1-S-U"
        )
        # book1, personalized, user
        interpretation_service.create_interpretation(
            book_id=book_id1, content=content, user_id=user_id,
            interpretation_type='personalized', chapter_title="B1-P-U"
        )
        # book2, standard, user
        interpretation_service.create_interpretation(
            book_id=book_id2, content=content, user_id=user_id,
            interpretation_type='standard', chapter_title="B2-S-U"
        )
        # book1, standard, no user
        interpretation_service.create_interpretation(
            book_id=book_id1, content=content, user_id=None,
            interpretation_type='standard', chapter_title="B1-S-NoU"
        )
        
        # Filter by book_id1 AND interpretation_type='standard'
        results = interpretation_service.list_interpretations(
            book_id=book_id1, interpretation_type='standard'
        )
        
        # Verify all results match ALL filters
        for r in results:
            assert r['book_id'] == book_id1, "All results should have book_id=book_id1"
            assert r['interpretation_type'] == 'standard', "All results should be 'standard' type"

    @given(content=content_strategy)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_filter_by_chapter_id(self, test_db, interpretation_service,
                                   book_service, chapter_service, content):
        """
        Property: Filtering by chapter_id SHALL return only interpretations for that chapter.
        
        **Validates: Requirements 6.7**
        
        For any chapter_id filter, only interpretations with matching chapter_id are returned.
        """
        # Create a book
        book_id = book_service.create_book(filename=f"book_{get_unique_suffix()}.pdf")
        
        # Create two chapters
        chapter_id1 = chapter_service.create_chapter(
            book_id=book_id, chapter_index=1, title="Chapter 1", content="Content 1"
        )
        chapter_id2 = chapter_service.create_chapter(
            book_id=book_id, chapter_index=2, title="Chapter 2", content="Content 2"
        )
        
        # Create interpretations for each chapter
        interpretation_service.create_interpretation(
            book_id=book_id, content=content, chapter_id=chapter_id1, chapter_title="Ch1"
        )
        interpretation_service.create_interpretation(
            book_id=book_id, content=content, chapter_id=chapter_id2, chapter_title="Ch2"
        )
        
        # Filter by chapter_id1
        results = interpretation_service.list_interpretations(chapter_id=chapter_id1)
        
        # Verify all results match the filter
        assert len(results) >= 1, "Should find at least one interpretation"
        for r in results:
            assert r['chapter_id'] == chapter_id1, f"All results should have chapter_id={chapter_id1}"


# ============================================================================
# Property 22: Interpretation Cascade Deletion
# **Validates: Requirements 7.3**
# ============================================================================

class TestInterpretationCascadeDeletionProperty:
    """
    Property-based tests for interpretation cascade deletion.
    
    Property 22: Interpretation Cascade Deletion
    *For any* interpretation, when deleted, its corresponding 
    interpretation_contents record SHALL also be deleted.
    
    **Validates: Requirements 7.3**
    """

    @given(content=content_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_content_deleted_with_interpretation(self, test_db, interpretation_service,
                                                  book_service, content):
        """
        Property: Deleting interpretation SHALL cascade delete its content.
        
        **Validates: Requirements 7.3**
        
        For any interpretation, when deleted, the corresponding 
        interpretation_contents record must also be deleted.
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create interpretation
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content,
            chapter_title="Test Chapter"
        )
        
        # Verify content exists before deletion
        with test_db.begin() as conn:
            content_before = conn.execute(
                text("SELECT COUNT(*) FROM interpretation_contents WHERE interpretation_id = :interpretation_id"),
                {"interpretation_id": interpretation_id}
            ).scalar()
        
        assert content_before == 1, "Content should exist before deletion"
        
        # Delete interpretation
        interpretation_service.delete_interpretation(interpretation_id)
        
        # Verify content is also deleted
        with test_db.begin() as conn:
            content_after = conn.execute(
                text("SELECT COUNT(*) FROM interpretation_contents WHERE interpretation_id = :interpretation_id"),
                {"interpretation_id": interpretation_id}
            ).scalar()
        
        assert content_after == 0, "Content SHALL be deleted when interpretation is deleted"

    @given(content=content_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_interpretation_not_found_after_deletion(self, test_db, interpretation_service,
                                                      book_service, content):
        """
        Property: Deleted interpretation SHALL not be retrievable.
        
        **Validates: Requirements 7.3**
        
        For any interpretation, after deletion, get_interpretation should return None.
        """
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create interpretation
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content,
            chapter_title="Test Chapter"
        )
        
        # Verify interpretation exists before deletion
        interpretation_before = interpretation_service.get_interpretation(interpretation_id)
        assert interpretation_before is not None, "Interpretation should exist before deletion"
        
        # Delete interpretation
        interpretation_service.delete_interpretation(interpretation_id)
        
        # Verify interpretation is not found after deletion
        interpretation_after = interpretation_service.get_interpretation(interpretation_id)
        assert interpretation_after is None, "Interpretation SHALL not be found after deletion"

    @given(
        content1=content_strategy,
        content2=content_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_other_interpretations_not_affected(self, test_db, interpretation_service,
                                                 book_service, content1, content2):
        """
        Property: Deleting one interpretation SHALL NOT affect other interpretations.
        
        **Validates: Requirements 7.3**
        
        For any interpretation deletion, other interpretations and their content
        should remain intact.
        """
        from sqlalchemy import text
        
        # Create a book first
        unique_filename = f"book_{get_unique_suffix()}.pdf"
        book_id = book_service.create_book(filename=unique_filename)
        
        # Create two interpretations
        interpretation_id1 = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content1,
            chapter_title="Test Chapter 1"
        )
        interpretation_id2 = interpretation_service.create_interpretation(
            book_id=book_id,
            content=content2,
            chapter_title="Test Chapter 2"
        )
        
        # Delete first interpretation
        interpretation_service.delete_interpretation(interpretation_id1)
        
        # Verify second interpretation still exists
        interpretation2 = interpretation_service.get_interpretation(interpretation_id2)
        assert interpretation2 is not None, "Other interpretation should still exist"
        
        # Verify second interpretation's content still exists
        with test_db.begin() as conn:
            content_count = conn.execute(
                text("SELECT COUNT(*) FROM interpretation_contents WHERE interpretation_id = :interpretation_id"),
                {"interpretation_id": interpretation_id2}
            ).scalar()
        
        assert content_count == 1, "Other interpretation's content should still exist"
        assert interpretation2['content'] == content2, "Other interpretation's content should be intact"
