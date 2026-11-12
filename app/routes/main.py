import logging
logger = logging.getLogger(__name__)

from flask import Blueprint, render_template, redirect, url_for, flash, send_from_directory, Response, session, current_app, request, jsonify
from flask_login import login_required, current_user
from app.utils.data_manager import data_manager
from app.utils.session_auth import is_account_authenticated
import json
import os
import yaml
from datetime import datetime
from dateutil.parser import parse as parse_date

main_bp = Blueprint('main', __name__)

@main_bp.route('/robots.txt')
@main_bp.route('/sitemap.xml')
@main_bp.route('/llms.txt')
def static_from_root():
    return send_from_directory(current_app.static_folder, request.path[1:])


def load_user_data():
    """Load all scraped data for the active account using new account system"""
    user_data = {
        'profile': None,
        'timetable': None,
        'attendance': None,
        'community_service': None,
        'gpa': None,
        'assignments_snapshot': None,
        'scraped_at': None,
        'today_schedule': [],
        'attendance_overview': []
    }
    
    try:
        # Get active account
        active_account = current_user.get_active_account()
        
        if not active_account:
            return user_data
        
        # Load each data type using the new data manager
        # Load standard data types
        data_types = ['profile', 'timetable', 'community_service', 'gpa']
        
        for data_type in data_types:
            try:
                data = data_manager.load_account_data(
                    account_id=active_account.account_id,
                    data_type=data_type
                )
                user_data[data_type] = data
            except Exception as e:
                logger.warning(f"Error loading {data_type} data: {e}")
                
        # Load unified snapshot data types
        try:
            unified = data_manager.get_unified_dashboard(active_account.account_id) or {}
            user_data['attendance'] = unified.get('attendance_snapshot')
            user_data['assignments_snapshot'] = unified.get('assignments_snapshot')
        except Exception as e:
            logger.warning(f"Error loading unified data: {e}")
        
        # Extract today's schedule from timetable data
        if user_data['timetable'] and 'schedule' in user_data['timetable']:
            today_name = datetime.now().strftime('%A')  # Gets day name like 'Monday'
            today_schedule_data = user_data['timetable']['schedule'].get(today_name, {})
            
            # Convert to list format expected by template
            today_schedule = []
            for time_slot, session_info in today_schedule_data.items():
                if session_info:  # Only add if there's actual class data
                    today_schedule.append({
                        'time': time_slot,
                        'course_name': session_info.get('name', 'N/A'),
                        'course_code': session_info.get('code', 'N/A'),
                        'instructor': session_info.get('instructor', 'N/A'),
                        'room': session_info.get('room', 'N/A'),
                        'building': session_info.get('building', 'N/A')
                    })
            
            # Sort by time (parse start time for sorting)
            def parse_time(time_str):
                try:
                    start_time = time_str.split(' - ')[0]
                    return datetime.strptime(start_time, '%I:%M %p').time()
                except:
                    return datetime.min.time()
            
            today_schedule.sort(key=lambda x: parse_time(x['time']))
            user_data['today_schedule'] = today_schedule
        
        # Build attendance overview for dashboard
        if user_data['attendance'] and 'courses' in user_data['attendance']:
            attendance_overview = []
            for course in user_data['attendance']['courses']:
                attendance_pct = course.get('attendance_percentage', 0)
                
                # Determine CSS class based on attendance percentage (75% min safe, 60% warning policy)
                if attendance_pct >= 75:
                    attendance_class = 'text-success'
                elif attendance_pct >= 60:
                    attendance_class = 'text-warning'
                else:
                    attendance_class = 'text-error'
                
                attendance_overview.append({
                    'name': course.get('name', course.get('code', 'N/A')),
                    'code': course.get('code', 'N/A'),
                    'attendance_percentage': attendance_pct,
                    'attendance_class': attendance_class
                })
            
            user_data['attendance_overview'] = attendance_overview
    
    except Exception as e:
        logger.warning(f"Error loading user data: {e}")
    
    return user_data

