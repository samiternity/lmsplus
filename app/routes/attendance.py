import logging
logger = logging.getLogger(__name__)

from flask import Blueprint, render_template, request, session, flash, redirect, url_for, jsonify
from flask_login import login_required, current_user
from app.utils.enhanced_scraper import scraper_manager
from app.utils.data_manager import data_manager
from app.utils.session_auth import is_account_authenticated
from app.services.attendance_change_tracker import AttendanceChangeTracker
from app.services.attendance_updates_renderer import AttendanceUpdatesRenderer
from app.services.notification_service import get_notification_service
from app.models.notification_models import NotificationType
import requests
from datetime import datetime, timedelta
import random
import time
import re

attendance_bp = Blueprint('attendance', __name__)


def send_attendance_notifications(user_id: str, changes: list, user=None):
    """
    Send notifications for attendance changes based on user preferences.
    
    Args:
        user_id: The user ID to send notifications to
        changes: List of attendance change objects
        user: User object to check notification preferences
    """
    result = {'sent': 0, 'failed': []}
    for change in changes or []:
        try:
            # Only process actual attendance changes
            if not hasattr(change, 'change_type') or change.change_type != 'attendance':
                continue
            
            # Determine notification type based on change
            severity = change.get_change_severity() if hasattr(change, 'get_change_severity') else 'neutral'
            is_scheduled = getattr(change, 'is_scheduled', True)
            
            # Determine if user was marked absent (negative) or present (positive)
            if getattr(change, 'absences_difference', 0) > 0 or severity == 'negative':
                # Absent notification
                if is_scheduled:
                    notification_type = NotificationType.ATTENDANCE_ABSENT_SCHEDULED
                    pref_key = 'absent_scheduled'
                else:
                    notification_type = NotificationType.ATTENDANCE_ABSENT_UNSCHEDULED
                    pref_key = 'absent_unscheduled'
            else:
                # Positive or Neutral changes - skip
                continue
            
            # Check user preference before sending
            if user and hasattr(user, 'notification_preferences'):
                if not user.notification_preferences.get(pref_key, False):
                    continue  # User has disabled this notification type
            
            # Build a concise push notification
            status_word = "Absent"
            title = f"{status_word} Marked"
            message = f"{status_word} marked for {change.course_name}"
            
            # Send the notification
            notification = get_notification_service().send_notification(
                user_id=user_id,
                notification_type=notification_type,
                title=title,
                message=message,
                data={
                    'course_code': change.course_code,
                    'course_name': change.course_name,
                    'is_scheduled': is_scheduled
                },
                send_push=True
            )
            if notification is None:
                result['failed'].append(change.course_code)
            else:
                result['sent'] += 1

        except Exception as e:
            result['failed'].append(getattr(change, 'course_code', ''))
            logger.warning(f"Error sending attendance notifications: {e}")

    return result

def get_attendance_updates(account_id):
    """Get attendance updates for the current user's active account"""
    try:
        from datetime import datetime
        from app.services.attendance_comparator import AttendanceComparator
        
        unified = data_manager.get_unified_dashboard(account_id) or {}
        today_data = unified.get('attendance_snapshot')
        yesterday_data = unified.get('attendance_yesterday_snapshot')
        
        changes = []
        is_first_time = not bool(yesterday_data)
        
        if today_data and yesterday_data:
            comparator = AttendanceComparator(data_manager, account_id)
            try:
                changes = comparator.compare_records(today_data, yesterday_data)
            except Exception as e:
                logger.warning(f"Error comparing attendance: {e}")
                
        # Get scheduled courses for today
        scheduled_courses = get_scheduled_courses_for_today(account_id)
        
        # Check if it's weekend
        is_weekend = datetime.now().weekday() >= 5  # Saturday = 5, Sunday = 6
        
        renderer = AttendanceUpdatesRenderer()
        updates_data = renderer.render_updates_section(
            changes, 
            scheduled_courses=scheduled_courses,
            is_weekend=is_weekend,
            is_first_time=is_first_time
        )
        return updates_data
        
    except Exception as e:
        logger.warning(f"Error getting attendance updates: {e}")
        return None

