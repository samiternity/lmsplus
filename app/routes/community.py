import logging
logger = logging.getLogger(__name__)

from flask import Blueprint, render_template, flash, redirect, url_for, session, jsonify
from flask_login import login_required, current_user
from app.utils.enhanced_scraper import scraper_manager
from app.utils.data_manager import data_manager
from app.utils.session_auth import is_account_authenticated
from datetime import datetime, timedelta
import json
import os

community_bp = Blueprint('community', __name__)

@community_bp.route('/community-service')
@login_required
def community_service():
    # Try to get community service data using new account system
    community_data = None
    
    try:
        active_account = current_user.get_active_account()
        
        if not active_account:
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return render_template('community_service.html', csp_data=create_empty_community_data())
        
        # Check session authentication
        account_id = active_account.account_id
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        # Check if IPP campus
        institute_id = str(getattr(active_account, 'institute_id', '') or '')
        cms_user = str(getattr(active_account, 'cms_username', '') or '')
        is_ipp = institute_id == '5' or cms_user.startswith('05-')
        if is_ipp:
            empty_ipp_data = {
                'not_applicable': True,
                'completed_hours': 0,
                'required_hours': 0,
                'remaining_hours': 0,
                'completion_percentage': 0,
                'completed_activities': [],
                'total_hours': 0
            }
            return render_template('community_service.html', csp_data=empty_ipp_data, is_ipp=True)
        
        # Try to load cached data first
        community_data = data_manager.load_account_data(
            account_id=active_account.account_id,
            data_type='community_service'
        )
        
        logger.debug(f"[COMMUNITY] Loaded community data: {community_data is not None}")
        if community_data:
            logger.debug(f"[COMMUNITY] Data keys: {list(community_data.keys())}")
            logger.debug(f"[COMMUNITY] Activities count: {len(community_data.get('activities', []))}")
            logger.debug(f"[COMMUNITY] Opportunities count: {len(community_data.get('opportunities', []))}")
            
            # Debug: Show sample activity structure
            activities = community_data.get('activities', [])
            opportunities = community_data.get('opportunities', [])
            
            if activities and len(activities) > 0:
                logger.debug(f"[COMMUNITY] Sample activity: {activities[0]}")
            elif opportunities and len(opportunities) > 0:
                logger.debug(f"[COMMUNITY] Sample opportunity: {opportunities[0]}")
        
        if community_data and (community_data.get('activities') or community_data.get('opportunities')):
            # Check for real data in opportunities first (these are the completed activities)
            opportunities = community_data.get('opportunities', [])
            activities = community_data.get('activities', [])
            
            has_real_data = False
            
            # Check opportunities for real data
            if opportunities:
                has_real_data = any(
                    opportunity.get('hours', 0) > 0 or 
                    opportunity.get('activity') or 
                    opportunity.get('status', '').lower() not in ['n/a', '', 'none']
                    for opportunity in opportunities
                )
                logger.debug(f"[COMMUNITY] Opportunities have real data: {has_real_data}")
            
            # If no real data in opportunities, check activities
            if not has_real_data and activities:
                has_real_data = any(
                    activity.get('hours', 0) > 0 or activity.get('status') != 'N/A'
                    for activity in activities
                )
                logger.debug(f"[COMMUNITY] Activities have real data: {has_real_data}")
            
            if has_real_data:
                # Normalize the data structure for the template
                logger.debug(f"[COMMUNITY] Normalizing data for template")
                normalized_data = normalize_community_data(community_data)
                logger.debug(f"[COMMUNITY] Normalized data has {len(normalized_data.get('completed_activities', []))} activities")
                return render_template('community_service.html', csp_data=normalized_data)
            else:
                logger.debug(f"[COMMUNITY] No real data found in loaded community data")
                community_data = None
        
        # If no cached data, try to scrape fresh data
        if not community_data:
            scraper = scraper_manager.get_scraper(active_account)
            community_data = scraper.scrape_community_service(active_account.cms_username)
            
            if community_data and (community_data.get('activities') or community_data.get('opportunities')):
                # Check for real data in opportunities or activities
                opportunities = community_data.get('opportunities', [])
                activities = community_data.get('activities', [])
                
                has_real_data = False
                
                # Check opportunities first
                if opportunities:
                    has_real_data = any(
                        opportunity.get('hours', 0) > 0 or 
                        opportunity.get('activity') or 
                        opportunity.get('status', '').lower() not in ['n/a', '', 'none']
                        for opportunity in opportunities
                    )
                
                # Check activities if no opportunities data
                if not has_real_data and activities:
                    has_real_data = any(
                        activity.get('hours', 0) > 0 or activity.get('status') != 'N/A'
                        for activity in activities
                    )
                
                if has_real_data:
                    # Normalize the data structure for the template
                    normalized_data = normalize_community_data(community_data)
                    return render_template('community_service.html', csp_data=normalized_data)
        
        if not community_data:
            flash('Unable to load community service data from CMS. Please try refreshing the page.', 'warning')
            community_data = create_empty_community_data()
            
    except Exception as e:
        logger.warning(f"Error loading community service data: {e}")
        flash('Error loading community service data. Please try again later.', 'error')
        community_data = create_empty_community_data()
    
    return render_template('community_service.html', csp_data=community_data)

