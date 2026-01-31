"""
Property-Based Tests for UserService

Feature: database-restructure
Properties 1-4: User Management Properties

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**

This module tests the following properties:
- Property 1: User Password Hashing - passwords are hashed, not stored in plaintext
- Property 2: Profile Update Persistence - profile updates are persisted correctly
- Property 3: Unique Constraint Enforcement - duplicate usernames/emails are rejected
- Property 4: User Deletion Soft Reference - deleted users' interpretations have user_id set to NULL
"""
import os
import sys
import json
import tempfile
import uuid
import pytest
from hypothesis import given, strategies as st, settings, assume, HealthCheck

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Global counter for unique usernames
_test_counter = 0

def get_unique_suffix():
    """Generate a unique suffix for usernames to avoid collisions."""
    global _test_counter
    _test_counter += 1
    return f"{uuid.uuid4().hex[:8]}_{_test_counter}"


# ============================================================================
# Test Data Generators (Strategies)
# ============================================================================

# Username generator - alphanumeric characters, reasonable length
username_strategy = st.text(
    min_size=3,
    max_size=50,
    alphabet=st.characters(whitelist_categories=('L', 'N'))  # Letters and Numbers
).filter(lambda x: len(x.strip()) >= 3)

# Password generator - at least 8 characters for security
password_strategy = st.text(
    min_size=8,
    max_size=100,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P'))  # Letters, Numbers, Punctuation
).filter(lambda x: len(x.strip()) >= 8)

# Email generator - valid email format
email_strategy = st.emails()

# Profession generator - text field
profession_strategy = st.text(max_size=100).filter(lambda x: '\x00' not in x)

# Reading goal generator - text field
reading_goal_strategy = st.text(max_size=500).filter(lambda x: '\x00' not in x)

# Focus areas generator - list of strings
focus_areas_strategy = st.lists(
    st.text(min_size=1, max_size=50).filter(lambda x: '\x00' not in x and len(x.strip()) > 0),
    max_size=10
)


# ============================================================================
# Service Classes - Standalone implementations for testing
# These mirror the implementations in app.py but can be used outside Flask context
# ============================================================================

class StandaloneUserService:
    """Standalone UserService for testing, mirrors app.py implementation."""
    
    def __init__(self, engine):
        self.engine = engine
    
    def create_user(self, username: str, password: str, email=None) -> int:
        """创建新用户，返回 user_id"""
        from werkzeug.security import generate_password_hash
        from sqlalchemy import text
        from datetime import datetime
        
        # Use pbkdf2:sha256 with reduced iterations for faster testing
        # In production, use default iterations (1000000)
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
        from datetime import datetime
        
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
            # 解析 focus_areas JSON
            if user.get("focus_areas"):
                try:
                    user["focus_areas"] = json.loads(user["focus_areas"])
                except:
                    user["focus_areas"] = []
            else:
                user["focus_areas"] = None
            return user
        return None
    
    def delete_user(self, user_id: int) -> bool:
        """删除用户（解读中的 user_id 设为 NULL）"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            # 先将相关解读的 user_id 设为 NULL
            conn.execute(
                text("UPDATE interpretations SET user_id = NULL WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            # 删除用户
            conn.execute(
                text("DELETE FROM users WHERE id = :user_id"),
                {"user_id": user_id}
            )
        return True


class StandaloneBookService:
    """Standalone BookService for testing."""
    
    def __init__(self, engine):
        self.engine = engine
    
    def create_book(self, filename: str, source_type: str = 'upload',
                   parent_book_id=None, language: str = 'zh',
                   file_path=None, file_hash=None,
                   chapter_count: int = 0, total_word_count: int = 0) -> int:
        """创建书籍记录，返回 book_id"""
        from sqlalchemy import text
        from datetime import datetime
        
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


class StandaloneInterpretationService:
    """Standalone InterpretationService for testing."""
    
    def __init__(self, engine):
        self.engine = engine
    
    def create_interpretation(self, book_id: int, content: str,
                              chapter_id=None, user_id=None,
                              interpretation_type: str = 'standard',
                              prompt_version=None, prompt_text=None,
                              thinking_process=None, model_used=None) -> int:
        """创建解读及其内容，返回 interpretation_id"""
        from sqlalchemy import text
        from datetime import datetime
        
        word_count = len(content) if content else 0
        
        with self.engine.begin() as conn:
            # 创建解读记录
            cursor = conn.execute(
                text(
                    """
                    INSERT INTO interpretations (book_id, chapter_id, user_id, 
                                                interpretation_type, prompt_version,
                                                prompt_text, thinking_process, word_count,
                                                model_used, chapter_title, created_at)
                    VALUES (:book_id, :chapter_id, :user_id, :interpretation_type,
                            :prompt_version, :prompt_text, :thinking_process, :word_count,
                            :model_used, :chapter_title, :created_at)
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
                    "chapter_title": "Test Chapter",
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            interpretation_id = cursor.lastrowid
            
            # 创建解读内容记录
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


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def test_db():
    """Create a temporary database for testing with all required tables."""
    from sqlalchemy import create_engine, text
    from datetime import datetime
    
    # Create a temporary database file
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    # Create engine
    engine = create_engine(f'sqlite:///{db_path}')
    
    # Initialize database schema
    with engine.begin() as conn:
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
    
    yield engine
    
    # Cleanup
    engine.dispose()
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture(scope="function")
def user_service(test_db):
    """Get UserService instance for testing."""
    return StandaloneUserService(test_db)


