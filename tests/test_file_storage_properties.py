"""
Property-Based Tests for FileStorageService

Feature: database-restructure
Property 9: File Storage and Hash Calculation

**Validates: Requirements 3.1, 3.2, 3.4**

This module tests the following properties:
- For any uploaded file, the system SHALL save it to uploads/ directory
- The stored file_hash SHALL equal the MD5 hash of the file content
"""
import os
import hashlib
import pytest
from hypothesis import given, strategies as st, settings, assume, HealthCheck

# ============================================================================
# Test Data Generators (Strategies)
# ============================================================================

# File content generator - generates binary data of various sizes
# Using reasonable sizes for testing (100 bytes to 10KB)
file_content_strategy = st.binary(min_size=1, max_size=10000)

# Filename generator - valid filenames without path separators
filename_strategy = st.text(
    min_size=1, 
    max_size=100,
    alphabet=st.characters(
        whitelist_categories=('L', 'N', 'P'),  # Letters, Numbers, Punctuation
        blacklist_characters='/<>:"|?*\\\x00'  # Invalid filename chars
    )
).filter(lambda x: x.strip() and not x.startswith('.'))


# ============================================================================
# Property 9: File Storage and Hash Calculation
# **Validates: Requirements 3.1, 3.2, 3.4**
# ============================================================================

