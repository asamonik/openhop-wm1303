"""Validate native optional beacon numeric fields before integer conversion."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for beacon configuration checks')
class NativeBeaconConfigurationTests(unittest.TestCase):
    def test_beacon_configuration_types_ranges_and_frequency_plan(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        marker = '    /* Validate beacon numeric configuration before narrowing conversions. */'
        start = source.index(marker)
        block = source[start:source.index('    /* Auto-quit threshold (optional) */', start)]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#define MSG(...)
typedef enum { JSONString, JSONNumber, JSONBoolean, JSONNull, Missing } JSON_Value_Type;
typedef struct { double number; JSON_Value_Type type; } JSON_Value;
typedef struct { JSON_Value fields[8]; } JSON_Object;
static const char *names[] = {"beacon_period", "beacon_freq_hz", "beacon_freq_nb",
    "beacon_freq_step", "beacon_datarate", "beacon_bw_hz", "beacon_power", "beacon_infodesc"};
static uint32_t beacon_period = 128, beacon_freq_hz = 869525000, beacon_freq_step = 0, beacon_bw_hz = 125000;
static uint8_t beacon_freq_nb = 1, beacon_datarate = 9, beacon_infodesc = 0;
static int8_t beacon_power = 14;
static int frees;
JSON_Value *json_object_get_value(JSON_Object *object, const char *key) {
    for (unsigned i=0; i<8; i++) if (!strcmp(key, names[i])) {
        return object->fields[i].type == Missing ? NULL : &object->fields[i];
    }
    abort();
}
double json_value_get_number(JSON_Value *value) { return value->type == JSONNumber ? value->number : 0; }
JSON_Value_Type json_value_get_type(JSON_Value *value) { return value->type; }
void json_value_free(JSON_Value *root) { (void)root; frees++; }
static int parse(JSON_Object *conf_obj) {
    JSON_Value *val = NULL, *root_val = NULL;
''' + block + r'''
    json_value_free(root_val);
    return 0;
}
int main(int argc, char **argv) {
    assert(argc == 3);
    int field = atoi(argv[1]), scenario = atoi(argv[2]);
    const double low[] = {0, 0, 0, 0, 8, 125000, INT8_MIN, 0};
    const double high[] = {UINT32_MAX, UINT32_MAX, UINT8_MAX, UINT32_MAX, 12, 500000, INT8_MAX, UINT8_MAX};
    const double ordinary[] = {128, 869525000, 1, 0, 9, 125000, 14, 0};
    JSON_Object object = {0};
    for (unsigned i=0; i<8; i++) object.fields[i] = (JSON_Value){ordinary[i], JSONNumber};
    bool accepted = false;
    switch (scenario) {
        case 0: object.fields[field].type = Missing; accepted = true; break;
        case 1: object.fields[field].number = low[field]; accepted = true; break;
        case 2: object.fields[field].number = high[field]; accepted = true; break;
        case 3: object.fields[field].number = low[field] - 1; break;
        case 4: object.fields[field].number = high[field] + 1; break;
        case 5: object.fields[field].number = ordinary[field] + 0.5; break;
        case 6: object.fields[field].number = 1e100; break;
        case 7: object.fields[field].number = NAN; break;
        case 8: object.fields[field].number = INFINITY; break;
        case 9: object.fields[field].type = JSONString; break;
        case 10: object.fields[field].type = JSONBoolean; break;
        case 11: object.fields[field].type = JSONNull; break;
        case 12:
            if (field == 0) object.fields[0].number = 5;
            else if (field == 4) object.fields[4].number = 11;
            else if (field == 5) object.fields[5].number = 250000;
            else { object.fields[2].number = 3; object.fields[3].number = UINT32_MAX; }
            break;
        default: abort();
    }
    int result = parse(&object);
    assert(frees == 1);
    assert((result == 0) == accepted);
    if (accepted) {
        const double actual[] = {beacon_period, beacon_freq_hz, beacon_freq_nb, beacon_freq_step,
            beacon_datarate, beacon_bw_hz, beacon_power, beacon_infodesc};
        assert(actual[field] == (scenario == 0 ? ordinary[field] : object.fields[field].number));
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'beacon-config')
            built = subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror',
                                    '-fsanitize=undefined,float-cast-overflow', '-fno-sanitize-recover=all',
                                    '-x', 'c', '-', '-lm', '-o', binary],
                                   input=fixture, text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for field in range(8):
                for scenario in range(13):
                    with self.subTest(field=field, scenario=scenario):
                        result = subprocess.run([binary, str(field), str(scenario)],
                                                capture_output=True, text=True, timeout=5)
                        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
