import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import time

class MobileCMSScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Mobile Safari/537.36'
        })
        
        self.login_url = 'https://cms.bahria.edu.pk/Logins/Student/Login.aspx'
        self.timetable_url = 'https://cms.bahria.edu.pk/Sys/Student/CourseRegistration/TimeTable.aspx'
        self.attendance_url = 'https://cms.bahria.edu.pk/Sys/Student/ClassAttendance/StudentWiseAttendance.aspx'
        self.profile_url = 'https://cms.bahria.edu.pk/Sys/Student/StudentProfile.aspx'
        self.institute_id = '2'  # Karachi campus

    def login(self, username, password):
        """Login to Bahria CMS and return success status"""
        try:
            # Get login form data
            form_data_base = self._get_login_form_data()
            if not form_data_base:
                return False
            
            # Prepare form data
            form_data = {
                '__LASTFOCUS': '',
                '__EVENTTARGET': 'ctl00$BodyPH$btnLogin',
                '__EVENTARGUMENT': '',
                '__VIEWSTATE': form_data_base['__VIEWSTATE'],
                '__VIEWSTATEGENERATOR': form_data_base['__VIEWSTATEGENERATOR'],
                '__EVENTVALIDATION': form_data_base['__EVENTVALIDATION'],
                'ctl00$BodyPH$tbEnrollment': username,
                'ctl00$BodyPH$tbPassword': password,
                'ctl00$BodyPH$ddlInstituteID': self.institute_id,
                'ctl00$BodyPH$ddlSubUserType': 'None',
                'ctl00$hfJsEnabled': '0'
            }
            
            # Submit login form
            response = self.session.post(self.login_url, data=form_data)
            response.raise_for_status()
            
            # Verify login
            if 'Student Portal' in response.text or 'TimeTable' in response.text:
                return True
            return False
                
        except Exception as e:
            print(f"Error during login: {e}")
            return False

    def _get_login_form_data(self):
        try:
            response = self.session.get(self.login_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            return {
                '__VIEWSTATE': soup.find('input', {'id': '__VIEWSTATE'}).get('value', ''),
                '__VIEWSTATEGENERATOR': soup.find('input', {'id': '__VIEWSTATEGENERATOR'}).get('value', ''),
                '__EVENTVALIDATION': soup.find('input', {'id': '__EVENTVALIDATION'}).get('value', '')
            }
        except Exception:
            return None

    def scrape_summary(self):
        """Scrape Profile Name and Attendance Summary quickly"""
        try:
            # 1. Get Profile Name
            profile_resp = self.session.get(self.profile_url)
            profile_soup = BeautifulSoup(profile_resp.text, 'html.parser')
            name_tag = profile_soup.find('span', {'id': 'ctl00_BodyPH_lblStudentName'})
            student_name = name_tag.text.strip() if name_tag else "Student"

            # 2. Get Attendance
            att_resp = self.session.get(self.attendance_url)
            att_soup = BeautifulSoup(att_resp.text, 'html.parser')
            attendance_data = self._parse_attendance(att_soup)

            return {
                'name': student_name,
                'attendance': attendance_data,
                'last_updated': datetime.now().strftime("%I:%M %p")
            }
        except Exception as e:
            print(f"Scrape Error: {e}")
            return None

    def _parse_attendance(self, soup):
        """Minimal Attendance Parser"""
        data = []
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) < 2: continue
            
            # Simple heuristic: Look for 'Course' and 'Percentage' headers
            headers = rows[0].get_text().lower()
            if 'course' in headers and 'attendance' in headers:
                for row in rows[1:]:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) < 10: continue
                    
                    course_name = cells[2].get_text(strip=True)
                    percentage_str = cells[9].get_text(strip=True) # "40 : 80%"
                    
                    percent = 0
                    if '%' in percentage_str:
                        try:
                            percent = float(percentage_str.split(':')[-1].replace('%', ''))
                        except: pass
                    
                    absences = 0
                    try:
                        absences = int(cells[10].get_text(strip=True))
                    except: pass
                    
                    data.append({
                        'course': course_name,
                        'percentage': percent,
                        'absences': absences
                    })
        return data
