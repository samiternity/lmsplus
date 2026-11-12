#!/usr/bin/env python3
"""Purge one explicitly selected account from local storage and both Firestore projects."""
import argparse
import os
import sys
import json
import re
import shutil
import stat
from pathlib import Path
import logging

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import firebase_admin
from firebase_admin import credentials, firestore

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def delete_user_from_firestore(credential, env_name, target_username):
    logger.info(f"==================================================")
    logger.info(f"STARTING DEEP PURGE: {target_username} in {env_name}")
    logger.info(f"==================================================")

    app_name = f"purge_{env_name}_{target_username.replace('-', '_')}"
    app = firebase_admin.initialize_app(credential, name=app_name)
    db = firestore.client(app=app)

    matched_account_ids = set()

    # 1. Check users collection
    user_doc_ref = db.collection('users').document(target_username)
    user_doc = user_doc_ref.get()
    if user_doc.exists:
        udata = user_doc.to_dict()
        if udata.get('account_id'):
            matched_account_ids.add(udata['account_id'])
        cms_acc = udata.get('cms_account')
        if isinstance(cms_acc, dict) and cms_acc.get('account_id'):
            matched_account_ids.add(cms_acc['account_id'])
        for acc_id in udata.get('cms_accounts', {}).keys():
            matched_account_ids.add(acc_id)

    # 2. Deep scan account_data to find ALL historical account_ids for this username
    logger.info(f"Scanning account_data collection for any document belonging to {target_username}...")
    account_data_docs_to_delete = []
    for doc in db.collection('account_data').stream():
        data = doc.to_dict() or {}
        doc_id = doc.id
        is_match = False

        if doc_id.startswith(f"{target_username}_"):
            is_match = True

        if data.get('cms_username') == target_username:
            is_match = True

        if data.get('account_id'):
            if is_match or data.get('cms_username') == target_username:
                matched_account_ids.add(data['account_id'])

        for known_acc_id in list(matched_account_ids):
            if doc_id.startswith(f"{known_acc_id}_"):
                is_match = True
                break

        if is_match:
            account_data_docs_to_delete.append(doc_id)
            if data.get('account_id'):
                matched_account_ids.add(data['account_id'])

    logger.info(f"Identified {len(matched_account_ids)} associated account_ids: {matched_account_ids}")
    logger.info(f"Identified {len(account_data_docs_to_delete)} account_data documents to delete")

    # Second pass on account_data with full set of matched account IDs
    for doc in db.collection('account_data').stream():
        if doc.id in account_data_docs_to_delete:
            continue
        for acc_id in matched_account_ids:
            if doc.id.startswith(f"{acc_id}_"):
                account_data_docs_to_delete.append(doc.id)
                break

    # 3. Delete from users
    if user_doc.exists:
        user_doc_ref.delete()
        logger.info(f"DELETED [users/{target_username}] from {env_name}")
    else:
        logger.info(f"[users/{target_username}] not found in {env_name}")

    # 4. Delete from fcm_tokens
    fcm_ref = db.collection('fcm_tokens').document(target_username)
    if fcm_ref.get().exists:
        fcm_ref.delete()
        logger.info(f"DELETED [fcm_tokens/{target_username}] from {env_name}")

    # 5. Delete all matched account_data docs
    for doc_id in set(account_data_docs_to_delete):
        db.collection('account_data').document(doc_id).delete()
        logger.info(f"DELETED [account_data/{doc_id}] from {env_name}")

    # 6. Delete all user_dashboards & accounts matching any matched account_id
    for acc_id in matched_account_ids:
        dash_ref = db.collection('user_dashboards').document(acc_id)
        if dash_ref.get().exists:
            dash_ref.delete()
            logger.info(f"DELETED [user_dashboards/{acc_id}] from {env_name}")

        acc_ref = db.collection('accounts').document(acc_id)
        if acc_ref.get().exists:
            acc_ref.delete()
            logger.info(f"DELETED [accounts/{acc_id}] from {env_name}")

    firebase_admin.delete_app(app)
    logger.info(f"PURGE COMPLETE for {env_name}\n")

