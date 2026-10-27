"""Compile the actual LBT selection helpers with no radio or HAL I/O."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for isolated LBT checks')
class NativeLBTTests(unittest.TestCase):
    def test_exact_bandwidth_precedes_fallback_and_ack_reports_selected_setting(self):
        hal = (ROOT / 'overlay/hal/libloragw/src/loragw_lbt.c').read_text()
        forwarder = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        functions = [function(hal, signature) for signature in (
            'static bool is_equal_freq(', 'static bool is_matching_bw(', 'static int is_lbt_channel(',
        )]
        functions.append(function(forwarder, 'static void hal_lbt_lookup('))
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#define BW_62K5HZ 3
#define BW_125KHZ 4
#define BW_250KHZ 5
#define DEBUG_PRINTF(...)
struct channel { uint32_t freq_hz; uint8_t bandwidth; int8_t rssi_target_dbm; bool enable; };
struct lgw_conf_lbt_s { int nb_channel; struct channel channels[8]; };
static struct channel hal_lbt_channels[8];
static int hal_lbt_nb_channels;
static bool hal_lbt_enabled_global = true;
static int8_t hal_lbt_global_target_dbm = -80;
'''
        checks = r'''
static void check(struct lgw_conf_lbt_s *config, uint32_t freq, uint8_t bw, int expected) {
    hal_lbt_nb_channels = config->nb_channel;
    for (int i = 0; i < config->nb_channel; i++) hal_lbt_channels[i] = config->channels[i];
    assert(is_lbt_channel(config, freq, bw) == expected);
    bool enabled = false;
    int8_t threshold = 0;
    hal_lbt_lookup(freq, bw, &enabled, &threshold);
    assert(enabled == (expected >= 0 && config->channels[expected].enable));
    assert(threshold == (expected >= 0 ? config->channels[expected].rssi_target_dbm : -80));
}
int main(void) {
    struct lgw_conf_lbt_s config = {2, {
        {869525000, BW_125KHZ, 127, false},
        {869525000, BW_62K5HZ, -95, true},
    }};
    check(&config, 869525000, BW_62K5HZ, 1); /* Exact entry must not lose its LBT gate. */
    check(&config, 869535000, BW_62K5HZ, 1); /* Preserve the HAL frequency tolerance. */
    check(&config, 869535001, BW_62K5HZ, -1);
    check(&config, 869525000, BW_125KHZ, 0);
    check(&config, 869525000, BW_250KHZ, -1);
    config.nb_channel = 1;
    check(&config, 869525000, BW_62K5HZ, 0); /* Wide fallback remains supported. */
    config.channels[0].bandwidth = BW_62K5HZ;
    check(&config, 869525000, BW_125KHZ, -1); /* Never scan narrower than TX. */
    bool enabled = true;
    int8_t threshold = 0;
    hal_lbt_enabled_global = false;
    hal_lbt_lookup(869525000, BW_62K5HZ, &enabled, &threshold);
    assert(!enabled && threshold == -80);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'lbt-check')
            subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror', '-x', 'c', '-', '-o', binary],
                           input=fixture + '\n'.join(functions) + checks, text=True, check=True,
                           capture_output=True)
            result = subprocess.run([binary], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
