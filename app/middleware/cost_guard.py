from functools import wraps
from flask import request, abort, jsonify
from app.services.budget_monitor import get_budget_monitor, BudgetExceededError
import logging

logger = logging.getLogger(__name__)

def cost_guard(f):
    """
    Middleware/Decorator to check budget limits before processing request.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        monitor = get_budget_monitor()
        try:
            # Assign a basic cost to every request (Cloud Run invocation)
            monitor.check_can_proceed('request', cost_weight=1)
            
            # Note: Detailed read/write costs are tracked inside the service calls
            # This guard prevents entry if we are already over global limits
            
        except BudgetExceededError as e:
            logger.critical(f"Request blocked by Cost Guard: {str(e)}")
            return jsonify({
                "error": "Service Temporarily Unavailable",
                "message": "The application has reached its safe usage limits for the current period."
            }), 503
            
        return f(*args, **kwargs)
    return decorated_function