def calculate_dashboard_stats(user_data):
    """Calculate dashboard statistics from user data"""
    stats = {
        'pending_assignments': 0,
        'average_attendance': 0,
        'current_gpa': '?',
        'csp_hours': 0,
        'attendance_warnings': 0,
        'low_attendance_courses': []
    }
    
    # Calculate from assignments data
    if user_data.get('assignments_snapshot'):
        assignments_data = user_data['assignments_snapshot']
        if 'pending' in assignments_data:
            stats['pending_assignments'] = len(assignments_data['pending'])
    
    # Calculate from attendance data
    if user_data.get('attendance'):
        attendance_data = user_data['attendance']
        if 'courses' in attendance_data:
            courses = attendance_data['courses']
            
            total_attendance = 0
            warning_count = 0
            low_attendance = []
            
            for course in courses:
                attendance_pct = course.get('attendance_percentage', 0)
                total_attendance += attendance_pct
                
                # Check for attendance warnings (below 75% requires attention)
                if attendance_pct < 75:
                    warning_count += 1
                    low_attendance.append({
                        'code': course.get('code', 'N/A'),
                        'name': course.get('name', 'N/A'),
                        'attendance': attendance_pct
                    })
            
            if len(courses) > 0:
                stats['average_attendance'] = round(total_attendance / len(courses), 1)
            
            stats['attendance_warnings'] = warning_count
            stats['low_attendance_courses'] = low_attendance
    
    # Calculate from GPA data
    if user_data.get('gpa') and user_data['gpa'].get('current_cgpa'):
        stats['current_gpa'] = user_data['gpa'].get('current_cgpa', '?')
    
    # Calculate from community service data
    if user_data.get('community_service'):
        cs_data = user_data['community_service']
        stats['csp_hours'] = int(cs_data.get('completed_hours', 0))
    
    return stats

@main_bp.route('/')
def index():
    return render_template('landing.html')

@main_bp.route('/login')
def login_redirect():
    return redirect(url_for('auth.login'), code=301)


@main_bp.route('/dashboard')
@login_required
def dashboard():
    # Force reload user from database to ensure fresh data
    from app.models.account import User
    fresh_user = User.get(current_user.id)
    
    if not fresh_user:
        flash('Error loading user data', 'error')
        return redirect(url_for('auth.logout'))
    
    # SINGLE ACCOUNT MODE: Check if user has CMS account
    if not fresh_user.cms_account:
        flash('Welcome! Please log in with your CMS credentials to get started.', 'info')
        return render_template('dashboard.html', 
                             user=fresh_user, 
                             user_data=None, 
                             stats=None,
                             gpa_color='var(--text-secondary)',
                             no_accounts=True)
    
    # MULTI-ACCOUNT MODE (disabled)
    # # Check if user has any CMS accounts
    # if not current_user.cms_accounts:
    #     flash('Welcome! Please add your CMS account to get started.', 'info')
    #     return render_template('dashboard.html', 
    #                          user=current_user, 
    #                          user_data=None, 
    #                          stats=None,
    #                          gpa_color='var(--text-secondary)',
    #                          no_accounts=True)
    # 
    # # Check if there's an active account
    # account_id = session.get('active_account_id')
    # if not account_id:
    #     flash('No active account', 'error')
    #     return redirect(url_for('auth.login'))
    # 
    # # Check if account is authenticated in this session
    # if not is_account_authenticated(account_id):
    #     flash('Account not authenticated in this session', 'error')
    #     return redirect(url_for('auth.login'))
    
    # Load user's scraped data
    user_data = load_user_data()
    
    # Check sync status: if syncing or never synced, redirect to /sync
    from app.utils.enhanced_scraper import scraper_manager
    sync_status = scraper_manager.get_sync_status(fresh_user.cms_account.account_id)
    if sync_status.get('is_syncing'):
        return redirect(url_for('main.sync_screen'))
    if not user_data.get('profile') and not sync_status.get('completed_steps') and not sync_status.get('failed_steps'):
        return redirect(url_for('main.sync_screen'))
    
    # Calculate dashboard statistics
    stats = calculate_dashboard_stats(user_data)

    # Determine GPA color
    gpa_color = 'var(--text-secondary)' # Default color for '?'
    if stats['current_gpa'] != '?':
        try:
            gpa = float(stats['current_gpa'])
            if gpa >= 3.5:
                gpa_color = 'var(--success-color)'
            elif gpa >= 3.0:
                gpa_color = 'var(--warning-color)'
            else:
                gpa_color = 'var(--error-color)'
        except (ValueError, TypeError):
            pass

    # Load upcoming events from YAML file
    upcoming_events = []
    try:
        events_path = os.path.join(current_app.root_path, '..', 'events.yaml')
        if os.path.exists(events_path):
            with open(events_path, 'r', encoding='utf-8') as f:
                events_data = yaml.safe_load(f)
                if isinstance(events_data, dict) and isinstance(events_data.get('events'), list):
                    today = datetime.today().date()
                    
                    for event in events_data['events']:
                        if not isinstance(event, dict):
                            continue
                            
                        # Normalize title back to dict
                        event['title'] = str(event.get('title') or 'Unknown Event').strip() or 'Unknown Event'

                        date_str = str(event.get('date') or '').strip()
                        if not date_str:
                            event['date'] = 'TBD'
                            event['calendar_month'] = '?'
                            event['calendar_day'] = '?'
                            upcoming_events.append(event)
                            continue
                            
                        # Normalize date back to dict to prevent Jinja errors
                        event['date'] = date_str
                            
                        # Try parsing full string first (handles ISO dates like '2026-10-12')
                        # If it fails, fallback to splitting by hyphen (handles ranges like 'Mar 30-Apr 7')
                        try:
                            try:
                                parsed_date = parse_date(date_str, fuzzy=True).date()
                            except Exception:
                                part = date_str.split('-')[-1].strip()
                                parsed_date = parse_date(part, fuzzy=True).date()
                                
                            if parsed_date >= today:
                                event['calendar_month'] = parsed_date.strftime('%b').upper()
                                event['calendar_day'] = str(parsed_date.day)
                                upcoming_events.append(event)
                        except Exception as e:
                            # If we can't parse it, better to show it than hide it
                            current_app.logger.warning(f"Could not parse date '{date_str}': {e}")
                            event['calendar_month'] = '?'
                            event['calendar_day'] = '?'
                            upcoming_events.append(event)
    except Exception as e:
        current_app.logger.error(f"Failed to load events.yaml: {e}")

    cms_acc = fresh_user.cms_account
    is_ipp = bool(cms_acc and (cms_acc.institute_id == '5' or (cms_acc.cms_username and cms_acc.cms_username.startswith('05-'))))

    return render_template('dashboard.html', 
                         user=fresh_user, 
                         user_data=user_data, 
                         stats=stats,
                         gpa_color=gpa_color,
                         upcoming_events=upcoming_events,
                         is_ipp=is_ipp,
                         no_accounts=False)

