"""Helper script.


helper print_vars <path-to-cmake-vars>
    print the vars table from the cmake -LH output

helper generate_script <path-to-vars.txt> <ubuntu-version> <arch>
    generate the build script from our "cmake-vars.txt"

"""

import re
import sys

QGIS_DIR = '/QGIS'
QGIS_BUILD_DIR = f'{QGIS_DIR}/_BUILD'
GBD_BUILD_DIR = '/BUILD'


def print_vars():
    rx = r'//\s*(.+)\n(\S+?):(\S+)\s*=\s*(\S+)'
    """
    Look for lines like
        // Determines whether QGIS server should be built
        WITH_SERVER:BOOL=FALSE
    """

    vs = []
    for comment, var, typ, value in re.findall(rx, read_file(sys.argv[2])):
        if typ.upper() == 'BOOL':
            vs.append(f'    {var:40s} | {value:8s} | {value:8s} | {comment}')

    print('\n'.join(sorted(vs)))


def generate_script():
    vars_path = sys.argv[2]
    ubuntu_version = sys.argv[3].strip().split().pop()
    arch = sys.argv[4]
    if arch == 'x86_64':
        arch = 'amd64'

    env = {}

    for ln in read_file(vars_path).split('\n'):
        ln = ln.strip()
        if not ln or ln.startswith('#'):
            continue
        var, value, default, _ = [s.strip() for s in ln.split('|')]
        if value != default:
            env[var] = value

    cvars = '\n'.join(
        f'-D{var}={value} \\'
        for var, value in sorted(env.items())
    )

    s = read_file(f'/{QGIS_DIR}/CMakeLists.txt')
    m = re.search(r'CPACK_PACKAGE_VERSION_MAJOR "(.+?)"', s)
    v1 = m.group(1)
    m = re.search(r'CPACK_PACKAGE_VERSION_MINOR "(.+?)"', s)
    v2 = m.group(1)
    m = re.search(r'CPACK_PACKAGE_VERSION_PATCH "(.+?)"', s)
    v3 = m.group(1)

    qgis_version = f'{v1}.{v2}.{v3}'

    script = f"""
        #!/usr/bin/env bash
        
        MODE=Release
        LMODE=release

        if [[ "$1" == "debug" ]]; then
            MODE=Debug
            LMODE=debug
        fi
        
        PACKAGE=gbd-qgis-server-{qgis_version}-{ubuntu_version}-{arch}-$LMODE
        
        echo "BUILDING $PACKAGE WITH MODE $MODE"
        
        set -x
        
        cd {QGIS_BUILD_DIR}
        
        cmake -GNinja -DCMAKE_BUILD_TYPE=$MODE \\
        {cvars}
        ..
        
        test $? -eq 0 || exit
        
        cd {QGIS_BUILD_DIR}
        
        ninja
        
        test $? -eq 0 || exit
        
        cd {QGIS_BUILD_DIR}
        
        rm -fr $PACKAGE
        rm -fr $PACKAGE.tar.gz
        
        mkdir -p $PACKAGE/usr
        
        # libraries
        cp -r output/lib $PACKAGE/usr
        
        # server
        mkdir -p $PACKAGE/usr/bin
        cp output/bin/qgis_mapserv.fcgi $PACKAGE/usr/bin
        
        # python (qgis package only)
        mkdir -p $PACKAGE/usr/lib/python3/dist-packages
        cp -r output/python/qgis $PACKAGE/usr/lib/python3/dist-packages
        
        # resources (no depth)
        mkdir -p $PACKAGE/usr/share/qgis/resources
        cp ../resources/* $PACKAGE/usr/share/qgis/resources
        cp -r ../resources/server $PACKAGE/usr/share/qgis/resources
        
        # svgs
        cp -r  ../images/svg $PACKAGE/usr/share/qgis
        
        # license
        cp ../COPYING $PACKAGE/usr/lib
            
        # delete lib symlinks
        find $PACKAGE -type l -exec rm -fr {{}} \\;
        
        # delete python caches
        find $PACKAGE -depth -name __pycache__ -exec rm -fr {{}} \\;
        
        tar -czf $PACKAGE.tar.gz $PACKAGE
        
        mv $PACKAGE.tar.gz {GBD_BUILD_DIR}
        
        set +x
        
        echo "CREATED {GBD_BUILD_DIR}/$PACKAGE.tar.gz"
    """

    for ln in script.strip().split('\n'):
        print(ln.strip())


def read_file(path):
    with open(path, 'rb') as fp:
        return fp.read().decode('utf8')


if __name__ == '__main__':

    if sys.argv[1] == 'print_vars':
        print_vars()

    if sys.argv[1] == 'generate_script':
        generate_script()
