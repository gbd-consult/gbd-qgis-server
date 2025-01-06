"""Docker builder for QGIS images."""

import os
import sys
import time
import re

# see /compile/make.sh

THIS_DIR = os.path.abspath(os.path.dirname(__file__))
BUILD_DIR = '/opt/gbd/gbd-qgis-server-build'

sys.path.insert(0, os.path.abspath(THIS_DIR + '/..'))

import cli

USAGE = """
QGIS Image Builder
~~~~~~~~~~~~~~~~~~
  
    python3 image.py <version> <arch> <options>

<version>
    3-digit QGIS version, e.g. "3.34.0"

<arch>
    image architecture "amd64" (default) or "arm64" 

Options:

    -major
        tag the image with the major version (e.g. "3.34")

    -debug
        build the debug image

    -no-cache
        disable cache

    -prep
        prepare the build, but don't run it

"""


class Builder:
    ubuntu_name = 'jammy'
    ubuntu_version = '22.04'

    arch = 'amd64'
    vendor = 'gbdconsult'

    packages_url = 'https://files.gbd-websuite.de'

    exclude_packages = ['3d', '-dev', 'landingpage', 'python']
    exclude_dependencies = ['qgis', 'libjs', ':any']

    def __init__(self, args):
        self.build_dir = BUILD_DIR + '/out'

        self.skip_cache = '_skip_cache_' + str(int(time.time() * 1000000)) + '_'

        self.args = args

        v = self.args.get(1, '').split('.')
        if len(v) != 3 or any(not s.isdigit() for s in v):
            cli.fatal('<version> must be xx.yy.zz')
        self.version = v[0] + '.' + v[1] + '.' + v[2]
        self.version_major = v[0] + '.' + v[1]

        self.arch = self.args.get(2)
        if self.arch not in {'amd64', 'arm64'}:
            cli.fatal('<arch> must be amd64 or arm64')

        self.mode = 'debug' if self.args.get('debug') else 'release'

        self.image_name = f'{self.vendor}/gbd-qgis-server-{self.arch}'
        if self.mode == 'debug':
            self.image_name += '-debug'
        self.image_full_name = f'{self.image_name}:{self.version}'

        self.image_description = f'QGIS Server {self.version}'

        self.debian_packages_url = f'https://debian.qgis.org/debian/dists/{self.ubuntu_name}/main/binary-amd64/Packages'
        self.debian_packages_path = f'{self.build_dir}/Packages'

        self.apt_list = lines(cli.read_file(f'{THIS_DIR}/apt.lst'))
        self.pip_list = lines(cli.read_file(f'{THIS_DIR}/pip.lst'))

        # resources from the NorBit alkis plugin
        self.alkisplugin_package = 'alkisplugin'
        self.alkisplugin_url = f'{self.packages_url}/{self.alkisplugin_package}.tar.gz'

        # our compiled qgis package (see /compile/make-helper.py)
        self.qgis_package = f'compiled-qgis-server-{self.version}-{self.arch}-{self.mode}'

    def main(self):
        nc = '--no-cache' if self.args.get('no-cache') else ''
        cmd = f'cd {self.build_dir} && docker build --progress plain -f Dockerfile -t {self.image_full_name} {nc} .'

        self.prepare()

        if self.args.get('prep'):
            cli.info(f'prepared in {self.build_dir}, now run:')
            print(cmd)
            return

        cli.run(cmd)
        cli.run(f'rm -fr {self.build_dir}/_skip_cache_*')

        if self.args.get('major'):
            cli.run(f'docker tag {self.image_full_name} {self.image_name}:{self.version_major}')

    def prepare(self):
        os.chdir(self.build_dir)

        if not os.path.isdir(f'{self.qgis_package}'):
            cli.fatal(f'not found: {self.build_dir}/{self.qgis_package}')

        if not os.path.isfile(self.debian_packages_path):
            cli.run(f'curl -o "{self.debian_packages_path}" "{self.debian_packages_url}"')

        self.apt_list.extend(self.get_apt_list())

        if not os.path.isdir(f'{self.alkisplugin_package}'):
            cli.run(f"curl -sL '{self.alkisplugin_url}' -o {self.alkisplugin_package}.tar.gz")
            cli.run(f"tar -xzf {self.alkisplugin_package}.tar.gz")

        # our stuff (always skip the cache for these)

        cli.run(f'cp {THIS_DIR}/qgis-start.py {self.skip_cache}qgis-start.py')
        cli.run(f'cp {THIS_DIR}/qgis-start.sh {self.skip_cache}qgis-start.sh')

        cli.write_file(f'Dockerfile', self.dockerfile())

    def get_apt_list(self):
        # extract dependencies from a debian Packages file
        # @TODO is there a better way?

        packages = []
        dependencies = set()

        with open(self.debian_packages_path) as fp:
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
            if any(p in name for p in self.exclude_packages):
                continue
            if self.version_major not in ver:
                continue
            deps = re.sub(r'\(.+?\)', '', deps)
            for dep in deps.split(','):
                # ignore alternatives
                dep = dep.split('|')[0].strip()
                if not dep:
                    continue
                if any(p in dep for p in self.exclude_dependencies):
                    continue
                dependencies.add(dep)

        return sorted(dependencies)

    def dockerfile(self):
        df = []
        __ = df.append

        __(f'#')
        __(f'# {self.image_full_name}')
        __(f'# generated by gbd-websuite/install/image.py')
        __(f'#')
        __(f'FROM ubuntu:{self.ubuntu_version}')
        __(f'LABEL Description="{self.image_description}" Vendor="{self.vendor}" Version="{self.version}"')

        apts = ' \\\n'.join(f"'{s}'" for s in self.apt_list)
        pips = ' \\\n'.join(f"'{s}'" for s in self.pip_list)

        __(f'RUN apt update')
        __(f'RUN apt install -y software-properties-common')
        __(f'RUN apt update')
        __(f'RUN DEBIAN_FRONTEND=noninteractive apt install -y {apts}')
        __(f'RUN apt-get -y clean')
        __(f'RUN apt-get -y purge --auto-remove')

        if pips:
            __(f'RUN pip3 install --no-cache-dir {pips}')

        __(f'COPY {self.qgis_package}/usr /usr')
        __(f'COPY {self.alkisplugin_package} /usr/share/{self.alkisplugin_package}')

        __(f'COPY {self.skip_cache}qgis-start.sh /qgis-start.sh')
        __(f'COPY {self.skip_cache}qgis-start.py /qgis-start.py')
        __(f'RUN chmod 777 /qgis-start.sh')
        __(f'ENV QT_SELECT=5')
        __(f'ENV LANG=C.UTF-8')
        __(f'CMD ["/qgis-start.sh"]')

        return '\n'.join(df) + '\n'


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
    cli.main('image.py', main, USAGE)
