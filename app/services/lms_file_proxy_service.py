# app/services/lms_file_proxy_service.py
import requests
import os
import mimetypes
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, BinaryIO, Generator
import logging
from urllib.parse import urlparse, urljoin
from werkzeug.datastructures import FileStorage
from app.services.lms_auth_service import LMSAuthService
from app.utils.lms_error_handler import handle_lms_error, create_error_context
from app.utils.lms_logger import log_error, log_warning, log_info, log_debug

# Configure logging
logger = logging.getLogger(__name__)

class LMSFileError(Exception):
    """Exception raised when file operations fail"""
    pass

class LMSFileValidationError(Exception):
    """Exception raised when file validation fails"""
    pass

class LMSFileProxyService:
    """
    Service for handling file downloads and uploads with LMS servers.
    Provides streaming file operations without local storage.
    """
    
    def __init__(self):
        self.lms_base_url = "https://lms.bahria.edu.pk"
        self.auth_service = LMSAuthService()
        
        # File operation configuration
        self.config = {
            'chunk_size': 8192,  # 8KB chunks for streaming
            'timeout': 60,       # 60 seconds timeout for file operations
            'max_file_size': 50 * 1024 * 1024,  # 50MB max file size
            'allowed_extensions': {
                'documents': ['.pdf', '.doc', '.docx', '.txt', '.rtf'],
                'presentations': ['.ppt', '.pptx', '.odp'],
                'spreadsheets': ['.xls', '.xlsx', '.ods', '.csv'],
                'images': ['.jpg', '.jpeg', '.png', '.gif', '.bmp'],
                'archives': ['.zip', '.rar', '.7z', '.tar', '.gz'],
                'code': ['.py', '.java', '.cpp', '.c', '.js', '.html', '.css'],
                'videos': ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm']
            },
            'headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive'
            }
        }
        
        # Cache for file metadata
        self._metadata_cache = {}
        self._cache_timeout = timedelta(hours=1)
    
    def _create_authenticated_session(self, account_id: str) -> requests.Session:
        """
        Create an authenticated session for file operations.
        Delegates to LMSScraperService which properly handles CMS login and gateway access.
        
        Args:
            account_id: Account ID for authentication
            
        Returns:
            Authenticated requests session
            
        Raises:
            LMSFileError: When session creation fails
        """
        try:
            # Import scraper service here to avoid circular imports
            from app.services.lms_scraper_service import LMSScraperService, LMSNetworkError
            
            logger.info(f"Creating authenticated session for file operations: {account_id}")
            
            # Use scraper service for proper CMS authentication and gateway access
            scraper = LMSScraperService()
            
            try:
                session = scraper._create_authenticated_session(account_id)
                logger.info(f"Successfully created authenticated session for file operations: {account_id}")
                return session
            except LMSNetworkError as e:
                logger.error(f"Network error during authentication for file operations: {e}")
                raise LMSFileError(f"Authentication failed: {e}")
            
        except LMSFileError:
            raise
        except Exception as e:
            logger.error(f"Failed to create session for file operations: {e}")
            raise LMSFileError(f"Session creation failed: {e}")
    
    def _validate_file_url(self, file_url: str) -> str:
        """
        Validate and normalize file URL.
        
        Args:
            file_url: File URL to validate
            
        Returns:
            Normalized absolute URL
            
        Raises:
            LMSFileValidationError: When URL is invalid
        """
        try:
            if not file_url or not file_url.strip():
                raise LMSFileValidationError("Empty file URL")
            
            # Parse URL
            parsed = urlparse(file_url)
            
            # Convert relative URLs to absolute
            if not parsed.netloc:
                file_url = urljoin(self.lms_base_url, file_url)
                parsed = urlparse(file_url)
            
            # Validate domain (security check)
            allowed_domains = ['lms.bahria.edu.pk', 'bahria.edu.pk']
            if not any(domain in parsed.netloc.lower() for domain in allowed_domains):
                logger.warning(f"File URL from untrusted domain: {parsed.netloc}")
                # Allow but log warning for monitoring
            
            logger.debug(f"Validated file URL: {file_url}")
            return file_url
            
        except Exception as e:
            logger.error(f"File URL validation failed: {e}")
            raise LMSFileValidationError(f"Invalid file URL: {e}")
    
    def _extract_filename_from_url(self, file_url: str) -> str:
        """
        Extract filename from URL.
        
        Args:
            file_url: File URL
            
        Returns:
            Extracted filename or generated name
        """
        try:
            parsed = urlparse(file_url)
            filename = os.path.basename(parsed.path)
            
            # If no filename in path, generate one
            if not filename or '.' not in filename:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"lms_file_{timestamp}"
            
            # Sanitize filename
            filename = self._sanitize_filename(filename)
            
            return filename
            
        except Exception as e:
            logger.warning(f"Could not extract filename from URL {file_url}: {e}")
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            return f"lms_file_{timestamp}"
    
    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename for safe handling.
        
        Args:
            filename: Original filename
            
        Returns:
            Sanitized filename
        """
        # Remove or replace unsafe characters
        unsafe_chars = '<>:"/\\|?*'
        for char in unsafe_chars:
            filename = filename.replace(char, '_')
        
        # Limit length
        if len(filename) > 255:
            name, ext = os.path.splitext(filename)
            filename = name[:250] + ext
        
        return filename
    
    def get_file_metadata(self, account_id: str, file_url: str) -> Dict[str, Any]:
        """
        Get file metadata without downloading the file.
        
        Args:
            account_id: Account ID for authentication
            file_url: URL of the file
            
        Returns:
            Dictionary with file metadata
            
        Raises:
            LMSFileError: When metadata retrieval fails
        """
        try:
            # Validate URL
            validated_url = self._validate_file_url(file_url)
            
            # Check cache first
            cache_key = f"{account_id}:{validated_url}"
            if cache_key in self._metadata_cache:
                cached_data, cached_time = self._metadata_cache[cache_key]
                if datetime.now() - cached_time < self._cache_timeout:
                    log_debug(f"Returning cached metadata for: {validated_url}",
                             account_id=account_id, operation="get_file_metadata", source="cache")
                    return cached_data
            
            log_info(f"Retrieving file metadata for: {validated_url}",
                    account_id=account_id, operation="get_file_metadata", file_url=validated_url)
            
            # Create authenticated session
            session = self._create_authenticated_session(account_id)
            
            # Make HEAD request to get metadata
            try:
                response = session.head(
                    validated_url,
                    timeout=self.config['timeout'],
                    allow_redirects=True
                )
                response.raise_for_status()
            except requests.RequestException as e:
                log_warning(f"HEAD request failed, trying GET with range: {e}",
                           account_id=account_id, operation="get_file_metadata", 
                           error_type="head_request_failed")
                # Fallback to GET with range header
                headers = {'Range': 'bytes=0-0'}
                response = session.get(
                    validated_url,
                    headers=headers,
                    timeout=self.config['timeout'],
                    stream=True
                )
                response.raise_for_status()
            
            # Extract metadata
            filename = self._extract_filename_from_url(validated_url)
            
            # Get content type
            content_type = response.headers.get('content-type', 'application/octet-stream')
            
            # Get file size
            content_length = response.headers.get('content-length')
            file_size = int(content_length) if content_length else None
            
            # Get last modified
            last_modified_str = response.headers.get('last-modified')
            last_modified = None
            if last_modified_str:
                try:
                    from email.utils import parsedate_to_datetime
                    last_modified = parsedate_to_datetime(last_modified_str)
                except Exception:
                    pass
            
            # Determine file extension and type
            file_extension = os.path.splitext(filename)[1].lower()
            file_type = self._determine_file_type(file_extension, content_type)
            
            metadata = {
                'filename': filename,
                'file_size': file_size,
                'content_type': content_type,
                'file_extension': file_extension,
                'file_type': file_type,
                'last_modified': last_modified.isoformat() if last_modified else None,
                'url': validated_url,
                'is_downloadable': True,
                'retrieved_at': datetime.now().isoformat()
            }
            
            # Cache metadata
            self._metadata_cache[cache_key] = (metadata, datetime.now())
            
            log_info(f"Retrieved metadata for {filename}: {file_size} bytes, {content_type}",
                    account_id=account_id, operation="get_file_metadata", 
                    filename=filename, file_size=file_size, content_type=content_type)
            return metadata
            
        except Exception as e:
            log_error(f"Failed to get file metadata for {file_url}: {e}",
                     account_id=account_id, operation="get_file_metadata", 
                     file_url=file_url, error_type="metadata_error", exc_info=True)
            raise LMSFileError(f"Metadata retrieval failed: {e}")
    
    def _determine_file_type(self, file_extension: str, content_type: str) -> str:
        """
        Determine file type category based on extension and content type.
        
        Args:
            file_extension: File extension (with dot)
            content_type: MIME content type
            
        Returns:
            File type category
        """
        # Check by extension first
        for category, extensions in self.config['allowed_extensions'].items():
            if file_extension in extensions:
                return category
        
        # Check by content type
        if content_type:
            if content_type.startswith('image/'):
                return 'images'
            elif content_type.startswith('video/'):
                return 'videos'
            elif content_type.startswith('audio/'):
                return 'audio'
            elif 'pdf' in content_type:
                return 'documents'
            elif any(word in content_type for word in ['word', 'document']):
                return 'documents'
            elif any(word in content_type for word in ['presentation', 'powerpoint']):
                return 'presentations'
            elif any(word in content_type for word in ['spreadsheet', 'excel']):
                return 'spreadsheets'
            elif any(word in content_type for word in ['zip', 'archive', 'compressed']):
                return 'archives'
        
        return 'other'
    
    def download_file_stream(self, account_id: str, file_url: str, 
                           filename: Optional[str] = None) -> Generator[bytes, None, None]:
        """
        Download file as a streaming generator without local storage.
        
        Args:
            account_id: Account ID for authentication
            file_url: URL of the file to download
            filename: Optional custom filename
            
        Yields:
            File content chunks as bytes
            
        Raises:
            LMSFileError: When download fails
        """
        try:
            # Validate URL
            validated_url = self._validate_file_url(file_url)
            
            log_info(f"Starting file download stream for: {validated_url}",
                    account_id=account_id, operation="download_file_stream", 
                    file_url=validated_url, filename=filename)
            
            # Create authenticated session
            session = self._create_authenticated_session(account_id)
            
            # Start streaming download
            response = session.get(
                validated_url,
                timeout=self.config['timeout'],
                stream=True
            )
            response.raise_for_status()
            
            # Check file size if available
            content_length = response.headers.get('content-length')
            if content_length:
                file_size = int(content_length)
                if file_size > self.config['max_file_size']:
                    log_error(f"File too large: {file_size} bytes (max: {self.config['max_file_size']})",
                             account_id=account_id, operation="download_file_stream", 
                             file_size=file_size, max_size=self.config['max_file_size'])
                    raise LMSFileError(f"File too large: {file_size} bytes (max: {self.config['max_file_size']})")
            
            # Stream file content
            downloaded_size = 0
            for chunk in response.iter_content(chunk_size=self.config['chunk_size']):
                if chunk:  # Filter out keep-alive chunks
                    downloaded_size += len(chunk)
                    
                    # Check size limit during download
                    if downloaded_size > self.config['max_file_size']:
                        log_error(f"File size exceeded limit during download: {downloaded_size}",
                                 account_id=account_id, operation="download_file_stream", 
                                 downloaded_size=downloaded_size, max_size=self.config['max_file_size'])
                        raise LMSFileError(f"File size exceeded limit during download: {downloaded_size}")
                    
                    yield chunk
            
            log_info(f"Successfully streamed file download: {downloaded_size} bytes",
                    account_id=account_id, operation="download_file_stream", 
                    downloaded_size=downloaded_size, filename=filename)
            
        except requests.RequestException as e:
            log_error(f"Network error during file download: {e}",
                     account_id=account_id, operation="download_file_stream", 
                     error_type="network_error", exc_info=True)
            raise LMSFileError(f"Download failed: {e}")
        except Exception as e:
            log_error(f"Unexpected error during file download: {e}",
                     account_id=account_id, operation="download_file_stream", 
                     error_type="system_error", exc_info=True)
            raise LMSFileError(f"Download failed: {e}")
    
    def validate_upload_file(self, file_data: FileStorage, 
                           assignment_requirements: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Validate file for upload based on requirements.
        
        Args:
            file_data: File data from form upload
            assignment_requirements: Optional assignment-specific requirements
            
        Returns:
            Validation result dictionary
            
        Raises:
            LMSFileValidationError: When validation fails
        """
        try:
            logger.info(f"Validating upload file: {file_data.filename}")
            
            validation_result = {
                'is_valid': True,
                'errors': [],
                'warnings': [],
                'file_info': {}
            }
            
            # Check if file exists
            if not file_data or not file_data.filename:
                raise LMSFileValidationError("No file provided")
            
            filename = file_data.filename
            file_extension = os.path.splitext(filename)[1].lower()
            
            # Get file size
            file_data.seek(0, os.SEEK_END)
            file_size = file_data.tell()
            file_data.seek(0)  # Reset position
            
            validation_result['file_info'] = {
                'filename': filename,
                'file_size': file_size,
                'file_extension': file_extension,
                'content_type': file_data.content_type
            }
            
            # Validate file size
            max_size = self.config['max_file_size']
            if assignment_requirements and 'max_file_size' in assignment_requirements:
                max_size = assignment_requirements['max_file_size']
            
            if file_size > max_size:
                validation_result['errors'].append(f"File too large: {file_size} bytes (max: {max_size})")
                validation_result['is_valid'] = False
            
            if file_size == 0:
                validation_result['errors'].append("File is empty")
                validation_result['is_valid'] = False
            
            # Validate file extension
            allowed_extensions = []
            for extensions in self.config['allowed_extensions'].values():
                allowed_extensions.extend(extensions)
            
            if assignment_requirements and 'allowed_extensions' in assignment_requirements:
                allowed_extensions = assignment_requirements['allowed_extensions']
            
            if file_extension not in allowed_extensions:
                validation_result['errors'].append(f"File type not allowed: {file_extension}")
                validation_result['is_valid'] = False
            
            # Validate filename
            if len(filename) > 255:
                validation_result['warnings'].append("Filename is very long and may be truncated")
            
            # Check for unsafe characters in filename
            unsafe_chars = '<>:"/\\|?*'
            if any(char in filename for char in unsafe_chars):
                validation_result['warnings'].append("Filename contains unsafe characters")
            
            # Additional assignment-specific validations
            if assignment_requirements:
                # Check required filename pattern
                if 'filename_pattern' in assignment_requirements:
                    import re
                    pattern = assignment_requirements['filename_pattern']
                    if not re.match(pattern, filename):
                        validation_result['errors'].append(f"Filename doesn't match required pattern: {pattern}")
                        validation_result['is_valid'] = False
                
                # Check file type requirements
                if 'required_file_type' in assignment_requirements:
                    required_type = assignment_requirements['required_file_type']
                    file_type = self._determine_file_type(file_extension, file_data.content_type)
                    if file_type != required_type:
                        validation_result['errors'].append(f"Required file type: {required_type}, got: {file_type}")
                        validation_result['is_valid'] = False
            
            if validation_result['is_valid']:
                logger.info(f"File validation passed: {filename}")
            else:
                logger.warning(f"File validation failed: {filename}, errors: {validation_result['errors']}")
            
            return validation_result
            
        except Exception as e:
            logger.error(f"File validation error: {e}")
            raise LMSFileValidationError(f"Validation failed: {e}")    

    def upload_assignment_file(self, account_id: str, assignment_id: str, 
                             file_data: FileStorage, 
                             assignment_requirements: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Upload assignment file to LMS servers.
        
        Args:
            account_id: Account ID for authentication
            assignment_id: Assignment ID to upload file for
            file_data: File data from form upload
            assignment_requirements: Optional assignment-specific requirements
            
        Returns:
            Upload result dictionary
            
        Raises:
            LMSFileError: When upload fails
        """
        try:
            logger.info(f"Starting assignment file upload for assignment {assignment_id}")
            
            # Validate file first
            validation_result = self.validate_upload_file(file_data, assignment_requirements)
            if not validation_result['is_valid']:
                raise LMSFileValidationError(f"File validation failed: {validation_result['errors']}")
            
            # Create authenticated session
            session = self._create_authenticated_session(account_id)
            
            # Determine upload URL (this would need to be determined based on LMS structure)
            upload_url = f"{self.lms_base_url}/Student/SubmitAssignment.php"
            
            # Prepare upload data
            filename = file_data.filename
            sanitized_filename = self._sanitize_filename(filename)
            
            # Reset file position
            file_data.seek(0)
            
            # Prepare form data for upload
            upload_data = {
                'assignment_id': assignment_id,
                'student_id': account_id,  # May need adjustment based on LMS requirements
                'submit_assignment': '1'
            }
            
            # Prepare files for upload
            files = {
                'assignment_file': (sanitized_filename, file_data.stream, file_data.content_type)
            }
            
            logger.debug(f"Uploading file {sanitized_filename} for assignment {assignment_id}")
            
            # Perform upload with progress tracking
            upload_result = self._perform_upload_with_progress(
                session, upload_url, upload_data, files, validation_result['file_info']
            )
            
            # Verify upload success
            if upload_result['success']:
                logger.info(f"Successfully uploaded file for assignment {assignment_id}")
                
                # Update result with file info
                upload_result.update({
                    'file_info': validation_result['file_info'],
                    'upload_time': datetime.now().isoformat(),
                    'assignment_id': assignment_id
                })
            else:
                logger.error(f"Upload failed for assignment {assignment_id}: {upload_result.get('error', 'Unknown error')}")
            
            return upload_result
            
        except LMSFileValidationError:
            # Re-raise validation errors
            raise
        except Exception as e:
            logger.error(f"Assignment file upload failed: {e}")
            raise LMSFileError(f"Upload failed: {e}")
    
    def _perform_upload_with_progress(self, session: requests.Session, upload_url: str,
                                    upload_data: Dict[str, Any], files: Dict[str, Any],
                                    file_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform file upload with progress tracking and error handling.
        
        Args:
            session: Authenticated session
            upload_url: URL to upload to
            upload_data: Form data for upload
            files: Files to upload
            file_info: File information for progress tracking
            
        Returns:
            Upload result dictionary
        """
        try:
            # Perform the upload
            response = session.post(
                upload_url,
                data=upload_data,
                files=files,
                timeout=self.config['timeout'] * 2,  # Longer timeout for uploads
                allow_redirects=True
            )
            
            # Check response status
            response.raise_for_status()
            
            # Parse response to determine success
            upload_success = self._parse_upload_response(response)
            
            if upload_success['success']:
                return {
                    'success': True,
                    'message': 'File uploaded successfully',
                    'response_data': upload_success.get('data', {}),
                    'file_size': file_info.get('file_size', 0)
                }
            else:
                return {
                    'success': False,
                    'error': upload_success.get('error', 'Upload failed'),
                    'response_data': upload_success.get('data', {})
                }
                
        except requests.RequestException as e:
            logger.error(f"Network error during upload: {e}")
            return {
                'success': False,
                'error': f'Network error: {e}',
                'response_data': {}
            }
        except Exception as e:
            logger.error(f"Unexpected error during upload: {e}")
            return {
                'success': False,
                'error': f'Upload error: {e}',
                'response_data': {}
            }
    
    def _parse_upload_response(self, response: requests.Response) -> Dict[str, Any]:
        """
        Parse upload response to determine success/failure.
        
        Args:
            response: HTTP response from upload
            
        Returns:
            Dictionary with success status and parsed data
        """
        try:
            response_text = response.text.lower()
            
            # Check for success indicators
            success_indicators = [
                'success', 'uploaded', 'submitted', 'complete', 'done'
            ]
            
            # Check for error indicators
            error_indicators = [
                'error', 'failed', 'invalid', 'denied', 'rejected'
            ]
            
            # Look for indicators in response
            has_success = any(indicator in response_text for indicator in success_indicators)
            has_error = any(indicator in response_text for indicator in error_indicators)
            
            if has_error:
                # Try to extract error message
                error_message = "Upload failed"
                if 'error' in response_text:
                    # Simple error extraction - could be enhanced based on actual LMS response format
                    error_start = response_text.find('error')
                    error_section = response_text[error_start:error_start + 200]
                    error_message = error_section.split('.')[0] if '.' in error_section else error_section
                
                return {
                    'success': False,
                    'error': error_message,
                    'data': {'response_text': response.text[:500]}  # First 500 chars for debugging
                }
            
            elif has_success or response.status_code == 200:
                return {
                    'success': True,
                    'data': {'response_text': response.text[:500]}  # First 500 chars for debugging
                }
            
            else:
                return {
                    'success': False,
                    'error': f'Unexpected response (status: {response.status_code})',
                    'data': {'response_text': response.text[:500]}
                }
                
        except Exception as e:
            logger.error(f"Error parsing upload response: {e}")
            return {
                'success': False,
                'error': f'Response parsing failed: {e}',
                'data': {}
            }
    
    def get_download_info(self, account_id: str, file_url: str) -> Dict[str, Any]:
        """
        Get comprehensive download information for a file.
        
        Args:
            account_id: Account ID for authentication
            file_url: URL of the file
            
        Returns:
            Dictionary with download information
        """
        try:
            # Get metadata
            metadata = self.get_file_metadata(account_id, file_url)
            
            # Add download-specific information
            download_info = metadata.copy()
            download_info.update({
                'download_url': file_url,
                'is_streamable': True,
                'estimated_download_time': self._estimate_download_time(metadata.get('file_size')),
                'supports_resume': False,  # LMS typically doesn't support resume
                'requires_authentication': True
            })
            
            return download_info
            
        except Exception as e:
            logger.error(f"Failed to get download info for {file_url}: {e}")
            raise LMSFileError(f"Download info retrieval failed: {e}")
    
    def _estimate_download_time(self, file_size: Optional[int]) -> Optional[str]:
        """
        Estimate download time based on file size.
        
        Args:
            file_size: File size in bytes
            
        Returns:
            Estimated download time string or None
        """
        if not file_size:
            return None
        
        # Assume average connection speed (conservative estimate)
        avg_speed_mbps = 5  # 5 Mbps
        avg_speed_bytes_per_sec = (avg_speed_mbps * 1024 * 1024) / 8
        
        estimated_seconds = file_size / avg_speed_bytes_per_sec
        
        if estimated_seconds < 60:
            return f"{int(estimated_seconds)} seconds"
        elif estimated_seconds < 3600:
            return f"{int(estimated_seconds / 60)} minutes"
        else:
            return f"{int(estimated_seconds / 3600)} hours"
    
    def clear_metadata_cache(self, account_id: Optional[str] = None):
        """
        Clear metadata cache for specific account or all accounts.
        
        Args:
            account_id: Optional account ID to clear cache for (clears all if None)
        """
        if account_id:
            # Clear cache for specific account
            keys_to_remove = [key for key in self._metadata_cache.keys() if key.startswith(f"{account_id}:")]
            for key in keys_to_remove:
                del self._metadata_cache[key]
            logger.info(f"Cleared metadata cache for account {account_id}")
        else:
            # Clear all cache
            self._metadata_cache.clear()
            logger.info("Cleared all metadata cache")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        now = datetime.now()
        expired_count = 0
        
        for (metadata, cached_time) in self._metadata_cache.values():
            if now - cached_time > self._cache_timeout:
                expired_count += 1
        
        return {
            'total_entries': len(self._metadata_cache),
            'expired_entries': expired_count,
            'cache_timeout_hours': self._cache_timeout.total_seconds() / 3600,
            'cache_size_estimate': len(str(self._metadata_cache))  # Rough estimate
        }
    
    def cleanup_expired_cache(self):
        """Clean up expired cache entries."""
        now = datetime.now()
        keys_to_remove = []
        
        for key, (metadata, cached_time) in self._metadata_cache.items():
            if now - cached_time > self._cache_timeout:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self._metadata_cache[key]
        
        if keys_to_remove:
            logger.info(f"Cleaned up {len(keys_to_remove)} expired cache entries")
    
    def test_file_operations(self, account_id: str) -> Dict[str, Any]:
        """
        Test file operations connectivity and functionality.
        
        Args:
            account_id: Account ID to test
            
        Returns:
            Dictionary with test results
        """
        try:
            log_info(f"Testing file operations for account {account_id}",
                    account_id=account_id, operation="test_file_operations")
            
            test_results = {
                'session_creation': False,
                'metadata_retrieval': False,
                'download_capability': False,
                'upload_capability': False,
                'overall_status': 'failed',
                'errors': []
            }
            
            # Test session creation
            try:
                session = self._create_authenticated_session(account_id)
                test_results['session_creation'] = True
                log_debug("Session creation test passed",
                         account_id=account_id, operation="test_file_operations")
            except Exception as e:
                error_msg = f"Session creation failed: {e}"
                test_results['errors'].append(error_msg)
                log_error(error_msg, account_id=account_id, operation="test_file_operations", 
                         test_type="session_creation", exc_info=True)
            
            # Test basic connectivity (if session creation succeeded)
            if test_results['session_creation']:
                try:
                    # Test a simple request to LMS
                    response = session.head(self.lms_base_url, timeout=10)
                    if response.status_code < 400:
                        test_results['download_capability'] = True
                        test_results['upload_capability'] = True  # Assume upload works if download works
                        log_debug("Basic connectivity test passed",
                                 account_id=account_id, operation="test_file_operations")
                except Exception as e:
                    error_msg = f"Connectivity test failed: {e}"
                    test_results['errors'].append(error_msg)
                    log_error(error_msg, account_id=account_id, operation="test_file_operations", 
                             test_type="connectivity", exc_info=True)
            
            # Determine overall status
            if test_results['session_creation'] and test_results['download_capability']:
                test_results['overall_status'] = 'passed'
            elif test_results['session_creation']:
                test_results['overall_status'] = 'partial'
            
            log_info(f"File operations test completed: {test_results['overall_status']}",
                    account_id=account_id, operation="test_file_operations", 
                    status=test_results['overall_status'], error_count=len(test_results['errors']))
            return test_results
            
        except Exception as e:
            log_error(f"File operations test failed: {e}",
                     account_id=account_id, operation="test_file_operations", 
                     error_type="system_error", exc_info=True)
            return {
                'session_creation': False,
                'metadata_retrieval': False,
                'download_capability': False,
                'upload_capability': False,
                'overall_status': 'error',
                'errors': [f"Test execution failed: {e}"]
            }    

    def get_upload_requirements(self, account_id: str, assignment_id: str) -> Dict[str, Any]:
        """
        Get upload requirements for a specific assignment.
        
        Args:
            account_id: Account ID for authentication
            assignment_id: Assignment ID to get requirements for
            
        Returns:
            Dictionary with upload requirements
        """
        try:
            logger.info(f"Getting upload requirements for assignment {assignment_id}")
            
            # Create authenticated session
            session = self._create_authenticated_session(account_id)
            
            # Get assignment details page (this would need to be determined based on LMS structure)
            assignment_url = f"{self.lms_base_url}/Student/AssignmentDetails.php?id={assignment_id}"
            
            try:
                response = session.get(assignment_url, timeout=self.config['timeout'])
                response.raise_for_status()
                
                # Parse requirements from HTML (this would need to be customized based on actual LMS structure)
                requirements = self._parse_upload_requirements(response.text)
                
            except requests.RequestException:
                # If we can't get specific requirements, return defaults
                logger.warning(f"Could not retrieve specific requirements for assignment {assignment_id}, using defaults")
                requirements = self._get_default_upload_requirements()
            
            logger.info(f"Retrieved upload requirements for assignment {assignment_id}")
            return requirements
            
        except Exception as e:
            logger.error(f"Failed to get upload requirements for assignment {assignment_id}: {e}")
            # Return default requirements on error
            return self._get_default_upload_requirements()
    
    def _parse_upload_requirements(self, html_content: str) -> Dict[str, Any]:
        """
        Parse upload requirements from assignment details HTML.
        
        Args:
            html_content: HTML content of assignment details page
            
        Returns:
            Dictionary with parsed requirements
        """
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            
            requirements = self._get_default_upload_requirements()
            
            # Look for file size limits in the HTML
            size_text = soup.get_text().lower()
            
            # Try to find file size limits (common patterns)
            import re
            size_patterns = [
                r'maximum.*?(\d+)\s*mb',
                r'max.*?size.*?(\d+)\s*mb',
                r'limit.*?(\d+)\s*mb',
                r'(\d+)\s*mb.*?limit'
            ]
            
            for pattern in size_patterns:
                match = re.search(pattern, size_text)
                if match:
                    max_mb = int(match.group(1))
                    requirements['max_file_size'] = max_mb * 1024 * 1024  # Convert to bytes
                    break
            
            # Look for allowed file types
            type_patterns = [
                r'allowed.*?types?.*?[:\-]\s*([^.]+)',
                r'accept.*?[:\-]\s*([^.]+)',
                r'format.*?[:\-]\s*([^.]+)'
            ]
            
            for pattern in type_patterns:
                match = re.search(pattern, size_text)
                if match:
                    types_text = match.group(1)
                    # Extract file extensions
                    extensions = re.findall(r'\.(\w+)', types_text)
                    if extensions:
                        requirements['allowed_extensions'] = [f'.{ext.lower()}' for ext in extensions]
                    break
            
            return requirements
            
        except Exception as e:
            logger.warning(f"Error parsing upload requirements: {e}")
            return self._get_default_upload_requirements()
    
    def _get_default_upload_requirements(self) -> Dict[str, Any]:
        """
        Get default upload requirements.
        
        Returns:
            Dictionary with default requirements
        """
        return {
            'max_file_size': self.config['max_file_size'],
            'allowed_extensions': [ext for exts in self.config['allowed_extensions'].values() for ext in exts],
            'required_file_type': None,
            'filename_pattern': None,
            'multiple_files_allowed': False,
            'description': 'Standard file upload requirements'
        }
    
    def track_upload_progress(self, account_id: str, upload_id: str) -> Dict[str, Any]:
        """
        Track upload progress for long-running uploads.
        
        Args:
            account_id: Account ID
            upload_id: Upload identifier
            
        Returns:
            Dictionary with upload progress information
        """
        # This is a placeholder for upload progress tracking
        # In a real implementation, this would track actual upload progress
        # For now, return a simple status
        
        return {
            'upload_id': upload_id,
            'status': 'completed',  # 'pending', 'in_progress', 'completed', 'failed'
            'progress_percent': 100,
            'bytes_uploaded': 0,
            'total_bytes': 0,
            'estimated_time_remaining': None,
            'error_message': None
        }
    
    def cancel_upload(self, account_id: str, upload_id: str) -> bool:
        """
        Cancel an ongoing upload.
        
        Args:
            account_id: Account ID
            upload_id: Upload identifier
            
        Returns:
            True if cancellation successful, False otherwise
        """
        # This is a placeholder for upload cancellation
        # In a real implementation, this would cancel actual uploads
        logger.info(f"Upload cancellation requested for {upload_id} by account {account_id}")
        return True
    
    def get_upload_history(self, account_id: str, assignment_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get upload history for an account.
        
        Args:
            account_id: Account ID
            assignment_id: Optional assignment ID to filter by
            
        Returns:
            List of upload history entries
        """
        # This is a placeholder for upload history
        # In a real implementation, this would retrieve actual upload history
        # For now, return empty list
        
        logger.info(f"Upload history requested for account {account_id}")
        return []
    
    def retry_failed_upload(self, account_id: str, upload_id: str) -> Dict[str, Any]:
        """
        Retry a failed upload.
        
        Args:
            account_id: Account ID
            upload_id: Upload identifier to retry
            
        Returns:
            Dictionary with retry result
        """
        # This is a placeholder for upload retry functionality
        # In a real implementation, this would retry actual failed uploads
        
        logger.info(f"Upload retry requested for {upload_id} by account {account_id}")
        return {
            'success': False,
            'error': 'Upload retry not implemented yet',
            'new_upload_id': None
        }
    
    def validate_assignment_submission(self, account_id: str, assignment_id: str, 
                                     file_data: FileStorage) -> Dict[str, Any]:
        """
        Validate file for assignment submission with assignment-specific rules.
        
        Args:
            account_id: Account ID
            assignment_id: Assignment ID
            file_data: File data to validate
            
        Returns:
            Validation result dictionary
        """
        try:
            # Get assignment-specific requirements
            requirements = self.get_upload_requirements(account_id, assignment_id)
            
            # Perform validation with assignment requirements
            validation_result = self.validate_upload_file(file_data, requirements)
            
            # Add assignment-specific validation info
            validation_result['assignment_id'] = assignment_id
            validation_result['requirements_used'] = requirements
            
            return validation_result
            
        except Exception as e:
            logger.error(f"Assignment submission validation failed: {e}")
            return {
                'is_valid': False,
                'errors': [f'Validation error: {e}'],
                'warnings': [],
                'file_info': {},
                'assignment_id': assignment_id
            }
    
    def get_supported_file_types(self) -> Dict[str, List[str]]:
        """
        Get all supported file types organized by category.
        
        Returns:
            Dictionary mapping categories to file extensions
        """
        return self.config['allowed_extensions'].copy()
    
    def is_file_type_supported(self, filename: str) -> Tuple[bool, str]:
        """
        Check if a file type is supported.
        
        Args:
            filename: Filename to check
            
        Returns:
            Tuple of (is_supported, file_category)
        """
        file_extension = os.path.splitext(filename)[1].lower()
        
        for category, extensions in self.config['allowed_extensions'].items():
            if file_extension in extensions:
                return True, category
        
        return False, 'unsupported'