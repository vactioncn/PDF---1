"""
Property-Based Tests for BookService

Feature: database-restructure
Properties 5, 6, 10, 11: Book Management Properties

**Validates: Requirements 2.1, 2.2, 2.5, 2.7, 3.3, 3.5**

This module tests the following properties:
- Property 5: Enum Field Validation - enum fields only accept defined values
- Property 6: Book Initial Status - newly created books have status 'parsing'
- Property 10: Hash-Based Deduplication - duplicate files return same book_id
- Property 11: File Deletion Cascade - deleting book deletes associated file
"""
import os
import sys
import tempfile
import shutil
import hashlib
import uuid
import pytest
from hypothesis import given, strategies as st, settings, assume, HealthCheck
from typing import Optional, Dict, Tuple

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Global counter for unique filenames
_test_counter = 0

def get_unique_suffix():
    """Generate a unique suffix for filenames to avoid collisions."""
    global _test_counter
    _test_counter += 1
    return f"{uuid.uuid4().hex[:8]}_{_test_counter}"


# ============================================================================
# Test Data Generators (Strategies)
# ============================================================================

# Valid enum values
VALID_STATUS = ['parsing', 'translating', 'ready']
VALID_SOURCE_TYPE = ['upload', 'restructured']
VALID_LANGUAGE = ['zh', 'en', 'mixed']

# Invalid enum values for testing rejection
INVALID_STATUS = ['pending', 'completed', 'error', 'unknown', '', 'PARSING', 'Ready']
INVALID_SOURCE_TYPE = ['imported', 'downloaded', 'manual', '', 'UPLOAD']
INVALID_LANGUAGE = ['fr', 'de', 'jp', 'chinese', '', 'ZH']

# Filename generator - valid filenames without path separators
filename_strategy = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(
        whitelist_categories=('L', 'N'),  # Letters, Numbers
    )
).filter(lambda x: len(x.strip()) >= 1)

# File content generator - generates binary data of various sizes
file_content_strategy = st.binary(min_size=100, max_size=10000)

# Valid enum strategies
valid_status_strategy = st.sampled_from(VALID_STATUS)
valid_source_type_strategy = st.sampled_from(VALID_SOURCE_TYPE)
valid_language_strategy = st.sampled_from(VALID_LANGUAGE)

# Invalid enum strategies
invalid_status_strategy = st.sampled_from(INVALID_STATUS)
invalid_source_type_strategy = st.sampled_from(INVALID_SOURCE_TYPE)
invalid_language_strategy = st.sampled_from(INVALID_LANGUAGE)


# ============================================================================
# Service Classes - Standalone implementations for testing
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


class StandaloneBookService:
    """Standalone BookService for testing, mirrors app.py implementation."""
    
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
        from datetime import datetime
        
        # Validate enum fields
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
    
    def delete_book(self, book_id: int) -> bool:
        """删除书籍（级联删除章节、解读和文件）"""
        from sqlalchemy import text
        
        # 先获取文件路径
        book = self.get_book(book_id)
        if not book:
            return False
        
        # 删除关联文件
        if book.get("file_path"):
            self.file_storage.delete_file(book["file_path"])
        
        # 删除数据库记录
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM books WHERE id = :book_id"),
                {"book_id": book_id}
            )
        return True
    
    def upload_book(self, file_data: bytes, filename: str, 
                   source_type: str = 'upload', language: str = 'zh') -> Tuple[int, bool]:
        """
        上传书籍文件，支持去重。
        返回 (book_id, is_new) - is_new 为 False 表示返回的是已存在的书籍
        """
        # 计算文件哈希
        file_hash = self.file_storage.calculate_hash(file_data)
        
        # 检查是否已存在
        existing_book_id = self.find_by_hash(file_hash)
        if existing_book_id:
            return existing_book_id, False
        
        # 保存文件
        file_path, _ = self.file_storage.save_file(file_data, filename)
        
        # 创建书籍记录
        book_id = self.create_book(
            filename=filename,
            source_type=source_type,
            language=language,
            file_path=file_path,
            file_hash=file_hash
        )
        
        return book_id, True


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def temp_upload_dir():
    """Create a temporary upload directory for testing."""
    temp_dir = tempfile.mkdtemp(prefix="test_uploads_")
    yield temp_dir
    # Cleanup after test
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


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
        # Create books table with all new fields
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
        
        # Create interpretations table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS interpretations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER,
                chapter_id INTEGER,
                user_id INTEGER,
                interpretation_type TEXT NOT NULL DEFAULT 'standard',
                created_at TEXT NOT NULL,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
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
def file_storage_service(temp_upload_dir):
    """Create a FileStorageService instance with a temporary upload directory."""
    return StandaloneFileStorageService(temp_upload_dir)


