"""Exercise the forwarder's actual calibrated-power selection without RF I/O."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native power checks')
class NativePowerTests(unittest.TestCase):
    def test_transmit_power_never_exceeds_requested_power(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        start = source.index('static int get_tx_gain_lut_index(uint8_t rf_chain, int8_t rf_power, uint8_t * lut_index) {')
        lookup = source[start:source.index('\n}\n', start) + 3]
        gate = source.split('/* check TX power before trying to queue packet, send a warning if not supported */', 1)[1].split(
            '/* WM1303: For IMMEDIATE mode, bypass JIT and send directly.', 1)[0]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define MSG(...)
enum jit_error_e { JIT_ERROR_OK, JIT_ERROR_TX_POWER };
typedef struct { const char *tx_result; } tx_ack_extra_t;
struct gain { int8_t rf_power; };
struct gain_lut { uint8_t size; struct gain lut[16]; };
static struct gain_lut txlut[2];
static int acks;
static int selected;
static void send_tx_ack(uint8_t high, uint8_t low, enum jit_error_e error,
                        int32_t value, const tx_ack_extra_t *extra) {
    (void)value;
    assert(high == 0xab && low == 0xcd);
    assert(error == JIT_ERROR_TX_POWER);
    assert(extra != NULL && strcmp(extra->tx_result, "rejected") == 0);
    acks++;
}
'''
        wrapper = r'''
static bool prepare(int8_t power) {
    struct { uint8_t rf_chain; int8_t rf_power; } txpkt = {0, power};
    uint8_t tx_lut_idx, buff_down[] = {2, 0xab, 0xcd};
    enum jit_error_e jit_result = JIT_ERROR_OK, warning_result = JIT_ERROR_OK;
    int i;
    int32_t warning_value = 0;
    acks = 0;
    for (int once = 0; once < 1; once++) {
''' + gate + r'''
        selected = txpkt.rf_power;
        (void)warning_result;
        return jit_result == JIT_ERROR_OK;
    }
    return false;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    txlut[0] = (struct gain_lut){3, {{12}, {16}, {20}}};
    if (strcmp(argv[1], "supported") == 0) {
        assert(prepare(16) && selected == 16 && acks == 0);
        assert(prepare(19) && selected == 16 && acks == 0);
        assert(prepare(127) && selected == 20 && acks == 0);
    } else if (strcmp(argv[1], "below") == 0) {
        assert(!prepare(11) && acks == 1);
    } else if (strcmp(argv[1], "empty") == 0) {
        txlut[0].size = 0;
        assert(!prepare(14) && acks == 1);
    } else {
        /* Calibration entries need not be sorted. */
        txlut[0] = (struct gain_lut){3, {{20}, {12}, {16}}};
        assert(prepare(19) && selected == 16 && acks == 0);
        assert(!prepare(-128) && acks == 1);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'power-check')
            subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror', '-Wno-unused-function',
                            '-Wno-unused-variable', '-x', 'c', '-', '-o', binary],
                           input=fixture + lookup + wrapper, text=True, capture_output=True, check=True)
            for part in ('supported', 'below', 'empty', 'unsorted'):
                with self.subTest(part=part):
                    result = subprocess.run([binary, part], capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
