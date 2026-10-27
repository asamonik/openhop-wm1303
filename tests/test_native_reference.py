"""Exercise native configured GPS coordinates before casts and beacon encoding."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for coordinate input checks')
class NativeReferenceTests(unittest.TestCase):
    def test_coordinates_reject_invalid_input_before_native_conversion(self):
        source = (ROOT / 'overlay/hal/packet_forwarder/src/lora_pkt_fwd.c').read_text()
        start = source.index('    /* get reference coordinates */')
        parse = source[start:source.index('    /* Gateway GPS coordinates hardcoding', start)]
        start = source.index('    /* calculate the latitude and longitude that must be publicly reported */')
        encode = source[start:source.index('    /* gateway specific beacon fields */', start)]
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include <math.h>
#define MSG(...)
typedef enum { JSONString, JSONNumber, JSONBoolean, JSONNull, Missing } JSON_Value_Type;
typedef struct { double number; JSON_Value_Type type; } JSON_Value;
typedef struct { JSON_Value fields[3]; } JSON_Object;
static struct { double lat, lon; short alt; } reference_coord;
static int frees;
JSON_Value *json_object_get_value(JSON_Object *object, const char *key) {
    int index = !strcmp(key, "ref_latitude") ? 0 : !strcmp(key, "ref_longitude") ? 1 : 2;
    return object->fields[index].type == Missing ? NULL : &object->fields[index];
}
double json_value_get_number(JSON_Value *value) { return value->type == JSONNumber ? value->number : 0; }
JSON_Value_Type json_value_get_type(JSON_Value *value) { return value->type; }
void json_value_free(JSON_Value *root) { (void)root; frees++; }
static int parse_coordinates(JSON_Object *conf_obj) {
    JSON_Value *val = NULL, *root_val = NULL;
''' + parse + r'''
    json_value_free(root_val);
    return 0;
}
static void encode_coordinates(int32_t *lat, int32_t *lon) {
    int32_t field_latitude, field_longitude;
''' + encode + r'''
    *lat = field_latitude;
    *lon = field_longitude;
}
int main(int argc, char **argv) {
    assert(argc == 3);
    int field = atoi(argv[1]), scenario = atoi(argv[2]);
    JSON_Object object = {{{48.2, JSONNumber}, {16.3, JSONNumber}, {120, JSONNumber}}};
    const double low[] = {-90, -180, SHRT_MIN}, high[] = {90, 180, SHRT_MAX};
    const double invalid[] = {NAN, INFINITY, -INFINITY, -1e100, 1e100};
    bool accepted = false;
    switch (scenario) {
        case 0: object.fields[field].type = Missing; accepted = true; break;
        case 1: object.fields[field].number = low[field]; accepted = true; break;
        case 2: object.fields[field].number = high[field]; accepted = true; break;
        case 3: object.fields[field].number = 0; accepted = true; break;
        case 4: object.fields[field].number = 0.5; accepted = true; break;
        case 5: object.fields[field].number = low[field] - 1; break;
        case 6: object.fields[field].number = high[field] + 1; break;
        case 7: object.fields[field].type = JSONString; break;
        case 8: object.fields[field].type = JSONBoolean; break;
        case 9: object.fields[field].type = JSONNull; break;
        default:
            assert(scenario >= 10 && scenario < 15);
            object.fields[field].number = invalid[scenario - 10];
            break;
    }
    int result = parse_coordinates(&object);
    assert(frees == 1);
    assert((result == 0) == accepted);
    if (accepted) {
        double wanted = scenario == 0 ? 0 : object.fields[field].number;
        if (field == 2) wanted = trunc(wanted);
        double actual = field == 0 ? reference_coord.lat : field == 1 ? reference_coord.lon : reference_coord.alt;
        assert(actual == wanted);
        int32_t lat, lon;
        encode_coordinates(&lat, &lon);
        assert(lat >= -8388608 && lat <= 8388607);
        assert(lon >= -8388608 && lon <= 8388607);
        if (field < 2 && scenario == 1) assert((field ? lon : lat) == -8388608);
        if (field < 2 && scenario == 2) assert((field ? lon : lat) == 8388607);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'coordinates')
            built = subprocess.run(['gcc', '-std=c99', '-Wall', '-Werror',
                                    '-fsanitize=undefined,float-cast-overflow', '-fno-sanitize-recover=all',
                                    '-x', 'c', '-', '-lm', '-o', binary],
                                   input=fixture, text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            for field in range(3):
                for scenario in range(15):
                    with self.subTest(field=field, scenario=scenario):
                        result = subprocess.run([binary, str(field), str(scenario)],
                                                capture_output=True, text=True, timeout=5)
                        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