def get_scheduled_courses_for_today(account_id):
    """Get list of courses scheduled for today"""
    try:
        from app.services.attendance_comparator import AttendanceComparator
        from datetime import datetime
        
        comparator = AttendanceComparator(data_manager, account_id)
        
        # Get current day schedule
        current_day = datetime.now().strftime('%A')  # Monday, Tuesday, etc.
        day_schedule = comparator.get_current_day_schedule(current_day)
        
        # Extract course codes from timetable and map back to attendance codes
        scheduled_courses = []
        course_mapping = comparator.get_course_mapping()
        
        # Reverse mapping: timetable_code -> attendance_code
        reverse_mapping = {v: k for k, v in course_mapping.items()}
        
        for time_slot, class_info in day_schedule.items():
            if class_info and 'code' in class_info:
                timetable_code = class_info['code']
                attendance_code = reverse_mapping.get(timetable_code)
                if attendance_code and attendance_code not in scheduled_courses:
                    scheduled_courses.append(attendance_code)
        
        return scheduled_courses
        
    except Exception as e:
        logger.warning(f"Error getting scheduled courses: {e}")
        return []

def categorize_course_by_schedule(course_code, account_id):
    """Determine if a course is scheduled for today based on timetable data"""
    try:
        # Use the enhanced AttendanceComparator for better categorization
        from app.services.attendance_comparator import AttendanceComparator
        
        comparator = AttendanceComparator(data_manager, account_id)
        return comparator.categorize_course_by_schedule(course_code)
        
    except Exception as e:
        logger.warning(f"Error categorizing course by schedule: {e}")
        return False

@attendance_bp.route('/attendance')
@login_required
def attendance():
    # Try to get attendance data using new account system
    attendance_data = None
    attendance_updates = None
    
    try:
        active_account = current_user.get_active_account()
        
        if not active_account:
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return render_template('attendance.html', attendance=create_empty_attendance(), updates=None)
        
        # Check session authentication
        account_id = active_account.account_id
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        # Try to load cached data first
        unified = data_manager.get_unified_dashboard(account_id) or {}
        attendance_data = unified.get('attendance_snapshot')
        
        if attendance_data and 'courses' in attendance_data and not attendance_data.get('error'):
            # Normalize policy metrics for cached snapshots
            attendance_data = normalize_attendance_policy_metrics(attendance_data)
            # Detect attendance changes when loading cached data
            attendance_updates = get_attendance_updates(active_account.account_id)
            return render_template('attendance.html', attendance=attendance_data, updates=attendance_updates)
        
        # If no cached data, try to scrape fresh data
        if not attendance_data:
            scraper = scraper_manager.get_scraper(active_account)
            attendance_data = scraper.scrape_attendance(active_account.cms_username)
            
            if attendance_data and 'courses' in attendance_data and not attendance_data.get('error'):
                attendance_data = normalize_attendance_policy_metrics(attendance_data)
                # Detect attendance changes after scraping fresh data
                attendance_updates = get_attendance_updates(active_account.account_id)
                return render_template('attendance.html', attendance=attendance_data, updates=attendance_updates)
        
        if not attendance_data:
            flash('Unable to load attendance data from CMS. Please try refreshing the page.', 'warning')
            attendance_data = create_empty_attendance()
            
    except Exception as e:
        logger.warning(f"Error loading attendance data: {e}")
        flash('Error loading attendance data. Please try again later.', 'error')
        attendance_data = create_empty_attendance()
    
    attendance_updates = None
    attendance_data = normalize_attendance_policy_metrics(attendance_data)
    return render_template('attendance.html', attendance=attendance_data, updates=attendance_updates)

