"""
Property-Based Tests for PromptService

Feature: database-restructure
Property 23: Prompt Activation Exclusivity

**Validates: Requirements 8.3, 8.4**

This module tests the following property:
- Property 23: Prompt Activation Exclusivity - when a prompt is set as active,
  all other prompts of the same type SHALL have is_active set to 0.
"""
import os
import sys
import tempfile
import uuid
import pytest
from hypothesis import given, strategies as st, settings, assume, HealthCheck

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Global counter for unique names
_test_counter = 0

def get_unique_suffix():
    """Generate a unique suffix for prompt names to avoid collisions."""
    global _test_counter
    _test_counter += 1
    return f"{uuid.uuid4().hex[:8]}_{_test_counter}"


# ============================================================================
# Test Data Generators (Strategies)
# ============================================================================

# Valid prompt types as defined in PromptService
VALID_PROMPT_TYPES = ['interpretation', 'restructure', 'translation', 'summary']

# Prompt type generator - only valid types
prompt_type_strategy = st.sampled_from(VALID_PROMPT_TYPES)

# Prompt name generator - reasonable text
prompt_name_strategy = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S'))
).filter(lambda x: len(x.strip()) >= 1 and '\x00' not in x)

# Version generator - semantic version style
version_strategy = st.from_regex(r'v[0-9]+\.[0-9]+\.[0-9]+', fullmatch=True)

# Content generator - prompt content text
content_strategy = st.text(
    min_size=1,
    max_size=1000,
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S', 'Z'))
).filter(lambda x: len(x.strip()) >= 1 and '\x00' not in x)


# ============================================================================
# Service Classes - Standalone implementations for testing
# ============================================================================

class StandalonePromptService:
    """Standalone PromptService for testing, mirrors app.py implementation."""
    
    VALID_TYPES = ['interpretation', 'restructure', 'translation', 'summary']
    
    def __init__(self, engine):
        self.engine = engine
    
    def create_prompt(self, name: str, prompt_type: str, version: str,
                     content: str, is_active: bool = False) -> int:
        """创建提示词版本，返回 prompt_id"""
        from sqlalchemy import text
        from datetime import datetime
        
        if prompt_type not in self.VALID_TYPES:
            raise ValueError(f"Invalid prompt type: {prompt_type}")
        
        with self.engine.begin() as conn:
            # 如果设为激活，先将同类型其他版本设为非激活
            if is_active:
                conn.execute(
                    text("UPDATE prompts SET is_active = 0 WHERE type = :type"),
                    {"type": prompt_type}
                )
            
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
    
    def get_active_prompt(self, prompt_type: str):
        """获取指定类型的激活提示词"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT * FROM prompts
                    WHERE type = :type AND is_active = 1
                    LIMIT 1
                    """
                ),
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
            ).scalar_one_or_none()
            
            if not result:
                return False
            
            prompt_type = result
            
            # 将同类型其他版本设为非激活
            conn.execute(
                text("UPDATE prompts SET is_active = 0 WHERE type = :type"),
                {"type": prompt_type}
            )
            
            # 设置当前版本为激活
            conn.execute(
                text("UPDATE prompts SET is_active = 1 WHERE id = :prompt_id"),
                {"prompt_id": prompt_id}
            )
        return True
    
    def list_prompts(self, prompt_type=None):
        """列出提示词，可按类型筛选"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            if prompt_type:
                results = conn.execute(
                    text(
                        """
                        SELECT * FROM prompts WHERE type = :type
                        ORDER BY created_at DESC
                        """
                    ),
                    {"type": prompt_type}
                ).mappings().all()
            else:
                results = conn.execute(
                    text("SELECT * FROM prompts ORDER BY type, created_at DESC")
                ).mappings().all()
        return [dict(r) for r in results]
    
    def get_all_prompts_by_type(self, prompt_type: str):
        """获取指定类型的所有提示词（用于测试验证）"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            results = conn.execute(
                text("SELECT * FROM prompts WHERE type = :type"),
                {"type": prompt_type}
            ).mappings().all()
        return [dict(r) for r in results]
    
    def count_active_prompts_by_type(self, prompt_type: str) -> int:
        """统计指定类型的激活提示词数量（用于测试验证）"""
        from sqlalchemy import text
        
        with self.engine.begin() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM prompts WHERE type = :type AND is_active = 1"),
                {"type": prompt_type}
            ).scalar()
        return count


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def test_db():
    """Create a temporary database for testing with prompts table."""
    from sqlalchemy import create_engine, text
    
    # Create a temporary database file
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    # Create engine
    engine = create_engine(f'sqlite:///{db_path}')
    
    # Initialize database schema
    with engine.begin() as conn:
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
    
    yield engine
    
    # Cleanup
    engine.dispose()
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture(scope="function")
def prompt_service(test_db):
    """Get PromptService instance for testing."""
    return StandalonePromptService(test_db)


