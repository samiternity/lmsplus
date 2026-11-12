import logging
logger = logging.getLogger(__name__)

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, session
from flask_login import login_required, current_user
from app.utils.enhanced_scraper import scraper_manager
from app.utils.data_manager import data_manager
from app.utils.session_auth import is_account_authenticated

gpa_bp = Blueprint('gpa', __name__)

@gpa_bp.route('/gpa-calculator')
def gpa_calculator():
    return render_template('gpa_calculator.html')

@gpa_bp.route('/calculate-gpa', methods=['POST'])
def calculate_gpa():
    """Calculate GPA based on courses and grades"""
    data = request.get_json()
    courses = data.get('courses', [])
    
    if not courses:
        return jsonify({'error': 'No courses provided'}), 400
    
    # Grade point mapping
    grade_points = {
        'A': 4.0, 'A-': 3.7,
        'B+': 3.3, 'B': 3.0, 'B-': 2.7,
        'C+': 2.3, 'C': 2.0, 'C-': 1.7,
        'D+': 1.3, 'D': 1.0, 'F': 0.0
    }
    
    total_points = 0
    total_credits = 0
    
    for course in courses:
        grade = course.get('grade', '').upper()
        credit_hours = float(course.get('credit_hours', 0))
        
        if grade in grade_points and credit_hours > 0:
            points = grade_points[grade] * credit_hours
            total_points += points
            total_credits += credit_hours
    
    if total_credits == 0:
        return jsonify({'error': 'No valid courses with credit hours'}), 400
    
    gpa = total_points / total_credits
    
    return jsonify({
        'gpa': round(gpa, 2),
        'total_credits': total_credits,
        'total_points': round(total_points, 2),
        'grade_interpretation': get_grade_interpretation(gpa)
    })

def get_grade_interpretation(gpa):
    """Get grade interpretation based on GPA"""
    if gpa >= 3.8:
        return {'level': 'Excellent', 'description': 'Outstanding academic performance'}
    elif gpa >= 3.5:
        return {'level': 'Very Good', 'description': 'Above average performance'}
    elif gpa >= 3.0:
        return {'level': 'Good', 'description': 'Satisfactory performance'}
    elif gpa >= 2.5:
        return {'level': 'Fair', 'description': 'Below average performance'}
    elif gpa >= 2.0:
        return {'level': 'Poor', 'description': 'Minimum passing performance'}
    else:
        return {'level': 'Fail', 'description': 'Failing performance'}

@gpa_bp.route('/provisional-results')
@login_required
def provisional_results():
    # Try to get GPA data using new account system
    gpa_data = None
    
    try:
        active_account = current_user.get_active_account()
        
        if not active_account:
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return render_template('provisional_results.html', gpa_data=create_empty_gpa_data())
        
        # Check session authentication
        account_id = active_account.account_id
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        # Try to load cached data first
        gpa_data = data_manager.load_account_data(
            account_id=active_account.account_id,
            data_type='gpa'
        )
        
        if gpa_data and gpa_data.get('semesters'):
            has_real_data = any(
                semester.get('courses') and len(semester['courses']) > 0
                for semester in gpa_data['semesters']
            )
            
            if has_real_data:
                return render_template('provisional_results.html', gpa_data=gpa_data)
            else:
                gpa_data = None
        
        # If no cached data, try to scrape fresh data
        if not gpa_data:
            scraper = scraper_manager.get_scraper(active_account)
            gpa_data = scraper.scrape_gpa_data(active_account.cms_username)
            
            if gpa_data and gpa_data.get('semesters'):
                has_real_data = any(
                    semester.get('courses') and len(semester['courses']) > 0
                    for semester in gpa_data['semesters']
                )
                
                if has_real_data:
                    return render_template('provisional_results.html', gpa_data=gpa_data)
        
        if not gpa_data:
            flash('Unable to load GPA data from CMS. Please try refreshing the page.', 'warning')
            gpa_data = create_empty_gpa_data()
            
    except Exception as e:
        logger.warning(f"Error loading GPA data: {e}")
        flash('Error loading GPA data. Please try again later.', 'error')
        gpa_data = create_empty_gpa_data()
    
    return render_template('provisional_results.html', gpa_data=gpa_data)

def create_empty_gpa_data():
    """Create empty GPA data structure"""
    return {
        'semesters': [],
        'cgpa': 0.0,
        'total_credit_hours': 0,
        'last_updated': 'Data unavailable',
        'error': 'No GPA data available'
    }

@gpa_bp.route('/provisional-results/refresh')
@login_required
def refresh_provisional_results():
    """Force refresh GPA/provisional results data from CMS"""
    try:
        active_account = current_user.get_active_account()
        
        if not active_account:
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return redirect(url_for('gpa.provisional_results'))
        
        # Check session authentication
        account_id = active_account.account_id
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        # Get scraper for this account
        scraper = scraper_manager.get_scraper(active_account)
        gpa_data = scraper.scrape_gpa_data(active_account.cms_username)
        if gpa_data and not gpa_data.get('error'):
            if gpa_data.get('semesters'):
                flash('Provisional results refreshed successfully!', 'success')
            else:
                flash('No provisional results found on CMS.', 'warning')
        else:
            flash('Failed to connect to CMS. Please check your credentials.', 'error')
            
    except Exception as e:
        logger.warning(f"Error refreshing provisional results: {e}")
        flash('Error occurred while refreshing provisional results.', 'error')
    
    return redirect(url_for('gpa.provisional_results'))