def normalize_attendance_policy_metrics(attendance_data):
    """Normalize course skip metrics and policy thresholds (including legacy snapshots)."""
    if not attendance_data or not isinstance(attendance_data, dict) or 'courses' not in attendance_data:
        return attendance_data
    
    for course in attendance_data.get('courses', []):
        try:
            raw_cr = course.get('credit_hours')
            credit_hrs = int(float(str(raw_cr).strip())) if raw_cr is not None else 3
        except (ValueError, TypeError):
            credit_hrs = 3

        course_name = (course.get('name') or '').lower()
        if 'lab' in course_name:
            safe_max = 12
            fine_max = 19
        elif credit_hrs == 0 or 'quran' in course_name:
            safe_max = 1
            fine_max = 2
        else:
            safe_max = int(credit_hrs * 16 * 0.25)
            fine_max = int(credit_hrs * 16 * 0.40)

        try:
            cur_abs = int(round(float(course.get('absences') or 0)))
        except (ValueError, TypeError):
            cur_abs = 0

        course['safe_max_absences'] = safe_max
        course['fine_max_absences'] = fine_max
        course['safe_skips'] = max(0, safe_max - cur_abs)
        course['fine_skips'] = max(0, fine_max - max(cur_abs, safe_max))
        course['shortfall_hours'] = max(0, cur_abs - safe_max)
        course['fine_amount'] = course['shortfall_hours'] * 500
        course['remaining_absences'] = course['safe_skips']
        course['max_absences'] = safe_max

        pct = float(course.get('attendance_percentage') or 0)
        tot = int(course.get('total_classes') or 0)
        if tot == 0 or (pct == 0 and cur_abs == 0):
            course['status'] = 'safe'
        elif pct >= 75.0:
            course['status'] = 'safe'
        elif pct >= 60.0:
            course['status'] = 'warning'
        else:
            course['status'] = 'danger'

    return attendance_data

def create_empty_attendance():
    """Create empty attendance data structure"""
    return {
        'courses': [],
        'overall_percentage': 0,
        'last_updated': 'Data unavailable',
        'error': 'No attendance data available'
    }

@attendance_bp.route('/attendance/refresh')
@login_required
def refresh_attendance():
    """Force refresh attendance data from CMS"""
    try:
        active_account = current_user.get_active_account()
        
        if not active_account:
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return redirect(url_for('attendance.attendance'))
        
        # Check session authentication
        account_id = active_account.account_id
            
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        scraper = scraper_manager.get_scraper(active_account)
        attendance_data = scraper.scrape_attendance(active_account.cms_username)
        
        if attendance_data and 'courses' in attendance_data and not attendance_data.get('error'):
            flash('Attendance data refreshed successfully!', 'success')
            
            # Invalidate cached detailed attendance summaries for this account
            global _summary_cache
            if '_summary_cache' in globals():
                prefix = f"{account_id}_"
                keys_to_clear = [k for k in _summary_cache.keys() if k.startswith(prefix)]
                for k in keys_to_clear:
                    _summary_cache.pop(k, None)

            # Detect changes with the new data (DO NOT update baseline automatically)
            try:
                unified = data_manager.get_unified_dashboard(active_account.account_id) or {}
                yesterday_data = unified.get('attendance_yesterday_snapshot')
                
                # If no yesterday record exists, establish baseline for first time
                if not yesterday_data:
                    payload = {
                        'attendance_yesterday_snapshot': attendance_data,
                        'last_updated': datetime.utcnow().isoformat() + 'Z',
                        'legacy_migrated': True
                    }
                    data_manager.save_unified_dashboard(active_account.account_id, payload, merge=True)
                    
                    # Keep legacy tracker in sync for fallback
                    try:
                        from app.services.attendance_change_tracker import AttendanceChangeTracker
                        change_tracker = AttendanceChangeTracker(data_manager, active_account.account_id)
                        change_tracker.save_today_as_yesterday()
                    except Exception:
                        pass
                        
                    flash('Baseline established for future change detection.', 'info')
                else:
                    # Detect changes but don't update baseline yet
                    # User must click "Mark as Seen" to update baseline
                    from app.services.attendance_comparator import AttendanceComparator
                    comparator = AttendanceComparator(data_manager, active_account.account_id)
                    changes = comparator.compare_records(attendance_data, yesterday_data)
                    
                    # Count actual attendance changes (not just new courses)
                    attendance_changes = [c for c in changes if hasattr(c, 'change_type') and c.change_type == 'attendance']
                    
                    if attendance_changes:
                        change_count = len(attendance_changes)
                        flash(f'Detected {change_count} attendance change{"s" if change_count != 1 else ""} since last check. Check the Attendance Updates section below.', 'info')
                        
                        # Send notifications for detected changes
                        send_attendance_notifications(
                            user_id=current_user.id,
                            changes=attendance_changes,
                            user=current_user
                        )
                    
            except Exception as e:
                logger.warning(f"Error processing changes after refresh: {e}")
        else:
            flash('Failed to refresh attendance data from CMS. Please check your credentials or try again later.', 'error')
            
    except Exception as e:
        logger.warning(f"Error refreshing attendance: {e}")
        flash('Error occurred while refreshing attendance data.', 'error')
    
    return redirect(url_for('attendance.attendance'))

