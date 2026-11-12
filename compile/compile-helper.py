"""Helper script.


helper print_vars <path-to-cmake-LH-output> [<cmake-options>]
    merge the cmake -LH output with "cmake-vars.json" and print the result as json
    <cmake-options> are the -D options passed to cmake, their values are not taken as defaults

helper cmake_cache
    print a cmake initial cache file (for "cmake -C") with the overrides ("set") from "cmake-vars.json"

helper shlibdeps <package-dir>
    print Debian packages for shared libraries the ELF files in <package-dir> are linked against (via dpkg-shlibdeps)

"""

import json
import os
import re
import subprocess
import sys
import tempfile


def print_vars(path, cmake_opts):
    rx = r'//\s*(.+)\n(\S+?):(\S+)\s*=\s*(\S+)'
    """
    Look for lines like
        // Determines whether QGIS server should be built
        WITH_SERVER:BOOL=FALSE
    """

    found = {}
    for desc, var, typ, value in re.findall(rx, read_file(path)):
        if typ.upper() == 'BOOL':
            found[var] = (value, desc)

    passed = set()
    for opt in cmake_opts.split():
        m = re.match(r'-D([^:=]+)', opt)
        if m:
            passed.add(m.group(1))

    existing = read_vars()

    added = []
    removed = []
    changed = []
    vs = {}

    for var in sorted(set(found) | set(existing)):
        e = existing.get(var)

        if var not in found:
            if 'set' in e:
                removed.append(f'{var} (kept, overridden)')
                vs[var] = e
            else:
                removed.append(var)
            continue

        cmake_value, cmake_desc = found[var]

        if e is None:
            added.append(var)
            vs[var] = {'desc': cmake_desc, 'default': cmake_value}
            continue

        default = e['default']
        if var not in passed:
            if cmake_value != default:
                changed.append(f'{var} ({default} -> {cmake_value})')
            default = cmake_value

        v = {'desc': cmake_desc or e['desc'], 'default': default}
        if 'set' in e:
            v['set'] = e['set']
        vs[var] = v

    print(json.dumps(vs, indent=4))

    for title, names in [('added', added), ('removed', removed), ('default changed', changed)]:
        for name in names:
            print(f'{title}: {name}', file=sys.stderr)


def cmake_cache():
    for var, e in sorted(read_vars().items()):
        if 'set' in e:
            print(f'set({var} {e["set"]} CACHE BOOL "")')


def shlibdeps(package_dir):
    package_dir = os.path.abspath(package_dir)

    elf_paths = []
    for dp, dn, fn in os.walk(package_dir):
        for f in fn:
            path = os.path.join(dp, f)
            if os.path.islink(path):
                continue
            with open(path, 'rb') as fp:
                if fp.read(4) == b'\x7fELF':
                    elf_paths.append(path)

    with tempfile.TemporaryDirectory() as tmp:
        os.mkdir(f'{tmp}/debian')
        with open(f'{tmp}/debian/control', 'w') as fp:
            fp.write('Source: qgis-server\n\nPackage: qgis-server\nArchitecture: any\n')
        cmd = ['dpkg-shlibdeps', '-O', '--ignore-missing-info', f'-l{package_dir}/usr/lib']
        out = subprocess.run(cmd + sorted(elf_paths), cwd=tmp, check=True, stdout=subprocess.PIPE, text=True).stdout

    m = re.search(r'^shlibs:Depends=(.*)$', out, re.MULTILINE)
    if not m:
        raise ValueError(f'no shlibs:Depends in dpkg-shlibdeps output: {out!r}')

    packages = set()
    for dep in m.group(1).split(','):
        dep = dep.split('|')[0]
        dep = re.sub(r'\(.*?\)', '', dep).strip()
        if dep:
            packages.add(dep)

    print('\n'.join(sorted(packages)))


def read_vars():
    return json.loads(read_file(os.path.dirname(__file__) + '/cmake-vars.json'))


def read_file(path):
    with open(path, 'rb') as fp:
        return fp.read().decode('utf8')


if __name__ == '__main__':

    if sys.argv[1] == 'print_vars':
        print_vars(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '')

    if sys.argv[1] == 'cmake_cache':
        cmake_cache()

    if sys.argv[1] == 'shlibdeps':
        shlibdeps(sys.argv[2])
