# GBD QGIS Server

QGIS Server (https://qgis.org) Docker image for use with the GBD WebSuite (https://gbd-websuite.de).

This repository contains tools for compiling the QGIS Server from source (`/compile`), building the Docker image (`/docker`)
and testing it (`/test`).

The following directory structure is assumed:

```
/opt/gbd/gbd-qgis-server       - directory where this repository is checked out
/opt/gbd/gbd-qgis-server-build - build directory
```

All tasks are controlled by the script `make.sh`. The script must be passed a command and a QGIS version
(see https://github.com/qgis/QGIS/releases). By default, everything is built for `amd64` in release mode. Pass `-arm`
to build for `arm64` and `-debug` for a debug build. Call `make.sh` without arguments to see all commands and options.

## Compiling the QGIS Server

Prerequisites: git, Docker (19+), Python 3.

First, clone the QGIS version you want to build:

```
/opt/gbd/gbd-qgis-server/make.sh clone 3.44.15
```

Create a Docker image with QGIS build dependencies:

```
/opt/gbd/gbd-qgis-server/make.sh docker 3.44.15
```

Then, invoke the QGIS builder with `make.sh compile`:

```
/opt/gbd/gbd-qgis-server/make.sh compile 3.44.15

or

/opt/gbd/gbd-qgis-server/make.sh compile 3.44.15 -debug
```

This creates the QGIS binary package `compiled-qgis-server-{version}-{arch}-{release|debug}` in the output directory
`/opt/gbd/gbd-qgis-server-build/out`. From there it will be picked up by the Docker builder.

At the end of the compilation, `dpkg-shlibdeps` is run on the compiled binaries. The list of Ubuntu packages they
depend on is saved as `shlibdeps.lst` in the package and later installed in the Docker image.

### cmake variables

Compilation options are kept in `compile/cmake-vars.json`:

```
{
    "WITH_DESKTOP": {
        "desc": "Determines whether QGIS desktop should be built",
        "default": "TRUE",
        "set": "FALSE"
    },
    ...
}
```

Only entries with `"set"` are passed to cmake, all other variables keep their QGIS defaults. See
https://github.com/qgis/QGIS/blob/master/INSTALL.md for details on cmake variables.

When upgrading to a new QGIS version, run

```
/opt/gbd/gbd-qgis-server/make.sh vars 3.44.15
```

This configures QGIS with default settings, updates `compile/cmake-vars.json` (keeping the `"set"` overrides) and
prints added, removed and changed variables. Review the diff before compiling.

## Building the Docker Image

Invoke `make.sh image`:

```
/opt/gbd/gbd-qgis-server/make.sh image 3.44.15

or

/opt/gbd/gbd-qgis-server/make.sh image 3.44.15 -debug
```

This creates an image `gbdconsult/gbd-qgis-server-{arch}[-debug]:{version}`, e.g.

```
gbdconsult/gbd-qgis-server-amd64:3.44.15

or

gbdconsult/gbd-qgis-server-amd64-debug:3.44.15
```

Options:

| Option      | Description                                            |
|-------------|--------------------------------------------------------|
| `-latest`   | also tag the image with the major version, e.g. `3.44` |

The image installs the packages from `docker/apt.lst` and from `shlibdeps.lst` of the compiled package. `docker/apt.lst`
lists Apache, Python and packages that QGIS loads at runtime and that `dpkg-shlibdeps` cannot detect (Qt SQL drivers,
Qt image format plugins, QCA plugins, PyQt5).

SVG symbols from the ALKIS plugin (`docker/alkisplugin`) are installed in `/usr/share/alkisplugin`.

## Testing the Image

```
/opt/gbd/gbd-qgis-server/make.sh test 3.44.15 [-arm] [-debug] [-keep]
```

This creates a test project in `/opt/gbd/gbd-qgis-server-build/test`, starts a container and runs WMS GetCapabilities,
GetMap (png, jpeg, webp), GetFeatureInfo, WFS GetFeature and GetPrint (png, pdf) requests against it. Rendered images are
checked by pixel colors. Responses are kept in the test directory.

A second container is then started with `HTTP_PROXY` pointing to a test proxy (`test/proxy.py`, with basic auth) in a
separate Docker network. It checks that a remote raster (`/vsicurl/`, read by GDAL) and a remote SVG picture in a print
layout (read by the QGIS network manager) are loaded through the proxy.

The check `proxy: QGIS svg picture, first request` is expected to fail, see known issues below.

`-debug` tests the debug image, `-keep` leaves the test container running.

### Known issues

Remote images (`http://...`, `https://...` paths) in print layouts do not work: QGIS renders a placeholder instead of
the image, and the QGIS process may crash. Use local paths.

## Profiling

`profile/profile.py` measures request latency of an image in different scenarios (cold and warm project loads, hung
workers, worker recycling):

```
python3 profile/profile.py --image gbdconsult/gbd-qgis-server-amd64:3.44.15 [cold warm hung recycle]
```

## Running the Image

Internally, the image runs Apache with `mod_fcgid`, which starts QGIS server worker processes on demand. All requests
are handled on port 80. Apache and QGIS log to the container's stdout and stderr.

No process runs as root. If the container is started as root (the default), the start script creates the user
`GWS_UID:GWS_GID`, empties `/qgis` and `/vrun` and re-runs itself as that user. The container can also be started
as any user directly (`docker run -u` or `user:` in compose), then `GWS_UID` and `GWS_GID` are ignored.

### Environment

The start script reads these variables:

| Name                       | Description                                                  | Default |
|----------------------------|--------------------------------------------------------------|---------|
| GWS_UID                    | User ID to run the server (root start only)                  | 1000    |
| GWS_GID                    | Group ID to run the server (root start only)                 | 1000    |
| HTTPS_PROXY, HTTP_PROXY    | Proxy URL, written to the QGIS proxy settings                |         |
| NO_PROXY                   | Hosts to reach without the proxy, comma separated (1)        |         |
| QGIS_WORKERS               | Maximum number of QGIS server processes                      | 1       |
| SVG_PATHS                  | Additional paths to SVG symbols, comma separated             |         |
| TIMEOUT                    | Apache, worker and QGIS network request timeout, seconds     | 60      |
| `FCGID_<NAME>`             | `mod_fcgid` directives, see below                            |         |

(1) QGIS matches these as URL prefixes (`http://<host>`, `https://<host>`), so domain suffixes like `.example.com`
and IP ranges are not supported.

All container environment variables are also passed to QGIS, so QGIS server settings
(https://docs.qgis.org/3.44/en/docs/server_manual/config.html#environment-variables) and library settings
(`TZ`, `PGSERVICEFILE`, `PROJ_*`, `GDAL_*`, `CPL_*`, ...) can be set on the container directly. Values containing
newlines are skipped.

Unless set on the container, the image uses these defaults:

| Name                               | Default     |
|------------------------------------|-------------|
| QGIS_DEBUG                         | 0           |
| QGIS_SERVER_CACHE_SIZE             | 10000000    |
| QGIS_SERVER_FORCE_READONLY_LAYERS  | 1           |
| QGIS_SERVER_IGNORE_BAD_LAYERS      | 1           |
| QGIS_SERVER_LOG_LEVEL              | 2           |
| QGIS_SERVER_LOG_STDERR             | 1           |
| QGIS_SERVER_PARALLEL_RENDERING     | 0           |
| QGIS_SERVER_PROJECT_CACHE_STRATEGY | filesystem  |
| QGIS_SERVER_TRUST_LAYER_METADATA   | 1           |
| GDAL_CACHEMAX                      | 256         |
| GDAL_FIX_ESRI_WKT                  | GEOGCS      |
| GDAL_HTTP_CONNECTTIMEOUT           | 10          |
| GDAL_HTTP_TIMEOUT                  | `TIMEOUT`   |
| PGAPPNAME                          | qgis-server |
| PGCONNECT_TIMEOUT                  | 10          |

`QGIS_SERVER_LOG_LEVEL` is `0` (info, logs every request), `1` (warning) or `2` (critical).
With `QGIS_SERVER_TRUST_LAYER_METADATA=1`, layer extents and CRS are read from the project file, not from the data
source, so changed data extents only show up after the project is saved again. `GDAL_CACHEMAX` is in MB, per QGIS process.

These variables are always set by the image and cannot be overridden:

| Name                         | Value                                 |
|------------------------------|---------------------------------------|
| DISPLAY                      | :99                                   |
| LC_ALL                       | C.UTF-8                               |
| QT_QPA_PLATFORM              | offscreen                             |
| XDG_RUNTIME_DIR              | /tmp/xdg                              |
| QGIS_PREFIX_PATH             | /usr                                  |
| QGIS_CUSTOM_CONFIG_PATH      | /qgis                                 |
| QGIS_OPTIONS_PATH            | /qgis/profiles/default                |
| QGIS_GLOBAL_SETTINGS_FILE    | /qgis/profiles/default/QGIS/QGIS3.ini |
| QGIS_SERVER_CACHE_DIRECTORY  | /qgis/cache/server                    |
| GDAL_DEFAULT_WMS_CACHE_PATH  | /qgis/cache/gdal                      |
| HOME                         | /qgis                                 |
| PROJ_USER_WRITABLE_DIRECTORY | /qgis/cache/proj                      |

`mod_fcgid` directives can be set with `FCGID_<NAME>` variables, e.g. `FCGID_BUSY_SCAN_INTERVAL=5` sets
`FcgidBusyScanInterval 5` (see https://httpd.apache.org/mod/mod_fcgid.html). The image sets `FCGID_MAX_PROCESSES` and
`FCGID_MAX_PROCESSES_PER_CLASS` to `QGIS_WORKERS`, `FCGID_IO_TIMEOUT` and `FCGID_BUSY_TIMEOUT` to `TIMEOUT`,
`FCGID_MAX_REQUESTS_PER_PROCESS=1000`, `FCGID_BUSY_SCAN_INTERVAL=2` and `FCGID_MAX_REQUEST_LEN=16777216`.

The QGIS environment is also passed with every request, so a container variable named like a CGI request variable
(`SERVER_PORT`, `QUERY_STRING`, `HTTP_*` etc.) can override the request value. On Kubernetes, service links create such
variables for services named e.g. `server` (`SERVER_PORT`), set `enableServiceLinks: false` on the pod in that case.

In debug images, you can also set `QGIS_DEBUG` to a value between 0 and 5 to control the debug verbosity.
