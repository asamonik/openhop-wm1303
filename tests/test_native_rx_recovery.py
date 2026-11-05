"""Exercise real RX restore paths against SPI and USB transport constraints."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _function(source, declaration):
    start = source.index(declaration)
    return source[start:source.index('\n}\n', start) + 3]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for RX recovery checks')
class NativeRXRecoveryTests(unittest.TestCase):
    def _run_fixture(self, fixture):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'config.h').touch()
            binary = str(Path(directory, 'rx-recovery'))
            built = subprocess.run(
                ['gcc', '-std=c99', '-Wall', '-Werror', '-Wno-unused-function',
                 '-fsanitize=undefined', '-fno-sanitize-recover=all', '-I', directory,
                 '-I', str(ROOT / 'overlay/hal/libloragw/inc'), '-x', 'c', '-', '-o', binary],
                input=fixture, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([binary], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_gain_and_receiver_settings_survive_start_and_light_restart(self):
        source = (ROOT / 'overlay/hal/libloragw/src/loragw_sx1261.c').read_text()
        constants = '\n'.join(re.findall(
            r'^#define SX1261_(?:IRQ_|REG_)[^\n]+', source, re.M))
        functions = '\n'.join(_function(source, declaration) for declaration in (
            'int sx1261_lora_rx_configure(', 'int sx1261_lora_rx_start(',
            'int sx1261_lora_rx_restart_light('))
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include "sx1261_defs.h"
#define LGW_REG_SUCCESS 0
#define LGW_REG_ERROR -1
#define LGW_COM_WRITE_MODE_SINGLE 0
#define LGW_COM_WRITE_MODE_BULK 1
#define BW_62K5HZ 3
#define BW_125KHZ 4
#define BW_250KHZ 5
#define BW_500KHZ 6
static bool sx1261_lora_rx_enabled, sx1261_lora_rx_boosted, sx1261_tx_inhibit_rx;
static uint32_t sx1261_lora_rx_freq;
static uint8_t sx1261_lora_rx_bw, sx1261_lora_rx_sf, sx1261_lora_rx_cr, sx1261_lora_rx_ldro;
static uint16_t sx1261_lora_rx_sync_word;
static int operations, fail_at, mode, queued_writes, gain_writes, expected_gain;
static bool receiving, usb;
static void wait_ms(int ms) { (void)ms; }
static int sx1261_com_set_write_mode(int next_mode) {
    if (++operations == fail_at) return -1;
    mode = next_mode; return 0;
}
static int sx1261_com_flush(void) {
    mode = queued_writes = 0; return ++operations == fail_at ? -1 : 0;
}
static int sx1261_reg_w(sx1261_op_code_t op, uint8_t *data, uint16_t size) {
    if (++operations == fail_at) return -1;
    /* Track pending USB commands; its read API rejects bulk mode. */
    if (usb && mode == LGW_COM_WRITE_MODE_BULK) queued_writes++;
    if (op == SX1261_WRITE_REGISTER) {
        assert(size >= 3);
        unsigned address = (data[0] << 8) | data[1];
        assert(address != 0x029F); /* This is the retention list, not RX gain. */
        if (address == 0x08AC) { assert(data[2] == expected_gain); gain_writes++; }
    }
    if (op == SX1261_SET_MODULATION_PARAMS) {
        assert(size == 4 && data[0] == 12 && data[1] == 3 && data[2] == 1 && data[3] == 1);
    }
    if (op == SX1261_SET_PACKET_PARAMS) assert(data[0] == 0 && data[1] == 16);
    if (op == SX1261_SET_DIO_IRQ_PARAMS) {
        assert(size == 8 && data[0] == 0 && data[1] == 0x56);
        assert(data[2] == data[0] && data[3] == data[1]);
    }
    if (op == SX1261_SET_RX) receiving = true;
    return 0;
}
static int sx1261_reg_r(sx1261_op_code_t op, uint8_t *data, uint16_t size) {
    (void)op; (void)size;
    if (++operations == fail_at || (usb && mode == LGW_COM_WRITE_MODE_BULK)) return -1;
    data[3] = 0; return 0;
}
static int sx1261_calibrate(uint32_t freq) {
    assert(freq == 869618000); return ++operations == fail_at ? -1 : 0;
}
''' + constants + '\n' + functions + r'''
static int configure(void) {
    return sx1261_lora_rx_configure(869618000, 3, 12, 1, expected_gain == 0x96, false);
}
int main(void) {
    int (*paths[])(void) = { configure, sx1261_lora_rx_start, sx1261_lora_rx_restart_light };
    for (int transport = 0; transport < 2; transport++) {
        usb = transport;
        for (int boosted = 0; boosted < 2; boosted++) {
            expected_gain = boosted ? 0x96 : 0x94;
            operations = fail_at = mode = 0;
            assert(configure() == 0);
            for (int path = 0; path < 3; path++) {
                operations = fail_at = gain_writes = mode = 0; receiving = false;
                assert(paths[path]() == 0 && gain_writes == 1);
                assert(receiving == (path != 0));
                assert(mode == LGW_COM_WRITE_MODE_SINGLE && queued_writes == 0);
                int count = operations;
                for (int fault = 1; fault <= count; fault++) {
                    operations = mode = 0; fail_at = fault; receiving = false;
                    assert(paths[path]() == -1 && !receiving);
                    assert(mode == LGW_COM_WRITE_MODE_SINGLE && queued_writes == 0);
                }
                fail_at = 0;
            }
        }
    }
    operations = 0; sx1261_tx_inhibit_rx = true;
    assert(sx1261_lora_rx_restart_light() == 0 && operations == 0);
    sx1261_tx_inhibit_rx = false; sx1261_lora_rx_enabled = false;
    assert(sx1261_lora_rx_restart_light() == 0 && operations == 0);
    return 0;
}
'''
        self._run_fixture(fixture)

    def test_failed_restore_is_reported_and_deferred_restore_can_retry(self):
        source = (ROOT / 'overlay/hal/libloragw/src/loragw_sx1261.c').read_text()
        functions = '\n'.join(_function(source, declaration) for declaration in (
            'int sx1261_lbt_stop(', 'int sx1261_spectral_scan_abort(',
            'void sx1261_set_tx_inhibit_rx('))
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/time.h>
#include "sx1261_defs.h"
#define LGW_REG_SUCCESS 0
#define LGW_REG_ERROR -1
#define CHECK_ERR(err) do { if ((err) != 0) return -1; } while (0)
#define DEBUG_MSG(...) ((void)0)
#define _meas_time_start(p) ((void)(p))
#define _meas_time_stop(n, t, name) ((void)(t))
static bool sx1261_lora_rx_enabled = true, sx1261_tx_inhibit_rx, sx1261_deferred_rx_restart;
static int failure, restarts;
static int sx1261_reg_w(sx1261_op_code_t op, uint8_t *data, uint16_t size) {
    (void)op; (void)data; (void)size; return 0;
}
static int sx1261_lora_rx_restart_light(void) { restarts++; return failure ? -1 : 0; }
''' + functions + r'''
int main(void) {
    int (*stops[])(void) = { sx1261_lbt_stop, sx1261_spectral_scan_abort };
    for (int path = 0; path < 2; path++) {
        failure = 1; restarts = 0;
        assert(stops[path]() == -1 && restarts == 1);
        failure = 0; assert(stops[path]() == 0 && restarts == 2);
        sx1261_set_tx_inhibit_rx(true);
        assert(stops[path]() == 0 && restarts == 2 && sx1261_deferred_rx_restart);
        failure = 1; sx1261_set_tx_inhibit_rx(false);
        assert(restarts == 3 && sx1261_deferred_rx_restart);
        failure = 0; sx1261_set_tx_inhibit_rx(false);
        assert(restarts == 4 && !sx1261_deferred_rx_restart);
        sx1261_set_tx_inhibit_rx(false); assert(restarts == 4);
    }
    return 0;
}
'''
        self._run_fixture(fixture)


if __name__ == '__main__':
    unittest.main()
