#!/usr/bin/env bash

CMD=$1
shift
VERSION=$1
shift
ARCH=$1
shift

if [ -z "$CMD" ] || [ -z "$VERSION" ]; then
    echo "
    Usage:

        make.sh clone   <version> [arch]  = clone the QGIS repo
        make.sh docker  <version> [arch]  = build the build image
        make.sh bash    <version> [arch]  = shell to a build container
        make.sh vars    <version> [arch]  = print cmake vars
        
        make.sh compile-release <version> [arch]  = build the release package
        make.sh compile-debug   <version> [arch]  = build the debug package
        
        make.sh image-release   <version> [arch] [options]  = build the release docker image
        make.sh image-debug     <version> [arch] [options]  = build the debug docker image

    version
        QGIS version like 3.40.7

    arch
        amd64 (default) or arm64

    image options:
        -latest     = tag the image with the major version (e.g. "3.40")
        -no-cache   = disable build cache
        -prep       = prepare the build, but do not run it
    "
    exit
fi

if [ -z "$ARCH" ] ; then
    ARCH=amd64
fi

##

BUILD_IMAGE=qgis-build-$VERSION-$ARCH

BASE_DIR=$(dirname $(realpath $BASH_SOURCE))
BUILD_DIR=/opt/gbd/gbd-qgis-server-build

mkdir -p $BUILD_DIR/src
mkdir -p $BUILD_DIR/out

SRC_DIR=$BUILD_DIR/src/$VERSION
OUT_DIR=$BUILD_DIR/out

set -ex

##

run_container() {
    docker run \
        -it \
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
    python3 /BASE_DIR/compile/compile-helper.py generate_build_script $VERSION $ARCH $1 > /QGIS/_BUILD/gbd_build_script
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
        -f qgis3-qt5-build-deps.dockerfile \
        --platform=linux/$ARCH \
        -t $BUILD_IMAGE \
        .
    ;;

bash)
    run_container bash
    ;;

vars)
    run_container /BASE_DIR/make.sh vars-in-container $VERSION $ARCH
    ;;

compile-release)
    run_container /BASE_DIR/make.sh compile-release-in-container $VERSION $ARCH
    ;;

compile-debug)
    run_container /BASE_DIR/make.sh compile-debug-in-container $VERSION $ARCH
    ;;

vars-in-container)
    prepare_build_in_container release
    cd /QGIS/_BUILD && cmake -LH .. 2>/dev/null > /tmp/vars || true
    python3 /BASE_DIR/compile/compile-helper.py print_vars /tmp/vars
    ;;

compile-release-in-container)
    prepare_build_in_container release
    exec bash /QGIS/_BUILD/gbd_build_script
    ;;

compile-debug-in-container)
    prepare_build_in_container debug
    exec bash /QGIS/_BUILD/gbd_build_script
    ;;

image-release)
    exec python3 $BASE_DIR/docker/image-helper.py release $VERSION $ARCH $@
    ;;

image-debug)
    exec python3 $BASE_DIR/docker/image-helper.py debug $VERSION $ARCH $@
    ;;
esac