# ============================================================================
# Property 23: Prompt Activation Exclusivity
# **Validates: Requirements 8.3, 8.4**
# ============================================================================

class TestPromptActivationExclusivityProperty:
    """
    Property-based tests for prompt activation exclusivity.
    
    Property 23: Prompt Activation Exclusivity
    *For any* prompt type, when a prompt is set as active, all other prompts
    of the same type SHALL have is_active set to 0.
    
    **Validates: Requirements 8.3, 8.4**
    """

    @given(
        prompt_type=prompt_type_strategy,
        name1=prompt_name_strategy,
        name2=prompt_name_strategy,
        version1=version_strategy,
        version2=version_strategy,
        content1=content_strategy,
        content2=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_set_active_deactivates_other_prompts_of_same_type(
        self, test_db, prompt_service, prompt_type, 
        name1, name2, version1, version2, content1, content2
    ):
        """
        Property: When a prompt is set as active, all other prompts of the same
        type SHALL have is_active set to 0.
        
        **Validates: Requirements 8.3, 8.4**
        
        For any prompt type, activating one prompt must deactivate all others
        of the same type.
        """
        # Ensure unique names and versions
        suffix = get_unique_suffix()
        unique_name1 = f"{name1}_{suffix}_1"
        unique_name2 = f"{name2}_{suffix}_2"
        unique_version1 = f"{version1}_{suffix}_1"
        unique_version2 = f"{version2}_{suffix}_2"
        
        # Create first prompt as active
        prompt_id1 = prompt_service.create_prompt(
            unique_name1, prompt_type, unique_version1, content1, is_active=True
        )
        
        # Verify first prompt is active
        active_count = prompt_service.count_active_prompts_by_type(prompt_type)
        assert active_count == 1, f"Expected 1 active prompt, got {active_count}"
        
        # Create second prompt as active (should deactivate first)
        prompt_id2 = prompt_service.create_prompt(
            unique_name2, prompt_type, unique_version2, content2, is_active=True
        )
        
        # Property: Only one prompt of this type should be active
        active_count = prompt_service.count_active_prompts_by_type(prompt_type)
        assert active_count == 1, (
            f"Expected exactly 1 active prompt of type '{prompt_type}', "
            f"but found {active_count}"
        )
        
        # Property: The active prompt should be the second one
        active_prompt = prompt_service.get_active_prompt(prompt_type)
        assert active_prompt is not None, "No active prompt found"
        assert active_prompt['id'] == prompt_id2, (
            f"Expected prompt {prompt_id2} to be active, "
            f"but prompt {active_prompt['id']} is active"
        )
        
        # Property: First prompt should be inactive
        all_prompts = prompt_service.get_all_prompts_by_type(prompt_type)
        first_prompt = next((p for p in all_prompts if p['id'] == prompt_id1), None)
        assert first_prompt is not None, "First prompt not found"
        assert first_prompt['is_active'] == 0, (
            f"First prompt should be inactive (is_active=0), "
            f"but is_active={first_prompt['is_active']}"
        )

    @given(
        prompt_type=prompt_type_strategy,
        num_prompts=st.integers(min_value=2, max_value=5),
        content=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_set_active_with_multiple_prompts(
        self, test_db, prompt_service, prompt_type, num_prompts, content
    ):
        """
        Property: With multiple prompts of the same type, setting any one as active
        SHALL result in exactly one active prompt.
        
        **Validates: Requirements 8.3, 8.4**
        
        For any number of prompts of the same type, only one can be active at a time.
        """
        suffix = get_unique_suffix()
        prompt_ids = []
        
        # Create multiple prompts (all inactive initially)
        for i in range(num_prompts):
            prompt_id = prompt_service.create_prompt(
                f"prompt_{suffix}_{i}",
                prompt_type,
                f"v1.0.{i}_{suffix}",
                f"{content}_{i}",
                is_active=False
            )
            prompt_ids.append(prompt_id)
        
        # Verify all created prompts are inactive
        all_prompts = prompt_service.get_all_prompts_by_type(prompt_type)
        created_prompts = [p for p in all_prompts if p['id'] in prompt_ids]
        inactive_count = sum(1 for p in created_prompts if p['is_active'] == 0)
        assert inactive_count == num_prompts, (
            f"Expected all {num_prompts} created prompts to be inactive, "
            f"but {inactive_count} are inactive"
        )
        
        # Activate each prompt in sequence and verify exclusivity
        for i, prompt_id in enumerate(prompt_ids):
            prompt_service.set_active(prompt_id)
            
            # Property: Exactly one prompt should be active among our created prompts
            all_prompts = prompt_service.get_all_prompts_by_type(prompt_type)
            created_prompts = [p for p in all_prompts if p['id'] in prompt_ids]
            active_created = [p for p in created_prompts if p['is_active'] == 1]
            
            assert len(active_created) == 1, (
                f"After activating prompt {i+1}/{num_prompts}, "
                f"expected 1 active prompt among created prompts, got {len(active_created)}"
            )
            
            # Property: The activated prompt should be the current one
            assert active_created[0]['id'] == prompt_id, (
                f"Expected prompt {prompt_id} to be active, "
                f"but prompt {active_created[0]['id']} is active"
            )
            
            # Property: Overall, only one prompt of this type should be active
            active_count = prompt_service.count_active_prompts_by_type(prompt_type)
            assert active_count == 1, (
                f"After activating prompt {i+1}/{num_prompts}, "
                f"expected 1 active prompt total, got {active_count}"
            )

    @given(
        type1=prompt_type_strategy,
        type2=prompt_type_strategy,
        name=prompt_name_strategy,
        version=version_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_activation_does_not_affect_other_types(
        self, test_db, prompt_service, type1, type2, name, version, content
    ):
        """
        Property: Activating a prompt of one type SHALL NOT affect prompts of
        other types.
        
        **Validates: Requirements 8.3, 8.4**
        
        Activation exclusivity only applies within the same prompt type.
        """
        # Skip if types are the same (we want to test cross-type behavior)
        assume(type1 != type2)
        
        suffix = get_unique_suffix()
        
        # Create and activate a prompt of type1
        prompt_id1 = prompt_service.create_prompt(
            f"{name}_{suffix}_t1",
            type1,
            f"{version}_{suffix}_t1",
            f"{content}_t1",
            is_active=True
        )
        
        # Create and activate a prompt of type2
        prompt_id2 = prompt_service.create_prompt(
            f"{name}_{suffix}_t2",
            type2,
            f"{version}_{suffix}_t2",
            f"{content}_t2",
            is_active=True
        )
        
        # Property: Both prompts should be active (different types)
        active1 = prompt_service.get_active_prompt(type1)
        active2 = prompt_service.get_active_prompt(type2)
        
        assert active1 is not None, f"No active prompt for type '{type1}'"
        assert active2 is not None, f"No active prompt for type '{type2}'"
        assert active1['id'] == prompt_id1, (
            f"Expected prompt {prompt_id1} to be active for type '{type1}'"
        )
        assert active2['id'] == prompt_id2, (
            f"Expected prompt {prompt_id2} to be active for type '{type2}'"
        )
        
        # Property: Each type should have exactly one active prompt
        assert prompt_service.count_active_prompts_by_type(type1) == 1
        assert prompt_service.count_active_prompts_by_type(type2) == 1

    @given(
        prompt_type=prompt_type_strategy,
        name=prompt_name_strategy,
        version=version_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_create_with_is_active_true_deactivates_existing(
        self, test_db, prompt_service, prompt_type, name, version, content
    ):
        """
        Property: Creating a prompt with is_active=True SHALL deactivate all
        existing prompts of the same type.
        
        **Validates: Requirements 8.3, 8.4**
        
        The create_prompt function with is_active=True should enforce exclusivity.
        """
        suffix = get_unique_suffix()
        
        # Create first prompt as active
        prompt_id1 = prompt_service.create_prompt(
            f"{name}_{suffix}_first",
            prompt_type,
            f"{version}_{suffix}_first",
            f"{content}_first",
            is_active=True
        )
        
        # Verify first prompt is active
        active_prompt = prompt_service.get_active_prompt(prompt_type)
        assert active_prompt is not None
        assert active_prompt['id'] == prompt_id1
        
        # Create second prompt as active
        prompt_id2 = prompt_service.create_prompt(
            f"{name}_{suffix}_second",
            prompt_type,
            f"{version}_{suffix}_second",
            f"{content}_second",
            is_active=True
        )
        
        # Property: Only the second prompt should be active
        active_prompt = prompt_service.get_active_prompt(prompt_type)
        assert active_prompt is not None
        assert active_prompt['id'] == prompt_id2, (
            f"Expected newly created prompt {prompt_id2} to be active, "
            f"but prompt {active_prompt['id']} is active"
        )
        
        # Property: First prompt should be inactive
        all_prompts = prompt_service.get_all_prompts_by_type(prompt_type)
        first_prompt = next((p for p in all_prompts if p['id'] == prompt_id1), None)
        assert first_prompt['is_active'] == 0, (
            f"First prompt should be deactivated after creating new active prompt"
        )

    @given(
        prompt_type=prompt_type_strategy,
        name=prompt_name_strategy,
        version=version_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_set_active_on_nonexistent_prompt_returns_false(
        self, test_db, prompt_service, prompt_type, name, version, content
    ):
        """
        Property: Calling set_active on a non-existent prompt_id SHALL return False
        and not affect any existing prompts.
        
        **Validates: Requirements 8.3, 8.4**
        
        Edge case: set_active should handle invalid prompt IDs gracefully.
        """
        suffix = get_unique_suffix()
        
        # Create a prompt as active
        prompt_id = prompt_service.create_prompt(
            f"{name}_{suffix}",
            prompt_type,
            f"{version}_{suffix}",
            content,
            is_active=True
        )
        
        # Try to activate a non-existent prompt
        nonexistent_id = 999999
        result = prompt_service.set_active(nonexistent_id)
        
        # Property: set_active should return False for non-existent prompt
        assert result is False, (
            f"set_active should return False for non-existent prompt ID {nonexistent_id}"
        )
        
        # Property: The existing active prompt should remain active
        active_prompt = prompt_service.get_active_prompt(prompt_type)
        assert active_prompt is not None
        assert active_prompt['id'] == prompt_id, (
            f"Existing active prompt should remain active after failed set_active"
        )

    @given(
        prompt_type=prompt_type_strategy,
        name=prompt_name_strategy,
        version=version_strategy,
        content=content_strategy
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_set_active_on_already_active_prompt_is_idempotent(
        self, test_db, prompt_service, prompt_type, name, version, content
    ):
        """
        Property: Calling set_active on an already active prompt SHALL be idempotent
        (the prompt remains active, no other changes).
        
        **Validates: Requirements 8.3, 8.4**
        
        Edge case: Activating an already active prompt should not cause issues.
        """
        suffix = get_unique_suffix()
        
        # Create a prompt as active
        prompt_id = prompt_service.create_prompt(
            f"{name}_{suffix}",
            prompt_type,
            f"{version}_{suffix}",
            content,
            is_active=True
        )
        
        # Call set_active on the already active prompt
        result = prompt_service.set_active(prompt_id)
        
        # Property: set_active should return True
        assert result is True, "set_active should return True for existing prompt"
        
        # Property: The prompt should still be active
        active_prompt = prompt_service.get_active_prompt(prompt_type)
        assert active_prompt is not None
        assert active_prompt['id'] == prompt_id
        
        # Property: There should still be exactly one active prompt
        active_count = prompt_service.count_active_prompts_by_type(prompt_type)
        assert active_count == 1, (
            f"Expected 1 active prompt after idempotent set_active, got {active_count}"
        )
