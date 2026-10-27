"""Compile native pending-ACK ownership with deterministic queue/time state."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native ACK checks')
class NativeACKTokenTests(unittest.TestCase):
    def test_queue_membership_retention_reclamation_and_zero_timestamp(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        start = source.index('#define TX_ACK_TOKEN_SLOTS')
        declarations = source[start:source.index('/* -------------------------------------------------------------------------- */', start)]
        helpers = '\n'.join(function(source, signature) for signature in (
            'static bool tx_ack_token_matches_packet(', 'static bool tx_ack_token_is_queued('))
        functions = '\n'.join(function(source, signature) for signature in (
            'static void tx_ack_token_store(', 'static bool tx_ack_token_claim(',
            'static void tx_ack_token_sweep_stale('))
        fixture = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <pthread.h>
#define MSG(...)
#define LGW_RF_CHAIN_NB 2
#define JIT_QUEUE_MAX 32
#define JIT_PKT_TYPE_BEACON 3
#define JIT_ERROR_TOO_LATE 1
struct lgw_pkt_tx_s { uint32_t count_us, freq_hz, datarate; uint16_t size; uint8_t bandwidth; };
static struct {
    unsigned num_pkt;
    struct { struct lgw_pkt_tx_s pkt; int pkt_type; } nodes[32];
} jit_queue[2];
static pthread_mutex_t mx_jit_access = PTHREAD_MUTEX_INITIALIZER;
static double now;
static unsigned acknowledgements;
static uint8_t acknowledged[64];
static double difftimespec(struct timespec a, struct timespec b) {
    return (double)(a.tv_sec - b.tv_sec) + (a.tv_nsec - b.tv_nsec) / 1e9;
}
static int test_clock_gettime(int clock, struct timespec *ts) {
    (void)clock;
    ts->tv_sec = (time_t)now;
    ts->tv_nsec = (long)((now - ts->tv_sec) * 1e9);
    return 0;
}
#define clock_gettime test_clock_gettime
static int send_tx_ack(uint8_t high, uint8_t low, int error, int value, const void *extra) {
    (void)high; (void)value; (void)extra;
    assert(error == JIT_ERROR_TOO_LATE);
    /* Network I/O must not retain the compound queue-operation lock. */
    assert(pthread_mutex_trylock(&mx_jit_access) == 0);
    pthread_mutex_unlock(&mx_jit_access);
    acknowledged[acknowledgements++] = low;
    return 0;
}
''' + declarations + helpers + functions + r'''
static struct lgw_pkt_tx_s packet(uint32_t count) {
    return (struct lgw_pkt_tx_s){.count_us=count, .freq_hz=869525000,
        .datarate=7, .size=16, .bandwidth=4};
}
static void enqueue(struct lgw_pkt_tx_s pkt, uint8_t token) {
    pthread_mutex_lock(&mx_jit_access);
    jit_queue[0].nodes[jit_queue[0].num_pkt++].pkt = pkt;
    tx_ack_token_store(0, 0x12, token, &pkt);
    pthread_mutex_unlock(&mx_jit_access);
}
static void claim(struct lgw_pkt_tx_s pkt, uint8_t expected_token) {
    uint8_t high = 0, low = 0;
    pthread_mutex_lock(&mx_jit_access);
    assert(tx_ack_token_claim(0, &pkt, &high, &low));
    pthread_mutex_unlock(&mx_jit_access);
    assert(high == 0x12 && low == expected_token);
}
int main(int argc, char **argv) {
    assert(argc == 2);
    int scenario = atoi(argv[1]);
    now = 100;
    switch (scenario) {
        case 0: /* Still scheduled in the future after the old two-second sweep. */
            enqueue(packet(10000000), 1);
            now += 3;
            tx_ack_token_sweep_stale();
            assert(acknowledgements == 0);
            claim(packet(10000000), 1);
            break;
        case 1: /* Store's old ten-second GC must retain real future entries. */
            enqueue(packet(200000000), 1);
            now += 15;
            enqueue(packet(300000000), 2);
            claim(packet(200000000), 1);
            claim(packet(300000000), 2);
            break;
        case 2: /* Reclaim a removed packet, never evict a valid oldest entry. */
            for (unsigned i=0; i<32; i++) {
                enqueue(packet(1000000 * (i + 1)), i + 1);
                now += .01;
            }
            jit_queue[0].num_pkt--; /* JIT expired the final queued packet. */
            enqueue(packet(100000000), 33);
            for (unsigned i=0; i<31; i++) claim(packet(1000000 * (i + 1)), i + 1);
            claim(packet(100000000), 33);
            break;
        case 3: /* A real purge gets one final rejection, never duplicate ACKs. */
            enqueue(packet(1000000), 1);
            jit_queue[0].num_pkt = 0;
            now += 3;
            tx_ack_token_sweep_stale();
            tx_ack_token_sweep_stale();
            assert(acknowledgements == 1 && acknowledged[0] == 1);
            break;
        case 4: /* Worker-owned in-flight tokens are not pending table entries. */
            enqueue(packet(1000000), 1);
            jit_queue[0].num_pkt = 0;
            claim(packet(1000000), 1);
            now += 3;
            tx_ack_token_sweep_stale();
            assert(acknowledgements == 0);
            break;
        case 5: /* Timestamp zero is valid at timer wrap, not a wildcard. */
            enqueue(packet(1000000), 1);
            now += .01;
            enqueue(packet(0), 2);
            claim(packet(0), 2);
            claim(packet(1000000), 1);
            break;
        case 6: /* Rejection must not clobber output parameters. */
            enqueue(packet(1000000), 1);
            uint8_t high = 99, low = 99;
            struct lgw_pkt_tx_s wrong = packet(1000001);
            assert(!tx_ack_token_claim(0, &wrong, &high, &low));
            assert(high == 99 && low == 99);
            claim(packet(1000000), 1);
            break;
        case 7: /* Once purged, no arbitrary wall-clock delay is needed. */
            enqueue(packet(1000000), 1);
            jit_queue[0].num_pkt = 0;
            tx_ack_token_sweep_stale();
            assert(acknowledgements == 1 && acknowledged[0] == 1);
            break;
        default: abort();
    }
    assert(pthread_mutex_trylock(&mx_jit_access) == 0);
    pthread_mutex_unlock(&mx_jit_access);
    assert(pthread_mutex_trylock(&mx_tx_ack_tokens) == 0);
    pthread_mutex_unlock(&mx_tx_ack_tokens);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'ack-token-check')
            built = subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror', '-pthread',
                                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                                    '-x', 'c', '-', '-o', binary], input=fixture,
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for scenario in range(8):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([binary, str(scenario)], capture_output=True,
                                            text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
