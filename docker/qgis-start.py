"""Prepare the qgis server and apache with mod_fcgid, then exec apache.

If started as root, creates the GWS_UID/GWS_GID user (default 1000/1000), empties /qgis and /vrun and re-runs itself as that user.
Otherwise (e.g. docker run -u), runs as the current user.
All container env vars are passed to qgis.
FCGID_<NAME> env vars set mod_fcgid directives, e.g. FCGID_BUSY_SCAN_INTERVAL=5 -> FcgidBusyScanInterval 5.
"""

import grp
import os
import pwd
import shutil
import subprocess
import sys
import time
import urllib.parse

HTTP_PROXY = os.getenv('HTTPS_PROXY') or os.getenv('HTTP_PROXY') or ''
QGIS_WORKERS = os.getenv('QGIS_WORKERS', '1')
SVG_PATHS = os.getenv('SVG_PATHS', '')
NO_PROXY = os.getenv('NO_PROXY') or os.getenv('no_proxy') or ''
TIMEOUT = os.getenv('TIMEOUT', '60')
REQUEST_LEN = 65535

XVFB_DISPLAY = ':99'
XVFB_BIN = '/usr/bin/Xvfb'
XVFB_ARGS = ['-dpi', '96', '-screen', '0', '1024x768x24', '-ac', '+extension', 'GLX', '+render', '-noreset', '-nolisten', 'tcp']
XVFB_WAIT = 10
USE_XVFB = os.getenv('USE_XVFB', '0') == '1'

HOME_DIR = '/qgis'
RUN_DIR = '/vrun'

QGIS_INI_PATH = f'{HOME_DIR}/profiles/default/QGIS/QGIS3.ini'
APACHE_CONF_PATH = f'{RUN_DIR}/apache.conf'

QGIS_ENV_FIXED = {
    'DISPLAY': XVFB_DISPLAY,
    'QT_QPA_PLATFORM': 'offscreen',
    'LC_ALL': 'C.UTF-8',
    'XDG_RUNTIME_DIR': '/tmp/xdg',
    'QGIS_PREFIX_PATH': '/usr',
    'GDAL_DEFAULT_WMS_CACHE_PATH': f'{HOME_DIR}/cache/gdal',
    'QGIS_OPTIONS_PATH': f'{HOME_DIR}/profiles/default',
    'QGIS_GLOBAL_SETTINGS_FILE': QGIS_INI_PATH,
    'QGIS_CUSTOM_CONFIG_PATH': HOME_DIR,
    'QGIS_SERVER_CACHE_DIRECTORY': f'{HOME_DIR}/cache/server',
    'HOME': HOME_DIR,
    'PROJ_USER_WRITABLE_DIRECTORY': f'{HOME_DIR}/cache/proj',
}

# qgis server:
# https://docs.qgis.org/3.44/en/docs/server_manual/config.html

QGIS_ENV_DEFAULTS = {
    'QGIS_DEBUG': '0',
    'GDAL_CACHEMAX': '256',
    'GDAL_FIX_ESRI_WKT': 'GEOGCS',
    'GDAL_HTTP_CONNECTTIMEOUT': '10',
    'GDAL_HTTP_TIMEOUT': TIMEOUT,
    'PGAPPNAME': 'qgis-server',
    'PGCONNECT_TIMEOUT': '10',
    'QGIS_SERVER_CACHE_SIZE': '10000000',
    'QGIS_SERVER_FORCE_READONLY_LAYERS': '1',
    'QGIS_SERVER_IGNORE_BAD_LAYERS': '1',
    'QGIS_SERVER_LOG_LEVEL': '2',
    'QGIS_SERVER_LOG_STDERR': '1',
    'QGIS_SERVER_PARALLEL_RENDERING': '0',
    'QGIS_SERVER_PROJECT_CACHE_STRATEGY': 'filesystem',
    'QGIS_SERVER_TRUST_LAYER_METADATA': '1',
}

FCGID_DEFAULTS = {
    'FCGID_MAX_PROCESSES': QGIS_WORKERS,
    'FCGID_MAX_PROCESSES_PER_CLASS': QGIS_WORKERS,
    'FCGID_MAX_REQUESTS_PER_PROCESS': '1000',
    'FCGID_IO_TIMEOUT': TIMEOUT,
    'FCGID_BUSY_TIMEOUT': TIMEOUT,
    'FCGID_BUSY_SCAN_INTERVAL': '2',
    'FCGID_MAX_REQUEST_LEN': str(REQUEST_LEN),
}


