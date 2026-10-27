"""Exercise real SX1302/HAL counter wrappers with failed register-read results."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native counter checks')
class NativeCounterTests(unittest.TestCase):
    def test_counter_failures_reach_hal_without_publishing_invalid_values(self):
        chip = (ROOT / 'overlay/hal/libloragw/src/loragw_sx1302.c').read_text()
        hal = (ROOT / 'overlay/hal/libloragw/src/loragw_hal.c').read_text()
        checked = 'int sx1302_timestamp_counter_get('
        functions = function(chip, checked)
        functions += function(chip, 'uint32_t sx1302_timestamp_counter(')
        functions += function(chip, 'int sx1302_update(')
        functions += function(hal, 'int lgw_get_instcnt(')
        functions += function(hal, 'int lgw_get_trigcnt(')
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <sys/time.h>
#define DEBUG_MSG(...)
#define DEBUG_PRINTF(...)
#define LGW_REG_SUCCESS 0
#define LGW_REG_ERROR -1
#define LGW_HAL_SUCCESS 0
#define LGW_HAL_ERROR -1
#define CHECK_NULL(ptr) if ((ptr) == NULL) return -1
static int counter_us, read_result, calls;
static uint32_t inst_value = 1234, pps_value = 5678;
static void _meas_time_start(struct timeval *tm) { *tm = (struct timeval){0}; }
static void _meas_time_stop(int level, struct timeval tm, const char *name) { (void)level; (void)tm; (void)name; }
static int timestamp_counter_get(int *self, uint32_t *inst, uint32_t *pps) {
    (void)self; calls++;
    /* Poisoned/partial values must never escape a failed read. */
    *inst = inst_value; *pps = pps_value;
    return read_result;
}
''' + functions + r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    int scenario = atoi(argv[1]);
    if (scenario < 2) {
        int (*read_counter)(uint32_t *) = scenario == 0 ? lgw_get_instcnt : lgw_get_trigcnt;
        uint32_t value = 9000;
        read_result = -1;
        assert(read_counter(&value) == LGW_HAL_ERROR && value == 9000);
        read_result = 1; /* Any non-success status must fail, not just -1. */
        assert(read_counter(&value) == LGW_HAL_ERROR && value == 9000);
        read_result = 0;
        assert(read_counter(&value) == LGW_HAL_SUCCESS);
        assert(value == (scenario == 0 ? inst_value : pps_value));
        inst_value = pps_value = 0; /* A valid counter at wrap is not an error. */
        assert(read_counter(&value) == LGW_HAL_SUCCESS && value == 0);
        inst_value = pps_value = UINT32_MAX;
        assert(read_counter(&value) == LGW_HAL_SUCCESS && value == UINT32_MAX);
        assert(read_counter(NULL) == LGW_HAL_ERROR && calls == 5);
    } else if (scenario == 2) {
        read_result = -1;
        assert(sx1302_update() == LGW_REG_ERROR);
        read_result = 1;
        assert(sx1302_update() == LGW_REG_ERROR);
        read_result = 0;
        assert(sx1302_update() == LGW_REG_SUCCESS);
    } else {
        assert(sx1302_timestamp_counter(false) == inst_value);
        assert(sx1302_timestamp_counter(true) == pps_value);
        read_result = -1;
        assert(sx1302_timestamp_counter(false) == 0);
        assert(sx1302_timestamp_counter(true) == 0);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'counter')
            built = subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror',
                                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                                    '-x', 'c', '-', '-o', binary],
                                   input=fixture, text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for scenario in range(4):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([binary, str(scenario)], capture_output=True,
                                            text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
