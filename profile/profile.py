"""Client-side profiling of the QGIS server image.

profile.py --image <image> [options] [scenario ...]

scenarios (default: all):
    cold     every request uses its own copy of the project, so every request is a cold project load
    warm     load the project in all workers first, then measure
    hung     like warm, then hang all workers with requests that never return, measure if and when the server recovers
    recycle  long warm run, tracks worker processes and their memory

Generated data and projects are kept in profile/___tmp (mounted into the containers).
"""

import argparse
import concurrent.futures
import dataclasses
import json
import math
import os
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, '___tmp')
REPO_DIR = os.path.dirname(BASE_DIR)

CONTAINER = 'gbd-qgis-profile'
DATA_DIR = '/data'

SCENARIOS = ['cold', 'warm', 'hung', 'recycle']

MAP_SIZE = 512
MAP_CELLS = 20
NOT_READY_CODES = {502, 503, 504}


@dataclasses.dataclass
class Sample:
    start: float
    latency: float
    status: int
    ok: bool
    error: str = ''


@dataclasses.dataclass
class Result:
    scenario: str
    samples: list[Sample]
    wall: float
    pids_before: list[int]
    pids_seen: list[int]
    rss: dict[int, list[int]]
    extra: dict = dataclasses.field(default_factory=dict)


# docker
# ------------------------------------------------------------------------------------


def docker(*args, check=True):
    res = subprocess.run(['docker', *args], capture_output=True, text=True)
    if check and res.returncode:
        sys.exit(f'docker {args[0]} failed: {res.stderr.strip()}')
    return res.stdout.strip()


def worker_processes():
    out = docker('exec', CONTAINER, 'ps', '-o', 'pid=,rss=', '-C', 'qgis_mapserv.fcgi', check=False)
    procs = {}
    for ln in out.splitlines():
        pid, rss = ln.split()
        procs[int(pid)] = int(rss)
    return procs


class Server:
    def __init__(self, args):
        self.args = args
        self.base_url = f'http://localhost:{args.port}/'

    def start(self):
        self.stop()
        cmd = [
            'run',
            '-d',
            '--name',
            CONTAINER,
            '-p',
            f'{self.args.port}:80',
            '-v',
            f'{TMP_DIR}:{DATA_DIR}:ro',
            '-e',
            f'QGIS_WORKERS={self.args.workers}',
            '-e',
            f'TIMEOUT={self.args.timeout}',
        ]
        for e in self.args.env:
            cmd.extend(['-e', e])
        cmd.append(self.args.image)
        docker(*cmd)
        self.wait_ready()

    def stop(self):
        docker('rm', '-f', CONTAINER, check=False)

    def wait_ready(self):
        url = self.base_url + '?SERVICE=WMS&REQUEST=GetCapabilities'
        t = time.monotonic() + 60
        while time.monotonic() < t:
            try:
                with urllib.request.urlopen(url, timeout=5) as res:
                    res.read()
                return
            except urllib.error.HTTPError as exc:
                if exc.code not in NOT_READY_CODES:
                    return
            except OSError:
                pass
            time.sleep(0.5)
        sys.exit('server not ready after 60s')


class ProcessSampler:
    def __init__(self, interval=0.5):
        self.interval = interval
        self.rss = {}
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stopped.set()
        self.thread.join()
        return self

    def run(self):
        while True:
            for pid, rss in worker_processes().items():
                self.rss.setdefault(pid, []).append(rss)
            if self.stopped.wait(self.interval):
                break


# data
# ------------------------------------------------------------------------------------


def ensure_data(args):
    overrides = {}
    for g in args.gen:
        key, _, val = g.partition('=')
        overrides[key] = int(val)

    meta_path = os.path.join(TMP_DIR, 'meta.json')
    if not args.regen and os.path.exists(meta_path):
        meta = read_json(meta_path)
        changed = False
        for key, val in overrides.items():
            if meta['opts'].get(key) != val:
                changed = True
        if not changed:
            return meta

    os.makedirs(TMP_DIR, exist_ok=True)
    print('generating data...')
    docker(
        'run',
        '--rm',
        '-v',
        f'{TMP_DIR}:{DATA_DIR}',
        '-v',
        f'{BASE_DIR}:/scripts:ro',
        '-e',
        'PYTHONPATH=/usr/share/qgis/python',
        '-e',
        'QT_QPA_PLATFORM=offscreen',
        args.image,
        'python3',
        '/scripts/gen_project.py',
        DATA_DIR,
        *args.gen,
    )
    return read_json(meta_path)