@main_bp.route('/gpa-results')
@login_required
def gpa_results():
    """Display semester-wise academic performance"""
    # Load user's scraped data using new account system
    user_data = load_user_data()
    
    # Calculate additional GPA statistics
    gpa_stats = {
        'total_semesters': 0,
        'total_courses': 0,
        'total_credit_hours': 0,
        'current_cgpa': 0.0,
        'highest_sgpa': 0.0,
        'lowest_sgpa': 0.0
    }
    
    if user_data['gpa']:
        gpa_data = user_data['gpa']
        
        # Semester statistics
        semesters = gpa_data.get('semesters', [])
        if semesters:
            gpa_stats['total_semesters'] = len(semesters)
            sgpas = [s.get('sgpa', 0.0) for s in semesters if s.get('sgpa', 0.0) > 0]
            if sgpas:
                gpa_stats['highest_sgpa'] = max(sgpas)
                gpa_stats['lowest_sgpa'] = min(sgpas)
        
        # Course and credit statistics
        courses = gpa_data.get('current_semester_courses', [])
        gpa_stats['total_courses'] = len(courses)
        gpa_stats['total_credit_hours'] = gpa_data.get('total_credit_hours', 0)
        gpa_stats['current_cgpa'] = gpa_data.get('current_cgpa', 0.0)
    
    return render_template('gpa_results.html', 
                         user=current_user, 
                         user_data=user_data, 
                         gpa_stats=gpa_stats)

