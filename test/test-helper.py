"""Smoke test for a QGIS server image.

Creates a test project in the image, starts a container and runs OWS requests against it.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# see /make.sh

BASE_DIR = os.path.abspath(os.path.dirname(__file__) + '/..')
BUILD_DIR = '/opt/gbd/gbd-qgis-server-build'

VENDOR = 'gbdconsult'

GREEN = (0, 170, 0)
BLUE = (0, 0, 255)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)
MAGENTA = (255, 0, 255)

# positions on the A4 landscape layout (297 x 210 mm), see project.py
PRINT_MAP = (55 / 297, 55 / 210)
PRINT_SVG = (240 / 297, 30 / 210)
PRINT_HTML_FRAME = (240 / 297, 80 / 210)
PRINT_HTML_LABEL = (240 / 297, 130 / 210)

# the map is -1..11 in both directions, (2, 8) is inside the polygon and away from the points
GETMAP_POLYGON = (3 / 12, 3 / 12)

WMS = 'SERVICE=WMS&VERSION=1.3.0'
BBOX = 'CRS=EPSG:4326&BBOX=-1,-1,11,11&WIDTH=400&HEIGHT=400'
PRINT_PARAMS = f'{WMS}&REQUEST=GetPrint&TEMPLATE=test&CRS=EPSG:4326&LAYERS=poly,points&map0:EXTENT=-1,-1,11,11'

COLOR_TOLERANCE = 40
START_TIMEOUT = 60

# see proxy.py, the password checks url-unquoting of the proxy credentials
PROXY_PORT = 3128
PROXY_USER = 'user_1'
PROXY_PASSWORD = 'pass@1'


class Tester:
    def __init__(self, args):
        self.args = args

        arch = 'arm64' if args.arm else 'amd64'
        self.image = f'{VENDOR}/gbd-qgis-server-{arch}'
        if args.debug:
            self.image += '-debug'
        self.image += f':{args.version}'

        self.test_dir = BUILD_DIR + '/test'
        self.name = f'gbd-qgis-server-test-{os.getpid()}'
        self.network = ''
        self.containers = []
        self.server = ''
        self.base_url = ''

        self.passed = 0
        self.failed = 0

    def main(self):
        sys.stdout.reconfigure(line_buffering=True)
        print(f'testing {self.image}')

        shutil.rmtree(self.test_dir, ignore_errors=True)
        os.makedirs(self.test_dir)

        self.docker_run_script('project.py')

        try:
            self.start_server(self.name, [])
            self.run_tests('test_')

            self.start_proxy()
            proxy_url = f'http://{PROXY_USER}:{urllib.parse.quote(PROXY_PASSWORD, safe="")}@proxy:{PROXY_PORT}'
            self.start_server(f'{self.name}-proxied', ['--network', self.network, '-e', f'HTTP_PROXY={proxy_url}'])
            self.run_tests('proxy_test_')
        finally:
            self.cleanup()

        print()
        print(f'{self.passed} passed, {self.failed} FAILED, output in {self.test_dir}')
        return 1 if self.failed else 0

    def cleanup(self):
        if self.failed:
            for name, url in self.containers:
                print(f'--- {name} log (last 40 lines) ---')
                subprocess.run(['docker', 'logs', '--tail', '40', name], check=False)

        if self.args.keep:
            for name, url in self.containers:
                print(f'container {name} is running {url}')
            return

        for name, url in self.containers:
            subprocess.run(['docker', 'rm', '-f', name], check=False, stdout=subprocess.DEVNULL)
        if self.network:
            subprocess.run(['docker', 'network', 'rm', self.network], check=False, stdout=subprocess.DEVNULL)

    def start_server(self, name, args):
        run(['docker', 'run', '-d', '--name', name, '-p', '127.0.0.1::80', *args, *self.mounts(), self.image], capture=True)
        self.server = name
        self.containers.append((name, ''))
        self.wait_for_server()
        self.containers[-1] = (name, f'at {self.base_url}')

    def start_proxy(self):
        # the proxy is the only container that knows the host "upstream", see proxy.py
        run(['docker', 'network', 'create', self.name], capture=True)
        self.network = self.name

        name = f'{self.name}-proxy'
        run(
            [
                'docker',
                'run',
                '-d',
                '--name',
                name,
                '--network',
                self.network,
                '--network-alias',
                'proxy',
                '--entrypoint',
                'python3',
                *self.mounts(),
                self.image,
                '/TEST/proxy.py',
                '/T/upstream',
                str(PROXY_PORT),
                '/T/proxy.log',
                f'{PROXY_USER}:{PROXY_PASSWORD}',
            ],
            capture=True,
        )
        self.containers.append((name, f'as proxy:{PROXY_PORT} in network {self.network}'))

    def mounts(self):
        return [
            '--mount',
            f'type=bind,src={self.test_dir},dst=/T',
            '--mount',
            f'type=bind,src={BASE_DIR}/test,dst=/TEST,readonly',
        ]

    def docker_run_script(self, name):
        run(
            [
                'docker',
                'run',
                '--rm',
                '--entrypoint',
                'python3',
                '-e',
                'QT_QPA_PLATFORM=offscreen',
                '-e',
                'PYTHONPATH=/usr/share/qgis/python',
                '-e',
                'HOME=/tmp',
                '-e',
                'XDG_RUNTIME_DIR=/tmp/xdg',
                *self.mounts(),
                self.image,
                f'/TEST/{name}',
            ]
        )

    def wait_for_server(self):
        out = run(['docker', 'port', self.server, '80'], capture=True)
        self.base_url = 'http://' + out.strip().splitlines()[0]

        t = time.monotonic() + START_TIMEOUT
        while time.monotonic() < t:
            try:
                urllib.request.urlopen(self.base_url, timeout=5)
                return
            except urllib.error.HTTPError:
                return
            except OSError:
                time.sleep(1)

        sys.exit(f'server did not start in {START_TIMEOUT}s')

    ##

    def run_tests(self, prefix):
        for name, fn in vars(type(self)).items():
            if name.startswith(prefix):
                fn(self)

    def test_wms_getcapabilities(self):
        status, ctype, body = self.request('getcapabilities.xml', f'{WMS}&REQUEST=GetCapabilities')
        self.check('WMS GetCapabilities', status == 200 and b'<Name>poly</Name>' in body and b'<Name>points</Name>' in body, status)

    def test_wms_getmap(self):
        for fmt in ['png', 'jpeg', 'webp']:
            name = f'getmap.{fmt}'
            status, ctype, body = self.request(name, f'{WMS}&REQUEST=GetMap&LAYERS=poly,points&{BBOX}&FORMAT=image/{fmt}')
            ok = status == 200 and ctype == f'image/{fmt}'
            self.check(f'WMS GetMap {fmt}', ok, status, ctype)
            if ok:
                self.check_colors(f'WMS GetMap {fmt} polygon color', name, [(GETMAP_POLYGON, GREEN)])

    def test_wms_getfeatureinfo(self):
        status, ctype, body = self.request(
            'getfeatureinfo.txt',
            f'{WMS}&REQUEST=GetFeatureInfo&LAYERS=poly&QUERY_LAYERS=poly&{BBOX}&I=200&J=200&INFO_FORMAT=text/plain',
        )
        self.check('WMS GetFeatureInfo', status == 200 and b"name = 'Area'" in body, status)

    def test_wfs_getfeature(self):
        status, ctype, body = self.request('getfeature.xml', 'SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&TYPENAME=points')
        count = len(re.findall(rb'<gml:featureMember', body))
        self.check('WFS GetFeature', status == 200 and count == 4, status, f'{count} features')

    def test_wms_getprint_png(self):
        status, ctype, body = self.request('getprint.png', f'{PRINT_PARAMS}&FORMAT=png&DPI=96')
        ok = status == 200 and ctype == 'image/png'
        self.check('WMS GetPrint png', ok, status, ctype)
        if ok:
            self.check_colors('WMS GetPrint png map', 'getprint.png', [(PRINT_MAP, GREEN)])
            self.check_colors('WMS GetPrint png svg picture', 'getprint.png', [(PRINT_SVG, BLUE)])
            self.check_colors('WMS GetPrint png html frame', 'getprint.png', [(PRINT_HTML_FRAME, RED)])
            self.check_colors('WMS GetPrint png html label', 'getprint.png', [(PRINT_HTML_LABEL, YELLOW)])

    def test_wms_getprint_pdf(self):
        status, ctype, body = self.request('getprint.pdf', f'{PRINT_PARAMS}&FORMAT=pdf')
        self.check('WMS GetPrint pdf', status == 200 and ctype == 'application/pdf' and body.startswith(b'%PDF'), status, ctype)

    def proxy_test_gdal(self):
        # gdal does not use the qgis proxy settings, but the proxy env vars (via curl)
        name = 'proxy-getmap.png'
        status, ctype, body = self.request(name, f'{WMS}&REQUEST=GetMap&LAYERS=raster&{BBOX}&FORMAT=image/png', 'proxy.qgs')
        ok = status == 200 and ctype == 'image/png'
        self.check('proxy: GDAL raster GetMap', ok, status, ctype)
        if ok:
            self.check_colors('proxy: GDAL raster color', name, [(GETMAP_POLYGON, MAGENTA)])
        self.check('proxy: GDAL request in proxy log', self.proxy_log_has('/raster.tif'))

    def proxy_test_qgis_network(self):
        # the layout picture is loaded by the qgis network manager, which uses the [proxy] section of QGIS3.ini
        # remote svgs are fetched asynchronously, so the first request of a worker renders a placeholder (known issue, see README),
        # the second request (same worker, QGIS_WORKERS=1) uses the cached svg
        for n, title in [(1, 'first request'), (2, 'second request')]:
            name = f'proxy-getprint-{n}.png'
            status, ctype, body = self.request(name, f'{WMS}&REQUEST=GetPrint&TEMPLATE=proxy&CRS=EPSG:4326&FORMAT=png&DPI=96', 'proxy.qgs')
            ok = status == 200 and ctype == 'image/png'
            self.check(f'proxy: QGIS GetPrint, {title}', ok, status, ctype)
            if ok:
                self.check_colors(f'proxy: QGIS svg picture, {title}', name, [(PRINT_SVG, BLUE)])
        self.check('proxy: QGIS request in proxy log', self.proxy_log_has('/blue.svg'))

    ##

    def request(self, name, query, project='test.qgs'):
        url = f'{self.base_url}/?MAP=/T/{project}&{query}'
        try:
            res = urllib.request.urlopen(url, timeout=60)
            status, ctype, body = res.status, res.headers.get('content-type', ''), res.read()
        except urllib.error.HTTPError as exc:
            status, ctype, body = exc.code, exc.headers.get('content-type', ''), exc.read()
        with open(f'{self.test_dir}/{name}', 'wb') as fp:
            fp.write(body)
        return status, ctype.split(';')[0].strip(), body

    def check_colors(self, title, name, expected):
        cmd = ['docker', 'exec', '-e', 'QT_QPA_PLATFORM=offscreen', '-e', 'XDG_RUNTIME_DIR=/tmp/xdg-test', self.server, 'python3', '/TEST/pixels.py', f'/T/{name}']
        for (rx, ry), _ in expected:
            cmd.append(f'{rx},{ry}')
        out = run(cmd, capture=True)

        ok = True
        details = []
        for ln, (_, color) in zip(out.strip().splitlines(), expected):
            got = tuple(int(s) for s in ln.split())
            details.append(f'{got} expected {color}')
            for a, b in zip(got, color):
                if abs(a - b) > COLOR_TOLERANCE:
                    ok = False
        self.check(title, ok, *details)

    def proxy_log_has(self, path):
        try:
            with open(f'{self.test_dir}/proxy.log') as fp:
                lines = fp.read().splitlines()
        except FileNotFoundError:
            return False
        for ln in lines:
            status, method, url = ln.split(' ', 2)
            if status in ('200', '206') and urllib.parse.urlsplit(url).path == path:
                return True
        return False

    def check(self, title, ok, *details):
        if ok:
            self.passed += 1
            print(f'  ok    {title}')
        else:
            self.failed += 1
            print(f'  FAIL  {title}: {" ".join(str(d) for d in details)}')


###


def run(cmd, capture=False):
    res = subprocess.run(cmd, check=False, stdout=subprocess.PIPE if capture else None, text=True)
    if res.returncode:
        sys.exit(f'COMMAND FAILED, code {res.returncode}: {" ".join(cmd)}')
    return res.stdout


def main():
    p = argparse.ArgumentParser(prog='test-helper.py', description='Smoke test for a QGIS server image.')
    p.add_argument('version', help='QGIS version like 3.44.15')
    p.add_argument('-arm', action='store_true', help='test the arm64 image (default amd64)')
    p.add_argument('-debug', action='store_true', help='test the debug image')
    p.add_argument('-keep', action='store_true', help='keep the container running after the test')
    sys.exit(Tester(p.parse_args()).main())


if __name__ == '__main__':
    main()
