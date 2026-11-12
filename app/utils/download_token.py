import os
import json
import zlib
import base64
import re
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _get_encryption_key() -> bytes:
    key_hex = os.environ.get("DOWNLOAD_ENCRYPTION_KEY", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key_hex):
        raise ValueError("Configure a 64-character hexadecimal DOWNLOAD_ENCRYPTION_KEY.")
    return bytes.fromhex(key_hex)


def generate_download_token(urls: list, cookies: dict) -> str:
    """
    Generates a secure AES-256-GCM encrypted token containing target URLs and cookies.
    The payload is zlib compressed to keep URL length under limits.
    """
    payload = {
        "u": urls,
        "c": cookies,
        "t": int(time.time())
    }
    
    # Convert to JSON and compress
    json_bytes = json.dumps(payload).encode('utf-8')
    compressed_bytes = zlib.compress(json_bytes, level=9)
    
    # Encrypt with AES-GCM
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    
    ciphertext = aesgcm.encrypt(iv, compressed_bytes, None)
    
    # Format: [IV (12)] + [Ciphertext + Tag]
    final_blob = iv + ciphertext
    
    # Base64URL encode for URL safety
    token = base64.urlsafe_b64encode(final_blob).decode('utf-8').rstrip("=")
    
    return token

def generate_encryption_key_hex() -> str:
    """Helper to generate a random 32-byte hex string for the .env file."""
    return os.urandom(32).hex()