@community_bp.route('/community-service/refresh')
@login_required
def refresh_community_service():
    """Force refresh community service data from CMS"""
    try:
        active_account = current_user.get_active_account()
        
        if not active_account:
            flash('No CMS account found. Please add a CMS account first.', 'error')
            return redirect(url_for('community.community_service'))
        
        # Check session authentication
        account_id = active_account.account_id
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        # Check if IPP campus
        institute_id = str(getattr(active_account, 'institute_id', '') or '')
        cms_user = str(getattr(active_account, 'cms_username', '') or '')
        is_ipp = institute_id == '5' or cms_user.startswith('05-')
        if is_ipp:
            flash('Community service is not applicable for IPP campus.', 'info')
            return redirect(url_for('community.community_service'))
        
        scraper = scraper_manager.get_scraper(active_account)
        community_data = scraper.scrape_community_service(active_account.cms_username)
        if community_data and not community_data.get('error'):
            if community_data.get('activities') or community_data.get('opportunities'):
                flash('Community service data refreshed successfully!', 'success')
            else:
                flash('Community service data refreshed, but no activities found.', 'warning')
        else:
            flash('Failed to connect to CMS. Please check your credentials.', 'error')
            
    except Exception as e:
        logger.warning(f"Error refreshing community service: {e}")
        flash('Error occurred while refreshing community service data.', 'error')
    
    return redirect(url_for('community.community_service'))

