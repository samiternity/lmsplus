"""Snapshot, configure, or restore only the unitrack-testing proxy pool."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import urllib.error

PROJECT = 'unitrack-testing'
DOC = f'https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents/system_config/proxy_state'

def request(url, method='GET', data=None):
    token = subprocess.check_output(['gcloud', 'auth', 'print-access-token'], text=True).strip()
    req = urllib.request.Request(url, method=method, headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'}, data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        if error.code == 404 and method == 'GET':
            return None
        raise RuntimeError(f'Staging API failed: HTTP {error.code}') from None

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['snapshot', 'configure', 'restore'])
args = parser.parse_args()
folder = Path(os.environ.get('STAGING_ROLLBACK_DIR', 'staging-rollback'))
folder.mkdir(mode=0o700, exist_ok=True)
if args.action == 'snapshot':
    service = json.loads(subprocess.check_output(['gcloud','run','services','describe','university-portal-test','--project',PROJECT,'--region','us-central1','--format=json(status)'],text=True))
    (folder/'cloud-run.json').write_text(json.dumps(service))
    (folder/'proxy-state.json').write_text(json.dumps(request(DOC)))
    hosting = request(f'https://firebasehosting.googleapis.com/v1beta1/sites/{PROJECT}/releases?pageSize=1')
    (folder/'hosting.json').write_text(json.dumps(hosting))
    print('Saved testing revision, Hosting release and proxy routing snapshot.')
elif args.action == 'configure':
    from app.utils.proxy_transport import proxy_workers
    workers = list(proxy_workers())
    if not workers or any('unitrack-app-testing.iamsamihome.workers.dev' not in w for w in workers):
        raise RuntimeError('This staging rollout must use only the verified isolated Worker.')
    request(DOC+'?updateMask.fieldPaths=workers&updateMask.fieldPaths=dead_workers', 'PATCH', {'fields': {'workers': {'arrayValue': {'values': [{'stringValue':w} for w in workers]}}, 'dead_workers': {'mapValue': {'fields': {}}}}})
    print('Testing proxy pool updated to verified staging Worker.')
else:
    old = json.loads((folder/'proxy-state.json').read_text())
    if old is not None:
        request(DOC, 'PATCH', {'fields': old.get('fields',{})})
    else:
        request(DOC, 'DELETE')
    print('Testing proxy pool restored.')
