"""Exercise the actual CAD driver with deterministic SPI/GPIO failures."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native CAD checks')
class NativeCADTests(unittest.TestCase):
    def test_timeout_scan_errors_and_failed_recovery_never_report_success(self):
        source = (ROOT / 'overlay/hal/libloragw/src/loragw_sx1261.c').read_text()
        start = source.index('int sx1261_cad_scan(')
        function = source[start:source.index('\n}\n', start) + 3]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <sys/time.h>
#include <unistd.h>
#include "sx1261_defs.h"
#define LGW_REG_SUCCESS 0
#define LGW_REG_ERROR -1
#define _meas_time_start(p) ((void)(p))
#define _meas_time_stop(n, t, name) ((void)(t))
typedef struct { bool detected; int16_t rssi_dbm; uint8_t status; } sx1261_cad_result_t;
struct lgw_gpio_paths_s { const char *sx1261_reset; };
static bool sx1261_lora_rx_enabled;
static int scenario, writes, reloads, setups, elapsed_ms, stop_calls;
static const struct lgw_gpio_paths_s *lgw_get_gpio_paths(void) {
    static struct lgw_gpio_paths_s paths = {"fixture-gpio"}; return &paths;
}
static void wait_ms(int ms) { elapsed_ms += ms; }
static int sx1261_lora_rx_stop(void) { stop_calls++; return scenario == 12 ? -1 : 0; }
static int sx1261_spectral_scan_abort(void) { return scenario == 8 ? -1 : 0; }
static int sx1261_reg_w(sx1261_op_code_t op, uint8_t *data, uint16_t size) {
    (void)data; (void)size;
    if (op == SX1261_SET_CAD) elapsed_ms = 0;
    return scenario == 9 && op == SX1261_SET_CAD ? -1 : 0;
}
static int sx1261_reg_r(sx1261_op_code_t op, uint8_t *data, uint16_t size) {
    (void)op; (void)size;
    if (scenario == 3) return -1;
    uint16_t flags = scenario == 2 ? 0 : SX1261_IRQ_CAD_DONE;
    if (scenario == 10 && elapsed_ms < 263) flags = 0;
    if (scenario == 1) flags |= SX1261_IRQ_CAD_DETECTED;
    data[0] = 0; data[1] = flags >> 8; data[2] = flags & 255;
    return 0;
}
static int sx1261_get_rssi_inst(int16_t *rssi) { *rssi = -100; return 0; }
static int sx1261_load_pram(void) { reloads++; return scenario == 6 ? -1 : 0; }
static int sx1261_setup(void) { setups++; return scenario == 7 ? -1 : 0; }
static int fake_open(const char *path, int flags) { (void)path; (void)flags; return scenario == 4 ? -1 : 42; }
static ssize_t fake_write(int fd, const void *data, size_t size) {
    (void)fd; (void)data; writes++; return scenario == 5 ? 0 : (ssize_t)size;
}
static int fake_close(int fd) { (void)fd; return 0; }
#define open fake_open
#define write fake_write
#define close fake_close
''' + function + r'''
int main(int argc, char **argv) {
    assert(argc == 2); scenario = atoi(argv[1]);
    sx1261_cad_result_t result = {0};
    if (scenario == 11 || scenario == 12 || scenario == 13) sx1261_lora_rx_enabled = true;
    int rc = sx1261_cad_scan(869618000, scenario == 10 ? 12 : scenario == 13 ? 6 : 8,
                           scenario == 11 ? 255 : 3, &result, false);
    if (scenario == 11 || scenario == 13) {
        assert(rc == LGW_REG_ERROR && result.status == 2);
        assert(stop_calls == 0 && writes == 0 && reloads == 0 && setups == 0);
        return 0;
    }
    assert(reloads == 1 && setups == 1); /* Recovery runs on failures too. */
    assert(writes == (scenario == 4 ? 0 : 2));
    if (scenario < 2 || scenario == 10) {
        assert(rc == LGW_REG_SUCCESS && result.status == 0);
        assert(result.detected == (scenario == 1));
    } else {
        assert(rc == LGW_REG_ERROR);
        assert(result.status == (scenario == 2 ? 1 : 2));
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'config.h').touch()
            binary = str(Path(directory, 'cad-check'))
            built = subprocess.run(
                ['gcc', '-std=c99', '-Wall', '-Werror', '-fsanitize=undefined',
                 '-fno-sanitize-recover=all', '-I', directory, '-I',
                 str(ROOT / 'overlay/hal/libloragw/inc'), '-x', 'c', '-', '-o', binary],
                input=fixture, text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for scenario in range(14):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([binary, str(scenario)], capture_output=True,
                                            text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