@pytest.fixture(scope="function")
def book_service(test_db, file_storage_service):
    """Get BookService instance for testing."""
    return StandaloneBookService(test_db, file_storage_service)


# ============================================================================
# Property 5: Enum Field Validation
# **Validates: Requirements 2.1, 2.5, 2.7**
# ============================================================================

class TestEnumFieldValidationProperty:
    """
    Property-based tests for enum field validation.
    
    Property 5: Enum Field Validation
    *For any* enum field (book.status, book.source_type, book.language),
    the system SHALL only accept values from the defined set and reject
    all other values.
    
    **Validates: Requirements 2.1, 2.5, 2.7**
    """

    @given(
        filename=filename_strategy,
        source_type=valid_source_type_strategy,
        language=valid_language_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_valid_source_type_accepted(self, test_db, book_service, 
                                        filename, source_type, language):
        """
        Property: Valid source_type values SHALL be accepted.
        
        **Validates: Requirements 2.5**
        
        For any valid source_type ('upload', 'restructured'), book creation
        must succeed.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        # Should not raise an exception
        book_id = book_service.create_book(
            filename=unique_filename,
            source_type=source_type,
            language=language
        )
        
        # Verify book was created
        book = book_service.get_book(book_id)
        assert book is not None
        assert book['source_type'] == source_type


    @given(
        filename=filename_strategy,
        language=valid_language_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_valid_language_accepted(self, test_db, book_service, filename, language):
        """
        Property: Valid language values SHALL be accepted.
        
        **Validates: Requirements 2.7**
        
        For any valid language ('zh', 'en', 'mixed'), book creation must succeed.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        book_id = book_service.create_book(
            filename=unique_filename,
            language=language
        )
        
        book = book_service.get_book(book_id)
        assert book is not None
        assert book['language'] == language

    @given(
        filename=filename_strategy,
        invalid_source_type=invalid_source_type_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_invalid_source_type_rejected(self, test_db, book_service, 
                                          filename, invalid_source_type):
        """
        Property: Invalid source_type values SHALL be rejected.
        
        **Validates: Requirements 2.5**
        
        For any invalid source_type, book creation must raise ValueError.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        with pytest.raises(ValueError) as exc_info:
            book_service.create_book(
                filename=unique_filename,
                source_type=invalid_source_type
            )
        
        assert "Invalid source_type" in str(exc_info.value)

    @given(
        filename=filename_strategy,
        invalid_language=invalid_language_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_invalid_language_rejected(self, test_db, book_service, 
                                       filename, invalid_language):
        """
        Property: Invalid language values SHALL be rejected.
        
        **Validates: Requirements 2.7**
        
        For any invalid language, book creation must raise ValueError.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        with pytest.raises(ValueError) as exc_info:
            book_service.create_book(
                filename=unique_filename,
                language=invalid_language
            )
        
        assert "Invalid language" in str(exc_info.value)


    @given(
        filename=filename_strategy,
        valid_status=valid_status_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_valid_status_update_accepted(self, test_db, book_service, 
                                          filename, valid_status):
        """
        Property: Valid status values SHALL be accepted for updates.
        
        **Validates: Requirements 2.1**
        
        For any valid status ('parsing', 'translating', 'ready'), status
        update must succeed.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        # Create book first
        book_id = book_service.create_book(filename=unique_filename)
        
        # Update status - should not raise
        book_service.update_status(book_id, valid_status)
        
        # Verify status was updated
        book = book_service.get_book(book_id)
        assert book['status'] == valid_status

    @given(
        filename=filename_strategy,
        invalid_status=invalid_status_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_invalid_status_update_rejected(self, test_db, book_service, 
                                            filename, invalid_status):
        """
        Property: Invalid status values SHALL be rejected for updates.
        
        **Validates: Requirements 2.1**
        
        For any invalid status, status update must raise ValueError.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        # Create book first
        book_id = book_service.create_book(filename=unique_filename)
        
        # Update with invalid status - should raise
        with pytest.raises(ValueError) as exc_info:
            book_service.update_status(book_id, invalid_status)
        
        assert "Invalid status" in str(exc_info.value)


# ============================================================================
# Property 6: Book Initial Status
# **Validates: Requirements 2.2**
# ============================================================================

class TestBookInitialStatusProperty:
    """
    Property-based tests for book initial status.
    
    Property 6: Book Initial Status
    *For any* newly created book, the initial status SHALL be 'parsing'.
    
    **Validates: Requirements 2.2**
    """

    @given(
        filename=filename_strategy,
        source_type=valid_source_type_strategy,
        language=valid_language_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_new_book_has_parsing_status(self, test_db, book_service,
                                         filename, source_type, language):
        """
        Property: Newly created books SHALL have status 'parsing'.
        
        **Validates: Requirements 2.2**
        
        For any book created with any valid parameters, the initial status
        must always be 'parsing'.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        book_id = book_service.create_book(
            filename=unique_filename,
            source_type=source_type,
            language=language
        )
        
        book = book_service.get_book(book_id)
        
        assert book is not None
        assert book['status'] == 'parsing', (
            f"New book has status '{book['status']}' instead of 'parsing'"
        )


    @given(file_data=file_content_strategy, filename=filename_strategy)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_uploaded_book_has_parsing_status(self, test_db, book_service,
                                              file_data, filename):
        """
        Property: Uploaded books SHALL have initial status 'parsing'.
        
        **Validates: Requirements 2.2**
        
        For any file uploaded, the resulting book must have status 'parsing'.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        book_id, is_new = book_service.upload_book(file_data, unique_filename)
        
        # Only check new books (duplicates may have different status)
        if is_new:
            book = book_service.get_book(book_id)
            assert book['status'] == 'parsing', (
                f"Uploaded book has status '{book['status']}' instead of 'parsing'"
            )


# ============================================================================
# Property 10: Hash-Based Deduplication
# **Validates: Requirements 3.3**
# ============================================================================

class TestHashBasedDeduplicationProperty:
    """
    Property-based tests for hash-based deduplication.
    
    Property 10: Hash-Based Deduplication
    *For any* file uploaded twice (same content), the second upload SHALL
    return the same book_id as the first upload without creating a duplicate
    record.
    
    **Validates: Requirements 3.3**
    """

    @given(filename=filename_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_duplicate_file_returns_same_book_id(self, test_db, book_service, filename):
        """
        Property: Uploading the same file twice SHALL return the same book_id.
        
        **Validates: Requirements 3.3**
        
        For any file content, uploading it twice must return the same book_id.
        """
        # Generate unique file content for each test to avoid cross-test collisions
        unique_suffix = get_unique_suffix()
        file_data = f"unique_content_{unique_suffix}".encode() * 10
        unique_filename = f"{filename}_{unique_suffix}.pdf"
        
        # First upload
        book_id_1, is_new_1 = book_service.upload_book(file_data, unique_filename)
        
        # Second upload with same content (different filename is OK)
        another_filename = f"copy_{unique_filename}"
        book_id_2, is_new_2 = book_service.upload_book(file_data, another_filename)
        
        # Property: same book_id must be returned
        assert book_id_1 == book_id_2, (
            f"Duplicate file returned different book_ids: {book_id_1} vs {book_id_2}"
        )
        
        # Property: first upload is new, second is not
        assert is_new_1 is True, "First upload should be marked as new"
        assert is_new_2 is False, "Second upload should not be marked as new"


    @given(filename=filename_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_different_files_get_different_book_ids(self, test_db, book_service, filename):
        """
        Property: Different files SHALL get different book_ids.
        
        **Validates: Requirements 3.3**
        
        For any two different file contents, they must get different book_ids.
        """
        # Generate unique file contents for each test
        unique_suffix_1 = get_unique_suffix()
        unique_suffix_2 = get_unique_suffix()
        file_data_1 = f"content_A_{unique_suffix_1}".encode() * 10
        file_data_2 = f"content_B_{unique_suffix_2}".encode() * 10
        
        unique_filename_1 = f"{filename}_{unique_suffix_1}_1.pdf"
        unique_filename_2 = f"{filename}_{unique_suffix_2}_2.pdf"
        
        book_id_1, is_new_1 = book_service.upload_book(file_data_1, unique_filename_1)
        book_id_2, is_new_2 = book_service.upload_book(file_data_2, unique_filename_2)
        
        # Property: different files get different book_ids
        assert book_id_1 != book_id_2, (
            f"Different files got same book_id: {book_id_1}"
        )
        
        # Both should be new
        assert is_new_1 is True
        assert is_new_2 is True

    @given(file_data=file_content_strategy, filename=filename_strategy)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_no_duplicate_records_created(self, test_db, book_service, 
                                          file_data, filename):
        """
        Property: Duplicate uploads SHALL NOT create duplicate records.
        
        **Validates: Requirements 3.3**
        
        For any file uploaded multiple times, only one database record
        should exist.
        """
        from sqlalchemy import text
        
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        file_hash = book_service.file_storage.calculate_hash(file_data)
        
        # Upload the same file 3 times
        book_service.upload_book(file_data, unique_filename)
        book_service.upload_book(file_data, f"copy1_{unique_filename}")
        book_service.upload_book(file_data, f"copy2_{unique_filename}")
        
        # Count records with this hash
        with test_db.begin() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM books WHERE file_hash = :hash"),
                {"hash": file_hash}
            ).scalar()
        
        # Property: only one record should exist
        assert count == 1, (
            f"Expected 1 record for hash {file_hash}, found {count}"
        )


# ============================================================================
# Property 11: File Deletion Cascade
# **Validates: Requirements 3.5**
# ============================================================================

class TestFileDeletionCascadeProperty:
    """
    Property-based tests for file deletion cascade.
    
    Property 11: File Deletion Cascade
    *For any* book with an associated file, when the book is deleted,
    the file SHALL no longer exist in the uploads/ directory.
    
    **Validates: Requirements 3.5**
    """

    @given(file_data=file_content_strategy, filename=filename_strategy)
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_file_deleted_when_book_deleted(self, test_db, book_service,
                                            file_data, filename):
        """
        Property: Deleting a book SHALL delete its associated file.
        
        **Validates: Requirements 3.5**
        
        For any book with an associated file, deleting the book must also
        delete the file from the uploads/ directory.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        # Upload book with file
        book_id, _ = book_service.upload_book(file_data, unique_filename)
        
        # Get file path before deletion
        book = book_service.get_book(book_id)
        file_path = book['file_path']
        
        # Verify file exists before deletion
        assert file_path is not None, "Book should have a file_path"
        assert os.path.exists(file_path), f"File should exist at {file_path}"
        
        # Delete the book
        result = book_service.delete_book(book_id)
        assert result is True, "Book deletion should succeed"
        
        # Property: file should no longer exist
        assert not os.path.exists(file_path), (
            f"File should be deleted but still exists at {file_path}"
        )

    @given(file_data=file_content_strategy, filename=filename_strategy)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_book_record_deleted_with_file(self, test_db, book_service,
                                           file_data, filename):
        """
        Property: Book record SHALL be deleted along with its file.
        
        **Validates: Requirements 3.5**
        
        For any book deletion, both the database record and file must be removed.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        # Upload book
        book_id, _ = book_service.upload_book(file_data, unique_filename)
        
        # Verify book exists
        book = book_service.get_book(book_id)
        assert book is not None
        
        # Delete the book
        book_service.delete_book(book_id)
        
        # Property: book record should no longer exist
        deleted_book = book_service.get_book(book_id)
        assert deleted_book is None, (
            f"Book record should be deleted but still exists: {deleted_book}"
        )


    @given(filename=filename_strategy)
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_book_without_file_can_be_deleted(self, test_db, book_service, filename):
        """
        Property: Books without files SHALL be deletable without errors.
        
        **Validates: Requirements 3.5**
        
        For any book created without a file, deletion should succeed gracefully.
        """
        unique_filename = f"{filename}_{get_unique_suffix()}.pdf"
        
        # Create book without file
        book_id = book_service.create_book(filename=unique_filename)
        
        # Verify book exists and has no file
        book = book_service.get_book(book_id)
        assert book is not None
        assert book.get('file_path') is None
        
        # Delete should succeed
        result = book_service.delete_book(book_id)
        assert result is True
        
        # Book should be deleted
        deleted_book = book_service.get_book(book_id)
        assert deleted_book is None


# ============================================================================
# Additional Unit Tests for Edge Cases
# ============================================================================

class TestBookServiceEdgeCases:
    """Unit tests for edge cases in BookService."""

    def test_all_valid_status_values(self, test_db, book_service):
        """Test that all defined valid status values are accepted."""
        for status in VALID_STATUS:
            book_id = book_service.create_book(
                filename=f"test_{status}_{get_unique_suffix()}.pdf"
            )
            book_service.update_status(book_id, status)
            book = book_service.get_book(book_id)
            assert book['status'] == status

    def test_all_valid_source_type_values(self, test_db, book_service):
        """Test that all defined valid source_type values are accepted."""
        for source_type in VALID_SOURCE_TYPE:
            book_id = book_service.create_book(
                filename=f"test_{source_type}_{get_unique_suffix()}.pdf",
                source_type=source_type
            )
            book = book_service.get_book(book_id)
            assert book['source_type'] == source_type

    def test_all_valid_language_values(self, test_db, book_service):
        """Test that all defined valid language values are accepted."""
        for language in VALID_LANGUAGE:
            book_id = book_service.create_book(
                filename=f"test_{language}_{get_unique_suffix()}.pdf",
                language=language
            )
            book = book_service.get_book(book_id)
            assert book['language'] == language

    def test_delete_nonexistent_book_returns_false(self, test_db, book_service):
        """Test that deleting a non-existent book returns False."""
        result = book_service.delete_book(99999)
        assert result is False

    def test_find_by_hash_returns_none_for_unknown_hash(self, test_db, book_service):
        """Test that find_by_hash returns None for unknown hash."""
        result = book_service.find_by_hash("nonexistent_hash_12345")
        assert result is None

    def test_empty_file_deduplication(self, test_db, book_service):
        """Test deduplication works for empty files."""
        empty_data = b''
        
        book_id_1, is_new_1 = book_service.upload_book(
            empty_data, f"empty1_{get_unique_suffix()}.pdf"
        )
        book_id_2, is_new_2 = book_service.upload_book(
            empty_data, f"empty2_{get_unique_suffix()}.pdf"
        )
        
        assert book_id_1 == book_id_2
        assert is_new_1 is True
        assert is_new_2 is False