@main_bp.route('/refresh-all-data')
@login_required
def refresh_all_cms_data():
    """Trigger background refresh of all CMS data with live pipeline tracking"""
    from app.utils.enhanced_scraper import scraper_manager
    
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('format') == 'json'
    
    try:
        from app.models.account import User
        fresh_user = User.get(current_user.id)
        active_account = fresh_user.get_active_account() if fresh_user else getattr(current_user, 'cms_account', None)
        
        if not active_account:
            if is_ajax:
                return jsonify({'error': 'No CMS account found'}), 400
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return redirect(url_for('main.dashboard'))
        
        # Check if already syncing
        current_status = scraper_manager.get_sync_status(active_account.account_id)
        if current_status and current_status.get('is_syncing'):
            if is_ajax:
                return jsonify({'success': True, 'already_syncing': True, 'status': current_status})
            return redirect(url_for('main.dashboard'))
            
        # Trigger async scrape with live stage tracking
        scraper_manager.scrape_account_data(
            active_account,
            data_types=['profile', 'timetable', 'attendance', 'community_service', 'gpa', 'assignments']
        )
        
        if is_ajax:
            return jsonify({'success': True, 'is_syncing': True, 'message': 'Refresh started'})
            
    except Exception as e:
        logger.warning(f"Error in refresh_all_cms_data: {e}")
        if is_ajax:
            return jsonify({'error': str(e)}), 500
        flash('An error occurred while starting data refresh. Please try again.', 'error')
        
    return redirect(url_for('main.dashboard'))


@main_bp.route('/sync')
@login_required
def sync_screen():
    from app.models.account import User
    fresh_user = User.get(current_user.id)
    if not fresh_user or not fresh_user.cms_account:
        return redirect(url_for('auth.login'))
    
    account_id = fresh_user.cms_account.account_id
    from app.utils.enhanced_scraper import scraper_manager
    sync_status = scraper_manager.get_sync_status(account_id)
    profile_data = data_manager.load_account_data(account_id, 'profile')
    
    # Tab Abandonment Guard: if sync done and profile exists, redirect to dashboard
    if not sync_status.get('is_syncing') and profile_data and not profile_data.get('sync_failed'):
        return redirect(url_for('main.dashboard'))
        
    # Auto-recovery: if user lands here with no active sync and never started, trigger sync
    if not sync_status.get('is_syncing') and not sync_status.get('completed_steps') and not sync_status.get('failed_steps'):
        try:
            scraper_manager.scrape_account_data(
                fresh_user.cms_account,
                data_types=['profile', 'timetable', 'attendance', 'community_service', 'gpa', 'assignments']
            )
            sync_status = scraper_manager.get_sync_status(account_id)
        except Exception as e:
            logger.warning(f"Failed to auto-start sync for {account_id}: {e}")
        
    cms_acc = fresh_user.cms_account
    is_ipp = bool(cms_acc and (cms_acc.institute_id == '5' or (cms_acc.cms_username and cms_acc.cms_username.startswith('05-'))))

    return render_template(
        'sync.html', 
        user=fresh_user, 
        username=fresh_user.cms_account.cms_username,
        initial_status=sync_status,
        is_ipp=is_ipp,
        hide_chrome=True
    )


@main_bp.route('/api/sync/status')
@login_required
def api_sync_status():
    from app.models.account import User
    fresh_user = User.get(current_user.id)
    if not fresh_user or not fresh_user.cms_account:
        return jsonify({'error': 'No CMS account found', 'is_syncing': False}), 400
        
    account_id = fresh_user.cms_account.account_id
    from app.utils.enhanced_scraper import scraper_manager
    status = scraper_manager.get_sync_status(account_id)
    return jsonify(status)


@main_bp.route('/api/sync/survey/resolve', methods=['POST'])
@login_required
def api_sync_survey_resolve():
    """Handle user response to CMS survey detection during sync (autofill, resume, or skip)."""
    from app.models.account import User
    fresh_user = User.get(current_user.id)
    if not fresh_user or not fresh_user.cms_account:
        return jsonify({'error': 'No CMS account found'}), 400

    account = fresh_user.cms_account
    account_id = account.account_id

    req_data = request.get_json(silent=True) or {}
    action = req_data.get('action', '').strip().lower()

    from app.utils.enhanced_scraper import scraper_manager

    if action == 'autofill_start':
        from app.routes.lms import auto_fill_surveys
        return auto_fill_surveys()

    elif action == 'resume':
        task_id = req_data.get('task_id')
        scraper_manager.resolve_survey_for_account(account_id, action='resume', task_id=task_id)
        return jsonify({'success': True, 'action': 'resumed', 'message': 'Sync resumed with fresh survey credentials'})

    elif action == 'skip':
        scraper_manager.resolve_survey_for_account(account_id, action='skip')
        return jsonify({'success': True, 'action': 'skipped', 'message': 'Survey step skipped'})

    return jsonify({'error': 'Invalid action. Must be autofill_start, resume, or skip.'}), 400