class TestFileStorageProperties:
    """
    Property-based tests for FileStorageService.
    
    Property 9: File Storage and Hash Calculation
    *For any* uploaded file, the system SHALL save it to uploads/ directory 
    AND the stored file_hash SHALL equal the MD5 hash of the file content.
    
    **Validates: Requirements 3.1, 3.2, 3.4**
    """

    @given(file_data=file_content_strategy)
    @settings(
        max_examples=100, 
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_hash_calculation_equals_md5(self, file_storage_service, file_data):
        """
        Property: The calculated hash SHALL equal the MD5 hash of the file content.
        
        **Validates: Requirements 3.2**
        
        For any binary file content, calculate_hash() must return the same
        value as hashlib.md5().hexdigest().
        """
        # Calculate hash using FileStorageService
        service_hash = file_storage_service.calculate_hash(file_data)
        
        # Calculate expected MD5 hash directly
        expected_hash = hashlib.md5(file_data).hexdigest()
        
        # Property: hashes must be equal
        assert service_hash == expected_hash, (
            f"Hash mismatch: service returned {service_hash}, "
            f"expected MD5 {expected_hash}"
        )

    @given(file_data=file_content_strategy, filename=filename_strategy)
    @settings(
        max_examples=100, 
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_file_saved_to_uploads_directory(self, file_storage_service, file_data, filename):
        """
        Property: Files SHALL be saved to the uploads/ directory.
        
        **Validates: Requirements 3.1**
        
        For any file content and filename, save_file() must create a file
        in the UPLOAD_DIR directory.
        """
        # Ensure filename is valid
        assume(len(filename.strip()) > 0)
        
        # Save the file
        file_path, file_hash = file_storage_service.save_file(file_data, filename)
        
        # Property 1: File path must be within UPLOAD_DIR
        assert file_path.startswith(file_storage_service.UPLOAD_DIR), (
            f"File path {file_path} is not within upload directory "
            f"{file_storage_service.UPLOAD_DIR}"
        )
        
        # Property 2: File must exist at the returned path
        assert os.path.exists(file_path), (
            f"File was not created at path: {file_path}"
        )
        
        # Property 3: File must be in the uploads directory (not a subdirectory)
        assert os.path.dirname(file_path) == file_storage_service.UPLOAD_DIR, (
            f"File was saved to subdirectory instead of uploads/: {file_path}"
        )

    @given(file_data=file_content_strategy, filename=filename_strategy)
    @settings(
        max_examples=100, 
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_saved_file_hash_equals_md5(self, file_storage_service, file_data, filename):
        """
        Property: The returned file_hash SHALL equal the MD5 hash of the file content.
        
        **Validates: Requirements 3.2, 3.4**
        
        For any file saved, the returned file_hash must match the MD5 hash
        of the original file content.
        """
        # Ensure filename is valid
        assume(len(filename.strip()) > 0)
        
        # Save the file
        file_path, returned_hash = file_storage_service.save_file(file_data, filename)
        
        # Calculate expected MD5 hash
        expected_hash = hashlib.md5(file_data).hexdigest()
        
        # Property: returned hash must equal MD5 of content
        assert returned_hash == expected_hash, (
            f"Returned hash {returned_hash} does not match "
            f"MD5 of content {expected_hash}"
        )

    @given(file_data=file_content_strategy, filename=filename_strategy)
    @settings(
        max_examples=100, 
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_saved_file_content_matches_original(self, file_storage_service, file_data, filename):
        """
        Property: The saved file content SHALL match the original content.
        
        **Validates: Requirements 3.1**
        
        For any file saved, reading the file back must return the exact
        same bytes that were written.
        """
        # Ensure filename is valid
        assume(len(filename.strip()) > 0)
        
        # Save the file
        file_path, file_hash = file_storage_service.save_file(file_data, filename)
        
        # Read the file back
        with open(file_path, 'rb') as f:
            saved_content = f.read()
        
        # Property: saved content must equal original content
        assert saved_content == file_data, (
            f"Saved file content does not match original. "
            f"Original size: {len(file_data)}, Saved size: {len(saved_content)}"
        )

    @given(file_data=file_content_strategy, filename=filename_strategy)
    @settings(
        max_examples=100, 
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_saved_file_hash_matches_file_on_disk(self, file_storage_service, file_data, filename):
        """
        Property: The MD5 hash of the saved file SHALL equal the returned hash.
        
        **Validates: Requirements 3.2, 3.4**
        
        For any file saved, computing the MD5 hash of the file on disk
        must return the same hash that was returned by save_file().
        """
        # Ensure filename is valid
        assume(len(filename.strip()) > 0)
        
        # Save the file
        file_path, returned_hash = file_storage_service.save_file(file_data, filename)
        
        # Read file and compute hash
        with open(file_path, 'rb') as f:
            disk_content = f.read()
        disk_hash = hashlib.md5(disk_content).hexdigest()
        
        # Property: hash of file on disk must equal returned hash
        assert disk_hash == returned_hash, (
            f"Hash of file on disk {disk_hash} does not match "
            f"returned hash {returned_hash}"
        )

    @given(file_data=file_content_strategy, filename=filename_strategy)
    @settings(
        max_examples=100, 
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_save_file_is_idempotent(self, file_storage_service, file_data, filename):
        """
        Property: Saving the same file twice SHALL return the same path and hash.
        
        **Validates: Requirements 3.1, 3.2**
        
        For any file content and filename, calling save_file() twice with
        the same arguments must return identical results.
        """
        # Ensure filename is valid
        assume(len(filename.strip()) > 0)
        
        # Save the file twice
        path1, hash1 = file_storage_service.save_file(file_data, filename)
        path2, hash2 = file_storage_service.save_file(file_data, filename)
        
        # Property: both calls must return the same path
        assert path1 == path2, (
            f"Saving same file twice returned different paths: "
            f"{path1} vs {path2}"
        )
        
        # Property: both calls must return the same hash
        assert hash1 == hash2, (
            f"Saving same file twice returned different hashes: "
            f"{hash1} vs {hash2}"
        )


# ============================================================================
# Additional Unit Tests for Edge Cases
# ============================================================================

class TestFileStorageEdgeCases:
    """Unit tests for edge cases in FileStorageService."""

    def test_empty_file_hash(self, file_storage_service):
        """Test that empty files have a consistent hash."""
        empty_data = b''
        hash_result = file_storage_service.calculate_hash(empty_data)
        expected = hashlib.md5(b'').hexdigest()
        assert hash_result == expected

    def test_large_file_hash(self, file_storage_service):
        """Test hash calculation for larger files."""
        # 1MB of data
        large_data = b'x' * (1024 * 1024)
        hash_result = file_storage_service.calculate_hash(large_data)
        expected = hashlib.md5(large_data).hexdigest()
        assert hash_result == expected

    def test_binary_file_content(self, file_storage_service):
        """Test that binary content (non-text) is handled correctly."""
        # Binary data with null bytes and high bytes
        binary_data = bytes(range(256)) * 10
        hash_result = file_storage_service.calculate_hash(binary_data)
        expected = hashlib.md5(binary_data).hexdigest()
        assert hash_result == expected

    def test_upload_directory_created_if_missing(self, file_storage_service, temp_upload_dir):
        """Test that upload directory is created if it doesn't exist."""
        import shutil
        
        # Remove the temp directory
        shutil.rmtree(temp_upload_dir)
        assert not os.path.exists(temp_upload_dir)
        
        # Save a file - should create the directory
        file_data = b'test content'
        file_path, file_hash = file_storage_service.save_file(file_data, 'test.pdf')
        
        # Directory should now exist
        assert os.path.exists(temp_upload_dir)
        assert os.path.exists(file_path)
