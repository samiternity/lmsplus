import logging
logger = logging.getLogger(__name__)

# app/models/lms_data_manager.py
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from .lms_models import LMSAssignment, LMSLectureNote, LMSCache

class LMSDataManager:
    """Manager for LMS data operations and caching"""
    
    def __init__(self, account):
        """Initialize with a CMSAccount instance"""
        self.account = account
        self.assignments_dir = account.get_lms_data_path('assignments')
        self.lecture_notes_dir = account.get_lms_data_path('lecture_notes')
        self.cache_dir = account.get_lms_data_path('cache')
        self.files_dir = account.get_lms_data_path('files')
    
    def save_assignments(self, assignments: List[LMSAssignment], semester: str = None) -> bool:
        """Save assignments to file"""
        try:
            filename = f"assignments_{semester or 'current'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            filepath = os.path.join(self.assignments_dir, filename)
            
            data = {
                'semester': semester,
                'saved_at': datetime.now().isoformat(),
                'assignments': [assignment.to_dict() for assignment in assignments]
            }
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # Also save as latest
            latest_filepath = os.path.join(self.assignments_dir, f"assignments_{semester or 'current'}_latest.json")
            with open(latest_filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            return True
        except Exception as e:
            logger.warning(f"Error saving assignments: {e}")
            return False
    
    def load_assignments(self, semester: str = None) -> List[LMSAssignment]:
        """Load assignments from file"""
        try:
            filename = f"assignments_{semester or 'current'}_latest.json"
            filepath = os.path.join(self.assignments_dir, filename)
            
            if not os.path.exists(filepath):
                return []
            
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            assignments = []
            for assignment_data in data.get('assignments', []):
                assignments.append(LMSAssignment.from_dict(assignment_data))
            
            return assignments
        except Exception as e:
            logger.warning(f"Error loading assignments: {e}")
            return []
    
    def save_lecture_notes(self, lecture_notes: List[LMSLectureNote], semester: str = None) -> bool:
        """Save lecture notes to file"""
        try:
            filename = f"lecture_notes_{semester or 'current'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            filepath = os.path.join(self.lecture_notes_dir, filename)
            
            data = {
                'semester': semester,
                'saved_at': datetime.now().isoformat(),
                'lecture_notes': [note.to_dict() for note in lecture_notes]
            }
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # Also save as latest
            latest_filepath = os.path.join(self.lecture_notes_dir, f"lecture_notes_{semester or 'current'}_latest.json")
            with open(latest_filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            return True
        except Exception as e:
            logger.warning(f"Error saving lecture notes: {e}")
            return False
    
    def load_lecture_notes(self, semester: str = None) -> List[LMSLectureNote]:
        """Load lecture notes from file"""
        try:
            filename = f"lecture_notes_{semester or 'current'}_latest.json"
            filepath = os.path.join(self.lecture_notes_dir, filename)
            
            if not os.path.exists(filepath):
                return []
            
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            lecture_notes = []
            for note_data in data.get('lecture_notes', []):
                lecture_notes.append(LMSLectureNote.from_dict(note_data))
            
            return lecture_notes
        except Exception as e:
            logger.warning(f"Error loading lecture notes: {e}")
            return []
    
    def save_cache(self, cache: LMSCache) -> bool:
        """Save cache data"""
        try:
            cache_key = f"{cache.data_type}_{cache.semester or 'current'}_{cache.course or 'all'}"
            filename = f"cache_{cache_key}.json"
            filepath = os.path.join(self.cache_dir, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(cache.to_dict(), f, indent=2, ensure_ascii=False)
            
            return True
        except Exception as e:
            logger.warning(f"Error saving cache: {e}")
            return False
    
    def load_cache(self, data_type: str, semester: str = None, course: str = None) -> Optional[LMSCache]:
        """Load cache data"""
        try:
            cache_key = f"{data_type}_{semester or 'current'}_{course or 'all'}"
            filename = f"cache_{cache_key}.json"
            filepath = os.path.join(self.cache_dir, filename)
            
            if not os.path.exists(filepath):
                return None
            
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            cache = LMSCache.from_dict(data)
            
            # Check if cache is still valid
            if cache.is_valid():
                return cache
            else:
                # Remove expired cache
                os.remove(filepath)
                return None
                
        except Exception as e:
            logger.warning(f"Error loading cache: {e}")
            return None
    
    def clear_cache(self, data_type: str = None) -> bool:
        """Clear cache files"""
        try:
            if not os.path.exists(self.cache_dir):
                return True
            
            for filename in os.listdir(self.cache_dir):
                if filename.startswith('cache_'):
                    if data_type is None or filename.startswith(f'cache_{data_type}_'):
                        filepath = os.path.join(self.cache_dir, filename)
                        os.remove(filepath)
            
            return True
        except Exception as e:
            logger.warning(f"Error clearing cache: {e}")
            return False
    
    def get_pending_assignments(self, semester: str = None) -> List[LMSAssignment]:
        """Get assignments that are pending submission"""
        assignments = self.load_assignments(semester)
        return [assignment for assignment in assignments if assignment.is_pending()]
    
    def get_overdue_assignments(self, semester: str = None) -> List[LMSAssignment]:
        """Get assignments that are overdue"""
        assignments = self.load_assignments(semester)
        return [assignment for assignment in assignments if assignment.is_overdue()]
    
    def get_assignments_by_course(self, course_code: str, semester: str = None) -> List[LMSAssignment]:
        """Get assignments for a specific course"""
        assignments = self.load_assignments(semester)
        return [assignment for assignment in assignments if assignment.course_code == course_code]
    
    def get_lecture_notes_by_course(self, course_code: str, semester: str = None) -> List[LMSLectureNote]:
        """Get lecture notes for a specific course"""
        lecture_notes = self.load_lecture_notes(semester)
        return [note for note in lecture_notes if note.course_code == course_code]
    
    def get_recent_lecture_notes(self, days: int = 7, semester: str = None) -> List[LMSLectureNote]:
        """Get lecture notes uploaded in the last N days"""
        lecture_notes = self.load_lecture_notes(semester)
        cutoff_date = datetime.now() - timedelta(days=days)
        
        recent_notes = []
        for note in lecture_notes:
            if note.upload_date and note.upload_date > cutoff_date:
                recent_notes.append(note)
        
        return sorted(recent_notes, key=lambda x: x.upload_date or datetime.min, reverse=True)
    
    def get_data_summary(self, semester: str = None) -> Dict[str, Any]:
        """Get summary of LMS data"""
        assignments = self.load_assignments(semester)
        lecture_notes = self.load_lecture_notes(semester)
        
        pending_assignments = [a for a in assignments if a.is_pending()]
        overdue_assignments = [a for a in assignments if a.is_overdue()]
        
        return {
            'total_assignments': len(assignments),
            'pending_assignments': len(pending_assignments),
            'overdue_assignments': len(overdue_assignments),
            'submitted_assignments': len(assignments) - len(pending_assignments),
            'total_lecture_notes': len(lecture_notes),
            'courses_with_assignments': len(set(a.course_code for a in assignments)),
            'courses_with_notes': len(set(n.course_code for n in lecture_notes)),
            'last_sync': self.account.lms_credentials.last_sync.isoformat() if (
                self.account.lms_credentials and self.account.lms_credentials.last_sync
            ) else None
        }