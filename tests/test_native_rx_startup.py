"""Run the actual RX startup without changing a radio or board oscillator."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for RX startup checks')
class NativeRXStartupTests(unittest.TestCase):
    def test_startup_preserves_oscillator_and_reports_each_failed_operation(self):
        source = (ROOT / 'overlay/hal/libloragw/src/loragw_sx1261.c').read_text()
        start = source.index('int sx1261_lora_rx_start(')
        function = source[start:source.index('\n}\n', start) + 3]
        constants = '\n'.join(re.findall(
            r'^#define SX1261_(?:IRQ_|REG_)[^\n]+', source, re.M))
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "sx1261_defs.h"
#define LGW_REG_SUCCESS 0
#define LGW_REG_ERROR -1
#define BW_62K5HZ 3
#define BW_125KHZ 4
#define BW_250KHZ 5
#define BW_500KHZ 6
#define SX1261_OP_SET_DIO3_AS_TCXO_CTRL 0x97
static bool sx1261_lora_rx_enabled = true, sx1261_lora_rx_boosted = true;
static uint32_t sx1261_lora_rx_freq = 869618000;
static uint8_t sx1261_lora_rx_bw = 3, sx1261_lora_rx_sf = 8;
static uint8_t sx1261_lora_rx_cr = 1, sx1261_lora_rx_ldro;
static uint16_t sx1261_lora_rx_sync_word = 0x1424;
static int operations, fail_at, oscillator_changes;
static bool receiving;
static uint32_t calibrated_frequency;
static void wait_ms(int ms) { (void)ms; }
static int sx1261_reg_w(sx1261_op_code_t op, uint8_t *data, uint16_t size) {
    (void)data; (void)size;
    if (++operations == fail_at) return LGW_REG_ERROR;
    if ((int)op == SX1261_OP_SET_DIO3_AS_TCXO_CTRL) oscillator_changes++;
    if (op == SX1261_SET_RX) receiving = true;
    return LGW_REG_SUCCESS;
}
static int sx1261_reg_r(sx1261_op_code_t op, uint8_t *data, uint16_t size) {
    (void)op; (void)size; data[3] = 0;
    return ++operations == fail_at ? LGW_REG_ERROR : LGW_REG_SUCCESS;
}
static int sx1261_calibrate(uint32_t freq) {
    calibrated_frequency = freq;
    return ++operations == fail_at ? LGW_REG_ERROR : LGW_REG_SUCCESS;
}
''' + constants + '\n' + function + r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    sx1261_lora_rx_freq = (uint32_t)strtoul(argv[1], NULL, 10);
    int rc = sx1261_lora_rx_start();
    assert(rc == LGW_REG_SUCCESS && receiving);
    assert(oscillator_changes == 0); /* Board initialization owns this setting. */
    assert(calibrated_frequency == sx1261_lora_rx_freq);
    int count = operations;
    for (int fault = 1; fault <= count; fault++) {
        operations = 0; receiving = false; fail_at = fault;
        rc = sx1261_lora_rx_start();
        assert(rc == LGW_REG_ERROR && !receiving);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'config.h').touch()
            binary = str(Path(directory, 'rx-startup'))
            built = subprocess.run(
                ['gcc', '-std=c99', '-Wall', '-Werror', '-Wno-unused-function',
                 '-fsanitize=undefined', '-fno-sanitize-recover=all', '-I', directory,
                 '-I', str(ROOT / 'overlay/hal/libloragw/inc'), '-x', 'c', '-', '-o', binary],
                input=fixture, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for frequency in ('869618000', '915000000'):
                with self.subTest(frequency=frequency):
                    result = subprocess.run([binary, frequency], capture_output=True,
                                            text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
