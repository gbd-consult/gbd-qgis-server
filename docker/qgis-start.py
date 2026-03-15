"""Prepare the qgis server, uwsgi and nginx."""

import grp
import os
import pwd
import urllib.parse

_ = os.system

GWS_UID = int(os.getenv('GWS_UID', '1000'))
GWS_GID = int(os.getenv('GWS_GID', '1000'))

try:
    GROUP_NAME = grp.getgrgid(GWS_GID).gr_name
except KeyError:
    GROUP_NAME = f'group_{GWS_GID}'
    _(f'groupadd -g {GWS_GID} {GROUP_NAME}')

try:
    USER_NAME = pwd.getpwuid(GWS_UID).pw_name
except KeyError:
    USER_NAME = f'user_{GWS_UID}'
    _(f'useradd -M -u {GWS_UID} -g {GWS_GID} {USER_NAME}')

HTTP_PROXY = os.getenv('HTTPS_PROXY') or os.getenv('HTTP_PROXY') or ''
PGSERVICEFILE = os.getenv('PGSERVICEFILE', '')
QGIS_DEBUG = os.getenv('QGIS_DEBUG', '0')
QGIS_WORKERS = os.getenv('QGIS_WORKERS', '1')
SVG_PATHS = os.getenv('SVG_PATHS', '')
TIMEOUT = os.getenv('TIMEOUT', '60')


def write(p, s):
    with open(p, 'wt') as fp:
        s = '\n'.join(line.strip() for line in s.strip().splitlines())
        fp.write(s.strip() + '\n')
    _(f'chmod 666 {p}')


# prepare the qgis environment
# ------------------------------------------------------------------------------------


def _from_env(default):
    return lambda k: os.getenv(k, default)


class QgisEnv:
    QGIS_PREFIX_PATH = '/usr'

    GDAL_DEFAULT_WMS_CACHE_PATH = '/qgis/cache/gdal'
    GDAL_FIX_ESRI_WKT = 'GEOGCS'

    QGIS_OPTIONS_PATH = '/qgis/profiles/default'
    QGIS_GLOBAL_SETTINGS_FILE = '/qgis/profiles/default/QGIS/QGIS3.ini'
    QGIS_CUSTOM_CONFIG_PATH = '/qgis'

    # qgis server:
    # https://docs.qgis.org/3.28/en/docs/server_manual/config.html

    QGIS_PLUGINPATH = ''
    QGIS_PROJECT_FILE = ''

    QGIS_SERVER_ALLOWED_EXTRA_SQL_TOKENS = _from_env('')
    QGIS_SERVER_API_RESOURCES_DIRECTORY = ''
    QGIS_SERVER_API_WFS3_MAX_LIMIT = ''
    QGIS_SERVER_CACHE_DIRECTORY = '/qgis/cache/server'
    QGIS_SERVER_CACHE_SIZE = _from_env('10000000')
    QGIS_SERVER_DISABLE_GETPRINT = ''
    QGIS_SERVER_FORCE_READONLY_LAYERS = _from_env('1')
    QGIS_SERVER_IGNORE_BAD_LAYERS = _from_env('1')
    QGIS_SERVER_LANDING_PAGE_PREFIX = ''
    QGIS_SERVER_LANDING_PAGE_PROJECTS_DIRECTORIES = ''
    QGIS_SERVER_LANDING_PAGE_PROJECTS_PG_CONNECTIONS = ''
    QGIS_SERVER_LOG_FILE = ''
    QGIS_SERVER_LOG_LEVEL = 2
    QGIS_SERVER_LOG_PROFILE = 0
    QGIS_SERVER_LOG_STDERR = 0
    QGIS_SERVER_MAX_THREADS = 0
    QGIS_SERVER_OVERRIDE_SYSTEM_LOCALE = _from_env('')
    QGIS_SERVER_PARALLEL_RENDERING = 0
    QGIS_SERVER_PROJECT_CACHE_CHECK_INTERVAL = _from_env('')
    QGIS_SERVER_PROJECT_CACHE_STRATEGY = _from_env('filesystem')
    QGIS_SERVER_SERVICE_URL = ''
    QGIS_SERVER_SHOW_GROUP_SEPARATOR = _from_env('')
    QGIS_SERVER_TRUST_LAYER_METADATA = _from_env(0)
    QGIS_SERVER_WCS_SERVICE_URL = ''
    QGIS_SERVER_WFS_SERVICE_URL = ''
    QGIS_SERVER_WMS_MAX_HEIGHT = ''
    QGIS_SERVER_WMS_MAX_WIDTH = ''
    QGIS_SERVER_WMS_SERVICE_URL = ''
    QGIS_SERVER_WMTS_SERVICE_URL = ''


