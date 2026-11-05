"""Exercise service stop policy with real sudo and harmless native cleanup.

Run as a regular user with noninteractive sudo on a systemd host:
    python3 tests/systemd_shutdown_scenario.py --output-dir /path/to/new-results
No radio, GPIO, application database or running OpenHop service is accessed.
"""

import argparse
import ast
import configparser
import json
import os
from pathlib import Path
import pwd
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def _function(source, declaration):
    start = source.index(declaration)
    return source[start:source.index('\n}\n', start) + 3]


def run(output):
    if os.geteuid() == 0:
        raise RuntimeError('Run as the regular service user to exercise its sudo wrapper')
    subprocess.run(['sudo', '-n', 'true'], check=True)
    output.mkdir(parents=True, exist_ok=True)
    native = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
    handler = _function(native, 'static void sig_handler(int sigio) {')
    child = r'''
#define _DEFAULT_SOURCE
#include <stdbool.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
static volatile sig_atomic_t exit_sig, quit_sig;
''' + handler + r'''
int main(void) {
    struct sigaction action = {0}; action.sa_handler = sig_handler;
    sigaction(SIGTERM, &action, NULL);
    puts("child ready"); fflush(stdout);
    while (!exit_sig) usleep(1000);
    usleep(100000); /* Simulate the native worker joins before GPIO cleanup. */
    int result = system("./reset_fixture.sh stop");
    printf("reset result=%d\n", result); fflush(stdout);
    return result != 0;
}
'''
    source = (ROOT / 'overlay/openhop_core/src/openhop_core/hardware/wm1303_backend.py').read_text()
    tree = ast.parse(source)
    backend = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'WM1303Backend')
    methods = ''.join('    ' + ast.get_source_segment(source, node) + '\n'
                      for node in backend.body if isinstance(node, ast.FunctionDef)
                      and node.name in ('_signal_pktfwd_group', '_stop_pktfwd_process'))
    parent = '''import logging,os,signal,subprocess,threading,time
from pathlib import Path
logger = logging.getLogger(__name__)
class Backend:
    def _stop_hourly_timer(self): pass
''' + methods + r'''
stopping = False
def handle(signo, frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, handle)
backend = Backend()
backend._pktfwd_lock = threading.RLock()
backend._stdout_thread = None
p = subprocess.Popen(['sudo', '-n', './fixture'], stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     text=True, start_new_session=True)
backend._proc, backend._pktfwd_pgid = p, p.pid
assert p.stdout.readline().strip() == 'child ready'
# An unrelated stubborn child stays in the service cgroup, outside the owned
# forwarder group. systemd must still reap it when the main process exits.
helper = subprocess.Popen(['/usr/bin/python3', '-c',
    "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"],
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, start_new_session=True)
assert helper.stdout.readline().strip() == 'ready'
Path('helper-pid').write_text(str(helper.pid))
Path('parent-ready').touch()
while not stopping: time.sleep(0.01)
time.sleep(0.5)  # HTTP/producer shutdown before the real backend stop.
backend._stop_pktfwd_process()
print(p.stdout.read(), end='', flush=True)
Path('child-exit').write_text(str(p.returncode))
'''
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.read(ROOT / 'config/openhop-repeater.service')
    configured_mode = config['Service'].get('KillMode', 'control-group')
    user = pwd.getpwuid(os.getuid()).pw_name
    results = {}
    for label, mode in (('baseline', 'control-group'), ('configured', configured_mode)):
        directory = output / label
        directory.mkdir()
        (directory / 'fixture.c').write_text(child)
        (directory / 'parent.py').write_text(parent)
        (directory / 'reset_fixture.sh').write_text(
            '#!/bin/sh\nset -eu\ntouch reset-started\nsleep 1.2\ntouch reset-finished\n')
        (directory / 'reset_fixture.sh').chmod(0o755)
        subprocess.run(['gcc', '-Wall', '-Werror', str(directory / 'fixture.c'),
                        '-o', str(directory / 'fixture')], check=True)
        unit = 'wm1303-shutdown-fixture-' + uuid.uuid4().hex[:12]
        subprocess.run(['sudo', '-n', 'systemd-run', '--unit=' + unit, '--collect',
                        '--uid=' + user, '--working-directory=' + str(directory),
                        '--property=KillMode=' + mode, '--property=TimeoutStopSec=8',
                        '/usr/bin/python3', str(directory / 'parent.py')], check=True)
        try:
            deadline = time.monotonic() + 5
            while not (directory / 'parent-ready').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert (directory / 'parent-ready').exists(), 'fixture failed to initialize'
        finally:
            subprocess.run(['sudo', '-n', 'systemctl', 'stop', unit], check=True, timeout=15)
            journal = subprocess.check_output(['sudo', '-n', 'journalctl', '-u', unit, '--no-pager'], text=True)
            (directory / 'journal.log').write_text(journal)
        helper_pid = int((directory / 'helper-pid').read_text())
        deadline = time.monotonic() + 2
        while Path('/proc', str(helper_pid)).exists() and time.monotonic() < deadline:
            time.sleep(.02)
        result = {
            'mode': mode, 'started': (directory / 'reset-started').exists(),
            'finished': (directory / 'reset-finished').exists(),
            'child_exit': int((directory / 'child-exit').read_text()),
            'leftover_reaped': not Path('/proc', str(helper_pid)).exists(),
        }
        results[label] = result
        print(label, json.dumps(result), flush=True)
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    assert results['baseline']['started'] and not results['baseline']['finished']
    assert results['baseline']['child_exit'] == 1
    assert results['configured']['started'] and results['configured']['finished']
    assert results['configured']['child_exit'] == 0
    assert all(result['leftover_reaped'] for result in results.values())
    print('Cleanup completes with the configured policy; systemd still kills leftovers.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    arguments = parser.parse_args()
    run(arguments.output_dir.resolve())
