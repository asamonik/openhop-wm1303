"""Run the native RX fetch against malformed packets and failed radio I/O."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native RX fetch checks')
class NativeRXFetchTests(unittest.TestCase):
    def test_only_complete_acknowledged_packets_are_published(self):
        source = (ROOT / 'overlay/hal/libloragw/src/loragw_sx1261.c').read_text()
        start = source.index('int sx1261_lora_rx_fetch(')
        function = source[start:source.index('\n}\n', start) + 3]
        constants = '\n'.join(re.findall(r'^#define SX1261_IRQ_[^\n]+', source, re.M))
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sx1261_defs.h"
#define LGW_REG_SUCCESS 0
#define LGW_REG_ERROR -1
#define LGW_HAL_SUCCESS 0
#define MOD_LORA 16
#define STAT_CRC_OK 1
#define STAT_CRC_BAD 17
struct lgw_pkt_rx_s {
    uint32_t freq_hz, datarate, count_us;
    uint8_t if_chain, rf_chain, modulation, bandwidth, coderate, status;
    float rssic, rssis, snr, snr_min, snr_max;
    uint16_t size, crc;
    uint8_t payload[256];
};
static uint32_t sx1261_lora_rx_freq = 869618000;
static uint8_t sx1261_lora_rx_bw = 3, sx1261_lora_rx_sf = 8, sx1261_lora_rx_cr = 4;
static int scenario, reads, clears;
static bool irq_pending = true;
static int lgw_get_instcnt(uint32_t *timestamp) {
    *timestamp = 123456; return scenario == 9 ? -1 : 0;
}
static int sx1261_reg_r(sx1261_op_code_t op, uint8_t *data, uint16_t size) {
    reads++;
    if (op == SX1261_GET_IRQ_STATUS) {
        if (scenario == 2) return -1;
        data[1] = 0; data[2] = irq_pending && scenario != 7 ? 2 : 0;
        if (scenario == 8) data[2] |= 64;
    } else if (op == SX1261_GET_RX_BUFFER_STATUS) {
        if (scenario == 3) return -1;
        assert(size == 3);
        data[1] = scenario == 1 ? 0 : scenario == 12 ? 1 : 255;
        data[2] = 255;
    } else if (op == SX1261_READ_BUFFER) {
        if (scenario == 4) return -1;
        assert(data[0] == 255 && size == (scenario == 12 ? 3 : 257));
        for (int i = 2; i < size; i++) data[i] = (i - 2) ^ 0x5A;
    } else if (op == SX1261_GET_PACKET_STATUS) {
        if (scenario == 5) return -1;
        assert(size == 4);
        data[1] = 201; data[2] = (uint8_t)-17; data[3] = 202;
    } else {
        assert(op == SX1261_GET_STATUS); data[0] = 0;
    }
    return 0;
}
static int sx1261_reg_w(sx1261_op_code_t op, uint8_t *data, uint16_t size) {
    assert(op == SX1261_CLR_IRQ_STATUS && size == 2);
    assert(data[0] == (SX1261_IRQ_ALL >> 8) && data[1] == (SX1261_IRQ_ALL & 255));
    clears++;
    if (scenario == 6) return -1;
    irq_pending = false; return 0;
}
''' + constants + '\n' + function + r'''
int main(int argc, char **argv) {
    assert(argc == 2); scenario = atoi(argv[1]);
    struct lgw_pkt_rx_s packets[2], original[2];
    memset(packets, 0xA5, sizeof(packets)); memcpy(original, packets, sizeof(packets));
    int rc = sx1261_lora_rx_fetch(scenario == 10 ? NULL : packets, scenario == 11 ? 0 : 1);
    assert(memcmp(&packets[1], &original[1], sizeof(packets[1])) == 0);
    if (scenario == 0 || scenario == 8 || scenario == 9 || scenario == 12) {
        assert(rc == 1 && clears == 1 && !irq_pending);
        struct lgw_pkt_rx_s *p = &packets[0];
        assert(p->freq_hz == 869618000 && p->if_chain == 9 && p->rf_chain == 1);
        assert(p->modulation == MOD_LORA && p->bandwidth == 3 && p->datarate == 8 && p->coderate == 4);
        assert(p->status == (scenario == 8 ? STAT_CRC_BAD : STAT_CRC_OK));
        assert(p->count_us == (scenario == 9 ? 0 : 123456));
        assert(p->rssic == -100.5f && p->rssis == -100.5f);
        assert(p->snr == -4.25f && p->snr_min == p->snr && p->snr_max == p->snr);
        assert(p->size == (scenario == 12 ? 1 : 255));
        for (int i = 0; i < p->size; i++) assert(p->payload[i] == (uint8_t)(i ^ 0x5A));
    } else {
        assert(rc == (scenario >= 2 && scenario <= 6 ? -1 : 0));
        assert(memcmp(packets, original, sizeof(packets)) == 0);
        assert(clears == (scenario == 1 || (scenario >= 3 && scenario <= 6)));
        if (scenario == 10 || scenario == 11) assert(reads == 0);
    }
    if (scenario == 6) {
        /* An unacknowledged packet is retried, then published just once. */
        assert(irq_pending); scenario = 0;
        assert(sx1261_lora_rx_fetch(packets, 1) == 1 && !irq_pending);
        memcpy(original, packets, sizeof(packets));
        assert(sx1261_lora_rx_fetch(packets, 1) == 0);
        assert(memcmp(packets, original, sizeof(packets)) == 0);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'config.h').touch()
            binary = str(Path(directory, 'rx-fetch'))
            built = subprocess.run(
                ['gcc', '-std=c99', '-Wall', '-Werror', '-fsanitize=undefined',
                 '-fno-sanitize-recover=all', '-I', directory, '-I',
                 str(ROOT / 'overlay/hal/libloragw/inc'), '-x', 'c', '-', '-o', binary],
                input=fixture, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for scenario in range(13):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([binary, str(scenario)], capture_output=True,
                                            text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
