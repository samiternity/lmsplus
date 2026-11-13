# app/services/lms_service.py
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import json
import time
from functools import wraps

from app.services.lms_auth_service import LMSAuthService, LMSAuthenticationError, LMSSessionError
from app.services.lms_scraper_service import LMSScraperService, LMSScrapingError, CMSSurveyRequiredError
from app.services.lms_file_proxy_service import LMSFileProxyService, LMSFileError
from app.models.lms_models import LMSAssignment, LMSLectureNote, LMSQuiz, LMSCache
from app.models.account import User
from app.utils.lms_error_handler import handle_lms_error, create_error_context, ErrorInfo
from app.utils.lms_logger import log_error, log_warning, log_info, log_debug, log_performance, log_user_activity

# Configure logging
logger = logging.getLogger(__name__)

class LMSServiceError(Exception):
    """Exception raised when LMS service operations fail"""
    pass

def handle_service_errors(operation_name: str):
    """
    Decorator to handle service errors with comprehensive logging and error handling.
    
    Args:
        operation_name: Name of the operation for logging and error tracking
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            start_time = time.time()
            account_id = args[0] if args else kwargs.get('account_id')
            
            try:
                log_debug(f"Starting {operation_name}", account_id=account_id, operation=operation_name)
                
                result = func(self, *args, **kwargs)
                
                duration = time.time() - start_time
                log_performance(operation_name, duration, account_id=account_id, success=True)
                log_user_activity(operation_name, account_id=account_id)
                
                return result
                
            except CMSSurveyRequiredError:
                raise
            except Exception as e:
                duration = time.time() - start_time
                
                # Create error context
                context = create_error_context(
                    account_id=account_id,
                    operation=operation_name,
                    duration=duration
                )
                
                # Handle error comprehensively
                error_info = handle_lms_error(e, context)
                
                # Log performance failure
                log_performance(operation_name, duration, account_id=account_id, success=False,
                              error_code=error_info.error_code)
                
                # Re-raise with enhanced error information
                enhanced_error = LMSServiceError(f"{error_info.user_message} (Error Code: {error_info.error_code})")
                enhanced_error.error_info = error_info
                raise enhanced_error
                
        return wrapper
    return decorator

class LMSService:
    """
    Main orchestration service for LMS operations.
    Coordinates authentication, scraping, file operations, and caching.
    """
    
    def __init__(self, provided_cms_session=None):
        # Initialize component services
        self.auth_service = LMSAuthService()
        self.scraper_service = LMSScraperService(provided_cms_session=provided_cms_session)
        self.file_service = LMSFileProxyService()
        
        # Cache configuration
        self.cache_config = {
            'default_timeout': timedelta(hours=1),  # 1 hour default cache
            'assignments_timeout': timedelta(minutes=30),  # 30 minutes for assignments
            'lecture_notes_timeout': timedelta(hours=2),   # 2 hours for lecture notes
            'max_cache_entries': 1000,  # Maximum cache entries per account
            'cleanup_interval': timedelta(hours=6)  # Clean up expired cache every 6 hours
        }
        
        # In-memory cache for quick access
        self._memory_cache = {}
        self._last_cleanup = datetime.now()
    
    def _get_user_and_cms_account(self, account_id: str):
        """
        Helper method to get user and CMS account from account_id.
        Note: account_id is actually a CMS account ID, not a user ID.
        
        Args:
            account_id: CMS account ID
            
        Returns:
            Tuple of (user, cms_account) or (None, None) if not found
        """
        try:
            from app.models.account import User
            
            # Fetch user from database using account_id (works in background threads)
            user = User.get_by_account_id(account_id)
            if not user:
                return None, None
                
            # SINGLE ACCOUNT MODE
            if user.account_id == account_id and user.cms_account:
                return user, user.cms_account
            
            return None, None
            
        except Exception as e:
            logger.error(f"Error getting user and CMS account for {account_id}: {e}")
            return None, None
    
    @handle_service_errors("authenticate_user")
    def authenticate_user(self, account_id: str) -> Dict[str, Any]:
        """
        Validate that CMS credentials exist for LMS access.
        Since LMS uses CMS credentials, no separate authentication is needed.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with authentication result
            
        Raises:
            LMSServiceError: When CMS credentials are not available
        """
        log_info(f"Validating CMS credentials for LMS access for account {account_id}", 
                account_id=account_id, operation="authenticate_user")
        
        # Validate CMS credentials exist
        success = self.auth_service.authenticate(account_id)
        
        if success:
            # Clear any existing cache for this account
            self._clear_account_cache(account_id)
            
            log_info(f"CMS credentials validated for LMS access for account {account_id}", 
                    account_id=account_id, operation="authenticate_user")
            
            return {
                'success': True,
                'message': 'CMS credentials validated for LMS access',
                'session_status': self.auth_service.get_session_status(account_id)
            }
        else:
            log_warning(f"CMS credentials validation failed for account {account_id}", 
                       account_id=account_id, operation="authenticate_user")
            return {
                'success': False,
                'message': 'CMS credentials not configured',
                'error': 'Please configure your CMS credentials to access LMS'
            }
    
    @handle_service_errors("get_assignments")
    def get_assignments(self, account_id: str, semester: Optional[str] = None, 
                       course: Optional[str] = None, force_refresh: bool = False,
                       allow_stale: bool = False) -> List[LMSAssignment]:
        """
        Get assignments for account with caching.
        
        Args:
            account_id: Account ID
            semester: Optional semester filter
            course: Optional course filter
            force_refresh: Force refresh from LMS (bypass cache)
            
        Returns:
            List of LMSAssignment objects
            
        Raises:
            LMSServiceError: When assignment retrieval fails
        """
        log_info(f"Getting assignments for account {account_id} (semester: {semester}, course: {course})",
                account_id=account_id, operation="get_assignments", 
                semester=semester, course=course, force_refresh=force_refresh)
        
        # Check cache first (unless force refresh)
        if not force_refresh:
            cached_assignments = self._get_cached_assignments(account_id, semester, course, allow_stale=allow_stale)
            if cached_assignments is not None:
                log_debug(f"Returning cached assignments for account {account_id}",
                         account_id=account_id, operation="get_assignments", 
                         count=len(cached_assignments), source="cache")
                return cached_assignments
        
        try:
            # Ensure valid session
            self._ensure_valid_session(account_id)
            
            # Scrape fresh data
            assignments = self.scraper_service.scrape_assignments_page(account_id, semester, course)
            
            # Cache the results
            self._cache_assignments(account_id, assignments, semester, course)
            
            # We no longer overwrite the global assignments_snapshot here.
            # Single-course fetches should not replace the background notification snapshot.
            # The background snapshot is now exclusively managed by lms_background_service and lms_dashboard_service.
            pass
            
            log_info(f"Retrieved {len(assignments)} assignments for account {account_id}",
                    account_id=account_id, operation="get_assignments", 
                    count=len(assignments), source="lms")
            return assignments
            
        except Exception as e:
            # If live fetch fails, try to return stale cache
            log_warning(f"Live assignments fetch failed for account {account_id}: {e}")
            
            stale_assignments = self._get_cached_assignments(account_id, semester, course, allow_stale=True)
            if stale_assignments is not None:
                log_info(f"Returning stale assignments due to live fetch failure",
                        account_id=account_id, operation="get_assignments",
                        count=len(stale_assignments), source="stale_cache")
                return stale_assignments
            
            # Resurface original error if no cache available
            raise
    
    @handle_service_errors("get_lecture_notes")
    def get_lecture_notes(self, account_id: str, semester: Optional[str] = None,
                         course: Optional[str] = None, force_refresh: bool = False,
                         allow_stale: bool = False) -> List[LMSLectureNote]:
        """
        Get lecture notes for account with caching.
        
        Args:
            account_id: Account ID
            semester: Optional semester filter
            course: Optional course filter
            force_refresh: Force refresh from LMS (bypass cache)
            
        Returns:
            List of LMSLectureNote objects
            
        Raises:
            LMSServiceError: When lecture notes retrieval fails
        """
        log_info(f"Getting lecture notes for account {account_id} (semester: {semester}, course: {course})",
                account_id=account_id, operation="get_lecture_notes", 
                semester=semester, course=course, force_refresh=force_refresh)
        
        # Check cache first (unless force refresh)
        if not force_refresh:
            cached_notes = self._get_cached_lecture_notes(account_id, semester, course, allow_stale=allow_stale)
            if cached_notes is not None:
                log_debug(f"Returning cached lecture notes for account {account_id}",
                         account_id=account_id, operation="get_lecture_notes", 
                         count=len(cached_notes), source="cache")
                return cached_notes
        
        try:
            # Ensure valid session
            self._ensure_valid_session(account_id)
            
            # Scrape fresh data
            lecture_notes = self.scraper_service.scrape_lecture_notes_page(account_id, semester, course)
            
            # Cache the results
            self._cache_lecture_notes(account_id, lecture_notes, semester, course)
            
            log_info(f"Retrieved {len(lecture_notes)} lecture notes for account {account_id}",
                    account_id=account_id, operation="get_lecture_notes", 
                    count=len(lecture_notes), source="lms")
            return lecture_notes
            
        except Exception as e:
            # If live fetch fails, try to return stale cache
            log_warning(f"Live lecture notes fetch failed for account {account_id}: {e}")
            
            stale_notes = self._get_cached_lecture_notes(account_id, semester, course, allow_stale=True)
            if stale_notes is not None:
                log_info(f"Returning stale lecture notes due to live fetch failure",
                        account_id=account_id, operation="get_lecture_notes",
                        count=len(stale_notes), source="stale_cache")
                return stale_notes
            
            # Resurface original error if no cache available
            raise
    
    def get_quizzes_options(self, account_id: str) -> Dict[str, Any]:
        key = self._get_cache_key(account_id, 'quizzes_options')
        cached = self._memory_cache.get(key)
        if cached and datetime.now() < cached[0]:
            return cached[1]
        self._ensure_valid_session(account_id)
        options = self.scraper_service.get_quizzes_options(account_id)
        self._memory_cache[key] = (datetime.now() + timedelta(hours=6), options)
        return options

    def get_quizzes_courses_for_semester(self, account_id: str, semester_code: str) -> List[Dict[str, str]]:
        self._ensure_valid_session(account_id)
        return self.scraper_service.get_quizzes_courses_for_semester(account_id, semester_code)

    def _get_cached_quizzes(self, account_id: str, semester: Optional[str], course: str,
                            allow_stale: bool = False) -> Optional[List[LMSQuiz]]:
        key = self._get_cache_key(account_id, 'quizzes', semester, course)
        entry = self._memory_cache.get(key)
        if entry is None:
            # Preserve expired entries for the stale-while-revalidate path.
            entry = self._load_persistent_cache(account_id, 'quizzes', semester, course, allow_stale=True)
            if entry is not None:
                self._memory_cache[key] = entry
        if entry is not None and (allow_stale or not entry.is_expired()):
            return [LMSQuiz.from_dict(row) for row in entry.data.get('quizzes', [])]
        return None

    def _cache_quizzes(self, account_id: str, quizzes: List[LMSQuiz],
                       semester: Optional[str], course: str):
        # The parser distinguishes valid empty tables from upstream failures.
        entry = LMSCache(account_id=account_id, data_type='quizzes', semester=semester,
                         course=course, data={'quizzes': [quiz.to_dict() for quiz in quizzes]},
                         cached_at=datetime.now(), expires_at=datetime.now() + timedelta(hours=2))
        self._memory_cache[self._get_cache_key(account_id, 'quizzes', semester, course)] = entry
        self._save_persistent_cache(entry)

    def get_quizzes_for_course(self, account_id: str, semester_code: Optional[str],
                               course_code: str, force_refresh: bool = False) -> List[LMSQuiz]:
        if not force_refresh:
            cached = self._get_cached_quizzes(account_id, semester_code, course_code)
            if cached is not None:
                return cached
        self._ensure_valid_session(account_id)
        quizzes = self.scraper_service.scrape_course_quizzes(account_id, semester_code, course_code)
        quizzes.sort(key=lambda quiz: quiz.quiz_no)
        self._cache_quizzes(account_id, quizzes, semester_code, course_code)
        return quizzes

    @handle_service_errors("get_lecture_notes_options")
    def get_lecture_notes_options(self, account_id: str) -> Dict[str, Any]:
        """
        Get available semesters and courses for lecture notes selection.
        Cached with a 6-hour TTL — these change at most once per semester.
        """
        log_info(f"Getting lecture notes options for account {account_id}",
                account_id=account_id, operation="get_lecture_notes_options")

        cache_key = f"lecture_notes_options:{account_id}"
        cached = self._memory_cache.get(cache_key)
        if cached:
            expires_at, options = cached
            if datetime.now() < expires_at:
                log_debug(f"Returning cached lecture notes options for account {account_id}",
                         account_id=account_id, operation="get_lecture_notes_options", source="cache")
                return options

        # Ensure valid session
        self._ensure_valid_session(account_id)
        
        # Get options from scraper
        options = self.scraper_service.get_lecture_notes_options(account_id)

        # Only cache if we actually got semesters — don't cache empty results
        if options.get('semesters'):
            self._memory_cache[cache_key] = (datetime.now() + timedelta(hours=6), options)
        elif not options.get('semesters'):
            raise LMSServiceError("No semesters returned from LMS. The server may be temporarily unavailable.")
        
        log_info(f"Retrieved {len(options.get('semesters', []))} semesters and {len(options.get('courses', []))} courses",
                account_id=account_id, operation="get_lecture_notes_options")
        return options
    
    @handle_service_errors("get_lecture_notes_courses_for_semester")
    def get_lecture_notes_courses_for_semester(self, account_id: str, semester_code: str) -> List[Dict[str, str]]:
        """
        Get available courses for a specific semester.
        
        Args:
            account_id: Account ID
            semester_code: Semester code (e.g., 'MjAyNTM=')
            
        Returns:
            List of dictionaries with 'code' and 'name' keys
            
        Raises:
            LMSServiceError: When courses retrieval fails
        """
        log_info(f"Getting courses for semester {semester_code}",
                account_id=account_id, operation="get_lecture_notes_courses_for_semester",
                semester_code=semester_code)
        
        # Ensure valid session
        self._ensure_valid_session(account_id)
        
        # Get courses from scraper
        courses = self.scraper_service.get_lecture_notes_courses_for_semester(account_id, semester_code)
        
        log_info(f"Retrieved {len(courses)} courses for semester {semester_code}",
                account_id=account_id, operation="get_lecture_notes_courses_for_semester")
        return courses
    
    @handle_service_errors("get_assignments_options")
    def get_assignments_options(self, account_id: str) -> Dict[str, Any]:
        """
        Get available semesters and courses for assignments selection.
        Cached with a 6-hour TTL — these change at most once per semester.
        """
        log_info(f"Getting assignments options for account {account_id}",
                account_id=account_id, operation="get_assignments_options")

        cache_key = f"assignments_options:{account_id}"
        cached = self._memory_cache.get(cache_key)
        if cached:
            expires_at, options = cached
            if datetime.now() < expires_at:
                log_debug(f"Returning cached assignments options for account {account_id}",
                         account_id=account_id, operation="get_assignments_options", source="cache")
                return options

        # Ensure valid session
        self._ensure_valid_session(account_id)
        
        # Get options from scraper
        options = self.scraper_service.get_assignments_options(account_id)

        # Only cache if we actually got semesters — don't cache empty results
        if options.get('semesters'):
            self._memory_cache[cache_key] = (datetime.now() + timedelta(hours=6), options)
        elif not options.get('semesters'):
            raise LMSServiceError("No semesters returned from LMS. The server may be temporarily unavailable.")
        
        log_info(f"Retrieved {len(options.get('semesters', []))} semesters and {len(options.get('courses', []))} courses",
                account_id=account_id, operation="get_assignments_options")
        return options
    
    @handle_service_errors("get_assignments_courses_for_semester")
    def get_assignments_courses_for_semester(self, account_id: str, semester_code: str) -> List[Dict[str, str]]:
        """
        Get available courses for a specific semester (for assignments).
        
        Args:
            account_id: Account ID
            semester_code: Semester code (e.g., 'MjAyNTM=')
            
        Returns:
            List of dictionaries with 'code' and 'name' keys
            
        Raises:
            LMSServiceError: When courses retrieval fails
        """
        log_info(f"Getting courses for assignments, semester {semester_code}",
                account_id=account_id, operation="get_assignments_courses_for_semester",
                semester_code=semester_code)
        
        # Ensure valid session
        self._ensure_valid_session(account_id)
        
        # Get courses from scraper
        courses = self.scraper_service.get_assignments_courses_for_semester(account_id, semester_code)
        
        log_info(f"Retrieved {len(courses)} courses for semester {semester_code}",
                account_id=account_id, operation="get_assignments_courses_for_semester")
        return courses
    
    @handle_service_errors("get_assignments_for_course")
    def get_assignments_for_course(self, account_id: str, semester_code: Optional[str],
                                   course_code: str, force_refresh: bool = False) -> List[LMSAssignment]:
        """
        Get assignments for a specific course.
        Mimics selecting a course from the dropdown on the original LMS site.
        
        Args:
            account_id: Account ID
            semester_code: Semester code (e.g., 'MjAyNTM=')
            course_code: Course code (e.g., 'MTM2NjM2')
            force_refresh: Force refresh from LMS (bypass cache)
            
        Returns:
            List of LMSAssignment objects for the course
            
        Raises:
            LMSServiceError: When assignments retrieval fails
        """
        log_info(f"Getting assignments for course {course_code} (semester: {semester_code})",
                account_id=account_id, operation="get_assignments_for_course",
                semester_code=semester_code, course_code=course_code)
        
        # Check cache first (unless force refresh)
        cache_key = f"{semester_code}:{course_code}" if semester_code else course_code
        if not force_refresh:
            cached_assignments = self._get_cached_assignments(account_id, cache_key, None)
            if cached_assignments is not None:
                log_debug(f"Returning cached assignments for course {course_code}",
                         account_id=account_id, operation="get_assignments_for_course",
                         count=len(cached_assignments), source="cache")
                return cached_assignments
        
        try:
            # Ensure valid session
            self._ensure_valid_session(account_id)
            
            # Scrape assignments for the specific course
            assignments = self.scraper_service.scrape_course_assignments(
                account_id, semester_code, course_code
            )
            
            # Cache the results
            self._cache_assignments(account_id, assignments, cache_key, None)
            
            log_info(f"Retrieved {len(assignments)} assignments for course {course_code}",
                    account_id=account_id, operation="get_assignments_for_course",
                    count=len(assignments), source="lms")
            return assignments
            
        except Exception as e:
            # If live fetch fails, try to return stale cache
            log_warning(f"Live course assignments fetch failed: {e}")
            
            stale_assignments = self._get_cached_assignments(account_id, cache_key, None, allow_stale=True)
            if stale_assignments is not None:
                log_info(f"Returning stale course assignments due to live fetch failure",
                        account_id=account_id, operation="get_assignments_for_course",
                        count=len(stale_assignments), source="stale_cache")
                return stale_assignments
            
            raise
    
    @handle_service_errors("get_lecture_notes_for_course")
    def get_lecture_notes_for_course(self, account_id: str, semester_code: Optional[str],
                                    course_code: str, force_refresh: bool = False) -> List[LMSLectureNote]:
        """
        Get lecture notes for a specific course.
        Mimics selecting a course from the dropdown on the original LMS site.
        
        Args:
            account_id: Account ID
            semester_code: Semester code (e.g., 'MjAyNTM=')
            course_code: Course code (e.g., 'MTM2NjMz')
            force_refresh: Force refresh from LMS (bypass cache)
            
        Returns:
            List of LMSLectureNote objects for the course
            
        Raises:
            LMSServiceError: When lecture notes retrieval fails
        """
        log_info(f"Getting lecture notes for course {course_code} (semester: {semester_code})",
                account_id=account_id, operation="get_lecture_notes_for_course",
                semester_code=semester_code, course_code=course_code)
        
        # Check cache first (unless force refresh)
        cache_key = f"{semester_code}:{course_code}" if semester_code else course_code
        if not force_refresh:
            cached_notes = self._get_cached_lecture_notes(account_id, cache_key, None)
            if cached_notes is not None:
                log_debug(f"Returning cached lecture notes for course {course_code}",
                         account_id=account_id, operation="get_lecture_notes_for_course",
                         count=len(cached_notes), source="cache")
                return cached_notes
        
        try:
            # Ensure valid session
            self._ensure_valid_session(account_id)
            
            # Scrape lecture notes for the specific course
            lecture_notes = self.scraper_service.scrape_course_lecture_notes(
                account_id, semester_code, course_code
            )
            
            # Cache the results
            self._cache_lecture_notes(account_id, lecture_notes, cache_key, None)
            
            log_info(f"Retrieved {len(lecture_notes)} lecture notes for course {course_code}",
                    account_id=account_id, operation="get_lecture_notes_for_course",
                    count=len(lecture_notes), source="lms")
            return lecture_notes
            
        except Exception as e:
            # If live fetch fails, try to return stale cache
            log_warning(f"Live course lecture notes fetch failed: {e}")
            
            stale_notes = self._get_cached_lecture_notes(account_id, cache_key, None, allow_stale=True)
            if stale_notes is not None:
                log_info(f"Returning stale course lecture notes due to live fetch failure",
                        account_id=account_id, operation="get_lecture_notes_for_course",
                        count=len(stale_notes), source="stale_cache")
                return stale_notes
            
            raise
    
    @handle_service_errors("get_pending_assignments_summary")
    def get_pending_assignments_summary(self, account_id: str, allow_stale: bool = False) -> Dict[str, Any]:
        """
        Get summary of pending assignments with urgency indicators.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with pending assignments summary
            
        Raises:
            LMSServiceError: When summary retrieval fails
        """
        log_info(f"Getting pending assignments summary for account {account_id}",
                account_id=account_id, operation="get_pending_assignments_summary")
        
        # Get all assignments (use cache if available)
        assignments = self.get_assignments(account_id, allow_stale=allow_stale)
        
        # Filter pending assignments
        pending_assignments = [a for a in assignments if a.is_pending()]
        
        # Sort by deadline (urgent first)
        pending_assignments.sort(key=lambda a: a.deadline or datetime.max)
        
        # Categorize by urgency
        now = datetime.now()
        urgent = []  # Due within 24 hours
        soon = []    # Due within 3 days
        later = []   # Due later or no deadline
        
        for assignment in pending_assignments:
            if assignment.deadline:
                time_until_due = assignment.deadline - now
                if time_until_due <= timedelta(hours=24):
                    urgent.append(assignment)
                elif time_until_due <= timedelta(days=3):
                    soon.append(assignment)
                else:
                    later.append(assignment)
            else:
                later.append(assignment)
        
        summary = {
            'total_pending': len(pending_assignments),
            'urgent_count': len(urgent),
            'soon_count': len(soon),
            'later_count': len(later),
            'urgent_assignments': [a.to_dict() for a in urgent[:5]],  # Top 5 urgent
            'soon_assignments': [a.to_dict() for a in soon[:5]],      # Top 5 soon
            'overdue_count': len([a for a in assignments if a.is_overdue()]),
            'last_updated': datetime.now().isoformat()
        }
        
        log_info(f"Generated pending assignments summary for account {account_id}: {summary['total_pending']} pending",
                account_id=account_id, operation="get_pending_assignments_summary", 
                total_pending=summary['total_pending'], urgent_count=summary['urgent_count'])
        return summary
    
    @handle_service_errors("refresh_lms_data")
    def refresh_lms_data(self, account_id: str) -> Dict[str, Any]:
        """
        Force refresh of all LMS data for account.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with refresh results
            
        Raises:
            LMSServiceError: When refresh fails
        """
        log_info(f"Refreshing all LMS data for account {account_id}",
                account_id=account_id, operation="refresh_lms_data")
        
        # Clear existing cache
        self._clear_account_cache(account_id)
        
        refresh_results = {
            'success': True,
            'assignments_count': 0,
            'lecture_notes_count': 0,
            'errors': [],
            'refreshed_at': datetime.now().isoformat()
        }
        
        # Refresh assignments
        try:
            assignments = self.get_assignments(account_id, force_refresh=True)
            refresh_results['assignments_count'] = len(assignments)
            log_debug(f"Successfully refreshed {len(assignments)} assignments",
                     account_id=account_id, operation="refresh_assignments", count=len(assignments))
        except Exception as e:
            error_msg = f"Assignments refresh failed: {e}"
            refresh_results['errors'].append(error_msg)
            log_error(error_msg, account_id=account_id, operation="refresh_assignments", exc_info=True)
        
        # Refresh lecture notes
        try:
            lecture_notes = self.get_lecture_notes(account_id, force_refresh=True)
            refresh_results['lecture_notes_count'] = len(lecture_notes)
            log_debug(f"Successfully refreshed {len(lecture_notes)} lecture notes",
                     account_id=account_id, operation="refresh_lecture_notes", count=len(lecture_notes))
        except Exception as e:
            error_msg = f"Lecture notes refresh failed: {e}"
            refresh_results['errors'].append(error_msg)
            log_error(error_msg, account_id=account_id, operation="refresh_lecture_notes", exc_info=True)
        
        # Update success status
        refresh_results['success'] = len(refresh_results['errors']) == 0
        
        log_info(f"LMS data refresh completed for account {account_id}: {refresh_results}",
                account_id=account_id, operation="refresh_lms_data", 
                success=refresh_results['success'], error_count=len(refresh_results['errors']))
        return refresh_results
    
    @handle_service_errors("download_file")
    def download_file(self, account_id: str, file_url: str, filename: Optional[str] = None):
        """
        Download file from LMS with authentication.
        
        Args:
            account_id: Account ID for authentication
            file_url: URL of file to download
            filename: Optional custom filename
            
        Returns:
            Generator yielding file content chunks
            
        Raises:
            LMSServiceError: When download fails
        """
        log_info(f"Downloading file for account {account_id}: {file_url}",
                account_id=account_id, operation="download_file", file_url=file_url, filename=filename)
        
        # Ensure valid session
        self._ensure_valid_session(account_id)
        
        # Stream file download
        return self.file_service.download_file_stream(account_id, file_url, filename)
    
    @handle_service_errors("upload_assignment_file")
    def upload_assignment_file(self, account_id: str, assignment_id: str, file_data, 
                              assignment_requirements: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Upload assignment file to LMS.
        
        Args:
            account_id: Account ID for authentication
            assignment_id: Assignment ID
            file_data: File data to upload
            assignment_requirements: Optional assignment requirements
            
        Returns:
            Dictionary with upload result
            
        Raises:
            LMSServiceError: When upload fails
        """
        log_info(f"Uploading assignment file for account {account_id}, assignment {assignment_id}",
                account_id=account_id, operation="upload_assignment_file", assignment_id=assignment_id)
        
        # Ensure valid session
        self._ensure_valid_session(account_id)
        
        # Perform upload
        upload_result = self.file_service.upload_assignment_file(
            account_id, assignment_id, file_data, assignment_requirements
        )
        
        # If upload successful, refresh assignments cache to reflect changes
        if upload_result.get('success'):
            self._invalidate_assignments_cache(account_id)
            log_info(f"Assignment file uploaded successfully for account {account_id}",
                    account_id=account_id, operation="upload_assignment_file", 
                    assignment_id=assignment_id, success=True)
        else:
            log_warning(f"Assignment file upload failed for account {account_id}",
                       account_id=account_id, operation="upload_assignment_file", 
                       assignment_id=assignment_id, error=upload_result.get('error'))
        
        return upload_result
    
    @handle_service_errors("get_file_metadata")
    def get_file_metadata(self, account_id: str, file_url: str) -> Dict[str, Any]:
        """
        Get file metadata without downloading.
        
        Args:
            account_id: Account ID for authentication
            file_url: URL of file
            
        Returns:
            Dictionary with file metadata
            
        Raises:
            LMSServiceError: When metadata retrieval fails
        """
        log_debug(f"Getting file metadata for account {account_id}: {file_url}",
                 account_id=account_id, operation="get_file_metadata", file_url=file_url)
        
        # Ensure valid session
        self._ensure_valid_session(account_id)
        
        return self.file_service.get_file_metadata(account_id, file_url)
    
    @handle_service_errors("get_service_status")
    def get_service_status(self, account_id: str) -> Dict[str, Any]:
        """
        Get comprehensive service status for account.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with service status information
        """
        log_debug(f"Getting service status for account {account_id}",
                 account_id=account_id, operation="get_service_status")
        
        # Check if credentials exist first
        from app.models.account import User
        user = User.get(account_id)
        if not user:
            return {
                'account_id': account_id,
                'overall_status': 'no_credentials',
                'authentication': {'is_valid': False, 'error': 'User not found'},
                'connectivity': {'success': False, 'error': 'User not found'},
                'file_operations': {'success': False, 'error': 'User not found'},
                'cache': {'memory_entries': 0, 'persistent_entries': 0},
                'last_checked': datetime.now().isoformat()
            }
        
        cms_account = user.get_active_account()
        if not cms_account:
            return {
                'account_id': account_id,
                'overall_status': 'no_credentials',
                'authentication': {'is_valid': False, 'error': 'No active account'},
                'connectivity': {'success': False, 'error': 'No active account'},
                'file_operations': {'success': False, 'error': 'No active account'},
                'cache': {'memory_entries': 0, 'persistent_entries': 0},
                'last_checked': datetime.now().isoformat()
            }
        
        lms_credentials = getattr(cms_account, 'lms_credentials', None)
        if not lms_credentials or not hasattr(lms_credentials, 'lms_username') or not lms_credentials.lms_username:
            return {
                'account_id': account_id,
                'overall_status': 'no_credentials',
                'authentication': {'is_valid': False, 'error': 'No LMS credentials configured'},
                'connectivity': {'success': False, 'error': 'No LMS credentials configured'},
                'file_operations': {'success': False, 'error': 'No LMS credentials configured'},
                'cache': {'memory_entries': 0, 'persistent_entries': 0},
                'last_checked': datetime.now().isoformat()
            }
        
        # Get authentication status
        try:
            auth_status = self.auth_service.get_detailed_session_status(account_id)
        except Exception as e:
            log_debug(f"Auth status check failed: {e}", account_id=account_id)
            auth_status = {'is_valid': False, 'error': str(e)}
        
        # Test connectivity (only if auth is valid)
        if auth_status.get('is_valid'):
            try:
                connectivity = self.scraper_service.test_connectivity(account_id)
            except Exception as e:
                log_debug(f"Connectivity test failed: {e}", account_id=account_id)
                connectivity = {'success': False, 'error': str(e)}
        else:
            connectivity = {'success': False, 'error': 'Authentication required'}
        
        # Get cache statistics (this should always work)
        try:
            cache_stats = self._get_cache_statistics(account_id)
        except Exception as e:
            log_debug(f"Cache stats failed: {e}", account_id=account_id)
            cache_stats = {'memory_entries': 0, 'persistent_entries': 0, 'error': str(e)}
        
        # Test file operations (only if auth is valid)
        if auth_status.get('is_valid'):
            try:
                file_status = self.file_service.test_file_operations(account_id)
            except Exception as e:
                log_debug(f"File operations test failed: {e}", account_id=account_id)
                file_status = {'success': False, 'error': str(e)}
        else:
            file_status = {'success': False, 'error': 'Authentication required'}
        
        overall_status = 'operational' if auth_status.get('is_valid') and connectivity.get('success') else 'degraded'
        
        status = {
            'account_id': account_id,
            'overall_status': overall_status,
            'authentication': auth_status,
            'connectivity': connectivity,
            'file_operations': file_status,
            'cache': cache_stats,
            'last_checked': datetime.now().isoformat()
        }
        
        log_debug(f"Service status for account {account_id}: {overall_status}",
                 account_id=account_id, operation="get_service_status", status=overall_status)
        
        return status
    
    @handle_service_errors("logout_user")
    def logout_user(self, account_id: str) -> bool:
        """
        Logout user and clear all session data.
        
        Args:
            account_id: Account ID
            
        Returns:
            True if logout successful
        """
        log_info(f"Logging out LMS user for account {account_id}",
                account_id=account_id, operation="logout_user")
        
        # Clear authentication session
        auth_cleared = self.auth_service.logout(account_id)
        
        # Clear cache
        self._clear_account_cache(account_id)
        
        # Clear file service cache
        self.file_service.clear_metadata_cache(account_id)
        
        log_info(f"Successfully logged out LMS user for account {account_id}",
                account_id=account_id, operation="logout_user", success=auth_cleared)
        return auth_cleared   
 
    def _ensure_valid_session(self, account_id: str):
        """
        Ensure user has valid LMS session, refresh if needed.
        
        Args:
            account_id: Account ID
            
        Raises:
            LMSServiceError: When session validation/refresh fails
        """
        try:
            if not self.auth_service.is_session_valid(account_id):
                logger.info(f"Session invalid for account {account_id}, attempting refresh")
                
                if not self.auth_service.refresh_session(account_id):
                    raise LMSServiceError("Session expired and refresh failed. Please re-authenticate.")
                
                logger.debug(f"Session refreshed successfully for account {account_id}")
            
        except CMSSurveyRequiredError:
            raise
        except LMSSessionError as e:
            logger.error(f"Session error for account {account_id}: {e}")
            raise LMSServiceError(f"Session error: {e}")
        except Exception as e:
            logger.error(f"Unexpected session error for account {account_id}: {e}")
            raise LMSServiceError(f"Session validation failed: {e}")
    
    def _get_cache_key(self, account_id: str, data_type: str, semester: Optional[str] = None, 
                      course: Optional[str] = None) -> str:
        """
        Generate cache key for data.
        
        Args:
            account_id: Account ID
            data_type: Type of data (e.g., 'assignments', 'lecture_notes', 'pending_assignments', 'assignments_summary')
            semester: Optional semester
            course: Optional course
            
        Returns:
            Cache key string
        
        Examples:
            - _get_cache_key('acc123', 'assignments') -> 'acc123:assignments'
            - _get_cache_key('acc123', 'assignments', 'MjAyNTM=') -> 'acc123:assignments:sem:MjAyNTM='
            - _get_cache_key('acc123', 'pending_assignments') -> 'acc123:pending_assignments'
            - _get_cache_key('acc123', 'assignments_summary') -> 'acc123:assignments_summary'
        """
        key_parts = [account_id, data_type]
        if semester:
            key_parts.append(f"sem:{semester}")
        if course:
            key_parts.append(f"course:{course}")
        
        return ":".join(key_parts)
    
    def is_cache_stale(self, account_id: str, data_type: str, semester: Optional[str] = None, course: Optional[str] = None) -> bool:
        """Check if a specific cache is missing or expired."""
        try:
            cache_key = self._get_cache_key(account_id, data_type, semester, course)
            
            # Check memory cache first
            if cache_key in self._memory_cache:
                return self._memory_cache[cache_key].is_expired()
                
            # Check persistent cache
            user, cms_account = self._get_user_and_cms_account(account_id)
            if not user or not cms_account:
                return True
                
            lms_cache_data = getattr(cms_account, 'lms_cache', {})
            cache_lookup_key = ":".join([data_type] + ([semester] if semester else []) + ([course] if course else []))
            
            if cache_lookup_key in lms_cache_data:
                cache_entry = LMSCache.from_dict(lms_cache_data[cache_lookup_key])
                return not cache_entry.is_valid()
                
            return True
        except Exception:
            return True

    def _get_cached_assignments(self, account_id: str, semester: Optional[str] = None,
                               course: Optional[str] = None, allow_stale: bool = False) -> Optional[List[LMSAssignment]]:
        """
        Get cached assignments if available and valid.
        
        Args:
            account_id: Account ID
            semester: Optional semester filter
            course: Optional course filter
            allow_stale: Whether to return stale (expired) cache
            
        Returns:
            List of cached assignments or None if not available/expired
        """
        try:
            cache_key = self._get_cache_key(account_id, 'assignments', semester, course)
            
            # Check memory cache first
            if cache_key in self._memory_cache:
                cache_entry = self._memory_cache[cache_key]
                if allow_stale or not cache_entry.is_expired():
                    logger.debug(f"Found cached assignments (stale={cache_entry.is_expired()}): {cache_key}")
                    return [LMSAssignment.from_dict(a) for a in cache_entry.data.get('assignments', [])]
                else:
                    # Remove expired entry if not allowing stale
                    del self._memory_cache[cache_key]
            
            # Check persistent cache
            cached_data = self._load_persistent_cache(account_id, 'assignments', semester, course, allow_stale=allow_stale)
            if cached_data:
                # Also store in memory cache for faster access (even if stale, to avoid re-reading DB)
                self._memory_cache[cache_key] = cached_data
                return [LMSAssignment.from_dict(a) for a in cached_data.data.get('assignments', [])]
            
            return None
            
        except Exception as e:
            logger.warning(f"Error retrieving cached assignments: {e}")
            return None
    
    def _get_cached_lecture_notes(self, account_id: str, semester: Optional[str] = None,
                                 course: Optional[str] = None, allow_stale: bool = False) -> Optional[List[LMSLectureNote]]:
        """
        Get cached lecture notes if available and valid.
        
        Args:
            account_id: Account ID
            semester: Optional semester filter
            course: Optional course filter
            allow_stale: Whether to return stale (expired) cache
            
        Returns:
            List of cached lecture notes or None if not available/expired
        """
        try:
            cache_key = self._get_cache_key(account_id, 'lecture_notes', semester, course)
            
            # Check memory cache first
            if cache_key in self._memory_cache:
                cache_entry = self._memory_cache[cache_key]
                if allow_stale or not cache_entry.is_expired():
                    logger.debug(f"Found cached lecture notes (stale={cache_entry.is_expired()}): {cache_key}")
                    return [LMSLectureNote.from_dict(n) for n in cache_entry.data.get('lecture_notes', [])]
                else:
                    # Remove expired entry if not allowing stale
                    del self._memory_cache[cache_key]
            
            # Check persistent cache
            cached_data = self._load_persistent_cache(account_id, 'lecture_notes', semester, course, allow_stale=allow_stale)
            if cached_data:
                # Also store in memory cache for faster access
                self._memory_cache[cache_key] = cached_data
                return [LMSLectureNote.from_dict(n) for n in cached_data.data.get('lecture_notes', [])]
            
            return None
            
        except Exception as e:
            logger.warning(f"Error retrieving cached lecture notes: {e}")
            return None
    
    def _cache_assignments(self, account_id: str, assignments: List[LMSAssignment],
                          semester: Optional[str] = None, course: Optional[str] = None):
        try:
            # Don't cache empty results — they likely indicate a scrape failure
            # and would poison the cache, causing stale-while-revalidate to serve
            # "No Assignments Found" on subsequent loads.
            if not assignments:
                logger.debug(f"Skipping cache for empty assignments result (account={account_id})")
                return

            cache_key = self._get_cache_key(account_id, 'assignments', semester, course)
            
            # Create cache entry
            cache_entry = LMSCache(
                account_id=account_id,
                data_type='assignments',
                semester=semester,
                course=course,
                data={'assignments': [a.to_dict() for a in assignments]},
                cached_at=datetime.now(),
                expires_at=datetime.now() + self.cache_config['assignments_timeout']
            )
            
            # Store in memory cache
            self._memory_cache[cache_key] = cache_entry
            
            # Store in persistent cache
            self._save_persistent_cache(cache_entry)
            
            logger.debug(f"Cached {len(assignments)} assignments: {cache_key}")
            
        except Exception as e:
            logger.warning(f"Error caching assignments: {e}")
    
    def _cache_lecture_notes(self, account_id: str, lecture_notes: List[LMSLectureNote],
                            semester: Optional[str] = None, course: Optional[str] = None):
        try:
            # Don't cache empty results — same reasoning as _cache_assignments.
            if not lecture_notes:
                logger.debug(f"Skipping cache for empty lecture notes result (account={account_id})")
                return

            cache_key = self._get_cache_key(account_id, 'lecture_notes', semester, course)
            
            # Create cache entry
            cache_entry = LMSCache(
                account_id=account_id,
                data_type='lecture_notes',
                semester=semester,
                course=course,
                data={'lecture_notes': [n.to_dict() for n in lecture_notes]},
                cached_at=datetime.now(),
                expires_at=datetime.now() + self.cache_config['lecture_notes_timeout']
            )
            
            # Store in memory cache
            self._memory_cache[cache_key] = cache_entry
            
            # Store in persistent cache
            self._save_persistent_cache(cache_entry)
            
            logger.debug(f"Cached {len(lecture_notes)} lecture notes: {cache_key}")
            
        except Exception as e:
            logger.warning(f"Error caching lecture notes: {e}")
    
    def _load_persistent_cache(self, account_id: str, data_type: str, 
                              semester: Optional[str] = None, course: Optional[str] = None,
                              allow_stale: bool = False) -> Optional[LMSCache]:
        """
        Load cache data from persistent storage.
        
        Args:
            account_id: Account ID
            data_type: Type of data
            semester: Optional semester
            course: Optional course
            allow_stale: Whether to return stale (expired) data
            
        Returns:
            LMSCache object or None if not found/expired (unless allow_stale=True)
        """
        try:
            # Get user and CMS account
            user, cms_account = self._get_user_and_cms_account(account_id)
            if not user or not cms_account:
                return None
            
            # Get LMS cache data from account
            lms_cache_data = getattr(cms_account, 'lms_cache', {})
            
            # Build cache key for lookup
            cache_key_parts = [data_type]
            if semester:
                cache_key_parts.append(semester)
            if course:
                cache_key_parts.append(course)
            
            cache_lookup_key = ":".join(cache_key_parts)
            
            # Look for matching cache entry
            if cache_lookup_key in lms_cache_data:
                cache_data = lms_cache_data[cache_lookup_key]
                cache_entry = LMSCache.from_dict(cache_data)
                
                # Check if cache is valid or if we allow stale
                if allow_stale or cache_entry.is_valid():
                    logger.debug(f"Loaded persistent cache (stale={not cache_entry.is_valid()}): {cache_lookup_key}")
                    return cache_entry
                else:
                    # Remove expired cache only if we're not allowing stale reads
                    # Actually, if we're not allowing stale reads, we can remove it.
                    # But if we MIGHT want it later for a fallback, maybe we shouldn't delete it on read?
                    # The original behavior deleted it. 
                    # Optimization: Don't delete on read failure here, rely on explicit cleanup.
                    # Or keep deletion if allow_stale is False (current behavior).
                    
                    # Remove expired cache
                    del lms_cache_data[cache_lookup_key]
                    cms_account.lms_cache = lms_cache_data
                    user.save()
                    logger.debug(f"Removed expired persistent cache: {cache_lookup_key}")
            
            return None
            
        except Exception as e:
            logger.warning(f"Error loading persistent cache: {e}")
            return None
    
    def _save_persistent_cache(self, cache_entry: LMSCache):
        """
        Save cache data to persistent storage.
        
        Args:
            cache_entry: Cache entry to save
        """
        try:
            # Get user and CMS account
            user, cms_account = self._get_user_and_cms_account(cache_entry.account_id)
            if not user or not cms_account:
                logger.warning(f"User or CMS account not found for cache save: {cache_entry.account_id}")
                return
            
            # Get existing cache data
            lms_cache_data = getattr(cms_account, 'lms_cache', {})
            
            # Build cache key
            cache_key_parts = [cache_entry.data_type]
            if cache_entry.semester:
                cache_key_parts.append(cache_entry.semester)
            if cache_entry.course:
                cache_key_parts.append(cache_entry.course)
            
            cache_key = ":".join(cache_key_parts)
            
            # Store cache entry
            lms_cache_data[cache_key] = cache_entry.to_dict()
            
            # Limit cache size
            if len(lms_cache_data) > self.cache_config['max_cache_entries']:
                # Remove oldest entries
                sorted_entries = sorted(
                    lms_cache_data.items(),
                    key=lambda x: x[1].get('cached_at', ''),
                    reverse=True
                )
                # Keep only the most recent entries
                lms_cache_data = dict(sorted_entries[:self.cache_config['max_cache_entries']])
            
            # Save back to account
            cms_account.lms_cache = lms_cache_data
            user.save()
            
            logger.debug(f"Saved persistent cache: {cache_key}")
            
        except Exception as e:
            logger.warning(f"Error saving persistent cache: {e}")
    
    def _cache_data(self, key: str, data: Any, timeout: Optional[timedelta] = None):
        """
        Generic helper method to cache arbitrary data.
        Used by background service and dashboard service for flexible caching.
        
        Args:
            key: Cache key (should include account_id for proper scoping)
            data: Data to cache (must be JSON-serializable)
            timeout: Optional timeout, defaults to cache_config default
        """
        try:
            from app.services.lms_cache_service import get_cache_service
            
            cache_service = get_cache_service()
            ttl = timeout or self.cache_config['default_timeout']
            cache_service.set(key, data, ttl=ttl)
            
            logger.debug(f"Cached data with key: {key}", ttl_seconds=ttl.total_seconds())
            
        except Exception as e:
            logger.warning(f"Error caching data with key {key}: {e}")
    
    def _get_cached_data(self, key: str, allow_stale: bool = False) -> Optional[Tuple[Any, bool]]:
        """
        Generic helper method to retrieve cached data.
        Used by background service and dashboard service for flexible cache retrieval.
        
        Args:
            key: Cache key
            allow_stale: Whether to return stale data
            
        Returns:
            Tuple of (data, is_stale) or None if not found
        """
        try:
            from app.services.lms_cache_service import get_cache_service
            
            cache_service = get_cache_service()
            return cache_service.get(key, allow_stale=allow_stale)
            
        except Exception as e:
            logger.warning(f"Error retrieving cached data with key {key}: {e}")
            return None
    
    def _clear_account_cache(self, account_id: str):
        """
        Clear all cache for account.
        
        Args:
            account_id: Account ID
        """
        try:
            # Clear memory cache
            keys_to_remove = [key for key in self._memory_cache.keys() if key.startswith(f"{account_id}:")]
            for key in keys_to_remove:
                del self._memory_cache[key]
            
            # Clear persistent cache
            user, cms_account = self._get_user_and_cms_account(account_id)
            if user and cms_account:
                cms_account.lms_cache = {}
                user.save()
            
            logger.debug(f"Cleared all cache for account {account_id}")
            
        except Exception as e:
            logger.warning(f"Error clearing cache for account {account_id}: {e}")
    
    def _invalidate_assignments_cache(self, account_id: str):
        """
        Invalidate assignments cache for account.
        
        Args:
            account_id: Account ID
        """
        try:
            # Clear memory cache for assignments
            keys_to_remove = [key for key in self._memory_cache.keys() 
                             if key.startswith(f"{account_id}:assignments")]
            for key in keys_to_remove:
                del self._memory_cache[key]
            
            # Clear persistent assignments cache
            user = User.get(account_id)
            if user:
                cms_account = user.get_active_account()
                if cms_account:
                    lms_cache_data = getattr(cms_account, 'lms_cache', {})
                    # Remove assignments cache entries
                    keys_to_remove = [key for key in lms_cache_data.keys() if key.startswith('assignments')]
                    for key in keys_to_remove:
                        del lms_cache_data[key]
                    cms_account.lms_cache = lms_cache_data
                    user.save()
            
            logger.debug(f"Invalidated assignments cache for account {account_id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating assignments cache for account {account_id}: {e}")
    
    def _get_cache_statistics(self, account_id: str) -> Dict[str, Any]:
        """
        Get cache statistics for account.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with cache statistics
        """
        try:
            # Count memory cache entries
            memory_entries = len([key for key in self._memory_cache.keys() if key.startswith(f"{account_id}:")])
            
            # Count persistent cache entries
            persistent_entries = 0
            user = User.get(account_id)
            if user:
                cms_account = user.get_active_account()
                if cms_account:
                    lms_cache_data = getattr(cms_account, 'lms_cache', {})
                    persistent_entries = len(lms_cache_data)
            
            # Perform cleanup if needed
            self._cleanup_expired_cache()
            
            return {
                'memory_entries': memory_entries,
                'persistent_entries': persistent_entries,
                'last_cleanup': self._last_cleanup.isoformat(),
                'cache_config': {
                    'assignments_timeout_minutes': int(self.cache_config['assignments_timeout'].total_seconds() / 60),
                    'lecture_notes_timeout_minutes': int(self.cache_config['lecture_notes_timeout'].total_seconds() / 60),
                    'max_entries': self.cache_config['max_cache_entries']
                }
            }
            
        except Exception as e:
            logger.warning(f"Error getting cache statistics: {e}")
            return {
                'memory_entries': 0,
                'persistent_entries': 0,
                'error': str(e)
            }
    
    def _cleanup_expired_cache(self):
        """
        Clean up expired cache entries.
        """
        try:
            now = datetime.now()
            
            # Only cleanup if enough time has passed
            if now - self._last_cleanup < self.cache_config['cleanup_interval']:
                return
            
            # Clean memory cache
            expired_keys = []
            for key, cache_entry in self._memory_cache.items():
                if cache_entry.is_expired():
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._memory_cache[key]
            
            if expired_keys:
                logger.debug(f"Cleaned up {len(expired_keys)} expired memory cache entries")
            
            # Update last cleanup time
            self._last_cleanup = now
            
        except Exception as e:
            logger.warning(f"Error during cache cleanup: {e}")
    
    def get_cache_info(self, account_id: str) -> Dict[str, Any]:
        """
        Get detailed cache information for account.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with detailed cache information
        """
        try:
            cache_info = {
                'account_id': account_id,
                'statistics': self._get_cache_statistics(account_id),
                'memory_cache_keys': [key for key in self._memory_cache.keys() if key.startswith(f"{account_id}:")],
                'cache_status': 'operational'
            }
            
            # Add persistent cache details
            user = User.get(account_id)
            if user:
                cms_account = user.get_active_account()
                if cms_account:
                    lms_cache_data = getattr(cms_account, 'lms_cache', {})
                    cache_info['persistent_cache_keys'] = list(lms_cache_data.keys())
                    
                    # Check cache validity
                    valid_entries = 0
                    expired_entries = 0
                    for cache_data in lms_cache_data.values():
                        cache_entry = LMSCache.from_dict(cache_data)
                        if cache_entry.is_valid():
                            valid_entries += 1
                        else:
                            expired_entries += 1
                    
                    cache_info['cache_health'] = {
                        'valid_entries': valid_entries,
                        'expired_entries': expired_entries,
                        'health_score': valid_entries / (valid_entries + expired_entries) if (valid_entries + expired_entries) > 0 else 1.0
                    }
            
            return cache_info
            
        except Exception as e:
            logger.error(f"Error getting cache info for account {account_id}: {e}")
            return {
                'account_id': account_id,
                'cache_status': 'error',
                'error': str(e)
            }    

    # Background refresh and advanced caching methods
    
    def schedule_background_refresh(self, account_id: str, refresh_interval: Optional[timedelta] = None) -> Dict[str, Any]:
        """
        Schedule background refresh for account data.
        
        Args:
            account_id: Account ID
            refresh_interval: Optional custom refresh interval
            
        Returns:
            Dictionary with scheduling result
        """
        try:
            if refresh_interval is None:
                refresh_interval = timedelta(hours=4)  # Default 4 hours
            
            logger.info(f"Scheduling background refresh for account {account_id} every {refresh_interval}")
            
            # Store refresh schedule in user preferences
            user = User.get(account_id)
            if not user:
                raise LMSServiceError(f"User not found: {account_id}")
            
            cms_account = user.get_active_account()
            if not cms_account:
                raise LMSServiceError(f"No active account found: {account_id}")
            
            # Get or create LMS preferences
            lms_preferences = getattr(cms_account, 'lms_preferences', {})
            lms_preferences.update({
                'auto_refresh_enabled': True,
                'refresh_interval_hours': refresh_interval.total_seconds() / 3600,
                'last_scheduled': datetime.now().isoformat(),
                'next_refresh': (datetime.now() + refresh_interval).isoformat()
            })
            
            cms_account.lms_preferences = lms_preferences
            user.save()
            
            return {
                'success': True,
                'message': 'Background refresh scheduled',
                'refresh_interval_hours': refresh_interval.total_seconds() / 3600,
                'next_refresh': (datetime.now() + refresh_interval).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error scheduling background refresh for account {account_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def perform_background_refresh(self, account_id: str) -> Dict[str, Any]:
        """
        Perform background refresh if scheduled and due.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with refresh result
        """
        try:
            logger.debug(f"Checking background refresh for account {account_id}")
            
            # Check if background refresh is enabled and due
            user = User.get(account_id)
            if not user:
                return {'success': False, 'error': 'User not found'}
            
            cms_account = user.get_active_account()
            if not cms_account:
                return {'success': False, 'error': 'No active account'}
            
            lms_preferences = getattr(cms_account, 'lms_preferences', {})
            
            if not lms_preferences.get('auto_refresh_enabled', False):
                return {'success': False, 'message': 'Background refresh not enabled'}
            
            # Check if refresh is due
            next_refresh_str = lms_preferences.get('next_refresh')
            if not next_refresh_str:
                return {'success': False, 'message': 'No refresh scheduled'}
            
            try:
                next_refresh = datetime.fromisoformat(next_refresh_str)
                if datetime.now() < next_refresh:
                    return {'success': False, 'message': 'Refresh not due yet'}
            except ValueError:
                logger.warning(f"Invalid next_refresh format for account {account_id}")
                return {'success': False, 'error': 'Invalid refresh schedule'}
            
            # Perform the refresh
            logger.info(f"Performing background refresh for account {account_id}")
            
            refresh_result = {
                'success': True,
                'background_refresh': True,
                'assignments_refreshed': False,
                'lecture_notes_refreshed': False,
                'errors': [],
                'refreshed_at': datetime.now().isoformat()
            }
            
            # Refresh assignments (silently handle errors)
            try:
                assignments = self.get_assignments(account_id, force_refresh=True)
                refresh_result['assignments_refreshed'] = True
                refresh_result['assignments_count'] = len(assignments)
                logger.debug(f"Background refresh: updated {len(assignments)} assignments")
            except Exception as e:
                refresh_result['errors'].append(f"Assignments refresh failed: {e}")
                logger.warning(f"Background assignments refresh failed for account {account_id}: {e}")
            
            # Refresh lecture notes (silently handle errors)
            try:
                lecture_notes = self.get_lecture_notes(account_id, force_refresh=True)
                refresh_result['lecture_notes_refreshed'] = True
                refresh_result['lecture_notes_count'] = len(lecture_notes)
                logger.debug(f"Background refresh: updated {len(lecture_notes)} lecture notes")
            except Exception as e:
                refresh_result['errors'].append(f"Lecture notes refresh failed: {e}")
                logger.warning(f"Background lecture notes refresh failed for account {account_id}: {e}")
            
            # Update next refresh time
            refresh_interval_hours = lms_preferences.get('refresh_interval_hours', 4)
            next_refresh = datetime.now() + timedelta(hours=refresh_interval_hours)
            lms_preferences['next_refresh'] = next_refresh.isoformat()
            lms_preferences['last_refresh'] = datetime.now().isoformat()
            
            cms_account.lms_preferences = lms_preferences
            user.save()
            
            # Update success status based on whether any refresh succeeded
            refresh_result['success'] = refresh_result['assignments_refreshed'] or refresh_result['lecture_notes_refreshed']
            
            if refresh_result['success']:
                logger.info(f"Background refresh completed for account {account_id}")
            else:
                logger.warning(f"Background refresh failed for account {account_id}: {refresh_result['errors']}")
            
            return refresh_result
            
        except Exception as e:
            logger.error(f"Error during background refresh for account {account_id}: {e}")
            return {
                'success': False,
                'background_refresh': True,
                'error': str(e),
                'refreshed_at': datetime.now().isoformat()
            }
    
    def disable_background_refresh(self, account_id: str) -> bool:
        """
        Disable background refresh for account.
        
        Args:
            account_id: Account ID
            
        Returns:
            True if disabled successfully
        """
        try:
            user = User.get(account_id)
            if not user:
                return False
            
            cms_account = user.get_active_account()
            if not cms_account:
                return False
            
            lms_preferences = getattr(cms_account, 'lms_preferences', {})
            lms_preferences['auto_refresh_enabled'] = False
            lms_preferences['disabled_at'] = datetime.now().isoformat()
            
            cms_account.lms_preferences = lms_preferences
            user.save()
            
            logger.info(f"Disabled background refresh for account {account_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error disabling background refresh for account {account_id}: {e}")
            return False
    
    def get_refresh_status(self, account_id: str) -> Dict[str, Any]:
        """
        Get background refresh status for account.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with refresh status
        """
        try:
            user = User.get(account_id)
            if not user:
                return {'enabled': False, 'error': 'User not found'}
            
            cms_account = user.get_active_account()
            if not cms_account:
                return {'enabled': False, 'error': 'No active account'}
            
            lms_preferences = getattr(cms_account, 'lms_preferences', {})
            
            status = {
                'enabled': lms_preferences.get('auto_refresh_enabled', False),
                'refresh_interval_hours': lms_preferences.get('refresh_interval_hours', 4),
                'last_refresh': lms_preferences.get('last_refresh'),
                'next_refresh': lms_preferences.get('next_refresh'),
                'last_scheduled': lms_preferences.get('last_scheduled')
            }
            
            # Calculate time until next refresh
            if status['next_refresh']:
                try:
                    next_refresh = datetime.fromisoformat(status['next_refresh'])
                    time_until = next_refresh - datetime.now()
                    if time_until.total_seconds() > 0:
                        status['time_until_next_refresh'] = {
                            'hours': int(time_until.total_seconds() // 3600),
                            'minutes': int((time_until.total_seconds() % 3600) // 60),
                            'is_due': False
                        }
                    else:
                        status['time_until_next_refresh'] = {
                            'hours': 0,
                            'minutes': 0,
                            'is_due': True
                        }
                except ValueError:
                    status['time_until_next_refresh'] = None
            
            return status
            
        except Exception as e:
            logger.error(f"Error getting refresh status for account {account_id}: {e}")
            return {'enabled': False, 'error': str(e)}
    
    def invalidate_cache(self, account_id: str, data_type: Optional[str] = None, 
                        semester: Optional[str] = None, course: Optional[str] = None) -> Dict[str, Any]:
        """
        Invalidate specific cache entries.
        
        Args:
            account_id: Account ID
            data_type: Optional data type to invalidate ('assignments' or 'lecture_notes')
            semester: Optional semester to invalidate
            course: Optional course to invalidate
            
        Returns:
            Dictionary with invalidation result
        """
        try:
            logger.info(f"Invalidating cache for account {account_id} (type: {data_type}, semester: {semester}, course: {course})")
            
            invalidated_count = 0
            
            if data_type is None:
                # Invalidate all cache
                self._clear_account_cache(account_id)
                invalidated_count = len([key for key in self._memory_cache.keys() if key.startswith(f"{account_id}:")])
                message = "All cache invalidated"
            else:
                # Invalidate specific cache entries
                if data_type == 'assignments':
                    self._invalidate_assignments_cache(account_id)
                    message = "Assignments cache invalidated"
                elif data_type == 'lecture_notes':
                    self._invalidate_lecture_notes_cache(account_id)
                    message = "Lecture notes cache invalidated"
                else:
                    return {'success': False, 'error': f'Invalid data type: {data_type}'}
                
                # Count invalidated entries (approximate)
                invalidated_count = len([key for key in self._memory_cache.keys() 
                                       if key.startswith(f"{account_id}:{data_type}")])
            
            return {
                'success': True,
                'message': message,
                'invalidated_entries': invalidated_count,
                'invalidated_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error invalidating cache for account {account_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _invalidate_lecture_notes_cache(self, account_id: str):
        """
        Invalidate lecture notes cache for account.
        
        Args:
            account_id: Account ID
        """
        try:
            # Clear memory cache for lecture notes
            keys_to_remove = [key for key in self._memory_cache.keys() 
                             if key.startswith(f"{account_id}:lecture_notes")]
            for key in keys_to_remove:
                del self._memory_cache[key]
            
            # Clear persistent lecture notes cache
            user = User.get(account_id)
            if user:
                cms_account = user.get_active_account()
                if cms_account:
                    lms_cache_data = getattr(cms_account, 'lms_cache', {})
                    # Remove lecture notes cache entries
                    keys_to_remove = [key for key in lms_cache_data.keys() if key.startswith('lecture_notes')]
                    for key in keys_to_remove:
                        del lms_cache_data[key]
                    cms_account.lms_cache = lms_cache_data
                    user.save()
            
            logger.debug(f"Invalidated lecture notes cache for account {account_id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating lecture notes cache for account {account_id}: {e}")
    
    def preload_cache(self, account_id: str, semesters: Optional[List[str]] = None, 
                     courses: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Preload cache with data for specified semesters/courses.
        
        Args:
            account_id: Account ID
            semesters: Optional list of semesters to preload
            courses: Optional list of courses to preload
            
        Returns:
            Dictionary with preload result
        """
        try:
            logger.info(f"Preloading cache for account {account_id}")
            
            preload_result = {
                'success': True,
                'preloaded_assignments': 0,
                'preloaded_lecture_notes': 0,
                'errors': [],
                'preloaded_at': datetime.now().isoformat()
            }
            
            # If no specific semesters/courses specified, preload current data
            if not semesters and not courses:
                semesters = [None]  # Load default/current semester
                courses = [None]    # Load all courses
            
            # Preload assignments
            for semester in (semesters or [None]):
                for course in (courses or [None]):
                    try:
                        assignments = self.get_assignments(account_id, semester, course, force_refresh=True)
                        preload_result['preloaded_assignments'] += len(assignments)
                    except Exception as e:
                        error_msg = f"Failed to preload assignments (semester: {semester}, course: {course}): {e}"
                        preload_result['errors'].append(error_msg)
                        logger.warning(error_msg)
            
            # Preload lecture notes
            for semester in (semesters or [None]):
                for course in (courses or [None]):
                    try:
                        lecture_notes = self.get_lecture_notes(account_id, semester, course, force_refresh=True)
                        preload_result['preloaded_lecture_notes'] += len(lecture_notes)
                    except Exception as e:
                        error_msg = f"Failed to preload lecture notes (semester: {semester}, course: {course}): {e}"
                        preload_result['errors'].append(error_msg)
                        logger.warning(error_msg)
            
            # Update success status
            preload_result['success'] = (preload_result['preloaded_assignments'] > 0 or 
                                       preload_result['preloaded_lecture_notes'] > 0)
            
            logger.info(f"Cache preload completed for account {account_id}: {preload_result}")
            return preload_result
            
        except Exception as e:
            logger.error(f"Error preloading cache for account {account_id}: {e}")
            return {
                'success': False,
                'error': str(e),
                'preloaded_at': datetime.now().isoformat()
            }
    
    def optimize_cache(self, account_id: str) -> Dict[str, Any]:
        """
        Optimize cache by removing unused entries and consolidating data.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with optimization result
        """
        try:
            logger.info(f"Optimizing cache for account {account_id}")
            
            optimization_result = {
                'success': True,
                'removed_entries': 0,
                'consolidated_entries': 0,
                'cache_size_before': 0,
                'cache_size_after': 0,
                'optimized_at': datetime.now().isoformat()
            }
            
            # Count initial cache size
            initial_memory_entries = len([key for key in self._memory_cache.keys() if key.startswith(f"{account_id}:")])
            optimization_result['cache_size_before'] = initial_memory_entries
            
            # Remove expired entries from memory cache
            expired_keys = []
            for key in list(self._memory_cache.keys()):
                if key.startswith(f"{account_id}:"):
                    cache_entry = self._memory_cache[key]
                    if cache_entry.is_expired():
                        expired_keys.append(key)
            
            for key in expired_keys:
                del self._memory_cache[key]
            
            optimization_result['removed_entries'] = len(expired_keys)
            
            # Optimize persistent cache
            user = User.get(account_id)
            if user:
                cms_account = user.get_active_account()
                if cms_account:
                    lms_cache_data = getattr(cms_account, 'lms_cache', {})
                    
                    # Remove expired persistent cache entries
                    expired_persistent_keys = []
                    for key, cache_data in lms_cache_data.items():
                        cache_entry = LMSCache.from_dict(cache_data)
                        if cache_entry.is_expired():
                            expired_persistent_keys.append(key)
                    
                    for key in expired_persistent_keys:
                        del lms_cache_data[key]
                    
                    optimization_result['removed_entries'] += len(expired_persistent_keys)
                    
                    # Save optimized cache
                    cms_account.lms_cache = lms_cache_data
                    user.save()
            
            # Count final cache size
            final_memory_entries = len([key for key in self._memory_cache.keys() if key.startswith(f"{account_id}:")])
            optimization_result['cache_size_after'] = final_memory_entries
            
            logger.info(f"Cache optimization completed for account {account_id}: removed {optimization_result['removed_entries']} entries")
            return optimization_result
            
        except Exception as e:
            logger.error(f"Error optimizing cache for account {account_id}: {e}")
            return {
                'success': False,
                'error': str(e),
                'optimized_at': datetime.now().isoformat()
            }
    
    def get_data_freshness(self, account_id: str) -> Dict[str, Any]:
        """
        Get data freshness information for account.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with data freshness information
        """
        try:
            freshness_info = {
                'account_id': account_id,
                'assignments': {'cached': False, 'age_minutes': None, 'is_fresh': False},
                'lecture_notes': {'cached': False, 'age_minutes': None, 'is_fresh': False},
                'overall_freshness': 'unknown',
                'checked_at': datetime.now().isoformat()
            }
            
            now = datetime.now()
            
            # Check assignments freshness
            assignments_cache = self._get_cached_assignments(account_id)
            if assignments_cache is not None:
                # Find the cache entry to get timestamp
                cache_key = self._get_cache_key(account_id, 'assignments')
                if cache_key in self._memory_cache:
                    cache_entry = self._memory_cache[cache_key]
                    age = now - cache_entry.cached_at
                    freshness_info['assignments'] = {
                        'cached': True,
                        'age_minutes': int(age.total_seconds() / 60),
                        'is_fresh': age < self.cache_config['assignments_timeout'],
                        'cached_at': cache_entry.cached_at.isoformat()
                    }
            
            # Check lecture notes freshness
            lecture_notes_cache = self._get_cached_lecture_notes(account_id)
            if lecture_notes_cache is not None:
                # Find the cache entry to get timestamp
                cache_key = self._get_cache_key(account_id, 'lecture_notes')
                if cache_key in self._memory_cache:
                    cache_entry = self._memory_cache[cache_key]
                    age = now - cache_entry.cached_at
                    freshness_info['lecture_notes'] = {
                        'cached': True,
                        'age_minutes': int(age.total_seconds() / 60),
                        'is_fresh': age < self.cache_config['lecture_notes_timeout'],
                        'cached_at': cache_entry.cached_at.isoformat()
                    }
            
            # Determine overall freshness
            assignments_fresh = freshness_info['assignments']['is_fresh']
            lecture_notes_fresh = freshness_info['lecture_notes']['is_fresh']
            
            if assignments_fresh and lecture_notes_fresh:
                freshness_info['overall_freshness'] = 'fresh'
            elif assignments_fresh or lecture_notes_fresh:
                freshness_info['overall_freshness'] = 'partial'
            elif freshness_info['assignments']['cached'] or freshness_info['lecture_notes']['cached']:
                freshness_info['overall_freshness'] = 'stale'
            else:
                freshness_info['overall_freshness'] = 'no_cache'
            
            return freshness_info
            
        except Exception as e:
            logger.error(f"Error getting data freshness for account {account_id}: {e}")
            return {
                'account_id': account_id,
                'overall_freshness': 'error',
                'error': str(e),
                'checked_at': datetime.now().isoformat()
            }
    
    def delete_assignment_submission(self, account_id: str, submission_id: Optional[str],
                                     assignment_id: Optional[str], offered_course_id: Optional[str],
                                     semester: Optional[str], course: str) -> bool:
        """
        Delete assignment submission from LMS.
        
        Args:
            account_id: User account ID
            submission_id: LMS submission ID (will be base64 encoded)
            assignment_id: LMS assignment ID (will be base64 encoded)
            offered_course_id: LMS offered course ID (will be base64 encoded)
            semester: Semester code
            course: Course code
            
        Returns:
            True if deletion successful, False otherwise
        """
        try:
            log_info(f"Deleting assignment submission for account {account_id}",
                    account_id=account_id, operation="delete_submission",
                    submission_id=submission_id, assignment_id=assignment_id)
            
            # If we don't have the LMS IDs, we can't perform actual deletion
            if not submission_id or not assignment_id or not offered_course_id:
                log_warning(f"Missing LMS IDs for deletion - submission_id: {submission_id}, assignment_id: {assignment_id}, offered_course_id: {offered_course_id}",
                           account_id=account_id, operation="delete_submission")
                # For now, just return success
                # The actual LMS deletion will be implemented when we have the IDs from scraping
                return True
            
            # Create authenticated session
            session = self.scraper_service._create_authenticated_session(account_id)
            
            # IDs from scraper are already base64-encoded, use them directly
            # The LMS server expects base64-encoded IDs
            from urllib.parse import quote
            
            log_debug(f"Using IDs for deletion - submission_id: {submission_id}, assignment_id: {assignment_id}, offered_course_id: {offered_course_id}",
                     account_id=account_id, operation="delete_submission")
            
            # URL-encode the submission ID (it may contain special characters like +)
            encoded_sub_id = quote(submission_id)
            
            # Construct delete URL with base64-encoded IDs
            delete_url = (
                f"https://lms.bahria.edu.pk/Student/includes/studentprocess.php"
                f"?s=assignmentSubmissionDelete"
                f"&aSubId={encoded_sub_id}"
                f"&a={assignment_id}"
                f"&oc={offered_course_id}"
            )
            
            log_debug(f"Making delete request to: {delete_url}",
                     account_id=account_id, operation="delete_submission")
            
            # Make request
            response = session.get(delete_url, timeout=30)
            
            # Check if successful (usually redirects back to assignments page)
            if response.status_code == 200 or response.status_code == 302:
                log_info(f"Successfully sent delete request for submission",
                        account_id=account_id, operation="delete_submission")
                return True
            else:
                log_warning(f"Delete request returned status {response.status_code}",
                           account_id=account_id, operation="delete_submission")
                return False
            
        except Exception as e:
            log_error(f"Error deleting submission: {e}",
                     account_id=account_id, operation="delete_submission", exc_info=True)
            return False
    
    def upload_assignment_submission(self, account_id: str, assignment_no: int,
                                     file, semester: Optional[str], course: str,
                                     assignment_id: Optional[str] = None,
                                     offered_course_id: Optional[str] = None,
                                     student_id: Optional[str] = None) -> bool:
        """
        Upload assignment submission to LMS.
        
        Args:
            account_id: User account ID
            assignment_no: Assignment number
            file: File object to upload
            semester: Semester code
            course: Course code
            assignment_id: LMS assignment ID (base64 encoded)
            offered_course_id: LMS offered course ID (base64 encoded)
            student_id: LMS student ID (base64 encoded)
            
        Returns:
            True if upload successful, False otherwise
        """
        try:
            log_info(f"Uploading assignment {assignment_no} for account {account_id}",
                    account_id=account_id, operation="upload_submission",
                    assignment_no=assignment_no)
            
            log_info(f"Upload IDs received - assignment_id: {assignment_id}, offered_course_id: {offered_course_id}, student_id: {student_id}",
                    account_id=account_id, operation="upload_submission")
            
            # If we don't have the LMS IDs, we can't perform actual upload
            if not assignment_id or not offered_course_id:
                log_warning(f"Missing LMS IDs for upload - assignment_id: {assignment_id}, offered_course_id: {offered_course_id}",
                           account_id=account_id, operation="upload_submission")
                log_warning("Cannot upload to LMS without assignment_id and offered_course_id. These should be extracted from the submit button modal during scraping.",
                           account_id=account_id, operation="upload_submission")
                # Return False to indicate upload didn't happen
                return False
            
            # Create authenticated session
            session = self.scraper_service._create_authenticated_session(account_id)
            
            import base64
            
            # IDs from scraper are already base64-encoded, use them directly
            # The LMS server expects base64-encoded IDs
            log_debug(f"Using IDs for upload - assignment_id: {assignment_id}, offered_course_id: {offered_course_id}, student_id: {student_id}",
                     account_id=account_id, operation="upload_submission")
            
            # Student ID must come from the scraped form data
            # It's a numeric ID (like "116636"), not the CMS username
            if not student_id:
                log_warning(f"Missing student_id for upload - this should be extracted from the submit form during scraping, but continuing anyway for test mode",
                           account_id=account_id, operation="upload_submission")
                # Removed: return False
            
            # Prepare the upload URL
            upload_url = "https://lms.bahria.edu.pk/Student/includes/studentprocess.php?s=assignmentSubmissionAdd"
            
            # Prepare form data with base64-encoded IDs
            form_data = {
                'assignmentID': assignment_id,  # Already base64-encoded from scraper
                'offeredCourseID': offered_course_id,  # Already base64-encoded from scraper
                'ocID': offered_course_id,  # Same as offeredCourseID
            }
            
            if student_id:
                form_data['studentID'] = student_id
            
            log_debug(f"Form data prepared: {list(form_data.keys())}",
                     account_id=account_id, operation="upload_submission")
            
            # Reset file stream to beginning before upload
            file.seek(0)
            
            # Read file content into memory to ensure it's fully available
            file_content = file.read()
            file.seek(0)  # Reset for potential retry
            
            # Prepare file for upload
            files = {
                'assignmentSubmissionFile': (file.filename, file_content, file.content_type)
            }
            
            log_debug(f"Uploading to: {upload_url}",
                     account_id=account_id, operation="upload_submission")
            log_debug(f"Form data values: assignmentID={assignment_id}, offeredCourseID={offered_course_id}, studentID={student_id}",
                     account_id=account_id, operation="upload_submission")
            log_debug(f"File info: name={file.filename}, type={file.content_type}, size={len(file_content)} bytes",
                     account_id=account_id, operation="upload_submission")
            
            # Make upload request
            response = session.post(upload_url, data=form_data, files=files, timeout=60, allow_redirects=False)
            
            log_info(f"Upload response: status={response.status_code}, url={response.url}",
                    account_id=account_id, operation="upload_submission")
            
            # Log response headers to see if there's a redirect
            if 'Location' in response.headers:
                log_info(f"Redirect location: {response.headers['Location']}",
                        account_id=account_id, operation="upload_submission")
            
            # Log first 500 chars of response to see any error messages
            response_preview = response.text[:500] if response.text else "No response body"
            log_debug(f"Response preview: {response_preview}",
                     account_id=account_id, operation="upload_submission")
            
            # Check if successful (usually redirects back to assignments page)
            if response.status_code == 200 or response.status_code == 302:
                # Check redirect location for failure indicators
                redirect_location = response.headers.get('Location', '')
                
                # Check if it's a known success redirect
                is_success_redirect = 'success' in redirect_location.lower()
                
                if not is_success_redirect and ('failure' in redirect_location.lower() or 'error' in redirect_location.lower() or '&r=' in redirect_location):
                    log_warning(f"Upload failed - LMS returned failure redirect: {redirect_location}",
                               account_id=account_id, operation="upload_submission")
                    
                    # Extract specific error reason if available
                    import re
                    error_match = re.search(r'&r=([^&]+)', redirect_location)
                    error_reason = error_match.group(1) if error_match else "Unknown rejection"
                    
                    # Return error string instead of bool for the route to catch
                    raise Exception(f"LMS rejected upload: {error_reason}")
                
                # Check if response contains error messages
                if 'error' in response.text.lower() or 'failed' in response.text.lower():
                    log_warning(f"Upload may have failed - response contains error keywords",
                               account_id=account_id, operation="upload_submission")
                    return False
                    
                log_info(f"Successfully uploaded assignment submission",
                        account_id=account_id, operation="upload_submission")
                return True
            else:
                log_warning(f"Upload request returned status {response.status_code}",
                           account_id=account_id, operation="upload_submission")
                return False
            
        except Exception as e:
            log_error(f"Error uploading submission: {e}",
                     account_id=account_id, operation="upload_submission", exc_info=True)
            return False

    def handle_group_test_upload(self, group_manager, group, semester, upload, enrollments, replace):
        """Run the testing-only upload for the owner and selected accepted members."""
        from flask import current_app

        if not current_app.config['TEST_GROUP_UPLOAD_ENABLED']:
            raise PermissionError('Group test upload is disabled')
        owner = group_manager.get_owner(group)
        members = {owner, *enrollments}
        return self._run_group_operation(group_manager, group, semester, upload, members, replace)

    def start_group_operation(self, group_manager, group, semester, upload):
        """Run an authorized group operation in a background thread."""
        from flask import current_app
        from threading import Thread

        app = current_app._get_current_object()

        def run():
            with app.app_context():
                self._run_group_operation(group_manager, group, semester, upload)

        try:
            Thread(target=run, daemon=True).start()
        except Exception:
            group_manager.set_group_state(group['group_id'], 'idle')
            raise

    def _run_group_operation(self, group_manager, group, semester, upload, selected=None, replace=False):
        from app.models.account import User
        from app.services.cms_blacklist import get_blacklist_service
        from app.services.testing_access import testing_login_allowed
        from io import BytesIO

        group_id = group['group_id']
        course = group['course_id']
        assignment_id = group['assignment_id']
        operation = 'Upload' if upload else 'Delete'
        filename = upload[1] if upload else '-'
        results = []

        def run_member(enrollment):
            user = User.get(enrollment)
            account = user.cms_account if user else None
            if (not account or account.cms_username != enrollment or not testing_login_allowed(enrollment)
                    or not get_blacklist_service().is_allowed(enrollment)):
                return False

            assignments = self.get_assignments_for_course(account.account_id, semester, course,
                                                           force_refresh=True)
            assignment = next((item for item in assignments
                               if item.assignment_id == assignment_id and item.offered_course_id), None)
            if not assignment:
                return False

            target = dict(account_id=account.account_id, semester=semester, course=course,
                          assignment_id=assignment.assignment_id, offered_course_id=assignment.offered_course_id)
            if upload is not None and not assignment.student_id:
                return False
            if upload is None or (replace and assignment.submission_id):
                if not assignment.submission_id:
                    return False
                if not self.delete_assignment_submission(submission_id=assignment.submission_id, **target):
                    return False
                if upload is None:
                    return True

            file = BytesIO(upload[0])
            file.filename = filename
            file.content_type = upload[2]
            return self.upload_assignment_submission(
                file=file, assignment_no=assignment.assignment_no, student_id=assignment.student_id, **target
            )

        try:
            for enrollment, status in group['roster'].items():
                if status != 'Accepted' or (selected is not None and enrollment not in selected):
                    continue
                try:
                    success = run_member(enrollment)
                except Exception:
                    logger.exception('Group %s failed for group %s', operation.lower(), group_id)
                    success = False
                message = 'Success' if success else 'LMS rejected the operation or the assignment was unavailable.'
                group_manager.append_audit_log(
                    group_id, enrollment, f'{operation} {"Success" if success else "Failed"}', filename, message
                )
                results.append({'enrollment': enrollment, 'status': 'success' if success else 'failed',
                                'message': message})
        finally:
            group_manager.set_group_state(group_id, 'idle')
        return results
