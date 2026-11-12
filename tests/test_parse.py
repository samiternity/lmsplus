from bs4 import BeautifulSoup

from app.utils.enhanced_scraper import EnhancedCMSScraper


PROFILE_HTML = """
<!DOCTYPE html>
<html>
<body>
<span id="ProfileInfo_lblUsername">01-000000-001</span>
<span id="ProfileInfo_lblName">SYNTHETIC STUDENT</span>
<span id="ProfileInfo_lblEmail">01-000000-001@student.example.edu</span>
<table class="info">
    <tr>
        <th>Enrollment</th>
        <td>01-000000-001</td>
        <th>Registration No.</th>
        <td>00000</td>
    </tr>
    <tr>
        <th>Name</th>
        <td>SYNTHETIC STUDENT</td>
        <th>Father Name</th>
        <td>SYNTHETIC PARENT</td>
    </tr>
    <tr>
        <th>Program</th>
        <td>BS (CS)</td>
        <th>Degree Duration</th>
        <td>4 Years</td>
    </tr>
    <tr>
        <th>Intake Semester</th>
        <td>Fall-2024</td>
        <th>Max Semester</th>
        <td>Spring-2030</td>
    </tr>
    <tr>
        <th>Mobile No.</th>
        <td>000-0000000</td>
        <th>Phone No.</th>
        <td>0000000000</td>
    </tr>
    <tr>
        <th>Personal Email</th>
        <td>student@example.test</td>
        <th>University Email</th>
        <td>01-000000-001@student.example.edu</td>
    </tr>
    <tr>
        <th>Current Address</th>
        <td colspan="3">Example Street</td>
    </tr>
    <tr>
        <th>Permanent Address</th>
        <td colspan="3">Example Street</td>
    </tr>
</table>
</body>
</html>
"""


def test_parse_profile_fields():
    scraper = EnhancedCMSScraper.__new__(EnhancedCMSScraper)
    profile_data = scraper.parse_profile(BeautifulSoup(PROFILE_HTML, 'html.parser'))

    assert profile_data is not None
    assert profile_data['enrollment'] == '01-000000-001'
    assert profile_data['registration_no'] == '00000'
    assert profile_data['name'] == 'SYNTHETIC STUDENT'
    assert profile_data['father_name'] == 'SYNTHETIC PARENT'
    assert profile_data['program'] == 'BS (CS)'
    assert profile_data['mobile_no'] == '000-0000000'
    assert profile_data['personal_email'] == 'student@example.test'
    assert profile_data['current_address'] == 'Example Street'
    assert profile_data['cms_username'] == '01-000000-001'
