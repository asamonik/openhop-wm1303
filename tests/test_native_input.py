"""Compile packet-forwarder input gates without a concentrator or UDP service."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native input checks')
class NativeInputTests(unittest.TestCase):
    def test_cad_scan_configuration_checks_types_and_bounds_before_casts(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        start = source.index('        const char *id = json_object_get_string(ch_obj, "id");')
        end = source.index('        cad_config.nb_channels++;', start) + len('        cad_config.nb_channels++;')
        block = source[start:end]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
typedef enum { JSONString, JSONNumber, JSONBoolean, JSONNull } JSON_Value_Type;
typedef struct { double number; JSON_Value_Type type; } JSON_Value;
typedef struct { JSON_Value fields[3]; } JSON_Object;
JSON_Value *json_object_get_value(JSON_Object *object, const char *key) {
    return &object->fields[strcmp(key, "freq_hz") == 0 ? 0 : strcmp(key, "sf") == 0 ? 1 : 2];
}
double json_value_get_number(JSON_Value *value) { return value->type == JSONNumber ? value->number : 0; }
JSON_Value_Type json_value_get_type(JSON_Value *value) { return value->type; }
double json_object_get_number(JSON_Object *object, const char *key) {
    return json_value_get_number(json_object_get_value(object, key));
}
const char *json_object_get_string(JSON_Object *object, const char *key) { return "fixture"; }
bool read_channel(JSON_Object *ch_obj) {
    struct {
        int nb_channels;
        struct { uint32_t freq_hz; uint8_t sf, bw; char id[32]; } channels[1];
    } cad_config = {0};
    for (int i = 0; i < 1; i++) {
''' + block + r'''
    }
    return cad_config.nb_channels == 1;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    int field = atoi(argv[1]);
    JSON_Object channel = {{{869525000, JSONNumber}, {7, JSONNumber}, {0, JSONNumber}}};
    for (int bw = 0; bw <= 3; bw++) {
        channel.fields[2].number = bw;
        assert(read_channel(&channel));
    }
    channel.fields[0].number = 800000000;
    assert(read_channel(&channel));
    channel.fields[0].number = 930000000;
    channel.fields[1].number = 12;
    assert(read_channel(&channel));
    channel = (JSON_Object){{{869525000, JSONNumber}, {7, JSONNumber}, {0, JSONNumber}}};
    const double invalid[3][8] = {
        {NAN, INFINITY, -INFINITY, 799999999, 930000001, 869525000.5, -1, 1e20},
        {7.5, NAN, INFINITY, -INFINITY, 6, 13, -1, 1e20},
        {256, NAN, INFINITY, -INFINITY, 4, 1.5, -1, 1e20},
    };
    for (int i = 0; i < 8; i++) {
        channel.fields[field].number = invalid[field][i];
        assert(!read_channel(&channel));
    }
    for (int type = JSONString; type <= JSONNull; type++) {
        if (type == JSONNumber) continue;
        channel.fields[field].type = type;
        assert(!read_channel(&channel));
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'cad-input-check')
            subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror',
                            '-fsanitize=undefined,float-cast-overflow', '-fno-sanitize-recover=all',
                            '-x', 'c', '-', '-lm', '-o', binary],
                           input=fixture, text=True, capture_output=True, check=True)
            for field in ('0', '1', '2'):
                with self.subTest(field=field):
                    result = subprocess.run([binary, field], capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_radio_parameters_are_checked_before_narrowing_conversions(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        blocks = {
            'timestamp': source.split('/* TX procedure: send on timestamp value */', 1)[1].split(
                '/* Concentrator timestamp is given, we consider it is a Class A downlink */', 1)[0],
            'bitrate': source.split('/* parse FSK bitrate (mandatory) */', 1)[1].split(
                '/* parse frequency deviation (mandatory) */', 1)[0],
            'deviation': source.split('/* parse frequency deviation (mandatory) */', 1)[1].split(
                '/* parse FSK preamble length (optional field, optimum min value enforced) */', 1)[0],
            'freq': source.split('/* parse target frequency (mandatory) */', 1)[1].split(
                '/* parse RF chain used for TX (mandatory) */', 1)[0],
            'power': source.split('/* parse TX power (optional field) */', 1)[1].split(
                '/* Parse modulation (mandatory) */', 1)[0],
            'lora': source.split('/* parse Lora preamble length (optional field, optimum min value enforced) */', 1)[1].split(
                '} else if (strcmp(str, "FSK") == 0)', 1)[0],
            'fsk': source.split('/* parse FSK preamble length (optional field, optimum min value enforced) */', 1)[1].split(
                '} else {\n                MSG("WARNING: [down] invalid modulation', 1)[0],
        }
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include <string.h>
#define MSG(...)
#define MIN_LORA_PREAMB 6
#define STD_LORA_PREAMB 8
#define MIN_FSK_PREAMB 3
#define STD_FSK_PREAMB 5
#define DR_FSK_MIN 500
#define DR_FSK_MAX 250000
typedef enum { JSONString, JSONNumber, JSONBoolean, JSONNull } JSON_Value_Type;
typedef struct { double number; JSON_Value_Type type; } JSON_Value;
typedef JSON_Value JSON_Object;
JSON_Value *json_object_get_value(JSON_Object *object, const char *key) { (void)key; return object; }
double json_value_get_number(JSON_Value *value) { return value->type == JSONNumber ? value->number : 0; }
JSON_Value_Type json_value_get_type(JSON_Value *value) { return value->type; }
void json_value_free(JSON_Value *value) { (void)value; }
static double parsed;
'''
        for name, block in blocks.items():
            field = {'freq': 'freq_hz', 'power': 'rf_power', 'lora': 'preamble', 'fsk': 'preamble',
                     'timestamp': 'count_us', 'bitrate': 'datarate', 'deviation': 'f_dev'}[name]
            fixture += f'''
bool read_{name}(double number, JSON_Value_Type type, int8_t antenna_gain) {{
    JSON_Value input = {{number, type}}, *val = &input, *root_val = &input;
    JSON_Object *txpk_obj = &input;
    struct {{ uint32_t freq_hz, count_us, datarate; int8_t rf_power; uint16_t preamble; uint8_t f_dev; }} txpkt = {{0}};
    int i;
    for (int once = 0; once < 1; once++) {{
{block}
        parsed = txpkt.{field};
        return true;
    }}
    return false;
}}
'''
        checks = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    bool (*read_value)(double, JSON_Value_Type, int8_t);
    double outside;
    if (strcmp(argv[1], "timestamp") == 0) {
        read_value = read_timestamp;
        outside = (double)UINT32_MAX + 1;
        assert(!read_value(outside, JSONNumber, 0));
        assert(read_value(0, JSONNumber, 0) && parsed == 0);
        assert(read_value(UINT32_MAX, JSONNumber, 0) && parsed == UINT32_MAX);
        assert(!read_value(17.5, JSONNumber, 0));
    } else if (strcmp(argv[1], "bitrate") == 0) {
        read_value = read_bitrate;
        outside = (double)UINT32_MAX + 1;
        assert(!read_value(outside, JSONNumber, 0));
        assert(read_value(DR_FSK_MIN, JSONNumber, 0) && parsed == DR_FSK_MIN);
        assert(read_value(DR_FSK_MAX, JSONNumber, 0) && parsed == DR_FSK_MAX);
        assert(!read_value(DR_FSK_MIN - 1, JSONNumber, 0));
        assert(!read_value(DR_FSK_MAX + 1, JSONNumber, 0));
        assert(!read_value(50000.5, JSONNumber, 0));
    } else if (strcmp(argv[1], "deviation") == 0) {
        read_value = read_deviation;
        outside = 456000; /* Used to wrap the uint8 kHz field to 200. */
        assert(!read_value(outside, JSONNumber, 0));
        assert(read_value(1000, JSONNumber, 0) && parsed == 1);
        assert(read_value(200000, JSONNumber, 0) && parsed == 200);
        assert(read_value(25500, JSONNumber, 0) && parsed == 25);
        assert(!read_value(999, JSONNumber, 0));
        assert(!read_value(200001, JSONNumber, 0));
        assert(!read_value(25000.5, JSONNumber, 0));
    } else if (strcmp(argv[1], "freq") == 0) {
        read_value = read_freq;
        assert(!read_freq(869.618 + ((double)UINT32_MAX + 1) / 1e6, JSONNumber, 0));
        assert(read_freq(869.618, JSONNumber, 0) && parsed == 869618000);
        assert(read_freq((double)UINT32_MAX / 1e6, JSONNumber, 0) && parsed == UINT32_MAX);
        assert(!read_freq(((double)UINT32_MAX + 1) / 1e6, JSONNumber, 0));
        assert(!read_freq(0, JSONNumber, 0));
        outside = 869.618 + ((double)UINT32_MAX + 1) / 1e6;
    } else if (strcmp(argv[1], "power") == 0) {
        read_value = read_power;
        assert(!read_power(-129, JSONNumber, 0));
        assert(read_power(20, JSONNumber, 3) && parsed == 17);
        assert(read_power(-128, JSONNumber, 0) && parsed == -128);
        assert(read_power(127, JSONNumber, 0) && parsed == 127);
        assert(!read_power(127, JSONNumber, -1));
        assert(!read_power(-128, JSONNumber, 1));
        assert(!read_power(20.5, JSONNumber, 0));
        outside = -129;
    } else {
        read_value = strcmp(argv[1], "lora") == 0 ? read_lora : read_fsk;
        assert(!read_value(65536, JSONNumber, 0));
        assert(read_value(17, JSONNumber, 0) && parsed == 17);
        assert(read_value(65535, JSONNumber, 0) && parsed == 65535);
        assert(read_value(0, JSONNumber, 0) && parsed == (read_value == read_lora ? 6 : 3));
        assert(!read_value(17.5, JSONNumber, 0));
        outside = 65536;
    }
    assert(!read_value(outside, JSONNumber, 0));
    assert(!read_value(-1e300, JSONNumber, 0));
    assert(!read_value(1e300, JSONNumber, 0));
    assert(!read_value(INFINITY, JSONNumber, 0));
    assert(!read_value(NAN, JSONNumber, 0));
    assert(!read_value(17, JSONString, 0));
    assert(!read_value(17, JSONBoolean, 0));
    assert(!read_value(17, JSONNull, 0));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'numeric-check')
            subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror', '-Wno-unused-variable',
                            '-fsanitize=undefined,float-cast-overflow', '-fno-sanitize-recover=all',
                            '-x', 'c', '-', '-lm', '-o', binary],
                           input=fixture + checks, text=True, capture_output=True, check=True)
            for part in blocks:
                with self.subTest(part=part):
                    result = subprocess.run([binary, part], capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_invalid_rf_chain_and_payload_before_transmission(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        rf = source.split('/* parse RF chain used for TX (mandatory) */', 1)[1].split(
            '/* parse TX power (optional field) */', 1)[0]
        payload = source.split('/* Parse payload length (mandatory) */', 1)[1].split(
            '/* free the JSON parse tree from memory */', 1)[0]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#define MSG(...)
#define LGW_RF_CHAIN_NB 2
typedef enum { JSONString, JSONNumber } JSON_Value_Type;
typedef struct { double number; JSON_Value_Type type; } JSON_Value;
typedef JSON_Value JSON_Object;
struct packet { uint8_t rf_chain; uint16_t size; uint8_t payload[256]; };
/* Give the fixture safe sentinel storage outside the two valid chains so
   missing range checks fail assertions rather than invoking undefined reads. */
bool tx_enable[256];
int decoded_size;
JSON_Value *json_object_get_value(JSON_Object *object, const char *key) { (void)key; return object; }
double json_value_get_number(JSON_Value *value) { return value->type == JSONNumber ? value->number : 0; }
JSON_Value_Type json_value_get_type(JSON_Value *value) { return value->type; }
const char *json_object_get_string(JSON_Object *object, const char *key) { (void)object; (void)key; return "fixture"; }
void json_value_free(JSON_Value *value) { (void)value; }
int b64_to_bin(const char *text, int length, uint8_t *out, int capacity) {
    (void)text; (void)length; (void)out; (void)capacity; return decoded_size;
}
'''
        rf_wrapper = r'''
bool read_rf(double number, JSON_Value_Type type) {
    JSON_Value input = {number, type}, *val, *root_val = &input;
    JSON_Object *txpk_obj = &input;
    struct packet txpkt = {0};
    for (int once = 0; once < 1; once++) {
''' + rf + r'''
        return true;
    }
    return false;
}
'''
        payload_wrapper = r'''
bool read_payload(double number, JSON_Value_Type type, int decoded) {
    JSON_Value input = {number, type}, *val, *root_val = &input;
    JSON_Object *txpk_obj = &input;
    struct packet txpkt = {0};
    const char *str;
    int i;
    decoded_size = decoded;
    for (int once = 0; once < 1; once++) {
''' + payload + r'''
        return true;
    }
    return false;
}
'''
        checks = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    for (int i = 0; i < 256; i++) tx_enable[i] = true;
    if (strcmp(argv[1], "rf") == 0) {
        assert(read_rf(0, JSONNumber));
        assert(read_rf(1, JSONNumber));
        assert(!read_rf(2, JSONNumber));
        assert(!read_rf(256, JSONNumber));
        assert(!read_rf(1.5, JSONNumber));
        assert(!read_rf(0, JSONString));
        assert(!read_rf(INFINITY, JSONNumber));
        assert(!read_rf(NAN, JSONNumber));
        tx_enable[1] = false;
        assert(!read_rf(1, JSONNumber));
    } else {
        assert(read_payload(2, JSONNumber, 2));
        assert(read_payload(255, JSONNumber, 255));
        assert(!read_payload(4, JSONNumber, 2));
        assert(!read_payload(2, JSONNumber, -1));
        assert(!read_payload(1.5, JSONNumber, 1));
        assert(!read_payload(65538, JSONNumber, 2));
        assert(!read_payload(0, JSONString, 0));
        assert(!read_payload(INFINITY, JSONNumber, 0));
        assert(!read_payload(NAN, JSONNumber, 0));
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'input-check')
            subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror', '-x', 'c', '-', '-lm', '-o', binary],
                           input=fixture + rf_wrapper + payload_wrapper + checks,
                           text=True, capture_output=True, check=True)
            for part in ('rf', 'payload'):
                with self.subTest(part=part):
                    result = subprocess.run([binary, part], capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
