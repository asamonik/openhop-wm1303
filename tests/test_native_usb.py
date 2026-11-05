"""Exercise the actual SX1261 USB framing and queue recovery without USB I/O."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('gcc'), 'gcc is required for native USB checks')
class NativeUSBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        (directory / 'config.h').touch()
        (directory / 'loragw_aux.h').touch()
        (directory / 'loragw_mcu.h').write_text('''
#include <stddef.h>
#include <stdint.h>
#define MAX_SPI_COMMAND 4195
#define MCU_SPI_REQ_TYPE_READ_WRITE 0
#define MCU_SPI_TARGET_SX1261 1
int mcu_spi_write(int fd, uint8_t *buffer, size_t size);
int mcu_spi_store(uint8_t *buffer, size_t size);
int mcu_spi_flush(int fd);
''')
        (directory / 'sx1261_usb.h').write_text('''
#include <stdint.h>
#include "sx1261_defs.h"
typedef enum { LGW_COM_WRITE_MODE_SINGLE, LGW_COM_WRITE_MODE_BULK,
               LGW_COM_WRITE_MODE_UNKNOWN } lgw_com_write_mode_t;
int sx1261_usb_w(void *target, sx1261_op_code_t op, uint8_t *data, uint16_t size);
int sx1261_usb_r(void *target, sx1261_op_code_t op, uint8_t *data, uint16_t size);
int sx1261_usb_set_write_mode(lgw_com_write_mode_t mode);
int sx1261_usb_flush(void *target);
''')
        fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "loragw_mcu.h"
#include "sx1261_usb.h"
static int writes, stores, flushes, pending, fail_write, fail_store, fail_flush;
static uint16_t expected_size;
static sx1261_op_code_t expected_op;
static bool read_response;
static uint8_t pattern(unsigned i) { return (uint8_t)((i * 17) ^ 0x5A); }
static void check_frame(uint8_t *buffer, size_t size, uint8_t id) {
    assert(size == (size_t)expected_size + 6);
    assert(buffer[0] == id && buffer[1] == MCU_SPI_REQ_TYPE_READ_WRITE);
    assert(buffer[2] == MCU_SPI_TARGET_SX1261 && buffer[5] == expected_op);
    assert(((buffer[3] << 8) | buffer[4]) == expected_size + 1);
    for (unsigned i = 0; i < expected_size; i++) assert(buffer[i + 6] == pattern(i));
}
int mcu_spi_write(int fd, uint8_t *buffer, size_t size) {
    assert(fd == 42); writes++; check_frame(buffer, size, pending);
    if (read_response) for (unsigned i = 0; i < expected_size; i++) buffer[i + 6] ^= 0xFF;
    return fail_write ? -1 : 0;
}
int mcu_spi_store(uint8_t *buffer, size_t size) {
    stores++; check_frame(buffer, size, pending);
    if (fail_store) return -1;
    pending++; return 0;
}
int mcu_spi_flush(int fd) {
    assert(fd == 42 && pending > 0); flushes++;
    if (fail_flush) return -1;
    pending = 0; return 0;
}
static void fill(uint8_t *data, size_t size) {
    for (unsigned i = 0; i < size; i++) data[i] = pattern(i);
}
int main(int argc, char **argv) {
    assert(argc == 2); int scenario = atoi(argv[1]), fd = 42;
    uint8_t data[UINT16_MAX], original[UINT16_MAX];
    if (scenario == 0) {
        /* Header + 250 bytes crosses the original uint8_t frame-length limit.
         * 257 bytes covers a full RX buffer; 1546 covers the PRAM upload. */
        const unsigned sizes[] = {0, 1, 249, 250, 255, 257, 1546, MAX_SPI_COMMAND - 6};
        for (unsigned n = 0; n < sizeof(sizes)/sizeof(sizes[0]); n++) {
            expected_size = sizes[n]; expected_op = SX1261_WRITE_REGISTER;
            fill(data, expected_size); read_response = false;
            assert(sx1261_usb_w(&fd, expected_op, data, expected_size) == 0);
            expected_op = SX1261_READ_BUFFER; read_response = true;
            assert(sx1261_usb_r(&fd, expected_op, data, expected_size) == 0);
            for (unsigned i = 0; i < expected_size; i++) assert(data[i] == (uint8_t)(pattern(i) ^ 0xFF));
        }
        assert(writes == 16);
    } else if (scenario == 1) {
        fill(data, sizeof(data)); memcpy(original, data, sizeof(data));
        assert(sx1261_usb_w(NULL, SX1261_SET_RX, data, 3) == -1);
        assert(sx1261_usb_r(&fd, SX1261_GET_STATUS, NULL, 1) == -1);
        const unsigned sizes[] = {MAX_SPI_COMMAND - 5, UINT16_MAX};
        for (unsigned i = 0; i < sizeof(sizes)/sizeof(sizes[0]); i++) {
            assert(sx1261_usb_w(&fd, SX1261_WRITE_REGISTER, data, sizes[i]) == -1);
            assert(sx1261_usb_r(&fd, SX1261_READ_BUFFER, data, sizes[i]) == -1);
        }
        assert(writes == 0 && stores == 0 && memcmp(data, original, sizeof(data)) == 0);
        assert(sx1261_usb_set_write_mode(LGW_COM_WRITE_MODE_BULK) == 0);
        assert(sx1261_usb_r(&fd, SX1261_GET_STATUS, data, 1) == -1 && writes == 0);
        assert(sx1261_usb_set_write_mode(LGW_COM_WRITE_MODE_SINGLE) == 0);
        expected_size = 257; expected_op = SX1261_READ_BUFFER; read_response = true; fail_write = 1;
        assert(sx1261_usb_r(&fd, expected_op, data, expected_size) == -1);
        assert(memcmp(data, original, sizeof(data)) == 0);
    } else {
        expected_size = 1546; expected_op = SX1261_WRITE_REGISTER; fill(data, expected_size);
        assert(sx1261_usb_set_write_mode(LGW_COM_WRITE_MODE_BULK) == 0);
        assert(sx1261_usb_w(&fd, expected_op, data, expected_size) == 0 && pending == 1);
        if (scenario == 2) {
            fail_store = 1; assert(sx1261_usb_w(&fd, expected_op, data, expected_size) == -1 && pending == 1);
            fail_store = 0;
        }
        assert(sx1261_usb_w(&fd, expected_op, data, expected_size) == 0 && pending == 2);
        if (scenario == 2) {
            assert(sx1261_usb_flush(&fd) == 0 && flushes == 1 && pending == 0 && stores == 3);
            return 0;
        }
        fail_flush = 1; assert(sx1261_usb_flush(&fd) == -1 && flushes == 1 && pending == 2);
        /* The actual MCU retains its queued requests on a failed flush. */
        assert(sx1261_usb_set_write_mode(LGW_COM_WRITE_MODE_BULK) == 0);
        fail_flush = 0; assert(sx1261_usb_flush(&fd) == 0 && flushes == 2 && pending == 0);
        assert(sx1261_usb_set_write_mode(LGW_COM_WRITE_MODE_BULK) == 0);
        assert(sx1261_usb_flush(&fd) == 0 && flushes == 2); /* Empty queue. */
        assert(stores == 2 && writes == 0);
    }
    return 0;
}
'''
        cls.binary = str(directory / 'usb-framing')
        built = subprocess.run(
            ['gcc', '-std=c99', '-Wall', '-Werror', '-Wno-sign-compare',
             '-fsanitize=undefined', '-fno-sanitize-recover=all', '-fstack-protector-all',
             '-I', str(directory), '-I', str(ROOT / 'overlay/hal/libloragw/inc'),
             str(ROOT / 'overlay/hal/libloragw/src/sx1261_usb.c'), '-x', 'c', '-', '-o', cls.binary],
            input=fixture, capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stderr)

    def _run(self, scenario):
        result = subprocess.run([self.binary, str(scenario)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_large_frames_and_invalid_requests(self):
        for scenario in (0, 1):
            with self.subTest(scenario=scenario):
                self._run(scenario)

    def test_failed_queue_operations_preserve_request_ids_and_allow_flush_retry(self):
        for scenario in (2, 3):
            with self.subTest(scenario=scenario):
                self._run(scenario)


if __name__ == '__main__':
    unittest.main()
