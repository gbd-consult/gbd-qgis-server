"""Docker builder for QGIS images.


helper release <version> [<arch>] [options]
helper debug   <version> [<arch>] [options]

options:
    -latest   - tag the image with the major version (e.g. "3.34")
    -no-cache - disable cache
    -prep     - prepare the build, but don't run it

"""

import os
import sys
import time
import re

# see /compile/make.sh

BASE_DIR = os.path.abspath(os.path.dirname(__file__) + '/..')
BUILD_DIR = '/opt/gbd/gbd-qgis-server-build'


# these should be the same as in the compile dockerfile
UBUNTU_NAME = 'noble'
UBUNTU_VERSION = '24.04'

##

sys.path.insert(0, BASE_DIR)

import cli

USAGE = """
image-helper.py <version> [<arch>] [-latest] [-no-cache] [-prep]
"""


class Builder:
    arch = 'amd64'
    vendor = 'gbdconsult'

    packages_url = 'https://files.gbd-websuite.de'

    def __init__(self, args):
        self.build_dir = BUILD_DIR + '/out'

        self.skip_cache = '_skip_cache_' + str(int(time.time() * 1000000)) + '_'

        self.args = args

        self.mode = self.args.get(1)
        if self.mode not in {'release', 'debug'}:
            cli.fatal('first argument must be "release" or "debug"')

        v = self.args.get(2, '').split('.')
        if len(v) != 3 or any(not s.isdigit() for s in v):
            cli.fatal('<version> must be xx.yy.zz')
        self.version = v[0] + '.' + v[1] + '.' + v[2]
        self.version_major = v[0] + '.' + v[1]

        self.arch = 'amd64'
        v = self.args.get(3)
        if v:
            if v not in {'amd64', 'arm64'}:
                cli.fatal('<arch> must be amd64 or arm64')
            self.arch = v

        self.image_name = f'{self.vendor}/gbd-qgis-server-{self.arch}'
        if self.mode == 'debug':
            self.image_name += '-debug'
        self.image_full_name = f'{self.image_name}:{self.version}'

        self.image_description = f'QGIS Server {self.version}'

        self.apt_list = lines(cli.read_file(f'{BASE_DIR}/docker/apt.lst'))

        # resources from the NorBit alkis plugin
        self.alkisplugin_package = 'alkisplugin'
        self.alkisplugin_url = f'{self.packages_url}/{self.alkisplugin_package}.tar.gz'

        # our compiled qgis package (see /compile/compile-helper.py)
        self.qgis_package = f'compiled-qgis-server-{self.version}-{self.arch}-{self.mode}'

    def main(self):
        nc = ''
        if self.args.get('no-cache') or self.args.get('nc'):
            nc = '--no-cache'
        cmd = f'cd {self.build_dir} && docker build --progress plain -f Dockerfile -t {self.image_full_name} {nc} .'

        self.prepare()

        if self.args.get('prep'):
            cli.info(f'prepared in {self.build_dir}, now run:')
            print(cmd)
            return

        cli.run(cmd)
        cli.run(f'rm -fr {self.build_dir}/_skip_cache_*')

        if self.args.get('latest'):
            cli.run(f'docker tag {self.image_full_name} {self.image_name}:{self.version_major}')

    def prepare(self):
        os.chdir(self.build_dir)

        if not os.path.isdir(f'{self.qgis_package}'):
            cli.fatal(f'not found: {self.build_dir}/{self.qgis_package}')

        self.apt_list.extend(self.get_apt_list())

        if not os.path.isdir(f'{self.alkisplugin_package}'):
            cli.run(f"curl -sL '{self.alkisplugin_url}' -o {self.alkisplugin_package}.tar.gz")
            cli.run(f'tar -xzf {self.alkisplugin_package}.tar.gz')

        # our stuff (always skip the cache for these)

        cli.run(f'cp {BASE_DIR}/docker/qgis-start.py {self.skip_cache}qgis-start.py')
        cli.run(f'cp {BASE_DIR}/docker/qgis-start.sh {self.skip_cache}qgis-start.sh')

        cli.write_file(f'Dockerfile', self.dockerfile())

    def get_apt_list(self):
        # extract dependencies from a debian Packages file
        # @TODO is there a better way?

        debian_packages_url = f'https://qgis.org/debian/dists/{UBUNTU_NAME}/main/binary-amd64/Packages'
        debian_packages_path = f'{self.build_dir}/Packages'

        exclude_packages = ['3d', '-dev', 'landingpage', 'python', 'qt6']
        exclude_dependencies = ['qgis', 'libjs', ':any', 'qt6']

        cli.run(f'curl -L -o "{debian_packages_path}" "{debian_packages_url}"')

        packages = []
        dependencies = set()

        with open(debian_packages_path) as fp:
            for ln in fp:
                ln = ln.strip()
                key, _, val = [s.strip() for s in ln.partition(':')]

                if key == 'Package':
                    packages.append([val, '', ''])
                    continue
                elif key == 'Version':
                    packages[-1][1] = val
                elif key == 'Depends':
                    packages[-1][2] = val

        for name, ver, deps in packages:
            if any(p in name for p in exclude_packages):
                continue
            if self.version_major not in ver:
                continue
            deps = re.sub(r'\(.+?\)', '', deps)
            for dep in deps.split(','):
                # ignore alternatives
                dep = dep.split('|')[0].strip()
                if not dep:
                    continue
                if any(p in dep for p in exclude_dependencies):
                    continue
                dependencies.add(dep)

        return sorted(dependencies)

    def dockerfile(self):
        apts = ' \\\n'.join(f"'{s}'" for s in self.apt_list)

        df = f"""
            #
            # {self.image_full_name}
            # generated by gbd-websuite/install/image.py
            #
            FROM ubuntu:{UBUNTU_VERSION}
            LABEL Description="{self.image_description}" Vendor="{self.vendor}" Version="{self.version}"

            RUN apt-get update && apt-get install -y software-properties-common
            RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y {apts} && apt-get -y clean && apt-get -y purge --auto-remove

            RUN pip install --disable-pip-version-check --no-cache-dir --break-system-packages uwsgi

            COPY {self.qgis_package}/usr /usr
            COPY {self.alkisplugin_package} /usr/share/{self.alkisplugin_package}
            COPY {self.skip_cache}qgis-start.sh /qgis-start.sh
            COPY {self.skip_cache}qgis-start.py /qgis-start.py

            RUN chmod 777 /qgis-start.sh

            ENV QT_SELECT=5
            ENV LANG=C.UTF-8

            CMD ["/qgis-start.sh"]
        """
        
        return '\n'.join(s.strip() for s in df.splitlines()) + '\n'


###


def main(args):
    b = Builder(args)
    b.main()
    return 0


def commands(txt):
    return ' \\\n&& '.join(lines(txt))


def lines(txt):
    ls = []
    for s in txt.strip().splitlines():
        s = s.strip()
        if s and not s.startswith('#'):
            ls.append(s)
    return ls


def uniq(ls):
    s = set()
    r = []
    for x in ls:
        if x not in s:
            r.append(x)
            s.add(x)
    return r


if __name__ == '__main__':
    cli.main('image-helper.py', main, USAGE)
