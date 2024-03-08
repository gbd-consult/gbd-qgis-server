#!/usr/bin/env bash

HELPER=/COMPILE/helper.py

cd /
rm -fr /QGIS
cp -r /QGIS_SRC /QGIS
mkdir /QGIS/_BUILD

git config --global --add safe.directory /QGIS

if [[ "$1" = "vars" ]]; then
    cd /QGIS/_BUILD
    cmake -LH .. > _gws_cmake_vars
    python3 $HELPER print_vars /QGIS/_BUILD/_gws_cmake_vars
   exit
fi

python3 $HELPER generate_script \
    "/COMPILE/cmake-vars.txt" \
    "$(lsb_release -r)" \
    "$(uname -m)" \
    > /QGIS/_BUILD/_gws_make

exec bash /QGIS/_BUILD/_gws_make $@
