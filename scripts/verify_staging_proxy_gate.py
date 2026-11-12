"""Verify a staged proxy before allowing a testing routing switch."""
import os
import re
import subprocess

origin = os.environ['PROXY_ORIGIN']
key = os.environ['PROXY_KEY']
assert re.fullmatch(r'https://[a-zA-Z0-9.-]+', origin), 'Invalid staging origin.'
assert re.fullmatch(r'[a-fA-F0-9]{64}', key), 'Invalid staging key configuration.'
wrong = ('0' if key[0] != '0' else '1') + key[1:]
for label, supplied, expected in [('missing', '', 401), ('wrong', wrong, 401), ('matching', key, 403)]:
    config = 'header = "X-LMS-Proxy-Key: ' + supplied + '"\n' if supplied else ''
    response = subprocess.run(['curl', '--max-time', '25', '--silent', '--show-error', '--config', '-', '--output', '/dev/null', '--write-out', '%{http_code}', origin + '/?url=https%3A%2F%2Foutside.example.test'], input=config, text=True, capture_output=True, check=True)
    assert response.stdout == str(expected), f'{label} gate check failed: HTTP {response.stdout}'
    print(f'{label} gate check passed: HTTP {expected}')
