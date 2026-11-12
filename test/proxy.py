"""HTTP proxy for the proxy tests. Runs inside the image.

proxy.py <dir> <port> <log-path> <user:password>

Requests for http://upstream/<name> are served from <dir> instead of being forwarded,
so the host "upstream" is only reachable through this proxy.
Requires basic proxy auth, supports HEAD and Range, appends "<status> <method> <url>" to the log for each request.
"""

import base64
import http.server
import mimetypes
import os
import re
import sys
import urllib.parse

DIR = ''
LOG_PATH = ''
EXPECTED_AUTH = ''


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.handle_request(True)

    def do_HEAD(self):
        self.handle_request(False)

    def handle_request(self, with_body):
        status, headers, body = self.get_response()

        with open(LOG_PATH, 'at') as fp:
            fp.write(f'{status} {self.command} {self.path}\n')

        self.send_response(status)
        for key, val in headers.items():
            self.send_header(key, val)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def get_response(self):
        if self.headers.get('Proxy-Authorization') != EXPECTED_AUTH:
            return 407, {'Proxy-Authenticate': 'Basic realm="test"'}, b''

        u = urllib.parse.urlsplit(self.path)
        if u.hostname != 'upstream':
            return 502, {}, b''

        name = os.path.basename(u.path)
        path = os.path.join(DIR, name)
        if not name or not os.path.isfile(path):
            return 404, {}, b''

        with open(path, 'rb') as fp:
            data = fp.read()

        headers = {
            'Content-Type': mimetypes.guess_type(path)[0] or 'application/octet-stream',
            'Accept-Ranges': 'bytes',
        }

        m = re.match(r'bytes=(\d+)-(\d*)$', self.headers.get('Range', ''))
        if not m:
            return 200, headers, data

        start = int(m.group(1))
        if start >= len(data):
            return 416, {'Content-Range': f'bytes */{len(data)}'}, b''
        end = len(data) - 1
        if m.group(2):
            end = min(int(m.group(2)), end)
        headers['Content-Range'] = f'bytes {start}-{end}/{len(data)}'
        return 206, headers, data[start : end + 1]

    def log_message(self, format, *args):
        pass


def main():
    global DIR, LOG_PATH, EXPECTED_AUTH

    DIR = sys.argv[1]
    port = int(sys.argv[2])
    LOG_PATH = sys.argv[3]
    EXPECTED_AUTH = 'Basic ' + base64.b64encode(sys.argv[4].encode('utf8')).decode('ascii')

    http.server.ThreadingHTTPServer(('', port), Handler).serve_forever()


if __name__ == '__main__':
    main()
