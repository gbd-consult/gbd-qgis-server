#!/usr/bin/env bash

CMD=$1
shift
VERSION=$1
shift
ARCH=$1
shift

if [ -z "$CMD" ] || [ -z "$VERSION" ]; then
    echo "
    Usage: make.sh <command> <version> [<arch>]
        commands
            - clone   = clone the QGIS repo
            - docker  = build the build image
            - bash    = shell to a build container
            - vars    = print cmake vars
            - release = build the release package
            - debug   = build the debug package
        version
            QGIS version like 3.38.1
        arch
            amd64 (default) or arm64
    "
    exit
fi

if [ -z "$ARCH" ] ; then
    ARCH=amd64
fi

##

BUILD_IMAGE=qgis-build-$VERSION-$ARCH

BASE_DIR=/opt/gbd
BUILD_DIR=$BASE_DIR/gbd-qgis-server-build

mkdir -p $BUILD_DIR/src
mkdir -p $BUILD_DIR/out

SRC_DIR=$BUILD_DIR/src/$VERSION
OUT_DIR=$BUILD_DIR/out

set -e

##

run_container() {
    docker run \
        -it \
        --rm \
        --mount type=bind,src=$SRC_DIR,dst=/SRC \
        --mount type=bind,src=$OUT_DIR,dst=/OUT \
        --mount type=bind,src=$BASE_DIR/gbd-qgis-server/compile,dst=/COMPILE \
        $BUILD_IMAGE \
        "$@"
}

prepare_build_in_container() {
    cd /
    # copy sources to the container to speed up things...
    rm -fr /QGIS && cp -r /SRC/QGIS / && mkdir /QGIS/_BUILD
    git config --global --add safe.directory /QGIS
    python3 /COMPILE/make-helper.py generate_build_script $VERSION $ARCH $1 > /QGIS/_BUILD/gbd_build_script
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

    cd $BASE_DIR/gbd-qgis-server/compile

    docker rmi -f $BUILD_IMAGE

    docker build \
        -f qgis3-qt5-build-deps.dockerfile \
        --platform=linux/$ARCH \
        -t $BUILD_IMAGE \
        .
    ;;

bash)
    run_container bash
    ;;

vars)
    run_container /COMPILE/make.sh vars-in-container $VERSION $ARCH
    ;;

release)
    run_container /COMPILE/make.sh release-in-container $VERSION $ARCH
    ;;

debug)
    run_container /COMPILE/make.sh debug-in-container $VERSION $ARCH
    ;;

vars-in-container)
    prepare_build_in_container release
    cd /QGIS/_BUILD && cmake -LH .. > /tmp/vars
    python3 /COMPILE/make-helper.py print_vars /tmp/vars
    ;;

release-in-container)
    prepare_build_in_container release
    exec bash /QGIS/_BUILD/gbd_build_script
    ;;

debug-in-container)
    prepare_build_in_container debug
    exec bash /QGIS/_BUILD/gbd_build_script
    ;;

esac
