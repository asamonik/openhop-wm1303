"""Compile the real HAL airtime calculations without radio I/O."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native airtime checks')
class NativeAirtimeTests(unittest.TestCase):
    def test_symbol_duration_long_preambles_and_legacy_outputs(self):
        aux = (ROOT / 'overlay/hal/libloragw/src/loragw_aux.c').read_text()
        hal = (ROOT / 'overlay/hal/libloragw/src/loragw_hal.c').read_text()
        functions = [function(aux, 'uint64_t lora_packet_time_on_air64('),
                     function(aux, 'uint32_t lora_packet_time_on_air('),
                     function(hal, 'uint32_t lgw_time_on_air(')]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <math.h>
#define DEBUG_PRINTF(...)
#define BW_62K5HZ 3
#define BW_125KHZ 4
#define BW_250KHZ 5
#define BW_500KHZ 6
#define MOD_LORA 0x10
#define MOD_FSK 0x20
#define IS_LORA_DR(v) ((v) >= 5 && (v) <= 12)
#define IS_LORA_BW(v) ((v) >= 3 && (v) <= 6)
#define IS_LORA_CR(v) ((v) >= 1 && (v) <= 4)
#define MAX(a,b) ((a) > (b) ? (a) : (b))
struct lgw_pkt_tx_s {
    uint8_t modulation, bandwidth, coderate;
    uint16_t preamble, size;
    uint32_t datarate;
    bool no_header, no_crc;
};
static struct { uint8_t sync_word_size; } fsk = {3};
#define CONTEXT_FSK fsk
'''
        checks = r'''
int main(void) {
    /* Explicit header+CRC, CR4/5, preamble17 and 16-byte payload. */
    struct lgw_pkt_tx_s pkt = {.modulation=MOD_LORA, .coderate=1, .preamble=17, .size=16};
    struct { uint8_t bw, sf; uint32_t us; } cases[] = {
        {BW_62K5HZ, 12, 3227648}, /* 49.25 symbols x 65.536ms */
        {BW_125KHZ, 12, 1613824},
        {BW_250KHZ, 12, 806912},
        {BW_500KHZ, 12, 362496},
        {BW_62K5HZ, 10, 888832},
    };
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        pkt.bandwidth = cases[i].bw;
        pkt.datarate = cases[i].sf;
        assert(lora_packet_time_on_air(pkt.bandwidth, pkt.datarate, 1, 17, false, false, 16,
                                       NULL, NULL, NULL) == cases[i].us);
        assert(lgw_time_on_air(&pkt) == (cases[i].us + 500) / 1000);
    }

    double symbols = -1;
    uint32_t payload_symbols = UINT32_MAX, symbol_us = 0;
    assert(lora_packet_time_on_air64(BW_62K5HZ, 12, 1, 17, false, false, 16,
                                    &symbols, &payload_symbols, &symbol_us) == 3227648);
    assert(symbols == 49.25 && payload_symbols == 20 && symbol_us == 65536);
    uint16_t legacy_symbol_us = 123;
    symbols = -1;
    payload_symbols = UINT32_MAX;
    assert(lora_packet_time_on_air(BW_62K5HZ, 12, 1, 17, false, false, 16,
                                   &symbols, &payload_symbols, &legacy_symbol_us) == 0);
    assert(legacy_symbol_us == 123 && symbols == -1 && payload_symbols == UINT32_MAX);
    assert(lora_packet_time_on_air(BW_125KHZ, 12, 1, 17, false, false, 16,
                                   &symbols, &payload_symbols, &legacy_symbol_us) == 1613824);
    assert(legacy_symbol_us == 32768 && symbols == 49.25 && payload_symbols == 20);

    pkt.bandwidth = BW_62K5HZ;
    pkt.datarate = 12;
    pkt.preamble = 65535;
    assert(lora_packet_time_on_air64(BW_62K5HZ, 12, 1, 65535, false, false, 16,
                                    NULL, NULL, NULL) == UINT64_C(4297015296));
    assert(lgw_time_on_air(&pkt) == 4297015); /* Milliseconds still fit the HAL API. */
    assert(lora_packet_time_on_air(BW_62K5HZ, 12, 1, 65535, false, false, 16,
                                   NULL, NULL, NULL) == 0); /* No wrapping legacy result. */
    pkt.coderate = 4;
    pkt.size = 255;
    assert(lgw_time_on_air(&pkt) > 4297015); /* Max valid inputs stay defined under UBSan. */

    pkt = (struct lgw_pkt_tx_s){.modulation=MOD_FSK, .preamble=5, .size=16, .datarate=500};
    assert(lgw_time_on_air(&pkt) == 433);
    pkt.preamble = 65535;
    pkt.size = 255;
    assert(lgw_time_on_air(&pkt) == 1052737);
    assert(lora_packet_time_on_air64(BW_62K5HZ, 13, 1, 17, false, false, 16,
                                    NULL, NULL, NULL) == 0);
    assert(lgw_time_on_air(NULL) == 0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'airtime-check')
            built = subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror',
                                    '-fsanitize=undefined,float-cast-overflow', '-fno-sanitize-recover=all',
                                    '-x', 'c', '-', '-lm', '-o', binary],
                                   input=fixture + '\n'.join(functions) + checks,
                                   text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([binary], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
