import logging
logger = logging.getLogger(__name__)

from flask import Blueprint, render_template, session, flash, redirect, url_for, jsonify
from flask_login import login_required, current_user
from app.utils.enhanced_scraper import scraper_manager
from app.utils.data_manager import data_manager
from app.utils.session_auth import is_account_authenticated
import requests
from datetime import datetime, timedelta

timetable_bp = Blueprint('timetable', __name__)

@timetable_bp.route('/timetable')
@login_required
def timetable():
    # Check session authentication
    active_account = current_user.get_active_account()
    if active_account:
        account_id = active_account.account_id
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
    
    # Get timetable data from CMS
    timetable_data = scrape_timetable()
    return render_template('timetable.html', timetable=timetable_data)

@timetable_bp.route('/timetable/refresh')
@login_required
def refresh_timetable():
    """Force refresh timetable data from CMS"""
    try:
        active_account = current_user.get_active_account()
        
        if not active_account:
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return redirect(url_for('timetable.timetable'))
        
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
        timetable_data = scraper.scrape_timetable(active_account.cms_username)
        if timetable_data and not timetable_data.get('error') and timetable_data.get('time_slots'):
            flash('Timetable data refreshed successfully!', 'success')
        elif timetable_data and not timetable_data.get('error'):
            flash('Timetable data refreshed, but no scheduled classes found.', 'info')
        else:
            flash('Failed to refresh timetable data from CMS.', 'error')
            
    except Exception as e:
        logger.warning(f"Error refreshing timetable: {e}")
        flash('Error occurred while refreshing timetable data.', 'error')
    
    return redirect(url_for('timetable.timetable'))

def scrape_timetable():
    """
    Get timetable data from CMS scraper or use cached data with new account system.
    """
    try:
        # Get active CMS account
        active_account = current_user.get_active_account()
        
        if not active_account:
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return create_empty_timetable('No CMS account')
        
        # Try to load existing data first
        timetable_data = data_manager.load_account_data(
            account_id=active_account.account_id,
            data_type='timetable'
        )
        
        logger.debug(f"Loaded cached timetable data for {active_account.cms_username}: {timetable_data is not None}")
        
        # Validate that the cached data has meaningful content
        if timetable_data and timetable_data.get('time_slots') and len(timetable_data.get('time_slots', [])) > 0:
            logger.debug("Found valid cached timetable data")
            # Validate schedule data
            schedule = timetable_data.get('schedule', {})
            has_classes = False
            for day in timetable_data['days']:
                if day in schedule:
                    for time_slot in schedule[day]:
                        if schedule[day][time_slot] is not None:
                            has_classes = True
                            break
                if has_classes:
                    break
            
            if has_classes:
                return timetable_data
            else:
                logger.debug("Cached timetable data has no classes scheduled")
        else:
            logger.debug("Cached timetable data is empty or invalid, will try to re-scrape")
        
        # If no valid cached data, try to scrape fresh data
        logger.debug(f"Attempting to scrape fresh timetable data for {active_account.cms_username}")
        try:
            # Get scraper for this account
            scraper = scraper_manager.get_scraper(active_account)
            fresh_timetable_data = scraper.scrape_timetable(active_account.cms_username)
            logger.debug(f"Scraped fresh timetable data: {fresh_timetable_data is not None}")
            
            if fresh_timetable_data and fresh_timetable_data.get('time_slots') and not fresh_timetable_data.get('error'):
                return fresh_timetable_data
            else:
                flash('No timetable data found on CMS. Your timetable may not be published yet.', 'warning')
        except Exception as scrape_error:
            logger.warning(f"Error during fresh scraping: {scrape_error}")
            flash('Error occurred while fetching latest timetable from CMS.', 'error')
        
        # Return empty timetable with appropriate message
        return create_empty_timetable('No timetable data available')
    
    except Exception as e:
        logger.warning(f"Error getting timetable data: {e}")
        flash('Unexpected error loading timetable data. Please try again later.', 'error')
        return create_empty_timetable(f'Error: {str(e)}')

def create_empty_timetable(error_message):
    """Create an empty timetable structure with error information"""
    return {
        'days': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
        'time_slots': [],
        'schedule': {day: {} for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']},
        'last_updated': 'Data unavailable',
        'error': error_message,
        'is_empty': True
    }

