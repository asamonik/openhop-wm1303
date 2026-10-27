"""Exercise native GPS downlink conversion with the pinned HAL's counter model."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for GPS input checks')
class NativeGPSInputTests(unittest.TestCase):
    def test_gps_milliseconds_types_precision_range_and_counter_wrap(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        start = source.index('                    /* Get GPS time from JSON */')
        block = source[start:source.index('                    /* GPS timestamp is given,', start)]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <limits.h>
#include <math.h>
#include <time.h>
#define MSG(...)
#define LGW_GPS_SUCCESS 0
/* Exercise a 32-bit time_t as well as the host representation. */
#ifdef CHECK_TIME32
#define time_t int32_t
#define timespec fixture_timespec
struct timespec { time_t tv_sec; long tv_nsec; };
#endif
typedef enum { JSONString, JSONNumber, JSONBoolean, JSONNull } JSON_Value_Type;
typedef struct { double number; JSON_Value_Type type; } JSON_Value;
double json_value_get_number(JSON_Value *val) { return val->type == JSONNumber ? val->number : 0; }
JSON_Value_Type json_value_get_type(JSON_Value *val) { return val->type; }
void json_value_free(void *root) { (void)root; }
struct tref { time_t systime; struct timespec gps; uint32_t count_us; double xtal_err; };
static int calls;
static struct timespec converted;
/* Same equations and limits as pinned lgw_gps2cnt. A dependency model here
   makes the forwarder's boundary test independent of a downloaded HAL tree. */
static int lgw_gps2cnt(struct tref ref, struct timespec gps_time, uint32_t *count_us) {
    calls++;
    converted = gps_time;
    if (ref.systime == 0 || ref.xtal_err > 1.00001 || ref.xtal_err < 0.99999) return -1;
    double delta_sec = (double)(gps_time.tv_sec - ref.gps.tv_sec);
    delta_sec += 1E-9 * (double)(gps_time.tv_nsec - ref.gps.tv_nsec);
    *count_us = ref.count_us + (uint32_t)(delta_sec * 1E6 * ref.xtal_err);
    return LGW_GPS_SUCCESS;
}
static bool parse(JSON_Value value, struct tref local_ref, uint32_t *count) {
    JSON_Value *val = &value;
    void *root_val = NULL;
    struct timespec gps_tx;
    struct { uint32_t count_us; } txpkt;
    int i;
    for (int once = 0; once < 1; once++) {
''' + block + r'''
        *count = txpkt.count_us;
        return true;
    }
    return false;
}
static bool input(double tmms, struct tref ref, uint32_t *count) {
    return parse((JSON_Value){tmms, JSONNumber}, ref, count);
}
int main(int argc, char **argv) {
    assert(argc == 2);
    int scenario = atoi(argv[1]);
    struct tref ref = {.systime=1, .gps={1500000000, 0}, .count_us=100, .xtal_err=1.0};
    uint32_t count = 123;
    switch (scenario) {
        case 0: /* Milliseconds must split exactly, without modf rounding. */
            assert(input(1500000000001.0, ref, &count));
            assert(converted.tv_sec == 1500000000 && converted.tv_nsec == 1000000);
            assert(count == 1100);
            assert(input(1500000000999.0, ref, &count));
            assert(converted.tv_nsec == 999000000 && count == 999100);
            break;
        case 1: assert(!input(NAN, ref, &count)); break;
        case 2: assert(!input(INFINITY, ref, &count)); break;
        case 3: assert(!input(-1, ref, &count)); break;
        case 4: assert(!input(1500000000000.5, ref, &count)); break;
        case 5:
            for (int type=JSONString; type<=JSONNull; type++) {
                if (type == JSONNumber) continue;
                assert(!parse((JSON_Value){1500000000000.0, type}, ref, &count));
            }
            break;
        case 6: assert(!input(1e30, ref, &count)); break;
        case 7: /* A pre-reference target otherwise causes a negative float cast. */
            assert(!input(1499999999999.0, ref, &count)); break;
        case 8: /* One complete timer cycle must not alias a near-future send. */
            assert(!input(1500004294968.0, ref, &count)); break;
        case 9:
            ref.xtal_err = NAN;
            assert(!input(1500000001000.0, ref, &count)); break;
        case 10:
            ref.count_us = UINT32_MAX - 499;
            assert(input(1500000000001.0, ref, &count));
            assert(count == 500); /* Ordinary modulo-32 counter wrap is valid. */
            assert(input(1500000542000.0, ref, &count)); /* JIT horizon + reference age */
            break;
        case 11:
            assert(!input(9007199254740992.0, ref, &count)); break;
        case 12:
            ref.gps.tv_nsec = 1000000000;
            assert(!input(1500000001000.0, ref, &count)); break;
        case 13:
            ref.gps.tv_sec = INT32_MAX;
            assert(input(2147483647999.0, ref, &count));
            assert(converted.tv_sec == INT32_MAX && converted.tv_nsec == 999000000);
#ifdef CHECK_TIME32
            assert(!input(2147483648000.0, ref, &count));
#else
            assert(input(2147483648000.0, ref, &count));
#endif
            break;
        default: abort();
    }
    if (scenario != 0 && scenario != 10 && scenario != 13) {
        assert(calls == 0 && count == 123); /* Reject before unsafe HAL conversion. */
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            for time32 in (False, True):
                binary = str(Path(directory) / ('gps32' if time32 else 'gps'))
                command = ['gcc', '-std=c99', '-Wall', '-Werror',
                           '-D_POSIX_C_SOURCE=200809L',
                           '-fsanitize=undefined,float-cast-overflow', '-fno-sanitize-recover=all',
                           '-x', 'c', '-', '-lm', '-o', binary]
                if time32:
                    command.append('-DCHECK_TIME32')
                built = subprocess.run(command, input=fixture, text=True, capture_output=True)
                self.assertEqual(built.returncode, 0, built.stderr)
                for scenario in range(14):
                    with self.subTest(time32=time32, scenario=scenario):
                        result = subprocess.run([binary, str(scenario)], capture_output=True,
                                                text=True, timeout=5)
                        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