def make_cold_projects(count):
    for name in os.listdir(TMP_DIR):
        if name.startswith('cold_'):
            os.unlink(os.path.join(TMP_DIR, name))
    names = []
    for n in range(count):
        name = f'cold_{n:05d}.qgs'
        shutil.copyfile(os.path.join(TMP_DIR, 'slow.qgs'), os.path.join(TMP_DIR, name))
        names.append(name)
    return names


# requests
# ------------------------------------------------------------------------------------


def getmap_url(server, meta, project, rnd):
    x0, y0, x1, y1 = meta['extent']
    size = MAP_CELLS * meta['cell']
    x = rnd.uniform(x0, x1 - size)
    y = rnd.uniform(y0, y1 - size)
    params = {
        'SERVICE': 'WMS',
        'REQUEST': 'GetMap',
        'VERSION': '1.3.0',
        'MAP': f'{DATA_DIR}/{project}',
        'LAYERS': ','.join(meta['layers']),
        'STYLES': '',
        'CRS': meta['crs'],
        'BBOX': f'{x},{y},{x + size},{y + size}',
        'WIDTH': MAP_SIZE,
        'HEIGHT': MAP_SIZE,
        'FORMAT': 'image/png',
    }
    return server.base_url + '?' + urllib.parse.urlencode(params)


def hung_url(server):
    params = {
        'SERVICE': 'WMS',
        'REQUEST': 'GetMap',
        'VERSION': '1.3.0',
        'MAP': f'{DATA_DIR}/hung.qgs',
        'LAYERS': 'hung',
        'STYLES': '',
        'CRS': 'EPSG:3857',
        'BBOX': '-1000,-1000,1000,1000',
        'WIDTH': 64,
        'HEIGHT': 64,
        'FORMAT': 'image/png',
    }
    return server.base_url + '?' + urllib.parse.urlencode(params)


def start_blackhole(port):
    code = (
        'import socket, time; '
        's = socket.socket(); '
        's.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); '
        f"s.bind(('127.0.0.1', {port})); "
        's.listen(1000); '
        'time.sleep(10 ** 9)'
    )
    docker('exec', '-d', CONTAINER, 'python3', '-c', code)


def fetch(url, timeout):
    start = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as res:
            body = res.read()
            ctype = res.headers.get('Content-Type', '')
            ok = res.status == 200 and ctype.startswith('image/png') and len(body) > 0
            err = '' if ok else f'bad response: {ctype}'
            return Sample(start, time.monotonic() - start, res.status, ok, err)
    except urllib.error.HTTPError as exc:
        return Sample(start, time.monotonic() - start, exc.code, False, f'http {exc.code}')
    except OSError as exc:
        return Sample(start, time.monotonic() - start, 0, False, str(exc))


def run_load(urls, threads, timeout):
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
        futures = []
        for url in urls:
            futures.append(pool.submit(fetch, url, timeout))
        samples = []
        for f in futures:
            samples.append(f.result())
    return samples, time.monotonic() - start


def run_for(duration, threads, make_url, timeout, seed):
    end = time.monotonic() + duration
    samples = []

    def loop(n):
        rnd = random.Random(seed * 1000 + n)
        while time.monotonic() < end:
            samples.append(fetch(make_url(rnd), timeout))

    ts = []
    for n in range(threads):
        t = threading.Thread(target=loop, args=(n,))
        t.start()
        ts.append(t)
    for t in ts:
        t.join()
    return samples


def recovered_after(samples, t0):
    ordered = sorted(samples, key=lambda s: s.start + s.latency)
    first_ok = None
    for s in ordered:
        if not s.ok:
            first_ok = None
        elif first_ok is None:
            first_ok = s
    if first_ok is None:
        return None
    return first_ok.start + first_ok.latency - t0