@attendance_bp.route('/attendance/mark-as-seen')
@login_required
def mark_as_seen():
    """Mark current attendance changes as seen and update baseline"""
    try:
        active_account = current_user.get_active_account()
        
        if not active_account:
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return redirect(url_for('attendance.attendance'))
        
        # Check session authentication
        account_id = active_account.account_id
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        unified = data_manager.get_unified_dashboard(account_id) or {}
        today_data = unified.get('attendance_snapshot')
        
        if today_data:
            payload = {
                'attendance_yesterday_snapshot': today_data,
                'last_updated': datetime.utcnow().isoformat() + 'Z',
                'legacy_migrated': True
            }
            data_manager.save_unified_dashboard(account_id, payload, merge=True)
            
            # Keep legacy tracker in sync
            try:
                change_tracker = AttendanceChangeTracker(data_manager, account_id)
                change_tracker.save_today_as_yesterday()
            except Exception as e:
                logger.warning(f"Error syncing legacy tracker: {e}")
                
            flash('Marked attendance changes as seen.', 'success')
        else:
            flash('No attendance changes to mark as seen.', 'info')
            
    except Exception as e:
        logger.warning(f"Error marking changes as seen: {e}")
        flash('Error occurred while updating baseline.', 'error')
    
    return redirect(url_for('attendance.attendance'))

@attendance_bp.route('/attendance/manage-records')
@login_required
def manage_records():
    """Perform record lifecycle management operations"""
    try:
        active_account = current_user.get_active_account()
        
        if not active_account:
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return redirect(url_for('attendance.attendance'))
        
        # Check session authentication
        account_id = active_account.account_id
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        flash('Record management is automatically handled by the new unified dashboard system.', 'success')
        
    except Exception as e:
        logger.warning(f"Error in record management: {e}")
        flash('Error occurred during record management.', 'error')
    
    return redirect(url_for('attendance.attendance'))

