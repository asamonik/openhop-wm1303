"""Exercise actual timestamp register unpacking and all 27-bit wrap epochs."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for timestamp checks')
class NativeTimestampTests(unittest.TestCase):
    def test_register_and_packet_timestamps_use_unsigned_shifts(self):
        source = (ROOT / 'overlay/hal/libloragw/src/loragw_sx1302_timestamp.c').read_text()
        functions = '\n'.join(function(source, name) for name in (
            'void timestamp_counter_update(', 'uint32_t timestamp_counter_expand(',
            'uint32_t timestamp_pkt_expand(', 'int timestamp_counter_get('))
        start = source.index('    timestamp_pps_reg  =')
        decode = source[start:source.index('    /* Ensure that the timestamp PPS history', start)]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#define LGW_REG_SUCCESS 0
#define SX1302_REG_TIMESTAMP_TIMESTAMP_PPS_MSB2_TIMESTAMP_PPS 1
struct timestamp_info_s { uint32_t counter_us_27bits_ref; uint8_t counter_us_27bits_wrap; };
typedef struct { struct timestamp_info_s inst, pps; } timestamp_counter_t;
static uint32_t raw_pps, raw_inst, history_value;
static int reads, history_calls, fail_read;
static bool changed_msb;
static void timestamp_pps_history_save(uint32_t value) { history_value = value; history_calls++; }
static void pack(uint8_t *out, uint32_t value) {
    for (unsigned i=0; i<4; i++) out[i] = (uint8_t)(value >> ((3-i)*8));
}
static int lgw_reg_rb(int addr, uint8_t *out, unsigned size) {
    (void)addr; assert(size == 8); reads++;
    if (reads == fail_read) return -1;
    uint32_t correction = changed_msb && reads > 1 ? 0x01000000u : 0;
    pack(out, raw_pps + correction);
    pack(out+4, raw_inst + correction);
    return LGW_REG_SUCCESS;
}
''' + functions + r'''
static uint32_t fine_pps_decode(uint32_t raw) {
    uint32_t timestamp_pps_reg;
    uint8_t buff[4]; pack(buff, raw);
''' + decode + r'''
    return timestamp_pps_reg;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    int scenario = atoi(argv[1]);
    timestamp_counter_t counter = {0};
    uint32_t inst = 77, pps = 88;
    if (scenario < 2) {
        raw_pps = scenario == 0 ? 0xfedcba98u : 0x01234567u;
        raw_inst = scenario == 0 ? 0x01234567u : 0x9abcdef0u;
        assert(timestamp_counter_get(&counter, &inst, &pps) == LGW_REG_SUCCESS);
        assert(inst == raw_inst / 32 && pps == raw_pps / 32);
        assert(history_calls == 1 && history_value == raw_pps);
    } else if (scenario == 2) {
        for (unsigned wrap=0; wrap<32; wrap++) {
            counter.inst.counter_us_27bits_wrap = wrap;
            counter.pps.counter_us_27bits_wrap = wrap;
            uint32_t low = (1u << 27) - 1;
            uint32_t expected = ((uint32_t)wrap << 27) | low;
            assert(timestamp_counter_expand(&counter, false, low) == expected);
            assert(timestamp_counter_expand(&counter, true, low) == expected);
        }
    } else if (scenario == 3) {
        for (unsigned wrap=0; wrap<32; wrap++) {
            counter.inst.counter_us_27bits_wrap = wrap;
            counter.inst.counter_us_27bits_ref = 2;
            assert(timestamp_pkt_expand(&counter, 1) == (((uint32_t)wrap << 27) | 1));
            uint32_t previous = (wrap + 31) % 32;
            assert(timestamp_pkt_expand(&counter, 3) == ((previous << 27) | 3));
        }
    } else if (scenario == 4) {
        const uint32_t values[] = {0, 0x7fffffffu, 0x80000000u, UINT32_MAX};
        for (unsigned i=0; i<4; i++) assert(fine_pps_decode(values[i]) == values[i]);
    } else if (scenario == 5) {
        /* No outputs, history or wrap state are published after any failed read. */
        for (fail_read=1; fail_read<=3; fail_read++) {
            changed_msb = fail_read == 3;
            reads = history_calls = 0;
            counter = (timestamp_counter_t){.inst={999, 5}, .pps={888, 6}};
            timestamp_counter_t saved = counter;
            assert(timestamp_counter_get(&counter, &inst, &pps) == -1);
            assert(inst == 77 && pps == 88 && history_calls == 0);
            assert(counter.inst.counter_us_27bits_ref == saved.inst.counter_us_27bits_ref);
            assert(counter.inst.counter_us_27bits_wrap == saved.inst.counter_us_27bits_wrap);
            assert(counter.pps.counter_us_27bits_ref == saved.pps.counter_us_27bits_ref);
            assert(counter.pps.counter_us_27bits_wrap == saved.pps.counter_us_27bits_wrap);
        }
    } else {
        /* Rollover from the last 27-bit epoch produces a valid zero counter. */
        counter = (timestamp_counter_t){.inst={(1u<<27)-1, 31}, .pps={(1u<<27)-1, 31}};
        timestamp_counter_update(&counter, 0, 0);
        assert(timestamp_counter_expand(&counter, false, 0) == 0);
        assert(timestamp_counter_expand(&counter, true, 0) == 0);
        assert(counter.inst.counter_us_27bits_wrap == 0 && counter.pps.counter_us_27bits_wrap == 0);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'timestamp')
            built = subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror',
                                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                                    '-x', 'c', '-', '-o', binary],
                                   input=fixture, text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for scenario in range(7):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([binary, str(scenario)], capture_output=True,
                                            text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
