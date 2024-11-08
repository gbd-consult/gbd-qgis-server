#!/usr/bin/env bash

echo "BUILDING {PACKAGE}"

set -x

mkdir -p {INSTALL_DIR}

# configure

cd {BUILD_DIR}
cmake -GNinja \
    -DCMAKE_BUILD_TYPE={BUILD_TYPE} \
    -DCMAKE_INSTALL_PREFIX={INSTALL_DIR} \
{CMAKE_VARS}
..

test $? -eq 0 || exit

# make

cd {BUILD_DIR}
ninja

test $? -eq 0 || exit

# make install

cd {BUILD_DIR}
ninja install

test $? -eq 0 || exit

# copy the install into the package dir

cd {BUILD_DIR}

rm -fr {PACKAGE}
mkdir -p {PACKAGE}/usr

mv {INSTALL_DIR}/* {PACKAGE}/usr

cp ../COPYING {PACKAGE}/usr/lib

# delete lib symlinks

find {PACKAGE} -type l -exec rm -fr {} \;

# delete python caches

find {PACKAGE} -depth -name __pycache__ -exec rm -fr {} \;

# remove unneeded stuff

rm -fr {PACKAGE}/usr/include
rm -fr {PACKAGE}/usr/man

# copy to the docker build dir

rm -fr {OUT_DIR}/{PACKAGE}
cp -vr {PACKAGE} {OUT_DIR}

set +x

echo "CREATED {OUT_DIR}/{PACKAGE}"