def main():
    # started as root: create the user, drop privileges and re-run this script as that user,
    # so that /proc/self and the stdout/stderr pipes belong to it (apache reopens /proc/self/fd/1,2 for logging)
    if os.getuid() == 0:
        uid = int(os.getenv('GWS_UID', '1000'))
        gid = int(os.getenv('GWS_GID', '1000'))
        prepare_user(uid, gid)
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])

    # from here on, running as GWS_UID (if started as root) or as the user set with docker run -u

    create_work_dirs()

    qgis_ini = get_qgis_ini()
    write(QGIS_INI_PATH, qgis_ini, 0o600)

    qgis_env = get_qgis_env()
    fcgid_opts = get_fcgid_opts()
    apache_conf = get_apache_conf(qgis_env, fcgid_opts)
    write(APACHE_CONF_PATH, apache_conf)

    subprocess.run(['/usr/bin/qgis_mapserv.fcgi', '--version'], stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    empty_dir('/tmp', ignore_errors=True)

    if USE_XVFB:
        start_xvfb()

    os.execv('/usr/sbin/apache2', ['/usr/sbin/apache2', '-f', APACHE_CONF_PATH, '-D', 'FOREGROUND'])


def prepare_user(uid, gid):
    # apache logs to /proc/self/fd/1 and 2, to reopen them as the user, the stdout/stderr pipes must belong to the user
    # and the process must be re-run (after setuid alone, /proc/self belongs to root)

    try:
        grp.getgrgid(gid)
    except KeyError:
        subprocess.run(['groupadd', '-g', str(gid), f'group_{gid}'], check=True)

    try:
        pwd.getpwuid(uid)
    except KeyError:
        subprocess.run(['useradd', '-M', '-u', str(uid), '-g', str(gid), f'user_{uid}'], check=True)

    # leftovers from a previous run may belong to another user
    empty_dir(HOME_DIR)
    empty_dir(RUN_DIR)

    for fd in [1, 2]:
        os.fchown(fd, uid, gid)

    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    os.environ['HOME'] = HOME_DIR


def create_work_dirs():
    # remove stale pid file, sockets etc. from a previous run
    empty_dir(RUN_DIR)

    for d in [
        f'{HOME_DIR}/profiles/default/QGIS',
        f'{HOME_DIR}/cache/gdal',
        f'{HOME_DIR}/cache/network',
        f'{HOME_DIR}/cache/proj',
        f'{HOME_DIR}/cache/server',
        f'{RUN_DIR}/apache',
        f'{RUN_DIR}/fcgid',
    ]:
        os.makedirs(d, exist_ok=True)


def get_qgis_ini():
    svg_paths = '/usr/share/qgis/svg,/usr/share/alkisplugin/svg'
    if SVG_PATHS:
        svg_paths += ',' + SVG_PATHS

    qgis_ini = f"""
        [cache]
        directory={HOME_DIR}/cache/network

        [network]
        network-timeout={int(TIMEOUT) * 1000}

        [svg]
        searchPathsForSVG={svg_paths}
    """

    if HTTP_PROXY:
        qgis_ini += get_qgis_ini_proxy()

    return qgis_ini


def get_qgis_ini_proxy():
    p = urllib.parse.urlsplit(HTTP_PROXY)
    cfg = f"""
        [proxy]
        proxyEnabled=true
        proxyType=HttpProxy
        proxyHost={p.hostname}
    """
    if p.port:
        cfg += f'proxyPort={p.port}\n'
    if p.username:
        cfg += f'proxyUser={urllib.parse.unquote(p.username)}\n'
    if p.password:
        cfg += f'proxyPassword={urllib.parse.unquote(p.password)}\n'

    # qgis matches noProxyUrls as url prefixes, so only plain host names from NO_PROXY work
    no_proxy_urls = []
    for host in NO_PROXY.split(','):
        host = host.strip()
        if host:
            no_proxy_urls.append(f'http://{host}')
            no_proxy_urls.append(f'https://{host}')
    if no_proxy_urls:
        cfg += f'noProxyUrls={",".join(no_proxy_urls)}\n'

    return cfg


def get_qgis_env():
    qgis_env = dict(QGIS_ENV_DEFAULTS)

    for key, val in os.environ.items():
        if key in QGIS_ENV_FIXED or '\n' in val:
            continue
        qgis_env[key] = val

    qgis_env.update(QGIS_ENV_FIXED)
    return qgis_env


def get_fcgid_opts():
    env = dict(FCGID_DEFAULTS)

    for key, val in os.environ.items():
        if key.startswith('FCGID_'):
            env[key] = val

    fcgid_opts = {}
    for key, val in env.items():
        name = ''.join(w.capitalize() for w in key.split('_'))
        fcgid_opts[name] = val

    return fcgid_opts


def get_apache_conf(qgis_env, fcgid_opts):
    # libfcgi replaces the process environment with request params on each request,
    # so the qgis env is passed both as the initial process env and as request params

    env_conf = ''
    for key, val in sorted(qgis_env.items()):
        env_conf += f'FcgidInitialEnv {key} {apache_quote(val)}\n'
    for key, val in sorted(qgis_env.items()):
        env_conf += f'SetEnv {key} {apache_quote(val)}\n'

    fcgid_conf = ''
    for key, val in sorted(fcgid_opts.items()):
        fcgid_conf += f'{key} {val}\n'

    return rf"""
        ServerRoot /etc/apache2
        ServerName localhost
        DefaultRuntimeDir {RUN_DIR}/apache
        PidFile {RUN_DIR}/apache/apache.pid
        Listen 80

        LoadModule mpm_event_module /usr/lib/apache2/modules/mod_mpm_event.so
        LoadModule authz_core_module /usr/lib/apache2/modules/mod_authz_core.so
        LoadModule alias_module /usr/lib/apache2/modules/mod_alias.so
        LoadModule rewrite_module /usr/lib/apache2/modules/mod_rewrite.so
        LoadModule headers_module /usr/lib/apache2/modules/mod_headers.so
        LoadModule env_module /usr/lib/apache2/modules/mod_env.so
        LoadModule fcgid_module /usr/lib/apache2/modules/mod_fcgid.so

        ErrorLog /proc/self/fd/2
        LogLevel warn
        LogFormat "%h %l %u %t \"%r\" %>s %b \"%{{Referer}}i\" \"%{{User-Agent}}i\"" combined
        CustomLog /proc/self/fd/1 combined

        Timeout {TIMEOUT}
        LimitRequestLine {REQUEST_LEN}
        LimitRequestFieldSize {REQUEST_LEN}

        FcgidIPCDir {RUN_DIR}/fcgid
        FcgidProcessTableFile {RUN_DIR}/fcgid/shm

        {fcgid_conf}
        {env_conf}

        <Directory />
            AllowOverride None
            Require all denied
        </Directory>

        <Directory /usr/bin>
            <Files qgis_mapserv.fcgi>
                SetHandler fcgid-script
                Options +ExecCGI
                Require all granted
            </Files>
        </Directory>

        Alias /qgis_mapserv.fcgi /usr/bin/qgis_mapserv.fcgi

        RewriteEngine On
        RewriteRule ^ /qgis_mapserv.fcgi [PT,L]

        Header set Access-Control-Allow-Origin "*"
    """


def start_xvfb():
    subprocess.Popen([XVFB_BIN, XVFB_DISPLAY, *XVFB_ARGS], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    xvfb_socket = '/tmp/.X11-unix/X' + XVFB_DISPLAY.lstrip(':')
    t = time.monotonic() + XVFB_WAIT
    while not os.path.exists(xvfb_socket):
        if time.monotonic() > t:
            sys.exit(f'Xvfb did not start in {XVFB_WAIT}s')
        time.sleep(0.2)


def write(path, text, mode=0o644):
    with open(path, 'wt') as fp:
        text = '\n'.join(line.strip() for line in text.strip().splitlines())
        fp.write(text.strip() + '\n')
    os.chmod(path, mode)


def empty_dir(path, ignore_errors=False):
    for name in os.listdir(path):
        p = os.path.join(path, name)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=ignore_errors)
            continue
        try:
            os.unlink(p)
        except OSError:
            if not ignore_errors:
                raise


def apache_quote(val):
    return '"' + str(val).replace('"', '\\"') + '"'


if __name__ == '__main__':
    main()