def timeline(samples, t0, bucket):
    buckets = {}
    for s in samples:
        k = int((s.start + s.latency - t0) // bucket) * bucket
        b = buckets.setdefault(k, {'t': k, 'ok': 0, 'failed': 0})
        if s.ok:
            b['ok'] += 1
        else:
            b['failed'] += 1
    rows = []
    for k in sorted(buckets):
        rows.append(buckets[k])
    return rows


def warm_up(server, args, meta, rnd):
    batch = args.workers * 2
    last = []
    for _ in range(args.warmup_rounds):
        urls = []
        for _ in range(batch):
            urls.append(getmap_url(server, meta, 'slow.qgs', rnd))
        last, _ = run_load(urls, args.workers, client_timeout(args))
    latencies = []
    for s in last:
        latencies.append(s.latency)
    return {'warmup_last_round_max': max(latencies)}


def client_timeout(args):
    return args.timeout + 30


def measure(scenario, server, args, urls, extra=None):
    pids_before = sorted(worker_processes())
    sampler = ProcessSampler().start()
    samples, wall = run_load(urls, args.threads, client_timeout(args))
    sampler.stop()
    return Result(
        scenario=scenario,
        samples=samples,
        wall=wall,
        pids_before=pids_before,
        pids_seen=sorted(sampler.rss),
        rss=sampler.rss,
        extra=extra or {},
    )


# scenarios
# ------------------------------------------------------------------------------------


def profile_cold(server, args, meta):
    rnd = random.Random(args.seed)
    projects = make_cold_projects(args.requests)
    server.start()
    urls = []
    for p in projects:
        urls.append(getmap_url(server, meta, p, rnd))
    return measure('cold', server, args, urls)


def profile_warm(server, args, meta):
    rnd = random.Random(args.seed)
    server.start()
    extra = warm_up(server, args, meta, rnd)
    urls = []
    for _ in range(args.requests):
        urls.append(getmap_url(server, meta, 'slow.qgs', rnd))
    return measure('warm', server, args, urls, extra)


def profile_hung(server, args, meta):
    rnd = random.Random(args.seed)
    server.start()
    start_blackhole(meta['opts']['hang_port'])
    extra = warm_up(server, args, meta, rnd)

    pids_before = sorted(worker_processes())
    sampler = ProcessSampler().start()

    hung_samples = []

    def run_hung():
        hung_samples.append(fetch(hung_url(server), client_timeout(args)))

    hung_threads = []
    for _ in range(args.hung_requests or args.workers):
        t = threading.Thread(target=run_hung)
        t.start()
        hung_threads.append(t)

    t0 = time.monotonic()
    time.sleep(1)

    window = args.hung_window or 2 * args.timeout + 60
    samples = run_for(window, args.threads, lambda r: getmap_url(server, meta, 'slow.qgs', r), client_timeout(args), args.seed)
    wall = time.monotonic() - t0 - 1

    for t in hung_threads:
        t.join()
    sampler.stop()

    hung = []
    for h in hung_samples:
        hung.append(dataclasses.asdict(h))
    extra['hung_requests'] = hung
    extra['hung_window'] = window
    extra['recovered_after'] = recovered_after(samples, t0)
    extra['timeline'] = timeline(samples, t0, 10)

    return Result(
        scenario='hung',
        samples=samples,
        wall=wall,
        pids_before=pids_before,
        pids_seen=sorted(sampler.rss),
        rss=sampler.rss,
        extra=extra,
    )


def profile_recycle(server, args, meta):
    rnd = random.Random(args.seed)
    server.start()
    extra = warm_up(server, args, meta, rnd)
    urls = []
    for _ in range(args.recycle_requests):
        urls.append(getmap_url(server, meta, 'slow.qgs', rnd))
    return measure('recycle', server, args, urls, extra)


PROFILE_FUNCTIONS = {
    'cold': profile_cold,
    'warm': profile_warm,
    'hung': profile_hung,
    'recycle': profile_recycle,
}


# reporting
# ------------------------------------------------------------------------------------


def percentile(values, p):
    if not values:
        return 0.0
    vs = sorted(values)
    k = max(0, math.ceil(p / 100 * len(vs)) - 1)
    return vs[k]


def summarize(res: Result):
    ok = []
    errors = {}
    for s in res.samples:
        if s.ok:
            ok.append(s.latency)
        else:
            key = s.error or f'status {s.status}'
            errors[key] = errors.get(key, 0) + 1

    new_pids = []
    for pid in res.pids_seen:
        if pid not in res.pids_before:
            new_pids.append(pid)

    rss = {}
    for pid, values in res.rss.items():
        rss[pid] = {'first_kb': values[0], 'last_kb': values[-1], 'max_kb': max(values)}

    return {
        'scenario': res.scenario,
        'requests': len(res.samples),
        'ok': len(ok),
        'errors': errors,
        'mean': sum(ok) / len(ok) if ok else 0.0,
        'p50': percentile(ok, 50),
        'p95': percentile(ok, 95),
        'max': max(ok) if ok else 0.0,
        'wall': res.wall,
        'throughput': len(ok) / res.wall if res.wall else 0.0,
        'processes_before': len(res.pids_before),
        'new_processes': len(new_pids),
        'rss': rss,
        **res.extra,
    }


def print_summary(sm):
    print()
    print(f'== {sm["scenario"]}')
    print(f'   requests {sm["requests"]}  ok {sm["ok"]}  wall {sm["wall"]:.1f}s  throughput {sm["throughput"]:.2f} req/s')
    print(f'   latency  mean {sm["mean"]:.3f}  p50 {sm["p50"]:.3f}  p95 {sm["p95"]:.3f}  max {sm["max"]:.3f}')
    print(f'   processes before {sm["processes_before"]}  new {sm["new_processes"]}')
    for err, count in sm['errors'].items():
        print(f'   error    {count} x {err}')
    if 'warmup_last_round_max' in sm:
        print(f'   warm-up  last round max {sm["warmup_last_round_max"]:.3f}')
    if 'hung_requests' in sm:
        hs = []
        for h in sm['hung_requests']:
            hs.append(f'{h["status"]}/{h["latency"]:.1f}s')
        print(f'   hung     {len(hs)} requests (status/latency): {" ".join(hs)}')
        rec = sm['recovered_after']
        if rec is None:
            print(f'   recovery NOT recovered within {sm["hung_window"]}s')
        else:
            print(f'   recovery first successful response (with no failures after it) {rec:.1f}s after the hang started')
        cells = []
        for b in sm['timeline']:
            cells.append(f'{b["t"]}s:{b["ok"]}/{b["failed"]}')
        print(f'   timeline (responses ok/failed per 10s) {" ".join(cells)}')
    if sm['scenario'] == 'recycle':
        for pid, r in sorted(sm['rss'].items()):
            print(f'   rss      pid {pid}  first {r["first_kb"] // 1024} MB  last {r["last_kb"] // 1024} MB  max {r["max_kb"] // 1024} MB')


def metadata(args, meta):
    return {
        'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'image': args.image,
        'image_id': docker('image', 'inspect', '--format', '{{.Id}}', args.image, check=False),
        'git_commit': subprocess.run(['git', '-C', REPO_DIR, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip(),
        'docker_cpus': docker('info', '--format', '{{.NCPU}}', check=False),
        'docker_mem': docker('info', '--format', '{{.MemTotal}}', check=False),
        'args': vars(args),
        'data': meta['opts'],
    }


def read_json(path):
    with open(path, encoding='utf8') as fp:
        return json.load(fp)


# main
# ------------------------------------------------------------------------------------


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('scenarios', nargs='*', help='scenarios to run (default: all)')
    p.add_argument('--image', required=True, help='image to profile')
    p.add_argument('--port', type=int, default=18090, help='host port for the server')
    p.add_argument('--workers', type=int, default=4, help='QGIS_WORKERS')
    p.add_argument('--threads', type=int, default=0, help='client threads (default: --workers)')
    p.add_argument('--requests', type=int, default=200, help='measured requests per scenario')
    p.add_argument('--recycle-requests', type=int, default=5000, help='measured requests in "recycle" (more than workers x max-requests)')
    p.add_argument('--warmup-rounds', type=int, default=5, help='warm-up rounds of 2 x workers requests')
    p.add_argument('--timeout', type=int, default=60, help='TIMEOUT passed to the server')
    p.add_argument('--hung-requests', type=int, default=0, help='hanging requests in "hung" (default: --workers)')
    p.add_argument('--hung-window', type=int, default=0, help='seconds to probe after the hang in "hung" (default: 2 x timeout + 60)')
    p.add_argument('--seed', type=int, default=1, help='seed for map extents')
    p.add_argument('--env', action='append', default=[], metavar='KEY=VAL', help='extra server env, repeatable')
    p.add_argument('--gen', action='append', default=[], metavar='KEY=VAL', help='data generator option, repeatable')
    p.add_argument('--regen', action='store_true', help='regenerate data')
    p.add_argument('--out', help='write results as JSON to this path')
    args = p.parse_args()
    for name in args.scenarios:
        if name not in SCENARIOS:
            p.error(f'unknown scenario: {name}')
    if not args.threads:
        args.threads = args.workers
    return args


def main():
    args = parse_args()
    scenarios = args.scenarios or SCENARIOS

    meta = ensure_data(args)
    server = Server(args)

    summaries = []
    raw = {}
    try:
        for name in scenarios:
            print(f'running {name}...')
            res = PROFILE_FUNCTIONS[name](server, args, meta)
            server.stop()
            sm = summarize(res)
            print_summary(sm)
            summaries.append(sm)
            samples = []
            for s in res.samples:
                samples.append(dataclasses.asdict(s))
            raw[name] = samples
    finally:
        server.stop()

    if args.out:
        out = {'meta': metadata(args, meta), 'summaries': summaries, 'samples': raw}
        with open(args.out, 'w', encoding='utf8') as fp:
            json.dump(out, fp, indent=4)
        print(f'\nwritten {args.out}')


if __name__ == '__main__':
    main()