@pytest.fixture(scope="function")
def interpretation_service(test_db):
    """Get InterpretationService instance for testing."""
    return StandaloneInterpretationService(test_db)


@pytest.fixture(scope="function")
def book_service(test_db):
    """Get BookService instance for testing."""
    return StandaloneBookService(test_db)


# ============================================================================
# Property 1: User Password Hashing
# **Validates: Requirements 1.1**
# ============================================================================

class TestUserPasswordHashingProperty:
    """
    Property-based tests for password hashing.
    
    Property 1: User Password Hashing
    *For any* valid username and password combination, when a user is created,
    the stored password_hash SHALL NOT equal the plaintext password and SHALL
    be a valid hash.
    
    **Validates: Requirements 1.1**
    """

    @given(username=username_strategy, password=password_strategy)
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_password_not_stored_in_plaintext(self, test_db, user_service, username, password):
        """
        Property: The stored password_hash SHALL NOT equal the plaintext password.
        
        **Validates: Requirements 1.1**
        
        For any username and password, the stored hash must never equal the
        original plaintext password.
        """
        from sqlalchemy import text
        
        # Ensure unique username for this test
        unique_username = f"{username}_{get_unique_suffix()}"
        
        # Create user
        user_id = user_service.create_user(unique_username, password)
        
        # Retrieve the stored password_hash directly from database
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT password_hash FROM users WHERE id = :user_id"),
                {"user_id": user_id}
            ).fetchone()
        
        stored_hash = result[0]
        
        # Property: password_hash must NOT equal plaintext password
        assert stored_hash != password, (
            f"Password was stored in plaintext! "
            f"password={password}, stored_hash={stored_hash}"
        )

    @given(username=username_strategy, password=password_strategy)
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_password_hash_is_valid_werkzeug_hash(self, test_db, user_service, username, password):
        """
        Property: The stored password_hash SHALL be a valid werkzeug hash.
        
        **Validates: Requirements 1.1**
        
        For any username and password, the stored hash must be verifiable
        using werkzeug's check_password_hash function.
        """
        from werkzeug.security import check_password_hash
        from sqlalchemy import text
        
        # Ensure unique username for this test
        unique_username = f"{username}_{get_unique_suffix()}_v"
        
        # Create user
        user_id = user_service.create_user(unique_username, password)
        
        # Retrieve the stored password_hash directly from database
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT password_hash FROM users WHERE id = :user_id"),
                {"user_id": user_id}
            ).fetchone()
        
        stored_hash = result[0]
        
        # Property: stored hash must be verifiable with original password
        assert check_password_hash(stored_hash, password), (
            f"Stored hash is not a valid hash of the password! "
            f"password={password}, stored_hash={stored_hash}"
        )

    @given(username=username_strategy, password=password_strategy)
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_authentication_works_with_correct_password(self, test_db, user_service, username, password):
        """
        Property: Authentication SHALL succeed with the correct password.
        
        **Validates: Requirements 1.1**
        
        For any user created with a password, authentication with that
        same password must succeed.
        """
        # Ensure unique username for this test
        unique_username = f"{username}_{get_unique_suffix()}_auth"
        
        # Create user
        user_service.create_user(unique_username, password)
        
        # Authenticate with correct password
        result = user_service.authenticate(unique_username, password)
        
        # Property: authentication must succeed
        assert result is not None, (
            f"Authentication failed with correct password! "
            f"username={unique_username}"
        )
        assert result['username'] == unique_username

    @given(
        username=username_strategy,
        password=password_strategy,
        wrong_password=password_strategy
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_authentication_fails_with_wrong_password(self, test_db, user_service, username, password, wrong_password):
        """
        Property: Authentication SHALL fail with an incorrect password.
        
        **Validates: Requirements 1.1**
        
        For any user, authentication with a different password must fail.
        """
        # Skip if passwords happen to be the same
        assume(password != wrong_password)
        
        # Ensure unique username for this test
        unique_username = f"{username}_{get_unique_suffix()}_wrong"
        
        # Create user
        user_service.create_user(unique_username, password)
        
        # Authenticate with wrong password
        result = user_service.authenticate(unique_username, wrong_password)
        
        # Property: authentication must fail
        assert result is None, (
            f"Authentication succeeded with wrong password! "
            f"username={unique_username}, correct={password}, wrong={wrong_password}"
        )


# ============================================================================
# Property 2: Profile Update Persistence
# **Validates: Requirements 1.2**
# ============================================================================

class TestProfileUpdatePersistenceProperty:
    """
    Property-based tests for profile update persistence.
    
    Property 2: Profile Update Persistence
    *For any* user and any valid profile data (profession, reading_goal, focus_areas),
    updating the profile and then retrieving the user SHALL return the same profile data.
    
    **Validates: Requirements 1.2**
    """

    @given(
        username=username_strategy,
        password=password_strategy,
        profession=profession_strategy,
        reading_goal=reading_goal_strategy,
        focus_areas=focus_areas_strategy
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_profile_update_persists_all_fields(self, test_db, user_service, 
                                                 username, password, profession, 
                                                 reading_goal, focus_areas):
        """
        Property: Updated profile data SHALL be retrievable with the same values.
        
        **Validates: Requirements 1.2**
        
        For any user and profile data, updating and then retrieving must
        return the exact same data.
        """
        # Ensure unique username for this test
        unique_username = f"{username}_{get_unique_suffix()}_profile"
        
        # Create user
        user_id = user_service.create_user(unique_username, password)
        
        # Update profile
        user_service.update_profile(
            user_id,
            profession=profession,
            reading_goal=reading_goal,
            focus_areas=focus_areas
        )
        
        # Retrieve user
        user = user_service.get_user(user_id)
        
        # Property: all profile fields must match
        assert user is not None, f"User not found after profile update"
        assert user['profession'] == profession, (
            f"Profession mismatch: expected {profession}, got {user['profession']}"
        )
        assert user['reading_goal'] == reading_goal, (
            f"Reading goal mismatch: expected {reading_goal}, got {user['reading_goal']}"
        )
        assert user['focus_areas'] == focus_areas, (
            f"Focus areas mismatch: expected {focus_areas}, got {user['focus_areas']}"
        )

    @given(
        username=username_strategy,
        password=password_strategy,
        profession1=profession_strategy,
        profession2=profession_strategy
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_profile_update_overwrites_previous_values(self, test_db, user_service,
                                                        username, password,
                                                        profession1, profession2):
        """
        Property: Subsequent profile updates SHALL overwrite previous values.
        
        **Validates: Requirements 1.2**
        
        For any user, updating a field twice must result in the second value
        being stored.
        """
        # Ensure unique username for this test
        unique_username = f"{username}_{get_unique_suffix()}_overwrite"
        
        # Create user
        user_id = user_service.create_user(unique_username, password)
        
        # First update
        user_service.update_profile(user_id, profession=profession1)
        
        # Second update
        user_service.update_profile(user_id, profession=profession2)
        
        # Retrieve user
        user = user_service.get_user(user_id)
        
        # Property: profession must be the second value
        assert user['profession'] == profession2, (
            f"Profile update did not overwrite: expected {profession2}, got {user['profession']}"
        )

    @given(
        username=username_strategy,
        password=password_strategy,
        profession=profession_strategy,
        reading_goal=reading_goal_strategy
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_partial_profile_update_preserves_other_fields(self, test_db, user_service,
                                                            username, password,
                                                            profession, reading_goal):
        """
        Property: Partial profile updates SHALL preserve unmodified fields.
        
        **Validates: Requirements 1.2**
        
        For any user, updating only some fields must not affect other fields.
        """
        # Ensure unique username for this test
        unique_username = f"{username}_{get_unique_suffix()}_partial"
        
        # Create user
        user_id = user_service.create_user(unique_username, password)
        
        # Update profession first
        user_service.update_profile(user_id, profession=profession)
        
        # Update reading_goal only
        user_service.update_profile(user_id, reading_goal=reading_goal)
        
        # Retrieve user
        user = user_service.get_user(user_id)
        
        # Property: profession must still be set
        assert user['profession'] == profession, (
            f"Partial update overwrote profession: expected {profession}, got {user['profession']}"
        )
        # Property: reading_goal must be updated
        assert user['reading_goal'] == reading_goal, (
            f"Reading goal not updated: expected {reading_goal}, got {user['reading_goal']}"
        )


# ============================================================================
# Property 3: Unique Constraint Enforcement
# **Validates: Requirements 1.3, 1.4**
# ============================================================================

class TestUniqueConstraintEnforcementProperty:
    """
    Property-based tests for unique constraint enforcement.
    
    Property 3: Unique Constraint Enforcement
    *For any* two user creation attempts with the same username OR the same email,
    the second attempt SHALL fail with an error.
    
    **Validates: Requirements 1.3, 1.4**
    """

    @given(
        username=username_strategy,
        password1=password_strategy,
        password2=password_strategy
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_duplicate_username_rejected(self, test_db, user_service,
                                          username, password1, password2):
        """
        Property: Creating two users with the same username SHALL fail.
        
        **Validates: Requirements 1.3, 1.4**
        
        For any username, the second attempt to create a user with that
        username must raise an error.
        """
        # Ensure unique username for this test run
        unique_username = f"{username}_{get_unique_suffix()}_dup"
        
        # Create first user - should succeed
        user_service.create_user(unique_username, password1)
        
        # Create second user with same username - should fail
        with pytest.raises(Exception) as exc_info:
            user_service.create_user(unique_username, password2)
        
        # Property: an error must be raised (typically IntegrityError or similar)
        assert exc_info.value is not None, (
            f"No error raised for duplicate username: {unique_username}"
        )

    @given(
        username1=username_strategy,
        username2=username_strategy,
        password1=password_strategy,
        password2=password_strategy,
        email=email_strategy
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_duplicate_email_rejected(self, test_db, user_service,
                                       username1, username2, password1, password2, email):
        """
        Property: Creating two users with the same email SHALL fail.
        
        **Validates: Requirements 1.3, 1.4**
        
        For any email, the second attempt to create a user with that
        email must raise an error.
        """
        # Ensure usernames are different
        assume(username1 != username2)
        
        # Make email unique for this iteration to avoid conflicts with previous iterations
        unique_suffix = get_unique_suffix()
        unique_email = f"{unique_suffix}_{email}"
        
        # Ensure unique usernames for this test run
        unique_username1 = f"{username1}_{unique_suffix}_email1"
        unique_username2 = f"{username2}_{unique_suffix}_email2"
        
        # Create first user with email - should succeed
        user_service.create_user(unique_username1, password1, email=unique_email)
        
        # Create second user with same email - should fail
        with pytest.raises(Exception) as exc_info:
            user_service.create_user(unique_username2, password2, email=unique_email)
        
        # Property: an error must be raised
        assert exc_info.value is not None, (
            f"No error raised for duplicate email: {unique_email}"
        )

    @given(
        username1=username_strategy,
        username2=username_strategy,
        password1=password_strategy,
        password2=password_strategy,
        email1=email_strategy,
        email2=email_strategy
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_different_username_and_email_allowed(self, test_db, user_service,
                                                   username1, username2,
                                                   password1, password2,
                                                   email1, email2):
        """
        Property: Creating users with different usernames AND emails SHALL succeed.
        
        **Validates: Requirements 1.3, 1.4**
        
        For any two different username/email combinations, both users
        should be created successfully.
        """
        # Ensure usernames and emails are different
        assume(username1 != username2)
        assume(email1 != email2)
        
        # Make emails unique for this iteration to avoid conflicts with previous iterations
        unique_suffix1 = get_unique_suffix()
        unique_suffix2 = get_unique_suffix()
        unique_email1 = f"{unique_suffix1}_{email1}"
        unique_email2 = f"{unique_suffix2}_{email2}"
        
        # Ensure unique usernames for this test run
        unique_username1 = f"{username1}_{unique_suffix1}_diff1"
        unique_username2 = f"{username2}_{unique_suffix2}_diff2"
        
        # Create first user - should succeed
        user_id1 = user_service.create_user(unique_username1, password1, email=unique_email1)
        
        # Create second user - should also succeed
        user_id2 = user_service.create_user(unique_username2, password2, email=unique_email2)
        
        # Property: both users must be created with different IDs
        assert user_id1 != user_id2, (
            f"Both users got the same ID: {user_id1}"
        )
        
        # Verify both users exist
        user1 = user_service.get_user(user_id1)
        user2 = user_service.get_user(user_id2)
        
        assert user1 is not None, f"First user not found"
        assert user2 is not None, f"Second user not found"


# ============================================================================
# Property 4: User Deletion Soft Reference
# **Validates: Requirements 1.5**
# ============================================================================

class TestUserDeletionSoftReferenceProperty:
    """
    Property-based tests for user deletion soft reference.
    
    Property 4: User Deletion Soft Reference
    *For any* user with associated interpretations, when the user is deleted,
    all associated interpretations SHALL still exist with user_id set to NULL.
    
    **Validates: Requirements 1.5**
    """

    @given(
        username=username_strategy,
        password=password_strategy
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_user_deletion_sets_interpretation_user_id_to_null(
        self, test_db, user_service, book_service, interpretation_service,
        username, password
    ):
        """
        Property: Deleting a user SHALL set user_id to NULL in associated interpretations.
        
        **Validates: Requirements 1.5**
        
        For any user with interpretations, deleting the user must preserve
        the interpretations but set their user_id to NULL.
        """
        from sqlalchemy import text
        
        # Ensure unique username for this test
        unique_username = f"{username}_{get_unique_suffix()}_del"
        
        # Create user
        user_id = user_service.create_user(unique_username, password)
        
        # Create a book first (required for interpretation)
        book_id = book_service.create_book(f"test_book_{user_id}.pdf")
        
        # Create an interpretation associated with the user
        interpretation_id = interpretation_service.create_interpretation(
            book_id=book_id,
            content="Test interpretation content",
            user_id=user_id,
            interpretation_type='personalized'
        )
        
        # Verify interpretation exists with user_id
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT user_id FROM interpretations WHERE id = :id"),
                {"id": interpretation_id}
            ).fetchone()
        
        assert result is not None, "Interpretation not created"
        assert result[0] == user_id, f"Interpretation user_id mismatch: expected {user_id}, got {result[0]}"
        
        # Delete the user
        user_service.delete_user(user_id)
        
        # Verify interpretation still exists but user_id is NULL
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT id, user_id FROM interpretations WHERE id = :id"),
                {"id": interpretation_id}
            ).fetchone()
        
        # Property: interpretation must still exist
        assert result is not None, (
            f"Interpretation was deleted when user was deleted! "
            f"interpretation_id={interpretation_id}"
        )
        
        # Property: user_id must be NULL
        assert result[1] is None, (
            f"Interpretation user_id not set to NULL after user deletion! "
            f"user_id={result[1]}"
        )

    @given(
        username=username_strategy,
        password=password_strategy
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_multiple_interpretations_user_id_set_to_null(
        self, test_db, user_service, book_service, interpretation_service,
        username, password
    ):
        """
        Property: Deleting a user SHALL set user_id to NULL in ALL associated interpretations.
        
        **Validates: Requirements 1.5**
        
        For any user with multiple interpretations, deleting the user must
        set user_id to NULL in all of them.
        """
        from sqlalchemy import text
        
        # Ensure unique username for this test
        unique_username = f"{username}_{get_unique_suffix()}_multi"
        
        # Create user
        user_id = user_service.create_user(unique_username, password)
        
        # Create a book
        book_id = book_service.create_book(f"test_book_multi_{user_id}.pdf")
        
        # Create multiple interpretations
        interpretation_ids = []
        for i in range(3):
            interp_id = interpretation_service.create_interpretation(
                book_id=book_id,
                content=f"Test interpretation content {i}",
                user_id=user_id,
                interpretation_type='personalized'
            )
            interpretation_ids.append(interp_id)
        
        # Delete the user
        user_service.delete_user(user_id)
        
        # Verify all interpretations still exist with user_id = NULL
        with test_db.begin() as conn:
            for interp_id in interpretation_ids:
                result = conn.execute(
                    text("SELECT id, user_id FROM interpretations WHERE id = :id"),
                    {"id": interp_id}
                ).fetchone()
                
                # Property: interpretation must still exist
                assert result is not None, (
                    f"Interpretation {interp_id} was deleted when user was deleted!"
                )
                
                # Property: user_id must be NULL
                assert result[1] is None, (
                    f"Interpretation {interp_id} user_id not set to NULL! "
                    f"user_id={result[1]}"
                )

    @given(
        username=username_strategy,
        password=password_strategy
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_user_deletion_does_not_affect_other_users_interpretations(
        self, test_db, user_service, book_service, interpretation_service,
        username, password
    ):
        """
        Property: Deleting a user SHALL NOT affect other users' interpretations.
        
        **Validates: Requirements 1.5**
        
        For any two users with interpretations, deleting one user must not
        affect the other user's interpretations.
        """
        from sqlalchemy import text
        
        # Ensure unique usernames for this test
        unique_username1 = f"{username}_{get_unique_suffix()}_other1"
        unique_username2 = f"{username}_{get_unique_suffix()}_other2"
        
        # Create two users
        user_id1 = user_service.create_user(unique_username1, password)
        user_id2 = user_service.create_user(unique_username2, password + "2")
        
        # Create a book
        book_id = book_service.create_book(f"test_book_other_{user_id1}.pdf")
        
        # Create interpretations for both users
        interp_id1 = interpretation_service.create_interpretation(
            book_id=book_id,
            content="User 1 interpretation",
            user_id=user_id1,
            interpretation_type='personalized'
        )
        interp_id2 = interpretation_service.create_interpretation(
            book_id=book_id,
            content="User 2 interpretation",
            user_id=user_id2,
            interpretation_type='personalized'
        )
        
        # Delete user 1
        user_service.delete_user(user_id1)
        
        # Verify user 2's interpretation is unaffected
        with test_db.begin() as conn:
            result = conn.execute(
                text("SELECT user_id FROM interpretations WHERE id = :id"),
                {"id": interp_id2}
            ).fetchone()
        
        # Property: user 2's interpretation must still have user_id2
        assert result is not None, "User 2's interpretation was deleted!"
        assert result[0] == user_id2, (
            f"User 2's interpretation user_id was changed! "
            f"expected {user_id2}, got {result[0]}"
        )


# ============================================================================
# Additional Unit Tests for Edge Cases
# ============================================================================

class TestUserServiceEdgeCases:
    """Unit tests for edge cases in UserService."""

    def test_get_nonexistent_user_returns_none(self, test_db, user_service):
        """Test that getting a non-existent user returns None."""
        result = user_service.get_user(999999)
        assert result is None

    def test_authenticate_nonexistent_user_returns_none(self, test_db, user_service):
        """Test that authenticating a non-existent user returns None."""
        result = user_service.authenticate("nonexistent_user_xyz", "password123")
        assert result is None

    def test_delete_nonexistent_user_succeeds(self, test_db, user_service):
        """Test that deleting a non-existent user doesn't raise an error."""
        # Should not raise an exception
        result = user_service.delete_user(999999)
        assert result is True  # delete_user always returns True

    def test_update_profile_with_empty_focus_areas(self, test_db, user_service):
        """Test that updating profile with empty focus_areas works."""
        user_id = user_service.create_user("test_empty_focus", "password123")
        user_service.update_profile(user_id, focus_areas=[])
        
        user = user_service.get_user(user_id)
        assert user['focus_areas'] == []

    def test_user_created_with_timestamps(self, test_db, user_service):
        """Test that users are created with proper timestamps."""
        user_id = user_service.create_user("test_timestamp", "password123")
        user = user_service.get_user(user_id)
        
        assert user['created_at'] is not None
        # updated_at should be None initially (only set on profile update)

    def test_profile_update_sets_updated_at(self, test_db, user_service):
        """Test that profile updates set the updated_at timestamp."""
        user_id = user_service.create_user("test_updated_at", "password123")
        user_service.update_profile(user_id, profession="Developer")
        
        user = user_service.get_user(user_id)
        assert user['updated_at'] is not None
