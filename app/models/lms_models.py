# app/models/lms_models.py
from datetime import datetime
from typing import List, Dict, Optional, Any
import json
import os

class LMSQuiz:
    """A downloadable quiz and its optional marks and solution."""

    def __init__(self, quiz_no: int, title: str, course_code: str = '',
                 course_name: str = '', marks: Optional[str] = None,
                 quiz_file: Optional[str] = None, solution_file: Optional[str] = None,
                 semester: Optional[str] = None):
        self.quiz_no = quiz_no
        self.title = title
        self.course_code = course_code
        self.course_name = course_name
        self.marks = marks
        self.quiz_file = quiz_file
        self.solution_file = solution_file
        self.semester = semester

    def to_dict(self) -> Dict[str, Any]:
        return dict(quiz_no=self.quiz_no, title=self.title, course_code=self.course_code,
                    course_name=self.course_name, marks=self.marks, quiz_file=self.quiz_file,
                    solution_file=self.solution_file, semester=self.semester)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LMSQuiz':
        return cls(**{key: data[key] for key in (
            'quiz_no', 'title', 'course_code', 'course_name', 'marks',
            'quiz_file', 'solution_file', 'semester') if key in data})


class LMSAssignment:
    """Model for LMS assignment data"""
    
    def __init__(self, assignment_no: int, title: str, course_code: str, course_name: str,
                 marks_obtained: Optional[float] = None, total_marks: Optional[float] = None,
                 submission_status: str = 'pending', deadline: Optional[datetime] = None,
                 submission_file: Optional[str] = None, assignment_file: Optional[str] = None,
                 solution_file: Optional[str] = None, remarks: Optional[str] = None, 
                 semester: Optional[str] = None, actual_deadline: Optional[datetime] = None,
                 extended_deadline: Optional[datetime] = None, returned_file: Optional[str] = None,
                 comments: Optional[str] = None, submission_id: Optional[str] = None,
                 assignment_id: Optional[str] = None, offered_course_id: Optional[str] = None,
                 student_id: Optional[str] = None):
        self.assignment_no = assignment_no
        self.title = title
        self.course_code = course_code
        self.course_name = course_name
        self.marks_obtained = marks_obtained
        self.total_marks = total_marks
        self.submission_status = submission_status  # 'submitted', 'pending', 'overdue'
        self.deadline = deadline  # Primary deadline (extended if available, otherwise actual)
        self.actual_deadline = actual_deadline  # Original deadline
        self.extended_deadline = extended_deadline  # Extended deadline if any
        self.submission_file = submission_file  # Student's submitted file URL
        self.assignment_file = assignment_file  # Assignment file URL
        self.solution_file = solution_file  # Solution file URL
        self.returned_file = returned_file  # Teacher's returned file URL
        self.remarks = remarks  # Assignment remarks
        self.comments = comments  # Teacher's comments
        self.semester = semester
        self.submission_id = submission_id  # LMS submission ID (for delete operations)
        self.assignment_id = assignment_id  # LMS assignment ID (for upload/delete operations)
        self.offered_course_id = offered_course_id  # LMS offered course ID (for upload/delete operations)
        self.student_id = student_id  # LMS student ID (for upload operations)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert assignment to dictionary for storage"""
        # Format deadline for display
        deadline_str = None
        if self.deadline:
            try:
                # Format like: "9 September 2025-11:55 pm"
                deadline_str = self.deadline.strftime("%d %B %Y-%I:%M %p").lower()
            except:
                deadline_str = self.deadline.isoformat()
        
        # Format actual deadline
        actual_deadline_str = None
        if self.actual_deadline:
            try:
                actual_deadline_str = self.actual_deadline.strftime("%d %B %Y-%I:%M %p").lower()
            except:
                actual_deadline_str = self.actual_deadline.isoformat()
        
        # Format extended deadline
        extended_deadline_str = None
        if self.extended_deadline:
            try:
                extended_deadline_str = self.extended_deadline.strftime("%d %B %Y-%I:%M %p").lower()
            except:
                extended_deadline_str = self.extended_deadline.isoformat()
        
        return {
            'assignment_no': self.assignment_no,
            'title': self.title,
            'course_code': self.course_code,
            'course_name': self.course_name,
            'marks_obtained': self.marks_obtained,
            'total_marks': self.total_marks,
            'submission_status': self.submission_status,
            'deadline': deadline_str,
            'actual_deadline': actual_deadline_str,
            'extended_deadline': extended_deadline_str,
            'submission_file': self.submission_file,
            'assignment_file': self.assignment_file,
            'solution_file': self.solution_file,
            'returned_file': self.returned_file,
            'remarks': self.remarks,
            'comments': self.comments,
            'semester': self.semester,
            'submission_id': self.submission_id,
            'assignment_id': self.assignment_id,
            'offered_course_id': self.offered_course_id,
            'student_id': self.student_id,
            # Additional fields for UI
            'is_overdue': self.is_overdue(),
            'is_pending': self.is_pending(),
            'is_upcoming': self.is_upcoming(),
            'is_past_due': self.is_past_due(),
            'can_submit': self.submission_status == 'pending',
            'status_display': self.get_status_display(),
            'has_extended_deadline': self.extended_deadline is not None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LMSAssignment':
        """Create assignment from dictionary"""
        def _parse_dt(value):
            if not value:
                return None
            if isinstance(value, datetime):
                return value
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                pass
            try:
                from app.utils.assignment_categorization import parse_deadline as parse_lms_deadline
                return parse_lms_deadline(str(value))
            except Exception:
                return None

        deadline = _parse_dt(data.get('deadline'))
        actual_deadline = _parse_dt(data.get('actual_deadline'))
        extended_deadline = _parse_dt(data.get('extended_deadline'))
        
        return cls(
            assignment_no=data.get('assignment_no', 0),
            title=data.get('title', ''),
            course_code=data.get('course_code', ''),
            course_name=data.get('course_name', ''),
            marks_obtained=data.get('marks_obtained'),
            total_marks=data.get('total_marks'),
            submission_status=data.get('submission_status', 'pending'),
            deadline=deadline,
            actual_deadline=actual_deadline,
            extended_deadline=extended_deadline,
            submission_file=data.get('submission_file'),
            assignment_file=data.get('assignment_file'),
            solution_file=data.get('solution_file'),
            returned_file=data.get('returned_file'),
            remarks=data.get('remarks'),
            comments=data.get('comments'),
            semester=data.get('semester'),
            submission_id=data.get('submission_id'),
            assignment_id=data.get('assignment_id'),
            offered_course_id=data.get('offered_course_id'),
            student_id=data.get('student_id')
        )
    
    def is_pending(self) -> bool:
        """Check if assignment is pending submission"""
        return self.submission_status == 'pending'
    
    def is_overdue(self) -> bool:
        """Check if assignment is overdue based on LMS status"""
        return self.submission_status == 'overdue'
    
    def is_upcoming(self) -> bool:
        """Check if assignment deadline is upcoming (within next 7 days)"""
        if not self.deadline:
            return False
        
        from datetime import timedelta
        pkt_now = datetime.utcnow() + timedelta(hours=5)
        seven_days_from_now = pkt_now + timedelta(days=7)
        
        return pkt_now <= self.deadline <= seven_days_from_now
    
    def is_past_due(self) -> bool:
        """Check if assignment deadline has passed"""
        if not self.deadline:
            return False
        
        from datetime import timedelta
        # Cloud Run servers are in UTC, deadlines are in PKT (UTC+5)
        pkt_now = datetime.utcnow() + timedelta(hours=5)
        return pkt_now > self.deadline
    
    def get_status_display(self) -> str:
        """Get human-readable status"""
        if self.is_overdue():
            return 'Overdue'
        elif self.is_pending():
            return 'Pending'
        elif self.submission_status == 'submitted':
            return 'Submitted'
        else:
            return self.submission_status.title()

class LMSLectureNote:
    """Model for LMS lecture notes data"""
    
    def __init__(self, week_no: int, title: str, course_code: str, course_name: str,
                 lecture_files: Optional[List[str]] = None, video_links: Optional[List[str]] = None,
                 supplementary_links: Optional[List[str]] = None, remarks: Optional[str] = None,
                 upload_date: Optional[datetime] = None, semester: Optional[str] = None,
                 lecture_files_info: Optional[List[Dict[str, Any]]] = None,
                 video_links_info: Optional[List[Dict[str, Any]]] = None,
                 supplementary_links_info: Optional[List[Dict[str, Any]]] = None):
        self.week_no = week_no
        self.title = title
        self.course_code = course_code
        self.course_name = course_name
        self.lecture_files = lecture_files or []  # URLs (for backward compatibility)
        self.video_links = video_links or []      # URLs (for backward compatibility)
        self.supplementary_links = supplementary_links or []  # URLs (for backward compatibility)
        self.lecture_files_info = lecture_files_info or []  # List of dicts with url, type, size, name
        self.video_links_info = video_links_info or []      # List of dicts with url, type, size, name
        self.supplementary_links_info = supplementary_links_info or []  # List of dicts with url, type, size, name
        self.remarks = remarks
        self.upload_date = upload_date
        self.semester = semester
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert lecture note to dictionary for storage"""
        return {
            'week_no': self.week_no,
            'title': self.title,
            'course_code': self.course_code,
            'course_name': self.course_name,
            'lecture_files': self.lecture_files,
            'video_links': self.video_links,
            'supplementary_links': self.supplementary_links,
            'lecture_files_info': self.lecture_files_info,
            'video_links_info': self.video_links_info,
            'supplementary_links_info': self.supplementary_links_info,
            'remarks': self.remarks,
            'upload_date': self.upload_date.isoformat() if self.upload_date else None,
            'semester': self.semester
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LMSLectureNote':
        """Create lecture note from dictionary"""
        upload_date = None
        if data.get('upload_date'):
            try:
                upload_date = datetime.fromisoformat(data['upload_date'])
            except (ValueError, TypeError):
                upload_date = None
        
        return cls(
            week_no=data.get('week_no', 0),
            title=data.get('title', ''),
            course_code=data.get('course_code', ''),
            course_name=data.get('course_name', ''),
            lecture_files=data.get('lecture_files', []),
            video_links=data.get('video_links', []),
            supplementary_links=data.get('supplementary_links', []),
            lecture_files_info=data.get('lecture_files_info', []),
            video_links_info=data.get('video_links_info', []),
            supplementary_links_info=data.get('supplementary_links_info', []),
            remarks=data.get('remarks'),
            upload_date=upload_date,
            semester=data.get('semester')
        )
    
    def has_files(self) -> bool:
        """Check if lecture note has any files"""
        return bool(self.lecture_files or self.video_links or self.supplementary_links)
    
    def get_file_count(self) -> int:
        """Get total number of files/links"""
        return len(self.lecture_files) + len(self.video_links) + len(self.supplementary_links)

class LMSCredentials:
    """Model for LMS authentication credentials and session data"""
    
    def __init__(self, lms_username: str, lms_password_encrypted: str,
                 session_cookies: Optional[Dict[str, Any]] = None,
                 session_expires: Optional[datetime] = None,
                 last_sync: Optional[datetime] = None):
        self.lms_username = lms_username
        self.lms_password_encrypted = lms_password_encrypted
        self.session_cookies = session_cookies or {}
        self.session_expires = session_expires
        self.last_sync = last_sync
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert credentials to dictionary for storage"""
        return {
            'lms_username': self.lms_username,
            'lms_password_encrypted': self.lms_password_encrypted,
            'session_cookies': self.session_cookies,
            'session_expires': self.session_expires.isoformat() if self.session_expires else None,
            'last_sync': self.last_sync.isoformat() if self.last_sync else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LMSCredentials':
        """Create credentials from dictionary"""
        session_expires = None
        if data.get('session_expires'):
            try:
                session_expires = datetime.fromisoformat(data['session_expires'])
            except (ValueError, TypeError):
                session_expires = None
        
        last_sync = None
        if data.get('last_sync'):
            try:
                last_sync = datetime.fromisoformat(data['last_sync'])
            except (ValueError, TypeError):
                last_sync = None
        
        return cls(
            lms_username=data.get('lms_username', ''),
            lms_password_encrypted=data.get('lms_password_encrypted', ''),
            session_cookies=data.get('session_cookies', {}),
            session_expires=session_expires,
            last_sync=last_sync
        )
    
    def is_session_valid(self) -> bool:
        """Check if current session is still valid"""
        if not self.session_expires:
            return False
        return datetime.now() < self.session_expires
    
    def clear_session(self):
        """Clear session data"""
        self.session_cookies = {}
        self.session_expires = None

class LMSCache:
    """Model for caching LMS data"""
    
    def __init__(self, account_id: str, data_type: str, semester: Optional[str] = None,
                 course: Optional[str] = None, data: Optional[Dict[str, Any]] = None,
                 cached_at: Optional[datetime] = None, expires_at: Optional[datetime] = None):
        self.account_id = account_id
        self.data_type = data_type  # 'assignments' or 'lecture_notes'
        self.semester = semester
        self.course = course
        self.data = data or {}
        self.cached_at = cached_at or datetime.now()
        self.expires_at = expires_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert cache to dictionary for storage"""
        return {
            'account_id': self.account_id,
            'data_type': self.data_type,
            'semester': self.semester,
            'course': self.course,
            'data': self.data,
            'cached_at': self.cached_at.isoformat() if self.cached_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LMSCache':
        """Create cache from dictionary"""
        cached_at = None
        if data.get('cached_at'):
            try:
                cached_at = datetime.fromisoformat(data['cached_at'])
            except (ValueError, TypeError):
                cached_at = datetime.now()
        
        expires_at = None
        if data.get('expires_at'):
            try:
                expires_at = datetime.fromisoformat(data['expires_at'])
            except (ValueError, TypeError):
                expires_at = None
        
        return cls(
            account_id=data.get('account_id', ''),
            data_type=data.get('data_type', ''),
            semester=data.get('semester'),
            course=data.get('course'),
            data=data.get('data', {}),
            cached_at=cached_at,
            expires_at=expires_at
        )
    
    def is_expired(self) -> bool:
        """Check if cache is expired"""
        if not self.expires_at:
            return True
        return datetime.now() > self.expires_at
    
    def is_valid(self) -> bool:
        """Check if cache is valid (not expired and has data)"""
        return not self.is_expired() and bool(self.data)
