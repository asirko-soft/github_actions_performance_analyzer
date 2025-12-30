"""
Rate Limit Tracker for GitHub API

Centralized rate limit tracking with:
- Persistent storage in database
- Coordinated throttling across parallel workers
- Two-tier warning system (5000/hr normal, 15000/hr enterprise)
- Pre-emptive throttling to avoid hitting hard limits
"""

import threading
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from database import GHADatabase


class RateLimitTracker:
    """
    Singleton class for tracking and managing GitHub API rate limits.
    
    Thread-safe and persists state to the database.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    # Rate limit thresholds
    NORMAL_LIMIT = 5000
    ENTERPRISE_LIMIT = 15000
    
    # Warning thresholds (percentage of limit used)
    WARNING_THRESHOLD = 0.80  # 80% - yellow warning
    CRITICAL_THRESHOLD = 0.95  # 95% - red warning, start throttling
    
    # Safety buffer - stop making requests when this many remain
    SAFETY_BUFFER = 100
    
    def __new__(cls, db_path: Optional[str] = None):
        """Singleton pattern - only one instance per process."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize the rate limit tracker."""
        if self._initialized:
            return
        
        self.db_path = db_path
        self._request_lock = threading.Lock()
        self._throttle_event = threading.Event()
        self._throttle_event.set()  # Not throttled by default
        self._throttle_until: Optional[float] = None
        self._github_remaining: Optional[int] = None
        self._github_reset: Optional[int] = None
        self._initialized = True
    
    def _get_db(self) -> GHADatabase:
        """Get a database connection and ensure table exists."""
        db = GHADatabase(self.db_path)
        db.connect()
        
        # Ensure rate limit tracking table exists
        try:
            db.conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_limit_tracking (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    hour_start TEXT NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    rate_limit_remaining INTEGER,
                    rate_limit_reset INTEGER,
                    last_updated TEXT NOT NULL
                )
            """)
            db.conn.commit()
        except Exception:
            pass  # Table likely already exists
        
        return db
    
    def _get_current_hour_start(self) -> str:
        """Get the ISO format string for the start of the current hour."""
        current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return current_hour.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    def register_request(self, count: int = 1,
                        remaining: Optional[int] = None,
                        reset_timestamp: Optional[int] = None) -> Dict[str, Any]:
        """
        Register API request(s) and update tracking state.
        
        Call this after each successful API request.
        
        :param count: Number of requests to register (default 1)
        :param remaining: X-RateLimit-Remaining header value from GitHub
        :param reset_timestamp: X-RateLimit-Reset header value (Unix timestamp)
        :return: Current rate limit state
        """
        with self._request_lock:
            db = self._get_db()
            try:
                state = db.increment_rate_limit_count(
                    count=count,
                    rate_limit_remaining=remaining,
                    rate_limit_reset=reset_timestamp
                )
                
                # Update in-memory GitHub rate limit info
                if remaining is not None:
                    self._github_remaining = remaining
                if reset_timestamp is not None:
                    self._github_reset = reset_timestamp
                
                return state
            finally:
                db.close()
    
    def get_current_state(self) -> Dict[str, Any]:
        """
        Get the current rate limit state.
        
        :return: Dict with:
            - hour_start: ISO timestamp of current hour
            - request_count: Requests made this hour
            - rate_limit_remaining: Last known remaining from GitHub
            - rate_limit_reset: Unix timestamp when limit resets
            - warning_level: 'none', 'warning', or 'critical'
            - is_throttled: Whether we're currently throttling
            - throttle_until: Unix timestamp when throttle ends (if throttled)
            - usage_percent_normal: Percentage of normal limit used
            - usage_percent_enterprise: Percentage of enterprise limit used
        """
        db = self._get_db()
        try:
            state = db.get_rate_limit_state()
            current_hour = self._get_current_hour_start()
            
            if not state or state['hour_start'] != current_hour:
                # No state or hour has changed - return fresh state
                return {
                    'hour_start': current_hour,
                    'request_count': 0,
                    'rate_limit_remaining': self._github_remaining,
                    'rate_limit_reset': self._github_reset,
                    'warning_level': 'none',
                    'is_throttled': not self._throttle_event.is_set(),
                    'throttle_until': self._throttle_until,
                    'usage_percent_normal': 0.0,
                    'usage_percent_enterprise': 0.0
                }
            
            request_count = state['request_count']
            
            # Calculate usage percentages
            usage_normal = (request_count / self.NORMAL_LIMIT) * 100
            usage_enterprise = (request_count / self.ENTERPRISE_LIMIT) * 100
            
            # Determine warning level based on normal limit (conservative)
            if request_count >= self.NORMAL_LIMIT * self.CRITICAL_THRESHOLD:
                warning_level = 'critical'
            elif request_count >= self.NORMAL_LIMIT * self.WARNING_THRESHOLD:
                warning_level = 'warning'
            else:
                warning_level = 'none'
            
            return {
                'hour_start': state['hour_start'],
                'request_count': request_count,
                'rate_limit_remaining': state.get('rate_limit_remaining') or self._github_remaining,
                'rate_limit_reset': state.get('rate_limit_reset') or self._github_reset,
                'warning_level': warning_level,
                'is_throttled': not self._throttle_event.is_set(),
                'throttle_until': self._throttle_until,
                'usage_percent_normal': round(usage_normal, 1),
                'usage_percent_enterprise': round(usage_enterprise, 1)
            }
        finally:
            db.close()
    
    def should_throttle(self) -> Tuple[bool, Optional[float]]:
        """
        Check if we should throttle based on current usage.
        
        :return: Tuple of (should_throttle, seconds_to_wait)
        """
        state = self.get_current_state()
        
        # If GitHub told us we're rate limited, definitely throttle
        if state['rate_limit_remaining'] is not None and state['rate_limit_remaining'] <= self.SAFETY_BUFFER:
            if state['rate_limit_reset']:
                wait_seconds = max(0, state['rate_limit_reset'] - time.time()) + 5
                return (True, wait_seconds)
        
        # Pre-emptive throttling based on our tracked count (using normal limit as baseline)
        if state['request_count'] >= self.NORMAL_LIMIT - self.SAFETY_BUFFER:
            # Calculate time until next hour
            now = datetime.now(timezone.utc)
            next_hour = now.replace(minute=0, second=0, microsecond=0)
            if now >= next_hour:
                from datetime import timedelta
                next_hour = next_hour + timedelta(hours=1)
            wait_seconds = (next_hour - now).total_seconds() + 5
            return (True, wait_seconds)
        
        return (False, None)
    
    def start_throttle(self, duration_seconds: float):
        """
        Start throttling for the specified duration.
        
        All threads calling wait_if_throttled() will block.
        
        :param duration_seconds: How long to throttle
        """
        with self._request_lock:
            self._throttle_until = time.time() + duration_seconds
            self._throttle_event.clear()
            
            # Schedule unthrottle
            def unthrottle():
                time.sleep(duration_seconds)
                self.stop_throttle()
            
            threading.Thread(target=unthrottle, daemon=True).start()
            print(f"[RateLimitTracker] Throttling for {duration_seconds:.1f}s until {time.ctime(self._throttle_until)}")
    
    def stop_throttle(self):
        """Stop throttling and allow requests to proceed."""
        with self._request_lock:
            self._throttle_until = None
            self._throttle_event.set()
            print("[RateLimitTracker] Throttle released")
    
    def wait_if_throttled(self, timeout: Optional[float] = None) -> bool:
        """
        Wait if currently throttled.
        
        :param timeout: Maximum time to wait (None = wait indefinitely)
        :return: True if we can proceed, False if timed out
        """
        return self._throttle_event.wait(timeout=timeout)
    
    def check_and_throttle_if_needed(self) -> bool:
        """
        Check if throttling is needed and start it if so.
        
        :return: True if we started throttling, False if not needed
        """
        should_throttle, wait_seconds = self.should_throttle()
        
        if should_throttle and wait_seconds:
            # Only start if not already throttling
            if self._throttle_event.is_set():
                self.start_throttle(wait_seconds)
            return True
        
        return False
    
    def handle_rate_limit_response(self, remaining: int, reset_timestamp: int):
        """
        Handle rate limit headers from a GitHub API response.
        
        Call this after every API request to update state.
        
        :param remaining: X-RateLimit-Remaining header value
        :param reset_timestamp: X-RateLimit-Reset header value (Unix timestamp)
        """
        with self._request_lock:
            self._github_remaining = remaining
            self._github_reset = reset_timestamp
            
            # If we've hit the limit, start throttling
            if remaining <= 0:
                wait_seconds = max(0, reset_timestamp - time.time()) + 5
                if self._throttle_event.is_set():
                    self.start_throttle(wait_seconds)
    
    def reset_for_new_hour(self):
        """
        Reset the tracker for a new hour.
        
        Called automatically when hour changes, but can be called manually.
        """
        db = self._get_db()
        try:
            current_hour = self._get_current_hour_start()
            db.update_rate_limit_state(
                hour_start=current_hour,
                request_count=0,
                rate_limit_remaining=None,
                rate_limit_reset=None
            )
        finally:
            db.close()


# Global instance for easy access
_global_tracker: Optional[RateLimitTracker] = None


def get_rate_limit_tracker(db_path: Optional[str] = None) -> RateLimitTracker:
    """
    Get the global rate limit tracker instance.
    
    :param db_path: Database path (only used on first call)
    :return: The RateLimitTracker singleton
    """
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = RateLimitTracker(db_path)
    return _global_tracker

