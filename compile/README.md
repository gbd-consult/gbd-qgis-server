### compiling qgis for gws

Prerequisites: git, docker (19+).

Assuming the following directory structure:

```
/opt/gbd/gbd-qgis-server          - directory where this repository is checked out 
/opt/gbd/gbd-qgis-server/compile  - the compilation directory, where this very README is located
/opt/gbd/qgis                     - QGIS sources directory
/opt/gbd/build                    - build directory
```

Clone the QGIS version you want to build (see https://github.com/qgis/QGIS/releases):

```
mkdir -p /opt/gbd/qgis/3.34.4 \
    && cd /opt/gbd/qgis/3.34.4 \
    && git clone --depth 1 --branch final-3_34_4 \
    https://github.com/qgis/QGIS
```

Create a docker image from the QGIS dependencies dockerfile and tag it like `qgis-build-3.34.4`:

```
cd /opt/gbd/qgis/3.34.4/QGIS/.docker \
    && sudo docker build -f qgis3-qt5-build-deps.dockerfile -t qgis-build-3.34.4 .
```

Run the docker image, mounting the `QGIS` source directory as `/QGIS_SRC`, the compilation directory as `/COMPILE` and the build directory as `/BUILD`:

```
sudo docker run \
    --mount type=bind,src=/opt/gbd/qgis/3.34.4/QGIS,dst=/QGIS_SRC \
    --mount type=bind,src=/opt/gbd/gbd-qgis-server/compile,dst=/COMPILE \
    --mount type=bind,src=/opt/gbd/build,dst=/BUILD \
    -it --rm qgis-build-3.34.4 bash
```

If you want to check the configuration variables before building, run

```
bash /COMPILE/make.sh vars
```

Review the output and update `cmake-vars.txt` accordingly.

To build the package, run 

```
bash /COMPILE/make.sh release
```

or 

```
bash /COMPILE/make.sh debug
```


This will compile QGIS and create the package tarball in the build directory. 
The tarball will be named like this (depending on the ubuntu version and the target platform):

```
gbd-qgis-server-3.34.4-22.04-amd64-release.tar.gz
```

When building docker images, copy this tarball to the context directory.
