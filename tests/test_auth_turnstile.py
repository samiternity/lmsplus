import unittest
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import create_app
from app.utils.turnstile import verify_turnstile


class TestTurnstileAuth(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['SECRET_KEY'] = 'test-secret-key'
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.app.config['TURNSTILE_SECRET_KEY'] = '1x0000000000000000000000000000000AA'
        self.client = self.app.test_client()

    def test_turnstile_verify_missing_token(self):
        """Empty or invalid token format should return False."""
        self.assertFalse(verify_turnstile(""))
        self.assertFalse(verify_turnstile(None))
        self.assertFalse(verify_turnstile("a" * 3000))

    @patch("requests.post")
    def test_turnstile_verify_success(self, mock_post):
        """Mock successful Cloudflare siteverify response."""
        with self.app.app_context():
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "success": True,
                "action": "login",
                "hostname": "localhost"
            }
            mock_post.return_value = mock_resp
            
            result = verify_turnstile("valid_test_token", remote_ip="127.0.0.1", action="login")
            self.assertTrue(result)

    @patch("requests.post")
    def test_turnstile_verify_failure(self, mock_post):
        """Mock failed Cloudflare siteverify response."""
        with self.app.app_context():
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "success": False,
                "error-codes": ["invalid-input-response"]
            }
            mock_post.return_value = mock_resp
            
            result = verify_turnstile("bad_token", remote_ip="127.0.0.1", action="login")
            self.assertFalse(result)

    def test_login_honeypot_triggers_rejection(self):
        """Bots that populate hidden honeypot field must get 400 Bad Request."""
        response = self.client.post('/auth/login', data={
            'username': '01-123456-001',
            'password': 'password123',
            'website_url': 'https://bot-site.com',
            'cf-turnstile-response': 'dummy_token'
        })
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn('Security verification failed', data['error'])

    @patch("app.routes.auth.verify_turnstile")
    def test_login_rejected_when_turnstile_fails(self, mock_verify):
        """Login should be blocked when Turnstile check fails."""
        mock_verify.return_value = False
        
        response = self.client.post('/auth/login', data={
            'username': '01-123456-001',
            'password': 'password123',
            'cf-turnstile-response': 'bad_token'
        })
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn('Security verification failed', data['error'])


if __name__ == '__main__':
    unittest.main()
