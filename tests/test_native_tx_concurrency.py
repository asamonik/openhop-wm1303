"""Run the actual immediate/JIT TX blocks with deterministic radio interleaving."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native TX checks')
class NativeTXConcurrencyTests(unittest.TestCase):
    def test_queue_admission_publishes_tokens_before_worker_can_select(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        start = source.index('            /* Fallback: use JIT queue')
        downstream = source[start:source.index('            /* Send acknoledge datagram', start)]
        start = source.index('                    /* Insert beacon packet in JiT queue */')
        beacon = source[start:source.index('                    if (jit_result == JIT_ERROR_OK)', start)]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#define MSG(...)
#define LGW_HAL_SUCCESS 0
#define JIT_ERROR_INVALID 2
#define JIT_ERROR_OK 0
#define JIT_ERROR_FULL 1
#define JIT_PKT_TYPE_BEACON 3
struct lgw_pkt_tx_s { unsigned rf_chain; };
static pthread_mutex_t mx_concent = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mx_jit_access = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mx_meas_dw = PTHREAD_MUTEX_INITIALIZER;
static int jit_queue[2], meas_nb_tx_requested, admissions, token_stores, interleavings, fail;
static void attempt_worker(void) {
    if (pthread_mutex_trylock(&mx_jit_access) == 0) {
        interleavings++;
        pthread_mutex_unlock(&mx_jit_access);
    }
}
static int lgw_get_instcnt(uint32_t *count) { *count = 100000; return fail == 2 ? -1 : 0; }
static int jit_enqueue(int *queue, uint32_t count, struct lgw_pkt_tx_s *pkt, int type) {
    (void)queue; (void)count; (void)pkt; (void)type;
    admissions++;
    attempt_worker(); /* queue mutation/publication can precede token publication */
    return fail == 1 ? JIT_ERROR_FULL : JIT_ERROR_OK;
}
static void tx_ack_token_store(unsigned chain, uint8_t high, uint8_t low, struct lgw_pkt_tx_s *pkt) {
    (void)chain; (void)high; (void)low; (void)pkt;
    attempt_worker();
    token_stores++;
}
static void submit(void) {
    struct lgw_pkt_tx_s txpkt = {0};
    int jit_result = JIT_ERROR_OK, downlink_type = 0, warning_result = JIT_ERROR_OK;
    uint32_t current_concentrator_time;
    uint8_t buff_down[] = {2, 1, 2};
''' + downstream + r'''
    if (fail == 2) assert(jit_result == JIT_ERROR_INVALID);
}
static void submit_beacon(void) {
    struct lgw_pkt_tx_s beacon_pkt = {0};
    int jit_result = JIT_ERROR_OK, i;
    uint32_t current_concentrator_time;
    for (int once=0; once<1; once++) {
''' + beacon + r'''
    if (fail != 2) assert(jit_result == (fail ? JIT_ERROR_FULL : JIT_ERROR_OK));
    }
    (void)i;
}
int main(int argc, char **argv) {
    assert(argc == 3);
    bool beacon = atoi(argv[1]);
    fail = atoi(argv[2]);
    if (beacon) submit_beacon(); else submit();
    assert(admissions == (fail == 2 ? 0 : 1) && interleavings == 0);
    assert(token_stores == (!beacon && !fail));
    assert(meas_nb_tx_requested == !beacon);
    assert(pthread_mutex_trylock(&mx_jit_access) == 0);
    pthread_mutex_unlock(&mx_jit_access);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'admission-check')
            built = subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror', '-pthread',
                                    '-x', 'c', '-', '-o', binary], input=fixture,
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for beacon in (0, 1):
                for fail in (0, 1, 2):
                    with self.subTest(beacon=beacon, fail=fail):
                        result = subprocess.run([binary, str(beacon), str(fail)],
                                                capture_output=True, text=True, timeout=5)
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tx_transaction_excludes_other_sends_but_keeps_rx_mutex_available(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        start = source.index('            if (jit_result == JIT_ERROR_OK && sent_immediate) {')
        immediate = source[start:source.index('            /* Fallback: use JIT queue', start)]
        start = source.index('void thread_jit(void) {')
        jit = source[start:source.index('\n}\n', start) + 3]
        fixture = r'''
#define _DEFAULT_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/time.h>
#include <pthread.h>
#include <errno.h>
#include <math.h>
#define MSG(...)
#define MSG_DEBUG(...)
#define LGW_HAL_SUCCESS 0
#define LGW_HAL_ERROR -1
#define LGW_LBT_NOT_ALLOWED -2
#define LGW_RF_CHAIN_NB 1
#define TX_STATUS 1
#define TX_STATUS_UNKNOWN 0
#define TX_OFF 1
#define TX_FREE 2
#define TX_EMITTING 3
#define TX_SCHEDULED 4
#define IMMEDIATE 0
#define BW_125KHZ 4
#define BW_250KHZ 5
#define BW_500KHZ 6
#define DR_LORA_SF7 7
#define DR_LORA_SF8 8
#define DR_LORA_SF9 9
#define DR_LORA_SF10 10
#define DR_LORA_SF11 11
#define DR_LORA_SF12 12
enum jit_error_e { JIT_ERROR_OK, JIT_ERROR_TOO_LATE, JIT_ERROR_EMPTY };
enum jit_pkt_type_e { JIT_PKT_TYPE_DOWNLINK_CLASS_A, JIT_PKT_TYPE_BEACON };
struct lgw_pkt_tx_s { uint32_t freq_hz, datarate, count_us; uint16_t size; uint8_t rf_chain, bandwidth, tx_mode; };
typedef struct { bool detected; int16_t rssi_dbm; } sx1261_cad_result_t;
typedef struct {
    bool cad_enabled, cad_detected, lbt_enabled, lbt_pass;
    uint8_t cad_retries, lbt_retries;
    uint32_t cad_duration_ms;
    int16_t tx_noisefloor, lbt_rssi_dbm;
    int8_t lbt_threshold_dbm;
    const char *cad_reason, *tx_result;
} tx_ack_extra_t;
static pthread_mutex_t mx_concent = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mx_tx = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mx_jit_access = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mx_meas_dw = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mx_xcorr = PTHREAD_MUTEX_INITIALIZER;
static struct { bool enable; } spectral_scan_params;
static int jit_queue[1];
static bool exit_sig, quit_sig, custom_lbt_rx_restart_pending;
static struct timespec custom_lbt_rx_restart_after;
static struct timeval lgw_forensic_last_tx;
static double xtal_correct = 1;
static int meas_nb_beacon_sent, meas_nb_tx_fail, meas_nb_tx_ok, meas_nb_tx_requested;
static int scenario, scans, sends, acknowledgements;
static int status_reads, aborts, counter_reads, peeks, dequeues;
static int premature_sent_acks;
static bool token_claimed, token_expired;
static int stale_acks;
static int queue_admissions;
static void attempt_queue_admission(void) {
    if (pthread_mutex_trylock(&mx_jit_access) == 0) {
        queue_admissions++;
        pthread_mutex_unlock(&mx_jit_access);
    }
}
static const char *last_result, *last_cad_reason;
static int competing_attempts, competing_sends, scans_during_tx, rx_restarts_during_tx, rx_accesses;
static int unprotected_rx_restarts;
static bool radio_emitting, own_tx;
static uint64_t elapsed_us, emission_us;
static struct lgw_pkt_tx_s packet(void) {
    return (struct lgw_pkt_tx_s){.freq_hz=869525000, .datarate=7, .size=12, .bandwidth=BW_125KHZ};
}
static double difftimespec(struct timespec a, struct timespec b) { return a.tv_sec - b.tv_sec; }
static void attempt_competing_tx(void) {
    competing_attempts++;
    /* A receiver must still be able to use the concentrator during waits. */
    if (pthread_mutex_trylock(&mx_concent) == 0) {
        rx_accesses++;
        pthread_mutex_unlock(&mx_concent);
    }
    /* Model another native TX path acquiring its transaction guard. */
    if (pthread_mutex_trylock(&mx_tx) == 0) {
        competing_sends++;
        radio_emitting = true;
        pthread_mutex_unlock(&mx_tx);
    }
}
static void wait_ms(int delay) {
    (void)delay;
    if (scenario == 1 && scans == 1 && competing_attempts == 0) attempt_competing_tx();
}
static int test_usleep(unsigned delay) {
    elapsed_us += delay;
    if (scenario == 22 && queue_admissions == 0) attempt_queue_admission();
    /* The downstream sweep runs independently during JIT TX polling. */
    if (scenario == 20 && elapsed_us > 2000000 && !token_claimed && !token_expired) {
        token_expired = true;
        stale_acks++;
    }
    if (own_tx && elapsed_us >= emission_us) { radio_emitting = false; own_tx = false; }
    if (scenario == 18) quit_sig = true; /* Shutdown interrupts a long TX wait. */
    if (scenario == 7 && competing_attempts == 0) attempt_competing_tx();
    return 0;
}
#define usleep test_usleep
static int lgw_status(uint8_t chain, uint8_t select, uint8_t *status) {
    (void)chain; (void)select;
    status_reads++;
    *status = radio_emitting ? TX_EMITTING : TX_FREE;
    if (scenario == 3) { *status = TX_STATUS_UNKNOWN; return LGW_HAL_ERROR; }
    if (scenario == 10) *status = TX_STATUS_UNKNOWN; /* real HAL behavior after a failed register read */
    if (scenario == 11 || scenario == 30) *status = TX_OFF;
    if (scenario >= 12 && scenario <= 15 && status_reads == 1) *status = TX_SCHEDULED;
    if (scenario == 13) *status = TX_SCHEDULED; /* abort returns success but FSM is still scheduled */
    if (scenario == 15 && status_reads > 1) { *status = TX_STATUS_UNKNOWN; return LGW_HAL_ERROR; }
    return LGW_HAL_SUCCESS;
}
static int lgw_get_instcnt(uint32_t *count) {
    *count = 123; counter_reads++;
    return (scenario == 34 || (scenario == 33 && counter_reads == 1)) ? LGW_HAL_ERROR : LGW_HAL_SUCCESS;
}
static int jit_peek(int *queue, uint32_t count, int *index) {
    (void)queue; (void)count; *index = 0; peeks++;
    if (scenario == 21) attempt_queue_admission(); /* enqueue would sort away this selected index */
    if (scenario == 23) *index = -1; /* no packet is due yet */
    return scenario == 5 ? JIT_ERROR_EMPTY : JIT_ERROR_OK;
}
static int jit_dequeue(int *queue, int index, struct lgw_pkt_tx_s *pkt, enum jit_pkt_type_e *type) {
    (void)queue; (void)index; dequeues++; *pkt = packet(); *type = JIT_PKT_TYPE_DOWNLINK_CLASS_A;
    if (scenario >= 24 && scenario <= 32) {
        *type = JIT_PKT_TYPE_BEACON;
        if (scenario == 27) pkt->freq_hz = UINT32_MAX;
    }
    return scenario == 6 ? JIT_ERROR_EMPTY : JIT_ERROR_OK;
}
static void print_tx_status(uint8_t status) { (void)status; }
static int lgw_abort_tx(uint8_t chain) { (void)chain; aborts++; return scenario == 12 ? LGW_HAL_ERROR : LGW_HAL_SUCCESS; }
static int lgw_spectral_scan_abort(void) { return 0; }
static int custom_lbt_hal_bw_to_cad(uint8_t bw) { (void)bw; return 0; }
static void sx1261_set_tx_inhibit_rx(bool inhibit) { (void)inhibit; }
static void sx1261_lora_rx_restart_light(void) {
    if (radio_emitting) rx_restarts_during_tx++;
    if (pthread_mutex_trylock(&mx_concent) == 0) {
        unprotected_rx_restarts++;
        pthread_mutex_unlock(&mx_concent);
    }
}
static void sx1261_lora_rx_stop(void) { }
static int sx1261_cad_scan(uint32_t freq, uint8_t sf, uint8_t bw, sx1261_cad_result_t *result, bool skip) {
    (void)freq; (void)sf; (void)bw; (void)skip;
    scans++;
    if (radio_emitting) scans_during_tx++;
    result->detected = (scenario == 1 && scans == 1) || scenario == 8;
    result->rssi_dbm = -100;
    return scenario == 9 ? -1 : 0;
}
static void hal_lbt_lookup(uint32_t freq, uint8_t bw, bool *enabled, int8_t *threshold) {
    (void)freq; (void)bw; *enabled = false; *threshold = -80;
}
static int lgw_send(struct lgw_pkt_tx_s *pkt) {
    if (scenario == 28) assert(pkt->freq_hz == 869533695);
    (void)pkt; sends++;
    if (scenario == 4 || scenario == 31 || radio_emitting) return LGW_HAL_ERROR;
    own_tx = radio_emitting = true;
    elapsed_us = 0;
    emission_us = (scenario == 19 || scenario == 32) ? UINT64_MAX : scenario == 17 ? UINT64_C(4297015296) :
                  scenario >= 16 ? UINT64_C(3227648) : 1;
    return LGW_HAL_SUCCESS;
}
static uint32_t lgw_time_on_air(const struct lgw_pkt_tx_s *pkt) {
    (void)pkt;
    return scenario == 17 ? 4297015 : scenario >= 16 ? 3228 : 100;
}
static void hal_lbt_capture_result(tx_ack_extra_t *extra, int result) { extra->lbt_pass = result == 0; }
static bool tx_ack_token_claim(uint8_t chain, struct lgw_pkt_tx_s *pkt, uint8_t *high, uint8_t *low) {
    (void)chain; (void)pkt;
    if (token_expired) return false;
    token_claimed = true;
    *high = 1; *low = 2; return true;
}
static void send_tx_ack(uint8_t high, uint8_t low, enum jit_error_e error, int32_t value, tx_ack_extra_t *extra) {
    (void)high; (void)low; (void)error; (void)value; acknowledgements++;
    assert(extra != NULL);
    if (strcmp(extra->tx_result, "sent") == 0 && radio_emitting) premature_sent_acks++;
    last_result = extra->tx_result;
    last_cad_reason = extra->cad_reason;
}
/* Stop the simulated JIT worker only after the whole transaction. Real mutex
   operations still execute, including every failure/empty-queue exit. */
static int test_mutex_unlock(pthread_mutex_t *mutex) {
    if (mutex == &mx_tx) exit_sig = true;
    return pthread_mutex_unlock(mutex);
}
#define pthread_mutex_unlock test_mutex_unlock
static void immediate(void) {
    struct lgw_pkt_tx_s txpkt = packet();
    enum jit_error_e jit_result = JIT_ERROR_OK;
    bool sent_immediate = true;
    uint8_t buff_down[] = {2, 1, 2};
    int32_t warning_value = 0;
    for (int once = 0; once < 1; once++) {
''' + immediate + r'''
    }
}
''' + jit + r'''
int main(int argc, char **argv) {
    assert(argc == 3);
    scenario = atoi(argv[2]);
    radio_emitting = scenario == 2;
    if (scenario == 24) xtal_correct = NAN;
    if (scenario == 25) xtal_correct = INFINITY;
    if (scenario == 26) xtal_correct = 0;
    if (scenario == 27 || scenario == 28) xtal_correct = 1.00001;
    if (scenario == 29) xtal_correct = -1;
    if (strcmp(argv[1], "immediate") == 0) immediate(); else thread_jit();
    if (scenario == 33) {
        assert(counter_reads == 1 && peeks == 0 && dequeues == 0);
        assert(sends == 0 && acknowledgements == 0);
        exit_sig = false;
        thread_jit(); /* The same pending work can send after a transient failure. */
        assert(counter_reads == 2 && peeks == 1 && dequeues == 1);
    }
    assert(pthread_mutex_trylock(&mx_tx) == 0); /* every return/continue releases ownership */
    pthread_mutex_unlock(&mx_tx);
    assert(pthread_mutex_trylock(&mx_concent) == 0);
    pthread_mutex_unlock(&mx_concent);
    assert(pthread_mutex_trylock(&mx_jit_access) == 0);
    pthread_mutex_unlock(&mx_jit_access);
    if (scenario == 21) assert(queue_admissions == 0);
    if (scenario == 22) assert(queue_admissions == 1); /* admit new work during RF wait */
    assert(unprotected_rx_restarts == 0);
    assert(premature_sent_acks == 0);
    assert(stale_acks == 0);
    assert(pthread_mutex_trylock(&mx_xcorr) == 0);
    pthread_mutex_unlock(&mx_xcorr);
    if (scenario == 33 || scenario == 34) {
        assert(meas_nb_tx_fail == 0);
        if (scenario == 33) assert(sends == 1 && acknowledgements == 1);
        else assert(peeks == 0 && dequeues == 0 && sends == 0 && acknowledgements == 0);
    } else if (scenario >= 24 && scenario <= 32) {
        assert(acknowledgements == 0);
        if (scenario == 28) {
            assert(sends == 1 && meas_nb_beacon_sent == 1 && meas_nb_tx_ok == 1);
        } else if (scenario == 31 || scenario == 32) {
            assert(sends == 1 && meas_nb_tx_fail == 1 && meas_nb_beacon_sent == 0);
        } else {
            assert(scans == 0 && sends == 0 && meas_nb_tx_fail == 1);
            assert(meas_nb_beacon_sent == 0);
        }
    } else if (scenario >= 16 && scenario != 23) {
        assert(sends == 1 && acknowledgements == 1);
        if (scenario == 18 || scenario == 19) {
            assert(elapsed_us < emission_us);
            assert(quit_sig == (scenario == 18));
            assert(strcmp(last_result, "tx_unconfirmed") == 0);
            assert(meas_nb_tx_ok == 0 && meas_nb_tx_fail == 1);
        } else {
            assert(rx_restarts_during_tx == 0);
            assert(elapsed_us >= emission_us && elapsed_us < emission_us + 100000);
        }
    } else if (scenario == 1 || scenario == 7) {
        assert(competing_attempts == 1);
        assert(rx_accesses == 1);
        assert(competing_sends == 0);
        assert(scans_during_tx == 0);
        assert(rx_restarts_during_tx == 0);
        assert(sends == 1 && acknowledgements == 1);
    } else if (scenario == 0 || scenario == 4 || scenario == 8 || scenario == 9 || scenario == 14) {
        assert(sends == 1 && acknowledgements == 1);
        if (scenario == 8) assert(scans == 16);
        if (scenario == 9) assert(scans == 1);
    } else if (scenario == 2 || scenario == 3 || strcmp(argv[1], "jit") == 0) {
        assert(sends == 0);
        if (strcmp(argv[1], "jit") == 0 && scenario != 5 && scenario != 6 && scenario != 23) {
            assert(scans == 0 && acknowledgements == 1 && meas_nb_tx_fail == 1);
            assert(strcmp(last_result, "send_failed") == 0);
            assert(strcmp(last_cad_reason, "not_run") == 0);
        }
    }
    if (scenario >= 12 && scenario <= 15) assert(aborts == 1);
    if (scenario == 23) assert(sends == 0 && acknowledgements == 0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'tx-concurrency-check')
            built = subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror', '-Wno-unused-function',
                                    '-pthread', '-fsanitize=undefined,float-cast-overflow',
                                    '-fno-sanitize-recover=all', '-x', 'c', '-', '-o', binary], input=fixture,
                                   text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for path in ('immediate', 'jit'):
                cases = {0: 'clear', 1: 'CAD retry interleaving', 2: 'busy radio',
                         3: 'status error', 4: 'send failure', 7: 'TX completion interleaving',
                         8: 'all CAD retries busy', 9: 'CAD scan error',
                         16: 'slow narrow-band TX', 17: 'long-preamble TX',
                         18: 'shutdown during TX wait', 19: 'TX completion polling timeout'}
                if path == 'jit':
                    cases.update({5: 'empty JIT queue', 6: 'dequeue failure',
                                  10: 'unknown status after register read error', 11: 'radio off',
                                  12: 'scheduled abort error', 13: 'abort did not free radio',
                                  14: 'scheduled abort recovered radio', 15: 'post-abort status error',
                                  20: 'stale-token sweep during long TX',
                                  21: 'enqueue sorting between peek and dequeue',
                                  22: 'queue admission during RF wait', 23: 'no packet due yet',
                                  24: 'nonfinite beacon correction', 25: 'infinite beacon correction',
                                  26: 'zero beacon correction', 27: 'beacon frequency overflow',
                                  28: 'valid beacon correction', 29: 'negative beacon correction',
                                  30: 'beacon radio off', 31: 'beacon send failure',
                                  32: 'beacon completion timeout',
                                  33: 'counter read failure then recovery', 34: 'persistent counter read failure'})
                for scenario, description in cases.items():
                    with self.subTest(path=path, scenario=description):
                        result = subprocess.run([binary, path, str(scenario)], capture_output=True,
                                                text=True, timeout=5)
                        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