def normalize_community_data(raw_data):
    """Normalize community service data to match template expectations"""
    try:
        logger.debug(f"[NORMALIZE] Starting data normalization")
        if raw_data and raw_data.get('not_applicable'):
            return {
                'not_applicable': True,
                'completed_hours': 0,
                'required_hours': 0,
                'remaining_hours': 0,
                'completion_percentage': 0,
                'completed_activities': [],
                'total_hours': 0
            }
        
        # Extract basic info
        activities = raw_data.get('activities', [])
        opportunities = raw_data.get('opportunities', [])
        
        logger.debug(f"[NORMALIZE] Found {len(activities)} activities and {len(opportunities)} opportunities")
        
        # Calculate completed hours from opportunities (these are the actual completed activities)
        completed_hours = 0
        completed_activities = []
        
        # Process opportunities first (these are the completed activities from CMS)
        if opportunities:
            logger.debug(f"[COMMUNITY] Processing {len(opportunities)} opportunities as completed activities")
            for opportunity in opportunities:
                # Extract hours - handle both numeric and string values
                hours = opportunity.get('hours', 0)
                try:
                    if isinstance(hours, str):
                        hours = float(hours) if hours.replace('.', '').isdigit() else 0
                    elif not isinstance(hours, (int, float)):
                        hours = 0
                except:
                    hours = 0
                
                # Always add the opportunity, even if hours is 0 (some might not have hours recorded)
                completed_hours += hours
                
                # Extract title and description more comprehensively
                title = (opportunity.get('activity') or 
                       opportunity.get('title') or 
                       opportunity.get('name') or 
                       'Community Service Activity')
                
                description = (opportunity.get('description') or 
                             opportunity.get('details') or 
                             opportunity.get('activity') or 
                             opportunity.get('title') or 
                             'Community service activity')
                
                # Extract additional fields that might be present
                date = opportunity.get('date') or opportunity.get('semester') or 'N/A'
                status = opportunity.get('status') or 'Completed'
                
                completed_activities.append({
                    'title': title,
                    'date': date,
                    'hours': hours,
                    'description': description,
                    'status': status,
                    'semester': opportunity.get('semester', ''),
                    'organization': opportunity.get('organization', ''),
                    'location': opportunity.get('location', '')
                })
                
                logger.debug(f"[COMMUNITY] Added activity: {title} ({hours}h) - {description[:50]}...")
        
        # Also process activities if they exist (as additional data)
        elif activities:
            logger.debug(f"[COMMUNITY] Processing {len(activities)} activities as fallback")
            for activity in activities:
                hours = activity.get('hours', 0)
                try:
                    if isinstance(hours, str):
                        hours = float(hours) if hours.replace('.', '').isdigit() else 0
                    elif not isinstance(hours, (int, float)):
                        hours = 0
                except:
                    hours = 0
                
                completed_hours += hours
                
                # Extract title and description more comprehensively
                title = activity.get('title') or activity.get('name') or activity.get('activity') or 'Community Service Activity'
                description = (activity.get('description') or 
                             activity.get('details') or 
                             activity.get('activity') or 
                             activity.get('title') or 
                             'Community service activity')
                
                completed_activities.append({
                    'title': title,
                    'date': activity.get('date', 'N/A'),
                    'hours': hours,
                    'description': description,
                    'status': activity.get('status', 'completed'),
                    'semester': activity.get('semester', ''),
                    'organization': activity.get('organization', ''),
                    'location': activity.get('location', '')
                })
        
        # Get required hours
        required_hours = raw_data.get('required_hours', 40)
        if not isinstance(required_hours, (int, float)) or required_hours <= 0:
            required_hours = 40
        
        # Calculate derived values
        remaining_hours = max(0, required_hours - completed_hours)
        completion_percentage = min(100, (completed_hours / required_hours) * 100) if required_hours > 0 else 0
        
        logger.debug(f"[NORMALIZE] Final stats: {completed_hours}h completed, {remaining_hours}h remaining, {completion_percentage:.1f}%")
        logger.debug(f"[NORMALIZE] Processed {len(completed_activities)} completed activities")
        
        result = {
            'activities': activities,
            'completed_activities': completed_activities,
            'completed_hours': completed_hours,
            'required_hours': required_hours,
            'remaining_hours': remaining_hours,
            'completion_percentage': round(completion_percentage, 1),
            'last_updated': raw_data.get('last_updated', 'Recently updated'),
            'error': None
        }
        
        logger.debug(f"[NORMALIZE] Normalization complete, returning data with {len(result['completed_activities'])} activities")
        return result
        
    except Exception as e:
        logger.warning(f"Error normalizing community data: {e}")
        return create_empty_community_data()

def create_empty_community_data():
    """Create empty community service data structure"""
    return {
        'activities': [],
        'completed_activities': [],
        'completed_hours': 0,
        'required_hours': 40,
        'remaining_hours': 40,
        'completion_percentage': 0,
        'last_updated': 'Data unavailable',
        'error': 'No community service data available'
    }