qgis_env = f'export QGIS_DEBUG={QGIS_DEBUG}\n'

for key, val in vars(QgisEnv).items():
    if key.startswith('_'):
        continue
    if callable(val):
        val = val(key)
    if val:
        qgis_env += f'export {key}={val}\n'


# prepare extra fastcgi params for nginx
# ------------------------------------------------------------------------------------

# these vars must be passed as fastcgi_param because uwsgi worker-exec'd
# FastCGI processes don't reliably inherit the shell environment

EXTRA_FCGI_PARAMS = {
    'PGSERVICEFILE': PGSERVICEFILE,
}

extra_fcgi_params = '\n'.join(
    f'fastcgi_param {key} {val};'
    for key, val in EXTRA_FCGI_PARAMS.items()
    if val  # fmt:skip
)


# create qgis runtime dirs
# ------------------------------------------------------------------------------------

_('mkdir -p /qgis/profiles/default/QGIS')
_('mkdir -p /qgis/profiles/profiles/default/QGIS')
_('mkdir -p /qgis/cache/gdal')
_('mkdir -p /qgis/cache/network')
_('mkdir -p /qgis/cache/server')
_(f'chown -R {GWS_UID}:{GWS_GID} /qgis')

_('mkdir -p /vrun')
_('chmod 777 /vrun')
_(f'chown -R {GWS_UID}:{GWS_GID} /vrun')

# qgis config
# ------------------------------------------------------------------------------------

svg_paths = '/usr/share/qgis/svg,/usr/share/alkisplugin/svg'
if SVG_PATHS:
    svg_paths += ',' + SVG_PATHS

qgis_ini = rf"""
[cache]
directory=/qgis/cache/network
size=@Variant(\0\0\0\x81\0\0\0\0\0@\0\0)

[qgis]
symbolsListGroupsIndex=0

[svg]
searchPathsForSVG={svg_paths}
"""

if HTTP_PROXY:
    p = urllib.parse.urlsplit(HTTP_PROXY)
    qgis_ini += f"""
    [proxy]
    proxyEnabled=true
    proxyType=HttpProxy
    proxyHost={p.hostname}
    proxyPort={p.port}
    proxyUser={p.username}
    proxyPassword={p.password}
    """


write('/qgis/profiles/default/QGIS/QGIS3.ini', qgis_ini)
_(f'chown -R {GWS_UID}:{GWS_GID} /qgis')

# uwsgi config
# ------------------------------------------------------------------------------------

uwsgi_ini = rf"""
[uwsgi]
uid = {GWS_UID}
gid = {GWS_GID}

chmod-socket = 666
die-on-term = true
fastcgi-socket = /vrun/uwsgi.sock
harakiri = {TIMEOUT}
master = true
pidfile = /vrun/uwsgi.pid
processes = {QGIS_WORKERS}
reload-mercy = 5
threads = 0
vacuum = true
worker-exec = /usr/bin/qgis_mapserv.fcgi
worker-reload-mercy = 5

buffer-size = 65535
listen = 256
max-requests = 1000
thunder-lock = true

daemonize = /vrun/uwsgi-start.log
logger = syslog:QGIS,local6
log-master = true

# force line-buffered stderr for workers to prevent split log lines
# glibc >= 2.39 (Ubuntu 24.04+) splits unbuffered fprintf into separate write() calls
# which breaks SOCK_SEQPACKET log datagrams in uwsgi's log pipe
env = LD_PRELOAD=/usr/libexec/coreutils/libstdbuf.so
env = _STDBUF_E=L
"""

