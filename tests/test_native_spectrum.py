"""Exercise the real spectral-result completion path with failed file I/O."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native spectrum checks')
class NativeSpectrumTests(unittest.TestCase):
    def test_failed_publication_preserves_previous_results_and_restores_rx(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        completion = source.split('/* --- Write JSON results file (spectral + CAD) --- */', 1)[1].split(
            '/* Reset sweep accumulator */', 1)[0]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
static int failure, renames, removals, closes, restores;
static FILE *test_open(const char *path, const char *mode) {
    (void)path; (void)mode; return failure == 1 ? NULL : (FILE *)1;
}
static int test_print(FILE *stream, const char *format, ...) {
    (void)stream; (void)format; return failure == 2 ? -1 : 1;
}
static int test_error(FILE *stream) { (void)stream; return failure == 2; }
static int test_flush(FILE *stream) { (void)stream; return failure == 3 ? EOF : 0; }
static int test_close(FILE *stream) { (void)stream; closes++; return failure == 4 ? EOF : 0; }
static int test_rename(const char *from, const char *to) {
    (void)from; (void)to; renames++; return failure == 5 ? -1 : 0;
}
static int test_remove(const char *path) { (void)path; removals++; return 0; }
#define fopen test_open
#define fprintf test_print
#define ferror test_error
#define fflush test_flush
#define fclose test_close
#define rename test_rename
#define unlink test_remove
#define printf(...) ((void)0)
static int mx_concent;
static void pthread_mutex_lock(int *mutex) { (void)mutex; }
static void pthread_mutex_unlock(int *mutex) { (void)mutex; }
static bool sx1261_lora_rx_active(void) { return false; }
static void sx1261_lora_rx_restart_light(void) { restores++; }
static struct { unsigned freq_hz, sample_count; double rssi_sum, rssi_min, rssi_max; } sweep_acc[1];
static struct { bool enable; int nb_channels;
    struct { char id[32]; unsigned freq_hz, sf; } channels[1]; } cad_config;
static struct { bool detected; int rssi_dbm; unsigned status; } cad_results[1];
static void complete(void) {
    int i, sweep_idx = 1, sweep_total = 1;
'''
        checks = r'''
}
int main(int argc, char **argv) {
    assert(argc == 2);
    failure = atoi(argv[1]);
    complete();
    assert(restores == 1); /* RX recovery must not depend on disk health. */
    assert(closes == (failure != 1));
    assert(renames == (failure == 0 || failure == 5));
    assert(removals == (failure >= 2));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'spectrum-check')
            subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror', '-Wno-unused-function',
                            '-x', 'c', '-', '-o', binary],
                           input=fixture + completion + checks, text=True, check=True,
                           capture_output=True)
            for failure in range(6):
                with self.subTest(failure=failure):
                    result = subprocess.run([binary, str(failure)], capture_output=True,
                                            text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
