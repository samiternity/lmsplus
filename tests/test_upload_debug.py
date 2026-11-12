#!/usr/bin/env python3
"""
Debug script to test assignment upload with detailed logging.
This will help identify what's being sent vs what the LMS expects.
"""

import sys
import os

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.services.lms_service import LMSService
from flask import Flask
import base64

def test_upload_data():
    """Test what data format the LMS expects"""
    
    print("=" * 80)
    print("ASSIGNMENT UPLOAD DEBUG TEST")
    print("=" * 80)
    
    # Example IDs from the HTML form
    print("\n1. Example IDs from LMS HTML form:")
    print("-" * 80)
    example_assignment_id = "MTkzMjcz"  # From Submit button example.html
    example_offered_course_id = "MTM2NjM2"
    example_student_id = "MTE2NjM2"
    
    print(f"assignmentID (base64):     {example_assignment_id}")
    print(f"offeredCourseID (base64):  {example_offered_course_id}")
    print(f"studentID (base64):        {example_student_id}")
    
    # Decode to see what they represent
    print("\n2. Decoded values (what they represent):")
    print("-" * 80)
    try:
        decoded_assignment = base64.b64decode(example_assignment_id).decode('utf-8')
        decoded_course = base64.b64decode(example_offered_course_id).decode('utf-8')
        decoded_student = base64.b64decode(example_student_id).decode('utf-8')
        
        print(f"assignmentID decoded:      {decoded_assignment}")
        print(f"offeredCourseID decoded:   {decoded_course}")
        print(f"studentID decoded:         {decoded_student}")
    except Exception as e:
        print(f"Error decoding: {e}")
    
    print("\n3. What your scraper should extract:")
    print("-" * 80)
    print("The scraper should extract the BASE64-ENCODED values from the HTML")
    print("and store them AS-IS (without decoding).")
    print("")
    print("Example from HTML:")
    print('  <input type="hidden" name="assignmentID" value="MTkzMjcz">')
    print("")
    print("Should be stored as: assignment_id = 'MTkzMjcz'")
    print("NOT as: assignment_id = '193273'")
    
    print("\n4. What gets sent to LMS server:")
    print("-" * 80)
    print("POST https://lms.bahria.edu.pk/Student/includes/studentprocess.php?s=assignmentSubmissionAdd")
    print("")
    print("Form data:")
    print(f"  assignmentID:     {example_assignment_id}")
    print(f"  offeredCourseID:  {example_offered_course_id}")
    print(f"  ocID:             {example_offered_course_id}")
    print(f"  studentID:        {example_student_id}")
    print("")
    print("Files:")
    print("  assignmentSubmissionFile: (file content)")
    
    print("\n5. Common issues:")
    print("-" * 80)
    print("❌ WRONG: Decoding IDs and sending plain integers")
    print("   assignmentID: 193273")
    print("")
    print("✅ CORRECT: Sending base64-encoded IDs")
    print("   assignmentID: MTkzMjcz")
    
    print("\n6. How to verify your fix:")
    print("-" * 80)
    print("1. Clear cache: python clear_lms_cache.py")
    print("2. Restart Flask server")
    print("3. Load assignments page (forces fresh scrape)")
    print("4. Check logs for extracted IDs:")
    print("   Get-Content logs\\lms.log -Tail 100 | Select-String 'Extracted.*IDs'")
    print("5. Try uploading a file")
    print("6. Check logs for upload request:")
    print("   Get-Content logs\\lms.log -Tail 100 | Select-String 'Form data values'")
    print("")
    print("The IDs should look like 'MTkzMjcz' not '193273'")
    
    print("\n" + "=" * 80)

if __name__ == '__main__':
    test_upload_data()
