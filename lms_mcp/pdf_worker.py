"""Isolated PDFium process: bounded stdin/stdout, no files or service credentials."""

import json
import math
import resource
import sys


def main():
    from lms_mcp.document_content import MAX_SOURCE_BYTES, pdf_content
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    seconds = max(1, min(50, math.ceil(float(sys.argv[1]))))
    resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
    data = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValueError('Invalid PDF size')
    # The content budget is enforced inside pdf_content before serialization.
    sys.stdout.write(json.dumps(pdf_content(data, zip_bundle=sys.argv[2:] == ['--zip-bundle'])))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit(2)
