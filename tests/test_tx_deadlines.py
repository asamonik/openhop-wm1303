"""Exercise long valid LoRa sends without sleeping for their RF airtime."""

import asyncio
import unittest
from unittest.mock import patch

from test_radio_bridge import tx, frame


class LongSendDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_airtime_does_not_extend_wait_before_dispatch(self):
        for priority in (False, True):
            with self.subTest(priority=priority):
                queue = tx.ChannelTXQueue('channel_e', 869525000, 62.5, 12, 5,
                                          preamble=2000, ttl_seconds=60)
                loop = asyncio.get_running_loop()
                actual_time = loop.time
                offset = 0
                with (patch.object(loop, 'time', side_effect=lambda: actual_time() + offset),
                      patch.object(loop, 'slow_callback_duration', float('inf'))):
                    caller = asyncio.create_task(queue.enqueue(frame(kind=3 if priority else 15)))
                    try:
                        await asyncio.sleep(0)
                        offset = 16 if priority else 76
                        for _ in range(10):
                            await asyncio.sleep(0)
                        self.assertTrue(caller.done())
                        self.assertEqual((await caller)['error'], 'timeout')
                        self.assertEqual(queue.stats['pending'], 0)
                        self.assertEqual(queue.stats['pending_priority'], 0)
                    finally:
                        queue.stop()
                        await asyncio.gather(caller, return_exceptions=True)

    async def test_inflight_wait_is_still_bounded_if_send_never_returns(self):
        queue = tx.ChannelTXQueue('channel_e', 869525000, 62.5, 12, 5, preamble=2000)
        started = asyncio.Event()

        async def send(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        scheduler = tx.GlobalTXScheduler(send, {'channel_e': queue})
        loop = asyncio.get_running_loop()
        actual_time = loop.time
        offset = 0
        with (patch.object(loop, 'time', side_effect=lambda: actual_time() + offset),
              patch.object(loop, 'slow_callback_duration', float('inf'))):
            await scheduler.start()
            caller = asyncio.create_task(queue.enqueue(frame()))
            try:
                await asyncio.wait_for(started.wait(), 1)
                offset = 200  # Beyond the full packet airtime plus ACK allowance.
                for _ in range(10):
                    await asyncio.sleep(0)
                self.assertTrue(caller.done())
                self.assertEqual((await caller)['error'], 'timeout')
                self.assertFalse(scheduler._task.done())
            finally:
                await scheduler.stop()
                await asyncio.gather(caller, return_exceptions=True)

    async def test_active_send_keeps_waiter_through_valid_long_airtime(self):
        for priority in (False, True):
            with self.subTest(priority=priority):
                queue = tx.ChannelTXQueue('channel_e', 869525000, 62.5, 12, 5,
                                          preamble=2000, ttl_seconds=60)
                payload = frame(kind=3 if priority else 15, payload=b'long')
                airtime_ms = tx.estimate_lora_airtime_ms(
                    len(payload), sf=12, bw_hz=62500, cr=5, preamble=2000)
                self.assertGreater(airtime_ms, 80000)
                started, finish = asyncio.Event(), asyncio.Event()

                async def send(*args, **kwargs):
                    started.set()
                    await finish.wait()
                    return {'ok': True, 'send_ms': 0.1, 'airtime_ms': airtime_ms}

                scheduler = tx.GlobalTXScheduler(send, {'channel_e': queue})
                loop = asyncio.get_running_loop()
                actual_time = loop.time
                offset = 0
                with (patch.object(loop, 'time', side_effect=lambda: actual_time() + offset),
                      patch.object(loop, 'slow_callback_duration', float('inf'))):
                    await scheduler.start()
                    caller = asyncio.create_task(queue.enqueue(payload))
                    try:
                        await asyncio.wait_for(started.wait(), 1)
                        offset = 80  # Beyond both old queue wait limits, inside this packet's airtime.
                        for _ in range(10):
                            await asyncio.sleep(0)
                        self.assertFalse(caller.done(), 'A valid in-flight TX must retain its caller')
                        self.assertEqual(queue.stats['total_failed'], 0)
                        finish.set()
                        result = await asyncio.wait_for(caller, 1)
                        self.assertTrue(result['ok'])
                        self.assertEqual(queue.stats['total_sent'], 1)
                    finally:
                        finish.set()
                        await scheduler.stop()
                        if not caller.done():
                            caller.cancel()
                        await asyncio.gather(caller, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
