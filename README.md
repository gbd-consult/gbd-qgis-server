# GBD QGIS Server

QGIS Server (https://qgis.org) Docker image for use with the GBD WebSuite (https://gbd-websuite.de).

This repository contains tools for compiling the QGIS Server from source (`/compile`) and building the Docker image (
`/docker`).

The following directory structure is assumed:

```
/opt/gbd/gbd-qgis-server       - directory where this repository is checked out 
/opt/gbd/gbd-qgis-server-build - build directory
```

## Compiling the QGIS Server

Prerequisites: git, Docker (19+).

All compilation is controlled by the script `compile/make.sh`. The script must be passed a
command, a QGIS version (see https://github.com/qgis/QGIS/releases) and the architecture: `amd64` (default) or `arm64`.

First, clone the QGIS version you want to build:

```
/opt/gbd/gbd-qgis-server/compile/make.sh clone 3.38.1
```

Create a Docker image with QGIS dependencies:

```
/opt/gbd/gbd-qgis-server/compile/make.sh docker 3.38.1
```

Then, invoke the QGIS builder with `make.sh release` or `make.sh debug`:

```
/opt/gbd/gbd-qgis-server/compile/make.sh release 3.38.1
```

This creates the QGIS binary package in the output directory `/opt/gbd/gbd-qgis-server-build/out`. From there it will be
picked up by the Docker builder.

To change compilation defaults, edit `compile/cmake-vars.txt` before running 'release'. See
https://github.com/qgis/QGIS/blob/master/INSTALL.md for details on cmake variables.

## Building the Docker Image

Invoke `/docker/image.py`, passing the QGIS version:

```
python3 /opt/gbd/gbd-qgis-server/docker/image.py 3.38.1
```

By default, this creates an image `gbdconsult/gbd-qgis-server-{arch}:{version}`, e.g.

```
gbdconsult/gbd-qgis-server-amd64:3.38.1
or
gbdconsult/gbd-qgis-server-amd64-debug:3.38.1
```

To fine-tune the build, call `image.py -h` to see available options.

## Running the image

Internally, the image runs a front-end Nginx server and, by default, 4 QGIS server workers. All requests are handled on
port 80.

The image can be configured with these environment variables:

| Name          | Description                                      |
|---------------|--------------------------------------------------|
| GWS_GID       | User ID to run the server                        |
| GWS_UID       | Group ID to run the server                       |
| HTTP_PROXY    | Proxy address                                    |
| PGSERVICEFILE | Path to `pg_service.conf`                        |
| QGIS_DEBUG    | QGIS debug level (0-20)                          |
| QGIS_WORKERS  | Number of server processes to run                |
| SVG_PATHS     | Additional paths to SVG symbols, comma separated |
| TIMEOUT       | Worker communication timeout, seconds            |

Additionally, these QGIS enviroment variables are supported (
see https://docs.qgis.org/3.34/en/docs/server_manual/config.html#environment-variables for explanations):

- `QGIS_SERVER_ALLOWED_EXTRA_SQL_TOKENS`
- `QGIS_SERVER_CACHE_SIZE`
- `QGIS_SERVER_FORCE_READONLY_LAYERS`
- `QGIS_SERVER_IGNORE_BAD_LAYERS`
- `QGIS_SERVER_LOG_LEVEL`
- `QGIS_SERVER_LOG_PROFILE`
- `QGIS_SERVER_OVERRIDE_SYSTEM_LOCALE`
- `QGIS_SERVER_PROJECT_CACHE_CHECK_INTERVAL`
- `QGIS_SERVER_PROJECT_CACHE_STRATEGY`
- `QGIS_SERVER_SHOW_GROUP_SEPARATOR`
- `QGIS_SERVER_TRUST_LAYER_METADATA`
