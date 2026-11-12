import requests
from bs4 import BeautifulSoup
import os

class MobileLMSScraper:
    def __init__(self, session):
        self.session = session
        self.lms_base_url = "https://lms.bahria.edu.pk"
        self.gateway_url = "https://cms.bahria.edu.pk/Sys/Common/GoToLMS.aspx"

    def connect_to_lms(self):
        """Cross-bridge from CMS to LMS"""
        try:
            # CMS Login must be done before calling this
            resp = self.session.get(self.gateway_url, allow_redirects=True)
            if 'lms.bahria.edu.pk' in resp.url and 'login' not in resp.url.lower():
                return True
            return False
        except Exception as e:
            print(f"LMS Connect Error: {e}")
            return False

    def get_assignments(self):
        """Scrape Assignments Table"""
        try:
            url = f"{self.lms_base_url}/Student/Assignments.php"
            resp = self.session.get(url)
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            assignments = []
            # Find the main table (usually table-striped or id='myTable')
            tables = soup.find_all('table')
            if not tables: return []
            
            # Assuming first big table
            rows = tables[0].find_all('tr')
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) < 5: continue
                
                # Extract data (Hypothetical index based on typical structure)
                # Course, Title, Due Date, Status, Action
                assignments.append({
                    'course': cols[0].text.strip(),
                    'title': cols[1].text.strip(),
                    'due_date': cols[2].text.strip(),
                    'status': cols[3].text.strip(),
                    # Store download link if available (A tag)
                    'download_url': self._extract_link(cols[-1], 'download')
                })
            return assignments
        except Exception as e:
            print(f"Assign Scrape Error: {e}")
            return []

    def get_lecture_notes(self):
        """Scrape Lecture Notes"""
        try:
            url = f"{self.lms_base_url}/Student/LectureNotes.php"
            resp = self.session.get(url)
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            notes = []
            tables = soup.find_all('table')
            if not tables: return []
            
            rows = tables[0].find_all('tr')
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) < 4: continue
                
                notes.append({
                    'course': cols[0].text.strip(),
                    'week': cols[1].text.strip(),
                    'title': cols[2].text.strip(),
                    'download_url': self._extract_link(cols[-1], 'download')
                })
            return notes
        except Exception:
            return []

    def _extract_link(self, cell, keyword):
        """Helper to find href"""
        a = cell.find('a', href=True)
        if a:
            link = a['href']
            if link.startswith('http'): return link
            return f"{self.lms_base_url}/Student/{link}"
        return None
