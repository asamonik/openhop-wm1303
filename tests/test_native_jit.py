"""Exercise the native JIT queue's expiration and timer-boundary behavior."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native JIT checks')
class NativeJITTests(unittest.TestCase):
    def test_airtime_reservations_and_asap_horizon_cannot_wrap(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/jitqueue.c').read_text()
        source = source.replace('#include "trace.h"', '').replace('#include "jitqueue.h"', '')
        fixture = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#define MSG(...)
#define MSG_DEBUG(...)
#define DEBUG_JIT 0
#define DEBUG_JIT_ERROR 0
#define DEBUG_BEACON 0
#define JIT_QUEUE_MAX 32
#define JIT_NUM_BEACON_IN_QUEUE 3
#define TIMESTAMPED 1
enum jit_error_e { JIT_ERROR_OK, JIT_ERROR_TOO_LATE, JIT_ERROR_TOO_EARLY,
    JIT_ERROR_FULL, JIT_ERROR_EMPTY, JIT_ERROR_COLLISION_PACKET,
    JIT_ERROR_COLLISION_BEACON, JIT_ERROR_INVALID };
enum jit_pkt_type_e { JIT_PKT_TYPE_DOWNLINK_CLASS_A, JIT_PKT_TYPE_DOWNLINK_CLASS_B,
    JIT_PKT_TYPE_DOWNLINK_CLASS_C, JIT_PKT_TYPE_BEACON };
struct lgw_pkt_tx_s { uint32_t count_us; uint16_t size; uint8_t tx_mode; };
struct jit_node_s {
    struct lgw_pkt_tx_s pkt;
    enum jit_pkt_type_e pkt_type;
    uint32_t pre_delay, post_delay;
};
struct jit_queue_s { uint8_t num_pkt, num_beacon; struct jit_node_s nodes[JIT_QUEUE_MAX]; };
static uint32_t airtime_ms = 10;
uint32_t lgw_time_on_air(struct lgw_pkt_tx_s *packet) { (void)packet; return airtime_ms; }
void jit_print_queue(struct jit_queue_s *queue, bool show_all, int debug_level);
''' + source + r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    int scenario = atoi(argv[1]);
    struct jit_queue_s queue = {0};
    struct lgw_pkt_tx_s packet = {.count_us=1000000, .size=16};
    switch (scenario) {
        case 0: /* F44's valid long LoRa preamble cannot fit this queue's us field. */
            airtime_ms = 4297015;
            assert(jit_enqueue(&queue, 0, &packet, JIT_PKT_TYPE_DOWNLINK_CLASS_A) == JIT_ERROR_INVALID);
            assert(queue.num_pkt == 0);
            break;
        case 1: /* Reservation margins must not overflow and hide a collision. */
            assert(jit_collision_test(50000000, 41500, 10000,
                                      1000000, 41500, UINT32_MAX - 500));
            assert(jit_collision_test(1000000, 41500, UINT32_MAX - 500,
                                      50000000, 41500, 10000));
            break;
        case 2: /* An ASAP slot beyond the native horizon cannot be kept queued. */
            queue.num_pkt = 1;
            queue.nodes[0] = (struct jit_node_s){.pkt={.count_us=80000},
                .pkt_type=JIT_PKT_TYPE_DOWNLINK_CLASS_A, .pre_delay=41500, .post_delay=4294000000u};
            assert(jit_enqueue(&queue, 0, &packet, JIT_PKT_TYPE_DOWNLINK_CLASS_C) == JIT_ERROR_TOO_EARLY);
            assert(queue.num_pkt == 1);
            break;
        case 3: /* Ordinary class A/B/C admissions still handle real counter wrap. */
            for (int type=JIT_PKT_TYPE_DOWNLINK_CLASS_A; type<=JIT_PKT_TYPE_DOWNLINK_CLASS_C; type++) {
                uint32_t now = UINT32_MAX - 50000;
                jit_queue_init(&queue);
                packet.count_us = now + 1000000;
                assert(jit_enqueue(&queue, now, &packet, type) == JIT_ERROR_OK);
                assert(queue.num_pkt == 1 && queue.nodes[0].post_delay == 10000);
            }
            break;
        case 4:
            airtime_ms = 0;
            assert(jit_enqueue(&queue, 0, &packet, JIT_PKT_TYPE_DOWNLINK_CLASS_A) == JIT_ERROR_INVALID);
            assert(queue.num_pkt == 0);
            break;
        case 5: /* Enqueue and peek agree at the exclusive maximum horizon. */
            for (int type=JIT_PKT_TYPE_DOWNLINK_CLASS_A; type<=JIT_PKT_TYPE_BEACON; type++) {
                if (type == JIT_PKT_TYPE_DOWNLINK_CLASS_C) continue; /* C assigns an ASAP time. */
                jit_queue_init(&queue);
                packet.count_us = TX_MAX_ADVANCE_DELAY;
                assert(jit_enqueue(&queue, 0, &packet, type) == JIT_ERROR_TOO_EARLY);
                assert(queue.num_pkt == 0);
                packet.count_us--;
                assert(jit_enqueue(&queue, 0, &packet, type) == JIT_ERROR_OK);
                assert(queue.num_pkt == 1);
                int index = 123;
                assert(jit_peek(&queue, 0, &index) == JIT_ERROR_OK);
                assert(queue.num_pkt == 1 && index == -1); /* Pending, not immediately purged. */
            }
            break;
        case 6: /* A beacon may cross the real counter wrap within the horizon. */
            packet.count_us = (UINT32_MAX - 50000u) + 1000000u;
            assert(jit_enqueue(&queue, UINT32_MAX - 50000u, &packet, JIT_PKT_TYPE_BEACON) == JIT_ERROR_OK);
            assert(queue.num_pkt == 1 && queue.num_beacon == 1);
            break;
        default: abort();
    }
    assert(pthread_mutex_trylock(&mx_jit_queue) == 0);
    pthread_mutex_unlock(&mx_jit_queue);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'jit-reservation-check')
            built = subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror', '-pthread',
                                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                                    '-x', 'c', '-', '-o', binary],
                                   input=fixture, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for scenario in range(7):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([binary, str(scenario)], capture_output=True,
                                            text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_expiration_restarts_search_without_stale_or_negative_index(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/jitqueue.c').read_text()
        functions = '\n'.join(function(source, signature) for signature in (
            'bool jit_queue_is_empty(', 'int compare(', 'void jit_sort_queue(', 'enum jit_error_e jit_peek('))
        fixture = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#define MSG(...)
#define MSG_DEBUG(...)
#define TX_MAX_ADVANCE_DELAY 512000000u
#define TX_JIT_DELAY 40000u
#define JIT_QUEUE_MAX 32
enum jit_error_e { JIT_ERROR_OK, JIT_ERROR_EMPTY, JIT_ERROR_INVALID };
enum jit_pkt_type_e { JIT_PKT_TYPE_DOWNLINK_CLASS_A, JIT_PKT_TYPE_BEACON };
struct jit_node_s { struct { uint32_t count_us; } pkt; enum jit_pkt_type_e pkt_type; };
struct jit_queue_s { uint8_t num_pkt, num_beacon; struct jit_node_s nodes[JIT_QUEUE_MAX]; };
static pthread_mutex_t mx_jit_queue = PTHREAD_MUTEX_INITIALIZER;
''' + functions + r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    int scenario = atoi(argv[1]);
    struct jit_queue_s queue = {0};
    uint32_t now = 100000;
    int index = -1;
    switch (scenario) {
        case 0: /* The only queued packet expired: formerly reads nodes[-1]. */
            queue.num_pkt = 1;
            queue.nodes[0].pkt.count_us = now - 1;
            break;
        case 1: /* Purging must revisit the packet swapped into index zero. */
            queue.num_pkt = 2;
            queue.nodes[0].pkt.count_us = now - 1;
            queue.nodes[1].pkt.count_us = now + 5000;
            break;
        case 2: /* Every expiration, including beacons, must be accounted for. */
            queue.num_pkt = 3;
            queue.num_beacon = 1;
            queue.nodes[0].pkt.count_us = now - 30;
            queue.nodes[1].pkt.count_us = now - 20;
            queue.nodes[1].pkt_type = JIT_PKT_TYPE_BEACON;
            queue.nodes[2].pkt.count_us = now - 10;
            break;
        case 3: /* Sorting after purge invalidates the previously saved index. */
            queue.num_pkt = 3;
            queue.nodes[0].pkt.count_us = now + 10000;
            queue.nodes[1].pkt.count_us = now - 1;
            queue.nodes[2].pkt.count_us = now + 5000;
            break;
        case 4: /* Real 32-bit timer wrap: expired and future packets together. */
            now = UINT32_MAX - 2000;
            queue.num_pkt = 3;
            queue.nodes[0].pkt.count_us = now - 1;
            queue.nodes[1].pkt.count_us = now + 5000;
            queue.nodes[2].pkt.count_us = now + 50000;
            break;
        case 5: /* Sorting across INT32_MAX must not overflow signed int. */
            now = INT32_MAX - 2000;
            queue.num_pkt = 2;
            queue.nodes[0].pkt.count_us = now + 1000;
            queue.nodes[1].pkt.count_us = now + 5000;
            jit_sort_queue(&queue);
            assert(queue.nodes[0].pkt.count_us == now + 1000);
            assert(queue.nodes[1].pkt.count_us == now + 5000);
            break;
        case 6: /* A future packet outside the send window is kept queued. */
            queue.num_pkt = 1;
            queue.nodes[0].pkt.count_us = now + 50000;
            break;
        case 7: /* Already empty. */
            break;
        case 8: /* Sort chronologically across the counter wrap, including ties. */
            now = UINT32_MAX - 2000;
            queue.num_pkt = 4;
            queue.nodes[0].pkt.count_us = now + 10000;
            queue.nodes[1].pkt.count_us = now + 1000;
            queue.nodes[2].pkt.count_us = now + 5000;
            queue.nodes[3].pkt.count_us = now + 5000;
            jit_sort_queue(&queue);
            assert(queue.nodes[0].pkt.count_us == now + 1000);
            assert(queue.nodes[1].pkt.count_us == now + 5000);
            assert(queue.nodes[2].pkt.count_us == now + 5000);
            assert(queue.nodes[3].pkt.count_us == now + 10000);
            return 0;
        default: abort();
    }
    int result = jit_peek(&queue, now, &index);
    if (scenario == 0 || scenario == 2 || scenario == 7) {
        assert(result == JIT_ERROR_EMPTY && index == -1);
        assert(queue.num_pkt == 0 && queue.num_beacon == 0);
    } else if (scenario == 6) {
        assert(result == JIT_ERROR_OK && index == -1 && queue.num_pkt == 1);
    } else {
        assert(result == JIT_ERROR_OK && index >= 0 && index < queue.num_pkt);
        assert(queue.nodes[index].pkt.count_us == now + (scenario == 5 ? 1000 : 5000));
        assert(queue.num_pkt == (scenario == 1 ? 1 : 2));
    }
    assert(pthread_mutex_trylock(&mx_jit_queue) == 0);
    pthread_mutex_unlock(&mx_jit_queue);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'jit-check')
            built = subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror', '-pthread',
                                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                                    '-x', 'c', '-', '-o', binary],
                                   input=fixture, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for scenario in range(9):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([binary, str(scenario)], capture_output=True,
                                            text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
