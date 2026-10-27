"""Exercise native beacon refill failures without hardware or waiting on time."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for beacon refill checks')
class NativeBeaconTests(unittest.TestCase):
    def test_beacon_refill_is_bounded_and_respects_shutdown_and_gps_failures(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        start = source.index('            /* Pre-allocate beacon slots in JiT queue, to check downlink collisions */')
        block = source[start:source.index('            /* if no network message was received,', start)]
        fixture = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <pthread.h>
#include <errno.h>
#include <math.h>
#include <limits.h>
#ifdef CHECK_TIME32
#define time_t int32_t
#define timespec fixture_timespec
struct timespec { time_t tv_sec; long tv_nsec; };
#endif
#define MSG(...)
#define MSG_DEBUG(...)
#define DEBUG_BEACON 0
#define LGW_GPS_SUCCESS 0
#define LGW_HAL_SUCCESS 0
#define JIT_NUM_BEACON_IN_QUEUE 3
#define JIT_QUEUE_MAX 32
#define JIT_PKT_TYPE_BEACON 4
#define JIT_ERROR_OK 0
#define JIT_ERROR_FULL 1
#define JIT_ERROR_TOO_EARLY 2
#define JIT_ERROR_COLLISION_BEACON 3
#define JIT_ERROR_TOO_LATE 4
#define JIT_ERROR_COLLISION_PACKET 5
#define JIT_ERROR_INVALID 6
static int scenario, enqueue_calls, gps_calls;
static bool gps_ref_valid = true, xtal_valid = true;
static bool exit_sig, quit_sig;
static uint32_t beacon_period = 128, beacon_freq_hz = 869525000, beacon_freq_step = 0;
static uint8_t beacon_freq_nb = 1, beacon_RFU1_size = 2;
static int meas_nb_beacon_queued, meas_nb_beacon_rejected;
struct queue { int num_beacon; };
static struct queue queue_storage[1];
static pthread_mutex_t mx_timeref = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mx_xcorr = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mx_meas_dw = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mx_concent = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mx_jit_access = PTHREAD_MUTEX_INITIALIZER;
static bool checked_xtal_valid(void) {
    assert(pthread_mutex_trylock(&mx_xcorr) == EBUSY);
    return xtal_valid;
}
static struct queue *checked_queue(void) {
    assert(pthread_mutex_trylock(&mx_jit_access) == EBUSY);
    return queue_storage;
}
#define xtal_correct_ok checked_xtal_valid()
#define jit_queue checked_queue()
struct tref { struct timespec gps; double xtal_err; uint32_t count_us; };
static struct tref time_reference_gps = {.gps={1500000000, 0}, .xtal_err=1.0};
struct packet { uint32_t count_us, freq_hz; uint16_t size; uint8_t payload[64]; };
static int lgw_gps2cnt(struct tref ref, struct timespec target, uint32_t *count) {
    gps_calls++;
    if (scenario == 3) return -1;
    if (scenario == 10) exit_sig = true;
    if (scenario == 11) assert(target.tv_sec > ref.gps.tv_sec);
    double delta_sec = (double)(target.tv_sec - ref.gps.tv_sec);
    delta_sec += 1E-9 * (double)(target.tv_nsec - ref.gps.tv_nsec);
    *count = ref.count_us + (uint32_t)(delta_sec * 1E6 * ref.xtal_err);
    return 0;
}
static int lgw_get_instcnt(uint32_t *count) { *count = 0; return scenario == 21 ? -1 : 0; }
static uint16_t crc16(const uint8_t *data, unsigned size) { (void)data; (void)size; return 0; }
static int jit_enqueue(void *queue, uint32_t now, struct packet *packet, int type) {
    (void)queue; (void)now; (void)packet; (void)type;
    enqueue_calls++;
    assert(enqueue_calls <= JIT_QUEUE_MAX + 1); /* Expose spinning without a wall-clock timeout. */
    if (scenario == 1) return JIT_ERROR_FULL;
    if (scenario == 2) return JIT_ERROR_TOO_EARLY;
    if (scenario == 6) return JIT_ERROR_COLLISION_PACKET;
    if (scenario == 5 && enqueue_calls <= 2) return JIT_ERROR_COLLISION_BEACON;
    return JIT_ERROR_OK;
}
static void refill(void) {
    int beacon_loop, retry, beacon_chan, beacon_pyld_idx, i, jit_result;
    uint16_t field_crc1;
    uint32_t current_concentrator_time;
    struct timespec last_beacon_gps_time = {0}, next_beacon_gps_time;
    struct packet beacon_pkt = {.size = 17};
    if (scenario == 11) last_beacon_gps_time.tv_sec = 100;
''' + block + r'''
}
int main(int argc, char **argv) {
    assert(argc == 2);
    scenario = atoi(argv[1]);
    if (scenario == 4) exit_sig = true;
    if (scenario == 7) quit_sig = true;
    if (scenario == 8) gps_ref_valid = false;
    if (scenario == 9) xtal_valid = false;
    if (scenario == 12) queue_storage[0].num_beacon = 3;
    if (scenario == 13) beacon_period = 86400;
    if (scenario == 14) time_reference_gps.gps.tv_sec = INT32_MAX;
    if (scenario == 15) time_reference_gps.xtal_err = NAN;
    if (scenario == 16) time_reference_gps.gps.tv_nsec = 1000000000;
    if (scenario == 17) time_reference_gps.gps.tv_sec = -1;
    if (scenario == 18) beacon_period = UINT32_MAX;
    if (scenario == 19) time_reference_gps.count_us = UINT32_MAX - 100;
    if (scenario == 20) time_reference_gps.gps.tv_sec =
        (time_t)((UINT64_C(1) << (sizeof(time_t) * CHAR_BIT - 1)) - 1);
    refill();
    if (scenario == 0) assert(enqueue_calls == 3 && meas_nb_beacon_queued == 3);
    if (scenario == 1 || scenario == 2) assert(enqueue_calls == 1);
    if (scenario == 3) assert(enqueue_calls == 0 && gps_calls == 1);
    if (scenario == 4) assert(enqueue_calls == 0 && gps_calls == 0);
    if (scenario == 5) assert(enqueue_calls == 5 && meas_nb_beacon_queued == 3);
    if (scenario == 6) assert(enqueue_calls <= JIT_QUEUE_MAX);
    if (scenario >= 7 && scenario <= 9) assert(enqueue_calls == 0 && gps_calls == 0);
    if (scenario == 10) assert(enqueue_calls == 0 && gps_calls == 1);
    if (scenario == 11) assert(enqueue_calls == 3 && meas_nb_beacon_queued == 3);
    if (scenario == 12) assert(enqueue_calls == 0 && gps_calls == 0);
    if (scenario == 13 || (scenario >= 15 && scenario <= 18) || scenario == 20)
        assert(enqueue_calls == 0 && gps_calls == 0);
    if (scenario == 19) assert(enqueue_calls == 3 && gps_calls == 3);
    if (scenario == 14) {
#ifdef CHECK_TIME32
        assert(enqueue_calls == 0 && gps_calls == 0);
#else
        assert(enqueue_calls == 3 && gps_calls == 3);
#endif
    }
    if (scenario == 21) assert(enqueue_calls == 0 && gps_calls == 1);
    pthread_mutex_t *locks[] = {&mx_timeref, &mx_xcorr, &mx_meas_dw, &mx_concent, &mx_jit_access};
    for (unsigned i=0; i<sizeof locks / sizeof locks[0]; i++) {
        assert(pthread_mutex_trylock(locks[i]) == 0);
        pthread_mutex_unlock(locks[i]);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            for time32 in (False, True):
                binary = str(Path(directory) / ('beacon32' if time32 else 'beacon'))
                command = ['gcc', '-std=c99', '-Wall', '-Werror', '-pthread',
                           '-fsanitize=undefined,float-cast-overflow', '-fno-sanitize-recover=all',
                           '-x', 'c', '-', '-lm', '-o', binary]
                if time32:
                    command.append('-DCHECK_TIME32')
                built = subprocess.run(command, input=fixture, text=True, capture_output=True)
                self.assertEqual(built.returncode, 0, built.stderr)
                for scenario in range(22):
                    with self.subTest(time32=time32, scenario=scenario):
                        result = subprocess.run([binary, str(scenario)], capture_output=True,
                                                text=True, timeout=5)
                        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
