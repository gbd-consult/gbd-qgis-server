#!/usr/bin/env bash

CMD=$1
shift
VERSION=$1
shift

ARCH=amd64
MODE=release

for OPT in "$@"; do
    case $OPT in
    -arm) ARCH=arm64 ;;
    -debug) MODE=debug ;;
    -latest|-keep) ;;
    *) echo "unknown option: $OPT"; exit 1 ;;
    esac
done

if [ -z "$CMD" ] || [ -z "$VERSION" ]; then
    echo "
    Usage:

        make.sh clone   <version>                            = clone the QGIS repo
        make.sh docker  <version> [-arm]                     = build the build image
        make.sh bash    <version> [-arm]                     = shell to a build container
        make.sh vars    <version> [-arm]                     = print cmake vars
        make.sh compile <version> [-arm] [-debug]            = build the QGIS package
        make.sh image   <version> [-arm] [-debug] [-latest]  = build the docker image
        make.sh test    <version> [-arm] [-debug] [-keep]    = test the docker image

    version
        QGIS version like 3.44.15

    options
        -arm        = build for arm64 (default amd64)
        -debug      = debug build (default release)
        -latest     = tag the image with the major version (e.g. "3.44")
        -keep       = keep the test container running
    "
    exit
fi

##

BUILD_IMAGE=qgis-build-$VERSION-$ARCH

case $VERSION in
3.*)
    BUILD_DOCKERFILE=qgis3-qt5-build-deps.dockerfile
    CMAKE_VARS=cmake-vars-3.json
    ;;
*)
    BUILD_DOCKERFILE=qgis3-ubuntu-qt6-build-deps.dockerfile
    CMAKE_VARS=cmake-vars-4.json
    ;;
esac

BASE_DIR=$(dirname $(realpath $BASH_SOURCE))
BUILD_DIR=/opt/gbd/gbd-qgis-server-build

mkdir -p $BUILD_DIR/src
mkdir -p $BUILD_DIR/out

SRC_DIR=$BUILD_DIR/src/$VERSION
OUT_DIR=$BUILD_DIR/out

set -e

##

run_container() {
    TTY=-i
    if [ -t 0 ] && [ -t 1 ]; then
        TTY=-it
    fi
    docker run \
        $TTY \
        --rm \
        --mount type=bind,src=$SRC_DIR,dst=/SRC \
        --mount type=bind,src=$OUT_DIR,dst=/OUT \
        --mount type=bind,src=$BASE_DIR,dst=/BASE_DIR \
        $BUILD_IMAGE \
        "$@"
}

prepare_build_in_container() {
    cd /
    # copy sources to the container to speed up things...
    rm -fr /QGIS && cp -r /SRC/QGIS / && mkdir /QGIS/_BUILD
    git config --global --add safe.directory /QGIS
}

##

case $CMD in

clone)
    rm -fr $SRC_DIR && mkdir -p $SRC_DIR
    cd $SRC_DIR

    BRANCH=${VERSION//./_}
    git clone --depth 1 --branch final-$BRANCH https://github.com/qgis/QGIS
    ;;

docker)

    cd $BASE_DIR/compile

    docker rmi -f $BUILD_IMAGE

    docker build \
        --progress=plain \
        -f $BUILD_DOCKERFILE \
        --platform=linux/$ARCH \
        -t $BUILD_IMAGE \
        .
    ;;

bash)
    run_container bash
    ;;

vars)
    run_container /BASE_DIR/make.sh vars-in-container $VERSION "$@"
    ;;

compile)
    run_container /BASE_DIR/make.sh compile-in-container $VERSION "$@"
    ;;

vars-in-container)
    prepare_build_in_container
    cd /QGIS/_BUILD
    CMAKE_OPTS="-DWITH_PDAL=NO -DWITH_SERVER=YES -DWITH_INTERNAL_SPATIALINDEX=YES"
    RC=0
    cmake $CMAKE_OPTS -LH .. > /tmp/vars 2> /tmp/vars.err || RC=$?
    if [ $RC -ne 0 ]; then
        echo "ERROR"
        cat /tmp/vars /tmp/vars.err
        exit 1
    fi
    python3 /BASE_DIR/compile/compile-helper.py print_vars /BASE_DIR/compile/$CMAKE_VARS /tmp/vars "$CMAKE_OPTS" > /tmp/cmake-vars.json
    cp /tmp/cmake-vars.json /BASE_DIR/compile/$CMAKE_VARS
    ;;

compile-in-container)
    if [ "$MODE" = release ]; then
        BUILD_TYPE=Release
        INSTALL_TARGET=install/strip
    else
        BUILD_TYPE=Debug
        INSTALL_TARGET=install
    fi

    # copy sources, write cmake cache with our overrides from $CMAKE_VARS
    prepare_build_in_container
    python3 /BASE_DIR/compile/compile-helper.py cmake_cache /BASE_DIR/compile/$CMAKE_VARS > /QGIS/_BUILD/gbd_cache.cmake

    # configure and compile, link with lld, faster than the default GNU ld
    cd /QGIS/_BUILD
    cmake -GNinja \
        -C gbd_cache.cmake \
        -DCMAKE_BUILD_TYPE=$BUILD_TYPE \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld \
        -DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=lld \
        -DCMAKE_MODULE_LINKER_FLAGS=-fuse-ld=lld \
        ..
    ninja

    # install into the package dir
    PACKAGE_DIR=/OUT/compiled-qgis-server-$VERSION-$ARCH-$MODE
    rm -fr $PACKAGE_DIR
    DESTDIR=$PACKAGE_DIR ninja $INSTALL_TARGET

    # clean up the package
    cp /QGIS/COPYING $PACKAGE_DIR/usr/lib
    find $PACKAGE_DIR -type l -delete
    find $PACKAGE_DIR -depth -name __pycache__ -exec rm -fr {} +
    rm -fr $PACKAGE_DIR/usr/include $PACKAGE_DIR/usr/man

    # list apt packages required by the compiled binaries
    python3 /BASE_DIR/compile/compile-helper.py shlibdeps $PACKAGE_DIR > $PACKAGE_DIR/shlibdeps.lst

    echo "CREATED $PACKAGE_DIR"
    ;;

image)
    exec python3 $BASE_DIR/docker/image-helper.py $VERSION "$@"
    ;;

test)
    exec python3 $BASE_DIR/test/test-helper.py $VERSION "$@"
    ;;

*)
    echo "unknown command: $CMD"
    exit 1
    ;;
esac