write('/uwsgi.ini', uwsgi_ini)

# nginx config
# ------------------------------------------------------------------------------------

nginx_conf = rf"""
user {USER_NAME};
worker_processes auto;
pid /vrun/nginx.pid;
daemon off;
error_log syslog:server=unix:/dev/log,nohostname,tag=NGINX warn;

events {{
    worker_connections 512;
}}

http {{
    access_log syslog:server=unix:/dev/log,nohostname,tag=NGINX;
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    client_max_body_size 16m;
    server {{
        listen 80;
        location / {{
            gzip off;
            add_header 'Access-Control-Allow-Origin' *;
            
            fastcgi_pass unix:/vrun/uwsgi.sock;
            fastcgi_read_timeout {TIMEOUT}s;
            fastcgi_buffering on;
            fastcgi_buffer_size 256k;
            fastcgi_buffers 16 256k;
            fastcgi_busy_buffers_size 512k;
            
            include /etc/nginx/fastcgi_params;
            {extra_fcgi_params}
            
            # replace mapproxy forward params (e.g. LAYERS__gws) with their real names
            
            if ($args ~* (.*?)__gws(.*)) {{
                set $args $1$2;
            }}
            if ($args ~* (.*?)__gws(.*)) {{
                set $args $1$2;
            }}
            if ($args ~* (.*?)__gws(.*)) {{
                set $args $1$2;
            }}
        }}
    }}
}}
"""

write('/nginx.conf', nginx_conf)

# rsyslogd config
# ------------------------------------------------------------------------------------

# silence some warnings unless debugging

silence = """
# 'QFont::setPointSize: Point size must be greater than 0'
:msg, contains, "QFont::setPointSizeF" stop

# 'Using QCharRef with an index pointing outside the valid range of a QString'
:msg, contains, "Using QCharRef" stop
"""

if QGIS_DEBUG != '0':
    silence = ''

rsyslogd_conf = rf"""
module(
    load="imuxsock"
    SysSock.UsePIDFromSystem="on"
)

template(name="gws" type="list") {{
    property(name="timestamp" dateFormat="mysql")
    constant(value=" ")
    property(name="syslogtag")
    property(name="msg" spifno1stsp="on" )
    property(name="msg" droplastlf="on" )
    constant(value="\\n")
}}

module(
    load="builtin:omfile" 
    Template="gws"
)

{silence}

*.* /dev/stdout
"""

write('/rsyslogd.conf', rsyslogd_conf)

# main startup script
# ------------------------------------------------------------------------------------

qgis_start_configured = f"""
#!/bin/bash

export DISPLAY=:99
export LC_ALL=C.UTF-8
export XDG_RUNTIME_DIR=/tmp/xdg

{qgis_env}

rm -fr /tmp/*

XVFB=/usr/bin/Xvfb
XVFBARGS='-dpi 96 -screen 0 1024x768x24 -ac +extension GLX +render -noreset -nolisten tcp'

until start-stop-daemon --status --exec $XVFB; do
    echo 'waiting for xvfb...'
    start-stop-daemon --start --background --exec $XVFB --oknodo -- $DISPLAY $XVFBARGS
    sleep 0.5
done

rsyslogd -i /vrun/rsyslogd.pid -f /rsyslogd.conf
uwsgi /uwsgi.ini
exec nginx -c /nginx.conf
"""

write('/qgis-start-configured', qgis_start_configured)
_('chmod 777 /qgis-start-configured')

# done
# ------------------------------------------------------------------------------------

_('/usr/bin/qgis_mapserv.fcgi  2>&1 | grep version')

print('-' * 80)
print(qgis_env)
print(extra_fcgi_params)
print('-' * 80)
