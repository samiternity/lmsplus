# app/services/lms_background_service.py
"""
Background service for asynchronous LMS data fetching.
Handles scheduled refreshes, parallel scraping, and cache warming.
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from queue import Queue, Empty
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.models.lms_models import LMSAssignment
from app.utils.assignment_identity import validate_courses, normalize_label, assignment_snapshot_payload, log_identity_conflicts
from app.utils.lms_logger import log_info, log_warning, log_error, log_debug, log_performance

logger = logging.getLogger(__name__)


class BackgroundTask:
    """Represents a background task with priority and retry logic"""
    
    def __init__(self, task_id: str, account_id: str, task_func: Callable, 
                 priority: int = 5, max_retries: int = 2):
        self.task_id = task_id
        self.account_id = account_id
        self.task_func = task_func
        self.priority = priority  # Lower number = higher priority
        self.max_retries = max_retries
        self.retry_count = 0
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        self.error = None
        self.result = None
    
    def __lt__(self, other):
        """For priority queue sorting"""
        return self.priority < other.priority
    
    def execute(self):
        """Execute the task"""
        self.started_at = datetime.now()
        try:
            log_debug(f"Executing background task {self.task_id}",
                     account_id=self.account_id, task_id=self.task_id,
                     priority=self.priority)
            
            self.result = self.task_func()
            self.completed_at = datetime.now()
            
            # Log performance
            duration = (self.completed_at - self.started_at).total_seconds()
            log_performance(f"background_task_{self.task_id}", duration,
                          account_id=self.account_id, success=True,
                          task_id=self.task_id, priority=self.priority)
            
            return True
        except Exception as e:
            self.error = str(e)
            self.retry_count += 1
            self.completed_at = datetime.now()
            
            # Log error with context
            duration = (self.completed_at - self.started_at).total_seconds()
            log_error(f"Background task {self.task_id} failed: {e}",
                     error_code="BACKGROUND_TASK_FAILED",
                     account_id=self.account_id, task_id=self.task_id,
                     retry_count=self.retry_count, duration=duration,
                     priority=self.priority, exc_info=True)
            
            # Log performance even for failures
            log_performance(f"background_task_{self.task_id}", duration,
                          account_id=self.account_id, success=False,
                          task_id=self.task_id, error=str(e))
            
            return False
    
    def should_retry(self) -> bool:
        """Check if task should be retried"""
        return self.retry_count < self.max_retries


class LMSBackgroundService:
    """
    Service for managing background LMS operations.
    Handles parallel scraping, task queuing, and cache warming.
    """
    
    def __init__(self, lms_service):
        self.lms_service = lms_service
        
        # Task queue and worker threads
        self.task_queue = Queue()
        self.active_tasks: Dict[str, BackgroundTask] = {}
        self.completed_tasks: Dict[str, BackgroundTask] = {}
        
        # Worker pool configuration
        self.max_workers = 3  # Limit concurrent scraping to avoid overwhelming LMS
        self.worker_pool = None
        self.is_running = False
        
        # Refresh schedules per account
        self.refresh_schedules: Dict[str, Dict[str, datetime]] = {}
        
        # Performance tracking
        self.stats = {
            'tasks_completed': 0,
            'tasks_failed': 0,
            'tasks_retried': 0,
            'total_scrape_time': 0,
            'avg_task_duration': 0.0,
            'max_task_duration': 0.0,
            'min_task_duration': float('inf'),
            'total_assignments_scraped': 0,
            'total_courses_scraped': 0,
            'scrape_errors': 0
        }
        
        # Service start time
        self.start_time = datetime.now()
    
    def start(self):
        """Start the background service"""
        try:
            if self.is_running:
                log_warning("Background service already running")
                return
            
            self.is_running = True
            self.worker_pool = ThreadPoolExecutor(max_workers=self.max_workers)
            
            # Start task processor thread
            processor_thread = threading.Thread(target=self._process_tasks, daemon=True)
            processor_thread.start()
            
            log_info("LMS Background Service started", max_workers=self.max_workers)
        
        except Exception as e:
            log_error(f"Failed to start background service: {e}",
                     error_code="BACKGROUND_SERVICE_START_ERROR", exc_info=True)
            self.is_running = False
            raise
    
    def stop(self):
        """Stop the background service"""
        try:
            log_info("Stopping LMS Background Service...")
            self.is_running = False
            
            if self.worker_pool:
                self.worker_pool.shutdown(wait=True)
            
            log_info("LMS Background Service stopped",
                    tasks_completed=self.stats['tasks_completed'],
                    tasks_failed=self.stats['tasks_failed'])
        
        except Exception as e:
            log_error(f"Error stopping background service: {e}",
                     error_code="BACKGROUND_SERVICE_STOP_ERROR", exc_info=True)
    
    def schedule_pending_assignments_refresh(self, account_id: str, 
                                            priority: int = 5,
                                            force: bool = False) -> str:
        """
        Schedule a background refresh of all pending assignments.
        
        Args:
            account_id: Account to refresh
            priority: Task priority (1=highest, 10=lowest)
            force: Force refresh even if recently updated
            
        Returns:
            Task ID for tracking
        """
        try:
            # Check if refresh is needed
            if not force and self._is_recently_refreshed(account_id, 'pending_assignments'):
                log_debug(f"Skipping refresh for {account_id} - recently updated",
                         account_id=account_id)
                return None
            
            # Create task
            task_id = f"pending_assignments_{account_id}_{int(time.time())}"
            
            def refresh_task():
                return self._refresh_all_pending_assignments(account_id)
            
            task = BackgroundTask(
                task_id=task_id,
                account_id=account_id,
                task_func=refresh_task,
                priority=priority
            )
            
            # Add to queue
            self.task_queue.put(task)
            self.active_tasks[task_id] = task
            
            log_info(f"Scheduled pending assignments refresh for {account_id}",
                    account_id=account_id, task_id=task_id, priority=priority)
            
            return task_id
        
        except Exception as e:
            log_error(f"Error scheduling refresh for {account_id}: {e}",
                     error_code="SCHEDULE_REFRESH_ERROR",
                     account_id=account_id, exc_info=True)
            return None
    
    def schedule_course_assignments_refresh(self, account_id: str, 
                                           semester_code: str,
                                           course_codes: List[str],
                                           priority: int = 7) -> List[str]:
        """
        Schedule parallel refresh of multiple courses.
        
        Args:
            account_id: Account ID
            semester_code: Semester code
            course_codes: List of course codes to refresh
            priority: Task priority
            
        Returns:
            List of task IDs
        """
        task_ids = []
        
        for course_code in course_codes:
            task_id = f"course_assignments_{account_id}_{course_code}_{int(time.time())}"
            
            def refresh_task(sem=semester_code, course=course_code):
                return self._refresh_course_assignments(account_id, sem, course)
            
            task = BackgroundTask(
                task_id=task_id,
                account_id=account_id,
                task_func=refresh_task,
                priority=priority
            )
            
            self.task_queue.put(task)
            self.active_tasks[task_id] = task
            task_ids.append(task_id)
        
        log_info(f"Scheduled {len(course_codes)} course refreshes for {account_id}",
                account_id=account_id, course_count=len(course_codes))
        
        return task_ids
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a background task"""
        task = self.active_tasks.get(task_id) or self.completed_tasks.get(task_id)
        
        if not task:
            return None
        
        status = {
            'task_id': task.task_id,
            'account_id': task.account_id,
            'created_at': task.created_at.isoformat(),
            'started_at': task.started_at.isoformat() if task.started_at else None,
            'completed_at': task.completed_at.isoformat() if task.completed_at else None,
            'retry_count': task.retry_count,
            'error': task.error,
            'is_complete': task.completed_at is not None,
            'is_failed': task.error is not None and not task.should_retry()
        }
        
        if task.started_at and not task.completed_at:
            status['duration_seconds'] = (datetime.now() - task.started_at).total_seconds()
        elif task.completed_at:
            status['duration_seconds'] = (task.completed_at - task.started_at).total_seconds()
        
        return status
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive service statistics"""
        # Calculate uptime
        uptime = datetime.now() - self.start_time
        uptime_hours = uptime.total_seconds() / 3600
        
        # Calculate success rate
        total_tasks = self.stats['tasks_completed'] + self.stats['tasks_failed']
        success_rate = (self.stats['tasks_completed'] / total_tasks * 100) if total_tasks > 0 else 0
        
        return {
            # Basic stats
            **self.stats,
            'active_tasks': len(self.active_tasks),
            'queued_tasks': self.task_queue.qsize(),
            'completed_tasks_stored': len(self.completed_tasks),
            'is_running': self.is_running,
            
            # Calculated metrics
            'total_tasks': total_tasks,
            'success_rate_percent': round(success_rate, 2),
            'avg_task_duration_seconds': round(self.stats['avg_task_duration'], 2),
            'max_task_duration_seconds': round(self.stats['max_task_duration'], 2),
            'min_task_duration_seconds': round(self.stats['min_task_duration'], 2) if self.stats['min_task_duration'] != float('inf') else 0,
            
            # Service info
            'uptime_hours': round(uptime_hours, 2),
            'start_time': self.start_time.isoformat(),
            'max_workers': self.max_workers
        }
    
    def _process_tasks(self):
        """Main task processing loop (runs in background thread)"""
        log_info("Task processor started")
        
        while self.is_running:
            try:
                # Get task from queue (with timeout to allow checking is_running)
                try:
                    task = self.task_queue.get(timeout=1.0)
                except Empty:
                    continue
                
                # Execute task
                log_debug(f"Executing task {task.task_id}",
                         account_id=task.account_id, task_id=task.task_id)
                
                success = task.execute()
                
                if success:
                    # Task completed successfully
                    self.stats['tasks_completed'] += 1
                    self.completed_tasks[task.task_id] = task
                    del self.active_tasks[task.task_id]
                    
                    # Update performance metrics
                    duration = (task.completed_at - task.started_at).total_seconds()
                    self._update_performance_metrics(duration)
                    
                    # Update refresh schedule
                    self._mark_refreshed(task.account_id, 'pending_assignments')
                    
                    log_info(f"Task {task.task_id} completed successfully",
                            account_id=task.account_id, task_id=task.task_id,
                            duration=task.completed_at - task.started_at)
                else:
                    # Task failed
                    if task.should_retry():
                        # Re-queue with lower priority
                        self.stats['tasks_retried'] += 1
                        task.priority += 2
                        self.task_queue.put(task)
                        log_warning(f"Task {task.task_id} failed, retrying",
                                   account_id=task.account_id, task_id=task.task_id,
                                   retry_count=task.retry_count)
                    else:
                        # Max retries reached
                        self.stats['tasks_failed'] += 1
                        self.completed_tasks[task.task_id] = task
                        del self.active_tasks[task.task_id]
                        log_error(f"Task {task.task_id} failed permanently",
                                 account_id=task.account_id, task_id=task.task_id,
                                 error=task.error)
                
                # Mark task as done in queue
                self.task_queue.task_done()
                
            except Exception as e:
                log_error(f"Error in task processor: {e}", exc_info=True)
                time.sleep(1)  # Prevent tight loop on errors
        
        log_info("Task processor stopped")
    
    def _refresh_all_pending_assignments(self, account_id: str) -> Dict[str, Any]:
        """
        Refresh all pending assignments for an account.
        This is the actual work done in background.
        """
        start_time = time.time()
        
        try:
            log_info(f"Starting background refresh for {account_id}",
                    account_id=account_id, operation="background_refresh")
            
            # Get latest semester
            options = self.lms_service.get_assignments_options(account_id, force_refresh=True)
            if not options.get('semesters'):
                log_warning(f"No semesters found for {account_id}",
                           account_id=account_id, error_code="NO_SEMESTERS")
                return {
                    'success': False,
                    'error': 'No semesters found',
                    'error_code': 'NO_SEMESTERS',
                    'user_message': 'No semesters available for your account'
                }
            
            latest_semester = options['semesters'][0]['code']
            latest_semester_name = options['semesters'][0]['name']
            if (not normalize_label(latest_semester_name) or
                    normalize_label(latest_semester_name) == normalize_label(latest_semester)):
                raise ValueError('Readable semester information is required')
            
            # Get all courses for the semester
            courses = self.lms_service.get_assignments_courses_for_semester(
                account_id, latest_semester
            )
            
            if not courses:
                log_warning(f"No courses found for semester {latest_semester}",
                           account_id=account_id, semester=latest_semester,
                           error_code="NO_COURSES")
                return {
                    'success': False,
                    'error': 'No courses found',
                    'error_code': 'NO_COURSES',
                    'user_message': 'No courses found for the current semester'
                }
            
            validate_courses(courses)

            # Scrape assignments from all courses sequentially to avoid connection drops
            all_assignments = []
            failed_courses = []
            
            for course in courses:
                try:
                    assignments = self.lms_service.scraper_service.scrape_course_assignments(
                        account_id,
                        semester_code=latest_semester,
                        course_code=course['code'],
                        strict=True, expected_course_name=course['name']
                    )
                    all_assignments.extend(assignments or [])
                except Exception as e:
                    failed_courses.append(course.get('name', 'Unknown'))
                    log_error(f"Sequential scrape failed for course {course.get('name')}: {e}",
                             account_id=account_id)
            
            if failed_courses:
                log_warning(f"Failed to scrape {len(failed_courses)} courses", 
                           account_id=account_id, failed_courses=failed_courses)
            # Filter for pending assignments (can still submit, deadline not passed)
            pending = [a for a in all_assignments 
                      if a.is_pending() and not a.is_past_due() and not a.submission_file]
            
            # Filter for missed assignments (deadline exceeded, nothing submitted)
            missed = [a for a in all_assignments 
                     if (a.is_overdue() or a.is_past_due()) and not a.submission_file]
            
            # Update statistics
            self.stats['total_assignments_scraped'] += len(all_assignments)
            self.stats['total_courses_scraped'] += len(courses)
            
            # Cache the results
            cache_data = {
                'pending': [a.to_dict() for a in pending],
                'missed': [a.to_dict() for a in missed],
                'all_assignments': [a.to_dict() for a in all_assignments],
                'latest_semester': latest_semester,
                'latest_semester_name': latest_semester_name,
                'refreshed_at': datetime.now().isoformat(),
                'course_count': len(courses),
                'total_assignments': len(all_assignments),
                'total_pending': len(pending),
                'total_missed': len(missed),
            }
            
            if failed_courses:
                raise RuntimeError('Incomplete assignment scrape; preserving previous snapshot')
            # Notification tracking validates identities separately from the monitor snapshot.
            log_identity_conflicts(account_id, cache_data)

            # Persist for background urgent checks (no TTL data_manager)
            try:
                from app.utils.data_manager import data_manager
                unified = data_manager.get_unified_dashboard(account_id) or {}
                if not data_manager.save_unified_dashboard(account_id, {
                    **assignment_snapshot_payload(unified, cache_data, account_id),
                    'last_updated': datetime.utcnow().isoformat() + 'Z',
                    'legacy_migrated': True
                }, merge=True):
                    raise RuntimeError('Failed to save assignments snapshot')
                from app.services.lms_cache_service import get_cache_service
                get_cache_service().delete(f'assignments_summary:{account_id}')
            except Exception as e:
                log_debug(f"Failed to persist assignment snapshot: {e}", account_id=account_id)
                raise
            
            # Store in cache with 30-minute TTL
            self.lms_service._cache_data(
                f"pending_assignments:{account_id}",
                cache_data,
                timeout=timedelta(minutes=30)
            )
            
            duration = time.time() - start_time
            self.stats['total_scrape_time'] += duration
            
            # Log performance
            log_performance("background_refresh_assignments", duration,
                          account_id=account_id, success=True,
                          pending_count=len(pending), missed_count=len(missed),
                          total_count=len(all_assignments), course_count=len(courses))
            
            log_info(f"Background refresh completed for {account_id}",
                    account_id=account_id, duration=duration,
                    pending_count=len(pending), missed_count=len(missed),
                    total_count=len(all_assignments))
            
            return {
                'success': True,
                'pending_count': len(pending),
                'missed_count': len(missed),
                'total_count': len(all_assignments),
                'duration': duration
            }
            
        except Exception as e:
            duration = time.time() - start_time
            log_error(f"Background refresh failed for {account_id}: {e}",
                     error_code="BACKGROUND_REFRESH_FAILED",
                     account_id=account_id, duration=duration, exc_info=True)
            
            # Log performance for failure
            log_performance("background_refresh_assignments", duration,
                          account_id=account_id, success=False, error=str(e))
            
            return {
                'success': False,
                'error': str(e),
                'error_code': 'BACKGROUND_REFRESH_FAILED',
                'user_message': 'Failed to refresh assignments. Please try again later.'
            }
    
    def _parallel_scrape_courses(self, account_id: str, semester_code: str,
                                courses: List[Dict[str, str]]) -> List[LMSAssignment]:
        """
        Scrape multiple courses in parallel for better performance.
        
        Args:
            account_id: Account ID
            semester_code: Semester code
            courses: List of course dicts with 'code' and 'name'
            
        Returns:
            Combined list of all assignments
        """
        all_assignments = []
        
        # Use ThreadPoolExecutor for parallel scraping
        with ThreadPoolExecutor(max_workers=min(3, len(courses))) as executor:
            # Submit all scraping tasks
            future_to_course = {
                executor.submit(
                    self._safe_scrape_course,
                    account_id,
                    semester_code,
                    course['code'],
                    course['name']
                ): course
                for course in courses
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_course):
                course = future_to_course[future]
                try:
                    assignments = future.result()
                    if assignments:
                        all_assignments.extend(assignments)
                        log_debug(f"Scraped {len(assignments)} assignments from {course['name']}",
                                 account_id=account_id, course=course['name'])
                except Exception as e:
                    log_warning(f"Failed to scrape course {course['name']}: {e}",
                               account_id=account_id, course=course['name'])
        
        return all_assignments
    
    def _safe_scrape_course(self, account_id: str, semester_code: str,
                           course_code: str, course_name: str) -> List[LMSAssignment]:
        """
        Safely scrape a single course with error handling.
        """
        start_time = time.time()
        try:
            assignments = self.lms_service.get_assignments_for_course(
                account_id,
                semester_code=semester_code,
                course_code=course_code,
                force_refresh=True
            )
            
            duration = time.time() - start_time
            log_performance(f"scrape_course_{course_code}", duration,
                          account_id=account_id, success=True,
                          course=course_name, assignment_count=len(assignments))
            
            return assignments
        except Exception as e:
            self.stats['scrape_errors'] += 1
            duration = time.time() - start_time
            log_error(f"Error scraping course {course_name}: {e}",
                     error_code="COURSE_SCRAPE_ERROR",
                     account_id=account_id, course=course_name,
                     course_code=course_code, exc_info=True)
            
            log_performance(f"scrape_course_{course_code}", duration,
                          account_id=account_id, success=False,
                          course=course_name, error=str(e))
            
            return []
    
    def _refresh_course_assignments(self, account_id: str, semester_code: str,
                                   course_code: str) -> Dict[str, Any]:
        """Refresh assignments for a single course"""
        try:
            assignments = self.lms_service.get_assignments_for_course(
                account_id,
                semester_code=semester_code,
                course_code=course_code,
                force_refresh=True
            )
            return {
                'success': True,
                'course_code': course_code,
                'count': len(assignments)
            }
        except Exception as e:
            return {
                'success': False,
                'course_code': course_code,
                'error': str(e)
            }
    
    def _is_recently_refreshed(self, account_id: str, data_type: str,
                              threshold: timedelta = timedelta(minutes=25)) -> bool:
        """Check if data was recently refreshed"""
        if account_id not in self.refresh_schedules:
            return False
        
        last_refresh = self.refresh_schedules[account_id].get(data_type)
        if not last_refresh:
            return False
        
        return datetime.now() - last_refresh < threshold
    
    def _mark_refreshed(self, account_id: str, data_type: str):
        """Mark data as refreshed"""
        if account_id not in self.refresh_schedules:
            self.refresh_schedules[account_id] = {}
        
        self.refresh_schedules[account_id][data_type] = datetime.now()
    
    def _update_performance_metrics(self, duration: float):
        """
        Update performance metrics with task duration.
        
        Args:
            duration: Task duration in seconds
        """
        try:
            # Update total scrape time
            self.stats['total_scrape_time'] += duration
            
            # Update average
            completed = self.stats['tasks_completed']
            if completed > 0:
                self.stats['avg_task_duration'] = self.stats['total_scrape_time'] / completed
            
            # Update max
            if duration > self.stats['max_task_duration']:
                self.stats['max_task_duration'] = duration
            
            # Update min
            if duration < self.stats['min_task_duration']:
                self.stats['min_task_duration'] = duration
        except Exception as e:
            log_debug(f"Error updating performance metrics: {e}")


# Global instance (initialized in app factory)
background_service: Optional[LMSBackgroundService] = None


def init_background_service(lms_service):
    """Initialize the global background service"""
    global background_service
    background_service = LMSBackgroundService(lms_service)
    background_service.start()
    return background_service


def get_background_service() -> Optional[LMSBackgroundService]:
    """Get the global background service instance"""
    return background_service
