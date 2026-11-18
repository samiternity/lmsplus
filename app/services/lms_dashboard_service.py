# app/services/lms_dashboard_service.py
"""
Dashboard-specific LMS service with optimized data fetching.
Handles pending/missed assignments widget with smart caching and background refresh.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.services.lms_cache_service import get_cache_service, LMSCacheService
from app.utils.assignment_identity import validate_courses, normalize_label, identity_index, assignment_snapshot_payload
from app.utils.lms_logger import log_info, log_warning, log_debug, log_error, log_performance
from app.utils.assignment_categorization import (
    parse_deadline,
    categorize_assignments,
    sort_assignments_by_deadline,
    filter_assignments_by_date_range,
    filter_assignments_by_urgency,
    enrich_assignment_with_metadata,
    get_assignments_summary,
    calculate_urgency_level
)

logger = logging.getLogger(__name__)


class LMSDashboardService:
    """
    Service for dashboard-specific LMS operations.
    Optimized for fast loading with background refresh.
    """
    
    def __init__(self, lms_service, background_service=None):
        self.lms_service = lms_service
        self.background_service = background_service
        self.cache = get_cache_service()
    
    def get_pending_assignments_widget_data(self, account_id: str,
                                           force_refresh: bool = False,
                                           min_version: Optional[str] = None) -> Dict[str, Any]:
        """
        Get data for pending assignments dashboard widget.
        
        This method implements the hybrid approach:
        1. Try to serve from cache (instant)
        2. If cache is stale, serve stale data and trigger background refresh
        3. If cache is missing, do synchronous fetch (slower but necessary)
        
        Args:
            account_id: Account ID
            force_refresh: Force immediate refresh (bypasses cache)
            min_version: Minimum valid refreshed_at timestamp string
            
        Returns:
            Dictionary with pending/missed assignments and metadata
        """
        start_time = datetime.now()
        cache_key = f"pending_assignments:{account_id}"
        
        try:
            # Force refresh - bypass cache
            if force_refresh:
                log_info(f"Force refresh requested for {account_id}",
                        account_id=account_id, operation="get_pending_widget")
                result = self._fetch_and_cache_assignments(account_id, cache_key)
                
                # Log performance
                duration = (datetime.now() - start_time).total_seconds()
                log_performance("get_pending_widget_force", duration,
                              account_id=account_id,
                              success=not self._refresh_failed(result), from_cache=False)
                
                return self._refresh_response(account_id, result)
            
            # Load DIRECTLY from the Single Source of Truth (Firestore Snapshot)
            from app.utils.data_manager import data_manager
            
            bypass_fs = bool(min_version)
            unified_snapshot = data_manager.get_unified_dashboard(account_id, bypass_cache=bypass_fs) or {}
            snapshot = unified_snapshot.get('assignments_snapshot', {})
            if snapshot and 'pending' in snapshot:
                log_debug(f"Serving data directly from SSOT snapshot for {account_id}",
                         account_id=account_id, operation="get_pending_widget")
                
                # Recategorize to apply any latest code logic changes
                data = self._recategorize_cached_data(snapshot)
                
                # Ensure totals are present for the frontend
                if 'total_pending' not in data and 'pending' in data:
                    data['total_pending'] = len(data['pending'])
                if 'total_missed' not in data and 'missed' in data:
                    data['total_missed'] = len(data['missed'])
                
                age_minutes = 0
                if 'refreshed_at' in data:
                    try:
                        refreshed = datetime.fromisoformat(data['refreshed_at'].replace('Z', '+00:00')).replace(tzinfo=None)
                        age_minutes = (datetime.now() - refreshed).total_seconds() / 60
                    except Exception:
                        pass
                
                duration = (datetime.now() - start_time).total_seconds()
                log_performance("get_pending_widget_ssot", duration,
                              account_id=account_id, success=True, from_cache=True)
                              
                return {
                    **data,
                    'from_cache': True,
                    'is_stale': age_minutes > 1440, # Mark stale if older than 24 hours
                    'cache_age_minutes': age_minutes
                }
            
            # If no snapshot exists at all, fetch synchronously
            log_info(f"Snapshot miss for {account_id}, fetching synchronously",
                    account_id=account_id, operation="get_pending_widget")
            
            result = self._fetch_and_cache_assignments(account_id, cache_key)
            
            # Return the fresh data
            return self._refresh_response(account_id, result)
        
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            log_error(f"Error getting pending assignments widget data: {e}",
                     error_code="GET_PENDING_WIDGET_ERROR",
                     account_id=account_id, exc_info=True)
            
            log_performance("get_pending_widget_error", duration,
                          account_id=account_id, success=False, error=str(e))
            
            # Return empty result with error
            return self._refresh_response(account_id, self._empty_result('Could not refresh assignments. Please try again.'))

    @staticmethod
    def _refresh_failed(result: Dict[str, Any]) -> bool:
        return bool(result.get('error') or result.get('refresh_failed') or
                    result.get('failed_courses') or result.get('warning'))

    def _refresh_response(self, account_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Keep presentation fallback separate from fresh scheduler input."""
        if not self._refresh_failed(result):
            return result
        try:
            from app.utils.data_manager import data_manager
            unified = data_manager.get_unified_dashboard(account_id, bypass_cache=True) or {}
            saved = unified.get('assignments_snapshot') or {}
            if (not self._refresh_failed(saved) and
                    ('all_assignments' in saved or 'assignments' in saved or 'pending' in saved)):
                return {
                    **self._recategorize_cached_data(saved),
                    'from_cache': True,
                    'is_stale': True,
                    'using_saved_snapshot': True,
                    'refresh_failed': True,
                    'error': 'Could not refresh assignments.',
                    'user_message': 'Refresh failed. Showing your last saved assignments. Please try again later.',
                }
        except Exception:
            logger.warning('Could not load saved assignments after a failed refresh', exc_info=True)
        return {
            **result,
            'refreshed_at': None,
            'using_saved_snapshot': False,
            'refresh_failed': True,
            'error': 'Could not refresh assignments.',
            'user_message': 'Could not load assignments. Please try again later.',
        }
    
    def get_assignments_summary(self, account_id: str) -> Dict[str, Any]:
        """
        Get quick summary of assignments for dashboard cards.
        Uses aggressive caching since this is just counts.
        
        Returns:
            Dictionary with counts and quick stats
        """
        try:
            cache_key = f"assignments_summary:{account_id}"
            
            # Try cache (5 minute TTL for summaries)
            cached_result = self.cache.get(cache_key, allow_stale=False)
            if cached_result:
                data, _ = cached_result
                return data
            
            # Get full data
            widget_data = self.get_pending_assignments_widget_data(account_id)
            
            # Create summary
            summary = {
                'total_pending': widget_data.get('total_pending', 0),
                'total_missed': widget_data.get('total_missed', 0),
                'urgent_count': len([a for a in widget_data.get('pending', []) 
                                    if self._is_urgent(a)]),
                'latest_semester': widget_data.get('latest_semester'),
                'last_updated': widget_data.get('refreshed_at'),
                'from_cache': True
            }
            
            # Cache for 5 minutes
            try:
                self.cache.set(cache_key, summary, ttl=LMSCacheService.TTL_SHORT)
            except Exception as cache_error:
                log_warning(f"Failed to cache summary: {cache_error}",
                           account_id=account_id)
            
            return summary
        
        except Exception as e:
            log_error(f"Error getting assignments summary: {e}",
                     error_code="GET_SUMMARY_ERROR",
                     account_id=account_id, exc_info=True)
            
            # Return empty summary
            return {
                'total_pending': 0,
                'total_missed': 0,
                'urgent_count': 0,
                'latest_semester': None,
                'last_updated': None,
                'from_cache': False,
                'error': 'Failed to load summary'
            }
    
    def get_upcoming_deadlines(self, account_id: str, days: int = 7) -> List[Dict[str, Any]]:
        """
        Get assignments with upcoming deadlines (within N days).
        
        Args:
            account_id: Account ID
            days: Number of days to look ahead
            
        Returns:
            List of assignments with upcoming deadlines, sorted by deadline
        """
        widget_data = self.get_pending_assignments_widget_data(account_id)
        pending = widget_data.get('pending', [])
        
        # Filter for upcoming deadlines using date range (adjusted to PKT)
        now = datetime.utcnow() + timedelta(hours=5)
        cutoff = now + timedelta(days=days)
        
        upcoming = filter_assignments_by_date_range(pending, start_date=now, end_date=cutoff)
        
        # Sort by deadline (soonest first)
        upcoming = sort_assignments_by_deadline(upcoming, ascending=True)
        
        return upcoming
    
    def get_course_assignment_counts(self, account_id: str) -> Dict[str, int]:
        """
        Get assignment counts per course for dashboard visualization.
        
        Returns:
            Dictionary mapping course names to assignment counts
        """
        widget_data = self.get_pending_assignments_widget_data(account_id)
        all_assignments = widget_data.get('all_assignments', [])
        
        course_counts = {}
        for assignment in all_assignments:
            course = assignment.get('course_name', 'Unknown')
            course_counts[course] = course_counts.get(course, 0) + 1
        
        return course_counts
    
    def invalidate_cache(self, account_id: str):
        """Invalidate all cached data for an account"""
        try:
            patterns = [
                f"pending_assignments:{account_id}",
                f"assignments_summary:{account_id}",
                f"*:{account_id}:*"
            ]
            
            for pattern in patterns:
                try:
                    self.cache.clear(pattern)
                except Exception as e:
                    log_warning(f"Failed to clear cache pattern {pattern}: {e}",
                               account_id=account_id, pattern=pattern)
            
            log_info(f"Invalidated cache for {account_id}", account_id=account_id)
        
        except Exception as e:
            log_error(f"Error invalidating cache: {e}",
                     error_code="INVALIDATE_CACHE_ERROR",
                     account_id=account_id, exc_info=True)
    
    def _fetch_and_cache_assignments(self, account_id: str, cache_key: str) -> Dict[str, Any]:
        """
        Fetch assignments from LMS and cache the results.
        This is the slow path - only called on cache miss or force refresh.
        """
        start_time = datetime.now()
        
        try:
            log_info(f"Fetching assignments from LMS for {account_id}",
                    account_id=account_id, operation="fetch_assignments")
            
            # Get latest semester
            try:
                options = self.lms_service.get_assignments_options(account_id, force_refresh=True)
            except Exception as e:
                log_error(f"Failed to get assignment options: {e}",
                         error_code="GET_OPTIONS_ERROR",
                         account_id=account_id, exc_info=True)
                return self._empty_result("Failed to load semester information. Please check your LMS connection.")
            
            if not options.get('semesters'):
                log_warning("No semesters found",
                           error_code="NO_SEMESTERS",
                           account_id=account_id)
                return self._empty_result("No semesters available for your account")
            
            latest_semester = options['semesters'][0]['code']
            latest_semester_name = options['semesters'][0]['name']
            if (not normalize_label(latest_semester_name) or
                    normalize_label(latest_semester_name) == normalize_label(latest_semester)):
                raise ValueError('Readable semester information is required')
            
            # Get all courses for the semester directly from the options payload
            # The LMS defaults to the latest semester, so options['courses'] is already correct.
            courses = options.get('courses', [])
            
            if not courses:
                log_warning("No courses found for semester",
                           error_code="NO_COURSES",
                           account_id=account_id, semester=latest_semester)
                return self._empty_result("No courses found for the current semester")
            validate_courses(courses)
            
            # Fetch assignments from all courses sequentially.
            # The LMS is too brittle and drops connections when hit with parallel requests,
            # which results in missing assignments. Sequential fetching guarantees flawless functioning.
            all_assignments = []
            failed_courses = []

            for course in courses:
                try:
                    assignments = self.lms_service.scraper_service.scrape_course_assignments(
                        account_id,
                        semester_code=latest_semester,
                        course_code=course['code'],
                        strict=True,
                        expected_course_name=course['name'],
                    )
                    if assignments:
                        cache_key_course = f"{latest_semester}:{course['code']}"
                        self.lms_service._cache_assignments(
                            account_id, assignments, cache_key_course, None
                        )
                        all_assignments.extend(assignments)
                except Exception as e:
                    failed_courses.append(course['name'])
                    log_error(f"Failed to fetch course {course['name']}: {e}",
                             error_code="COURSE_FETCH_ERROR",
                             account_id=account_id, course=course['name'],
                             course_code=course['code'], exc_info=True)
            
            # Log if some courses failed
            if failed_courses:
                log_warning(f"Failed to fetch {len(failed_courses)} courses",
                           account_id=account_id, failed_courses=failed_courses)
            
            # Convert all assignments to dict format
            all_assignments_dict = []
            for assignment in all_assignments:
                try:
                    if hasattr(assignment, 'to_dict'):
                        assignment_dict = assignment.to_dict()
                    else:
                        assignment_dict = assignment
                    
                    # Enrich with metadata (deadline_datetime, urgency_level, etc.)
                    enriched = enrich_assignment_with_metadata(assignment_dict)
                    all_assignments_dict.append(enriched)
                except Exception as e:
                    log_warning(f"Failed to process assignment: {e}",
                               account_id=account_id)
                    raise
            
            # Categorize assignments using utility function
            try:
                categorized = categorize_assignments(all_assignments_dict)
                pending = categorized['pending']
                missed = categorized['missed']
            except Exception as e:
                log_error(f"Failed to categorize assignments: {e}",
                         error_code="CATEGORIZE_ERROR",
                         account_id=account_id, exc_info=True)
                # Fallback to empty lists
                pending = []
                missed = []
                raise
            
            # Sort pending by deadline (earliest first)
            try:
                pending = sort_assignments_by_deadline(pending, ascending=True)
            except Exception as e:
                log_warning(f"Failed to sort pending assignments: {e}",
                           account_id=account_id)
            
            # Sort missed by deadline (most recent first)
            try:
                missed = sort_assignments_by_deadline(missed, ascending=False)
            except Exception as e:
                log_warning(f"Failed to sort missed assignments: {e}",
                           account_id=account_id)
            
            # Create result
            result = {
                'pending': pending,
                'missed': missed,
                'all_assignments': all_assignments_dict,
                'total_pending': len(pending),
                'total_missed': len(missed),
                'total_assignments': len(all_assignments_dict),
                'latest_semester': latest_semester,
                'latest_semester_name': latest_semester_name,
                'course_count': len(courses),
                'refreshed_at': datetime.utcnow().isoformat() + 'Z',
                'from_cache': False,
                'is_stale': False
            }
            
            # Add warning if some courses failed
            if failed_courses:
                result['warning'] = f"Could not load {len(failed_courses)} course(s)"
                result['failed_courses'] = failed_courses
                # Incomplete results must not update caches or notification baselines.
                return result
            _, invalid_identities = identity_index(account_id, result)
            if invalid_identities:
                raise ValueError(f'Assignment identities are ambiguous or missing: {invalid_identities}')
            
            # Persist for background urgent checks only if changed or baseline missing
            try:
                from app.utils.data_manager import data_manager
                unified = data_manager.get_unified_dashboard(account_id) or {}
                old_snapshot = unified.get('assignments_snapshot') or {}
                old_pending = old_snapshot.get('pending', [])
                old_missed = old_snapshot.get('missed', [])
                old_total = int(old_snapshot.get('total_assignments') if old_snapshot.get('total_assignments') is not None else len(old_snapshot.get('all_assignments', [])))
                
                # Dirty check: compare static assignment fingerprints (ignore dynamic hours_until_due floats)
                def _assignment_sig(a):
                    if not isinstance(a, dict):
                        return str(a)
                    return (
                        str(a.get('assignment_no') or '').strip(),
                        str(a.get('title') or '').strip(),
                        str(a.get('course_code') or '').strip(),
                        str(a.get('course_name') or '').strip(),
                        str(a.get('semester') or '').strip(),
                        str(a.get('deadline') or '').strip(),
                        str(a.get('actual_deadline') or '').strip(),
                        str(a.get('extended_deadline') or '').strip(),
                        str(a.get('submission_status') or '').strip(),
                        str(a.get('submission_file') or '').strip(),
                        str(a.get('assignment_file') or '').strip(),
                        str(a.get('solution_file') or '').strip(),
                        str(a.get('remarks') or '').strip(),
                        str(a.get('marks_obtained') or '').strip(),
                        str(a.get('total_marks') or '').strip(),
                        str(a.get('category') or '').strip(),
                        str(a.get('urgency_level') or '').strip()
                    )

                new_pending_sigs = [_assignment_sig(a) for a in pending]
                old_pending_sigs = [_assignment_sig(a) for a in old_pending]
                new_missed_sigs = [_assignment_sig(a) for a in missed]
                old_missed_sigs = [_assignment_sig(a) for a in old_missed]
                new_all_sigs = [_assignment_sig(a) for a in all_assignments_dict]
                old_all_sigs = [_assignment_sig(a) for a in old_snapshot.get('all_assignments', [])]
                
                has_changed = (
                    not old_snapshot or
                    latest_semester_name != old_snapshot.get('latest_semester_name') or
                    len(pending) != len(old_pending) or
                    len(missed) != len(old_missed) or
                    len(all_assignments_dict) != old_total or
                    sorted(new_pending_sigs) != sorted(old_pending_sigs) or
                    sorted(new_missed_sigs) != sorted(old_missed_sigs) or
                    sorted(new_all_sigs) != sorted(old_all_sigs)
                )
                
                # Guard against overwriting clean baseline with partial degraded scrape
                if failed_courses and old_snapshot:
                    log_warning(f"Preserving existing assignments snapshot for {account_id}: {len(failed_courses)} course(s) failed to scrape", account_id=account_id)
                elif has_changed:
                    payload = {
                        **assignment_snapshot_payload(unified, result, account_id),
                        'last_updated': datetime.utcnow().isoformat() + 'Z',
                        'legacy_migrated': True
                    }
                    if not data_manager.save_unified_dashboard(account_id, payload, merge=True):
                        raise RuntimeError('Failed to save assignments snapshot')
                    self.cache.delete(f'assignments_summary:{account_id}')
                    log_info(f"Persisted changed assignments snapshot for {account_id}", account_id=account_id)
                else:
                    log_debug(f"Assignments snapshot unchanged for {account_id}, skipping Firestore write", account_id=account_id)
            except Exception as e:
                log_debug(f"Failed to persist dashboard assignment snapshot: {e}", account_id=account_id)
                raise
            
            # Cache the result (30 minute TTL)
            try:
                self.cache.set(cache_key, result, ttl=LMSCacheService.TTL_MEDIUM)
            except Exception as cache_error:
                log_warning(f"Failed to cache results: {cache_error}",
                           account_id=account_id)
            
            duration = (datetime.now() - start_time).total_seconds()
            log_performance("fetch_and_cache_assignments", duration,
                          account_id=account_id, success=True,
                          pending=len(pending), missed=len(missed),
                          total=len(all_assignments_dict), courses=len(courses))
            
            log_info(f"Fetched and cached {len(all_assignments)} assignments for {account_id}",
                    account_id=account_id, pending=len(pending), missed=len(missed))
            
            return result
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            log_error(f"Error fetching assignments for {account_id}: {e}",
                     error_code="FETCH_ASSIGNMENTS_ERROR",
                     account_id=account_id, duration=duration, exc_info=True)
            
            log_performance("fetch_and_cache_assignments", duration,
                          account_id=account_id, success=False, error=str(e))
            
            return self._empty_result("Failed to load assignments. Please try again later.")
    
    def _empty_result(self, message: str = "No data available") -> Dict[str, Any]:
        """Return empty result structure"""
        return {
            'pending': [],
            'missed': [],
            'all_assignments': [],
            'total_pending': 0,
            'total_missed': 0,
            'total_assignments': 0,
            'latest_semester': None,
            'latest_semester_name': None,
            'course_count': 0,
            'refreshed_at': None,
            'from_cache': False,
            'is_stale': False,
            'error': message
        }
    
    def _recategorize_cached_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Re-run categorization on cached data using all_assignments as source of truth.
        This ensures stale cached missed/pending lists are always corrected by the
        latest categorization logic without requiring a server restart.
        Only runs if all_assignments have the fields needed for categorization.
        """
        all_assignments = data.get('all_assignments') or data.get('assignments')
        if not all_assignments:
            return data

        # Only re-categorize if assignments have the required fields
        sample = all_assignments[0] if all_assignments else {}
        has_required_fields = 'submission_status' in sample or 'is_past_due' in sample
        if not has_required_fields:
            return data

        try:
            categorized = categorize_assignments(all_assignments)
            pending = sort_assignments_by_deadline(categorized['pending'], ascending=True)
            missed = sort_assignments_by_deadline(categorized['missed'], ascending=False)
            return {
                **data,
                'pending': pending,
                'missed': missed,
                'total_pending': len(pending),
                'total_missed': len(missed),
            }
        except Exception as e:
            log_warning(f"Failed to re-categorize cached data: {e}")
            return data

    def _is_urgent(self, assignment: Dict[str, Any]) -> bool:
        """Check if assignment is urgent (due within 24 hours)"""
        urgency_level = calculate_urgency_level(assignment)
        return urgency_level == 'urgent'