def delete_local(target_username):
    logger.info(f"--- Cleaning local storage for {target_username} ---")
    data_dir = os.path.join(BASE_DIR, 'app', 'data')
    users_file = os.path.join(data_dir, 'users.json')
    accounts_dir = os.path.join(data_dir, 'accounts')

    account_ids = set()

    if os.path.exists(users_file):
        try:
            with open(users_file, 'r') as f:
                users = json.load(f)
            if target_username in users:
                u_data = users[target_username]
                if u_data.get('account_id'):
                    account_ids.add(u_data['account_id'])
                for a_id in u_data.get('cms_accounts', {}).keys():
                    account_ids.add(a_id)
                del users[target_username]
                with open(users_file, 'w') as f:
                    json.dump(users, f, indent=2)
                logger.info(f"Removed {target_username} from {users_file}")
        except Exception as e:
            logger.error(f"Error updating {users_file}: {e}")

    if os.path.exists(accounts_dir):
        for entry in os.listdir(accounts_dir):
            entry_path = os.path.join(accounts_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            is_match = entry in account_ids
            if not is_match:
                for root, _, files in os.walk(entry_path):
                    for file in files:
                        if file.endswith('.json'):
                            try:
                                with open(os.path.join(root, file), 'r', errors='ignore') as f:
                                    if target_username in f.read():
                                        is_match = True
                                        break
                            except:
                                pass
                    if is_match:
                        break
            if is_match:
                def _handle_readonly(func, path, _):
                    try:
                        os.chmod(path, stat.S_IWRITE)
                        func(path)
                    except Exception:
                        pass

                try:
                    shutil.rmtree(entry_path, onexc=_handle_readonly)
                    logger.info(f"Deleted local account directory: {entry_path}")
                except Exception as e:
                    logger.error(f"Failed to delete {entry_path}: {e}")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', help='Enrollment ID to purge')
    parser.add_argument('--protected-accounts-file', type=Path, required=True,
                        help='Restricted file outside the repository with one protected enrollment ID per line')
    parser.add_argument('--production-credentials', type=Path, required=True)
    parser.add_argument('--testing-credentials', type=Path, required=True)
    args = parser.parse_args()

    target = args.target.strip().lower()
    if not re.fullmatch(r'\d{2}-\d{6}-\d{3}', target):
        parser.error('target must be an enrollment ID in NN-NNNNNN-NNN format')
    protected_path = args.protected_accounts_file.resolve()
    production_path = args.production_credentials.resolve()
    testing_path = args.testing_credentials.resolve()
    for path in (protected_path, production_path, testing_path):
        if path.is_relative_to(Path(BASE_DIR)):
            parser.error('operator configuration must be outside the repository')
        if not path.is_file():
            parser.error('all operator configuration files must exist')
    try:
        protected = {line.split('#', 1)[0].strip().lower()
                     for line in protected_path.read_text(encoding='utf-8').splitlines()}
    except (OSError, UnicodeError):
        parser.error('cannot read the protected accounts file')
    protected.discard('')
    if not protected or any(not re.fullmatch(r'\d{2}-\d{6}-\d{3}', account) for account in protected):
        parser.error('the protected accounts file must list valid enrollment IDs')
    if target in protected:
        parser.error('refusing to delete a protected account')

    try:
        production = credentials.Certificate(str(production_path))
        testing = credentials.Certificate(str(testing_path))
    except (OSError, ValueError):
        parser.error('cannot load Firestore credentials')
    if not production.project_id or not testing.project_id or production.project_id == testing.project_id:
        parser.error('testing and production credentials must name distinct projects')

    delete_local(target)
    delete_user_from_firestore(production, 'production', target)
    delete_user_from_firestore(testing, 'testing', target)


if __name__ == '__main__':
    main()