@attendance_bp.route('/attendance/<code>/summary')
@login_required
def attendance_summary(code):
    """Fetch and display detailed attendance summary for a specific course."""
    try:
        active_account = current_user.get_active_account()
        if not active_account:
            flash('No CMS account found.', 'error')
            return redirect(url_for('attendance.attendance'))
            
        account_id = active_account.account_id
        if not is_account_authenticated(account_id):
            return redirect(url_for('attendance.attendance'))
            
        # Load attendance data
        unified = data_manager.get_unified_dashboard(account_id) or {}
        attendance_data = unified.get('attendance_snapshot') or {}
        last_updated = unified.get('last_updated') or attendance_data.get('last_updated')
        
        scraper = scraper_manager.get_scraper(active_account)
        norm_req_code = re.sub(r'[\s\-_]+', '', str(code)).lower()
        courses = attendance_data.get('courses', [])
        course = next(
            (c for c in courses 
             if c.get('code') == code or re.sub(r'[\s\-_]+', '', str(c.get('code') or '')).lower() == norm_req_code),
            None
        )

        # If attendance data or this specific course is missing from cached snapshot, fetch fresh from CMS
        if not course:
            logger.debug(f"Course {code} missing in cached snapshot for {account_id}, fetching fresh from CMS...")
            fresh_att = scraper.scrape_attendance(active_account.cms_username)
            if fresh_att and 'courses' in fresh_att:
                attendance_data = fresh_att
                courses = attendance_data.get('courses', [])
                course = next(
                    (c for c in courses 
                     if c.get('code') == code or re.sub(r'[\s\-_]+', '', str(c.get('code') or '')).lower() == norm_req_code),
                    None
                )
                unified = data_manager.get_unified_dashboard(account_id) or {}
                last_updated = unified.get('last_updated') or attendance_data.get('last_updated')

        if not course:
            flash('Course not found in your attendance records.', 'error')
            return redirect(url_for('attendance.attendance'))

        # Shallow copy course to avoid in-place mutation of cached dashboard object
        course_view = dict(course)

        # Check in-memory cache first (30-minute TTL per course summary)
        global _summary_cache
        if '_summary_cache' not in globals():
            _summary_cache = {}

        # Limit cache size to 200 entries to prevent memory leaks
        if len(_summary_cache) > 200:
            now_evict = time.time()
            expired_keys = [k for k, v in _summary_cache.items() if now_evict - v[1] > 1800]
            for k in expired_keys:
                _summary_cache.pop(k, None)
            if len(_summary_cache) > 200:
                oldest_keys = sorted(_summary_cache.keys(), key=lambda k: _summary_cache[k][1])[:50]
                for k in oldest_keys:
                    _summary_cache.pop(k, None)

        normalized_code_key = re.sub(r'[\s\-_]+', '', str(course.get('code') or code)).lower()
        cache_key = f"{account_id}_{normalized_code_key}"
        now_ts = time.time()
        force_refresh = request.args.get('force', '').lower() in ['1', 'true']
        if not force_refresh and cache_key in _summary_cache:
            cached_summary, cached_time = _summary_cache[cache_key]
            if now_ts - cached_time < 1800:
                course_view['summary'] = cached_summary
                return render_template('attendance_summary.html', course=course_view, last_updated=last_updated)

        detail_url = course.get('detail_url')

        # Fetch the summary using atomic edge macro priority
        summary = scraper.scrape_attendance_summary(
            detail_url=detail_url,
            username=active_account.cms_username,
            password=active_account.cms_password,
            course_code=code
        )

        if summary is not None and summary not in ["STALE_TOKEN", "EXPIRED"]:
            _summary_cache[cache_key] = (summary, now_ts)
            course_view['summary'] = summary

            # Detect if detailed summary has updated attendance numbers for this course
            try:
                if isinstance(summary, list) and len(summary) > 0:
                    valid_rows = [
                        r for r in summary 
                        if r.get('date') and not ('%' in str(r.get('present_hours', '')) or ':' in str(r.get('present_hours', '')))
                    ]
                    
                    if valid_rows:
                        total_hours_sum = 0.0
                        present_hours_sum = 0.0
                        absent_hours_sum = 0.0

                        for row in valid_rows:
                            try:
                                t_str = str(row.get('total_hours') or '0').split(':')[0].strip()
                                t_val = float(t_str)
                            except (ValueError, TypeError):
                                t_val = 0.0
                            
                            try:
                                p_str = str(row.get('present_hours') or '0').split(':')[0].strip()
                                p_val = float(p_str)
                            except (ValueError, TypeError):
                                p_val = 0.0

                            try:
                                a_str = str(row.get('absent_hours') or '0').split(':')[0].strip()
                                a_val = float(a_str)
                            except (ValueError, TypeError):
                                a_val = 0.0

                            total_hours_sum += t_val
                            present_hours_sum += p_val
                            absent_hours_sum += a_val

                        new_total = int(round(total_hours_sum))
                        new_att = int(round(present_hours_sum))
                        new_abs = int(round(absent_hours_sum))

                        prev_total = int(course.get('total_classes') or 0)
                        prev_abs = int(course.get('absences') or 0)
                        prev_att = int(course.get('attended_classes') or 0)

                        if new_total != prev_total or new_abs != prev_abs or new_att != prev_att:
                            logger.info(
                                f"Attendance update for {code}: classes {prev_total}->{new_total}, "
                                f"attended {prev_att}->{new_att}, absences {prev_abs}->{new_abs}"
                            )
                            course['total_classes'] = new_total
                            course['attended_classes'] = new_att
                            course['absences'] = new_abs
                            if new_total > 0:
                                course['attendance_percentage'] = round((new_att / new_total) * 100.0, 2)

                        cur_abs = int(course.get('absences') or 0)
                        try:
                            credit_hrs = int(float(str(course.get('credit_hours') or 3).strip()))
                        except:
                            credit_hrs = 3

                        c_name_lower = (course.get('name') or '').lower()
                        if 'lab' in c_name_lower:
                            safe_max = 12
                            fine_max = 19
                        elif credit_hrs == 0 or 'understanding quran' in c_name_lower or 'quran' in c_name_lower:
                            safe_max = 1
                            fine_max = 2
                        else:
                            safe_max = int(credit_hrs * 16 * 0.25)
                            fine_max = int(credit_hrs * 16 * 0.40)

                        course['safe_max_absences'] = safe_max
                        course['fine_max_absences'] = fine_max
                        course['max_absences'] = safe_max
                        course['safe_skips'] = max(0, safe_max - cur_abs)
                        course['fine_skips'] = max(0, fine_max - max(cur_abs, safe_max))
                        course['shortfall_hours'] = max(0, cur_abs - safe_max)
                        course['fine_amount'] = course['shortfall_hours'] * 500
                        course['remaining_absences'] = course['safe_skips']

                        pct = course.get('attendance_percentage', 0)
                        total_classes_val = int(course.get('total_classes') or 0)
                        if total_classes_val == 0 or (pct == 0 and cur_abs == 0):
                            course['status'] = 'safe'
                        elif pct >= 75:
                            course['status'] = 'safe'
                        elif pct >= 60:
                            course['status'] = 'warning'
                        else:
                            course['status'] = 'danger'

                        course_view.update(course)

                        # Recalculate overall percentage across all courses
                        all_courses = attendance_data.get('courses', [])
                        valid_pcts = [c.get('attendance_percentage', 0) for c in all_courses if int(c.get('total_classes', 0) or 0) > 0]
                        if valid_pcts:
                            attendance_data['overall_percentage'] = round(sum(valid_pcts) / len(valid_pcts), 2)

                        now_iso = datetime.utcnow().isoformat() + 'Z'
                        attendance_data['last_updated'] = now_iso
                        last_updated = now_iso

                        # Single atomic merge write to Firestore user_dashboards (0 extra reads, 1 write)
                        data_manager.save_unified_dashboard(
                            account_id,
                            {
                                'attendance_snapshot': attendance_data,
                                'last_updated': now_iso
                            },
                            merge=True
                        )
            except Exception as e_comp:
                logger.warning(f"Error checking summary attendance changes: {e_comp}")

            return render_template('attendance_summary.html', course=course_view, last_updated=last_updated)
        else:
            flash('Failed to fetch detailed summary. Please try again later.', 'error')
            return redirect(url_for('attendance.attendance'))
        
    except Exception as e:
        logger.warning(f"Error fetching attendance summary: {e}")
        flash('An error occurred while fetching the summary.', 'error')
        return redirect(url_for('attendance.attendance'))
