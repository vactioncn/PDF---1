"""
Pytest configuration and fixtures for database-restructure tests.
"""
import os
import sys
import tempfile
import shutil
import hashlib
import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="function")
def temp_upload_dir():
    """Create a temporary upload directory for testing."""
    temp_dir = tempfile.mkdtemp(prefix="test_uploads_")
    yield temp_dir
    # Cleanup after test
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


class StandaloneFileStorageService:
    """
    Standalone FileStorageService for testing.
    
    This is a copy of the FileStorageService logic that can be used
    independently of the Flask app context, with a configurable upload directory.
    """
    
    def __init__(self, upload_dir: str):
        self.UPLOAD_DIR = upload_dir
    
    def calculate_hash(self, file_data: bytes) -> str:
        """计算文件 MD5 哈希"""
        return hashlib.md5(file_data).hexdigest()
    
    def save_file(self, file_data: bytes, filename: str) -> tuple:
        """保存文件，返回 (file_path, file_hash)"""
        file_hash = self.calculate_hash(file_data)
        # 使用哈希值作为文件名前缀，避免重名
        safe_filename = f"{file_hash}_{filename}"
        file_path = os.path.join(self.UPLOAD_DIR, safe_filename)
        
        # 如果文件已存在，直接返回
        if os.path.exists(file_path):
            return file_path, file_hash
        
        # 确保目录存在
        if not os.path.exists(self.UPLOAD_DIR):
            os.makedirs(self.UPLOAD_DIR)
        
        # 保存文件
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
        except Exception as e:
            print(f"删除文件失败: {e}", flush=True)
            return False


@pytest.fixture(scope="function")
def file_storage_service(temp_upload_dir):
    """
    Create a FileStorageService instance with a temporary upload directory.
    This isolates tests from the production uploads/ directory.
    """
    return StandaloneFileStorageService(temp_upload_dir)


@pytest.fixture(scope="function")
def app_context():
    """Create Flask app context for tests that need database access."""
    from app import create_app
    
    app = create_app()
    
    with app.app_context():
        yield app
