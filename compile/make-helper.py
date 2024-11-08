"""Helper script.


helper print_vars <path-to-cmake-vars>
    print the vars table from the cmake -LH output

helper generate_script <path-to-vars.txt> <ubuntu-version> <arch>
    generate the build script from our "cmake-vars.txt"

"""

import re
import sys
import os


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


def generate_script(
        qgis_version,
        arch,
        build_type,
):
    template_args = {
        'BUILD_DIR': '/QGIS/_BUILD',
        'INSTALL_DIR': '/QGIS/_BUILD/INSTALL',
        'OUT_DIR': '/OUT',
    }

    d = {}

    for ln in read_file('/COMPILE/cmake-vars.txt').split('\n'):
        ln = ln.strip()
        if not ln or ln.startswith('#'):
            continue
        var, value, default, _ = [s.strip() for s in ln.split('|')]
        if var.startswith('*'):
            d[var[1:]] = value

    template_args['CMAKE_VARS'] = '\n'.join(
        f'-D{var}={value} \\'
        for var, value in sorted(d.items())
    )

    s = read_file(f'/QGIS/CMakeLists.txt')
    m = re.search(r'CPACK_PACKAGE_VERSION_MAJOR "(.+?)"', s)
    v1 = m.group(1)
    m = re.search(r'CPACK_PACKAGE_VERSION_MINOR "(.+?)"', s)
    v2 = m.group(1)
    m = re.search(r'CPACK_PACKAGE_VERSION_PATCH "(.+?)"', s)
    v3 = m.group(1)
    qgis_version = f'{v1}.{v2}.{v3}'

    template_args['PACKAGE'] = f'compiled-qgis-server-{qgis_version}-{arch}-{build_type.lower()}'
    template_args['BUILD_TYPE'] = build_type.title()

    script = read_file(os.path.dirname(__file__) + f'/build_script.template.sh')
    script = re.sub(r'\{([A-Z_]+)\}', lambda m: template_args[m.group(1)], script)
    print(script)


def read_file(path):
    with open(path, 'rb') as fp:
        return fp.read().decode('utf8')


if __name__ == '__main__':

    if sys.argv[1] == 'print_vars':
        print_vars()

    if sys.argv[1] == 'generate_build_script':
        generate_script(
            qgis_version=sys.argv[2],
            arch=sys.argv[3],
            build_type=sys.argv[4],
        )
