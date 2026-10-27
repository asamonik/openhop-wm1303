"""Exercise packet router lifecycle races with real queue and route tasks."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_engine_protocol import router_module


class RouterLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_start_owns_only_one_consumer(self):
        router = router_module.PacketRouter(SimpleNamespace())
        consumers = set()

        async def start_and_capture():
            await router.start()
            consumers.add(router.router_task)

        try:
            await asyncio.gather(*(start_and_capture() for _ in range(8)))
            self.assertEqual(len(consumers), 1)
            consumer = router.router_task
            await router.stop()
            self.assertTrue(consumer.done())
            await router.stop()
            self.assertFalse(router.running)
            self.assertIsNone(router.router_task)
        finally:
            router.running = False
            for consumer in consumers:
                consumer.cancel()
            await asyncio.gather(*consumers, return_exceptions=True)

    async def test_restart_waits_for_previous_routes_to_drain(self):
        router = router_module.PacketRouter(SimpleNamespace())
        route_started = asyncio.Event()
        drain_started = asyncio.Event()
        release_route = asyncio.Event()
        second_routed = asyncio.Event()
        routed = []
        real_wait = asyncio.wait
        operations = []

        async def route(packet):
            route_started.set()
            await release_route.wait()
            routed.append(packet)
            if packet == 'second':
                second_routed.set()

        async def wait_during_stop(*args, **kwargs):
            drain_started.set()
            return await real_wait(*args, **kwargs)

        router._route_packet = route
        try:
            await router.start()
            consumer = router.router_task
            await router.enqueue('first')
            await asyncio.wait_for(route_started.wait(), 1)
            with patch.object(router_module.asyncio, 'wait', wait_during_stop):
                stopping = asyncio.create_task(router.stop())
                operations.append(stopping)
                await asyncio.wait_for(drain_started.wait(), 1)
                starting = asyncio.create_task(router.start())
                operations.append(starting)
                await asyncio.sleep(0)
                self.assertFalse(starting.done(), 'restart bypassed old routes still draining')
                self.assertFalse(router.running)
                self.assertTrue(consumer.done())
                release_route.set()
                await asyncio.wait_for(asyncio.gather(stopping, starting), 1)
            self.assertEqual(routed, ['first'])
            self.assertTrue(router.running)
            self.assertIsNot(router.router_task, consumer)
            await router.enqueue('second')
            await asyncio.wait_for(second_routed.wait(), 1)
            await router.stop()
            self.assertEqual(routed, ['first', 'second'])
            self.assertEqual(router._in_flight, 0)
            self.assertEqual(router._route_tasks, set())
        finally:
            release_route.set()
            await asyncio.gather(*operations, return_exceptions=True)
            await router.stop()


    async def test_shutdown_timeout_or_cancellation_joins_routes(self):
        for cancel_stop in (False, True):
            with self.subTest(cancel_stop=cancel_stop):
                router = router_module.PacketRouter(SimpleNamespace())
                route_started = asyncio.Event()
                drain_started = asyncio.Event()
                route_cancelled = asyncio.Event()
                release_cleanup = asyncio.Event()
                stopping = None

                async def route(packet):
                    route_started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        route_cancelled.set()
                        await release_cleanup.wait()

                async def controlled_drain(tasks, *, timeout):
                    self.assertEqual(timeout, 5.0)
                    drain_started.set()
                    if cancel_stop:
                        await asyncio.Event().wait()
                    return set(), set(tasks)

                router._route_packet = route
                try:
                    await router.start()
                    await router.enqueue('pending')
                    await asyncio.wait_for(route_started.wait(), 1)
                    route_task = next(iter(router._route_tasks))
                    with patch.object(router_module.asyncio, 'wait', controlled_drain):
                        stopping = asyncio.create_task(router.stop())
                        await asyncio.wait_for(drain_started.wait(), 1)
                        if cancel_stop:
                            stopping.cancel()
                        await asyncio.wait_for(route_cancelled.wait(), 1)
                        self.assertFalse(stopping.done(), 'stop returned before route cleanup')
                        release_cleanup.set()
                        if cancel_stop:
                            with self.assertRaises(asyncio.CancelledError):
                                await stopping
                        else:
                            await stopping
                    self.assertTrue(route_task.cancelled())
                    self.assertEqual(router._in_flight, 0)
                    self.assertEqual(router._route_tasks, set())
                    self.assertFalse(router.running)
                    self.assertIsNone(router.router_task)
                    await router.start()
                    self.assertTrue(router.running)
                    await router.stop()
                finally:
                    release_cleanup.set()
                    remaining = set(router._route_tasks)
                    if router.router_task is not None:
                        remaining.add(router.router_task)
                    if stopping is not None:
                        remaining.add(stopping)
                    for task in remaining:
                        task.cancel()
                    await asyncio.gather(*remaining, return_exceptions=True)


    async def test_cancelling_stop_while_joining_consumer_propagates(self):
        router = router_module.PacketRouter(SimpleNamespace())
        consumer_started = asyncio.Event()
        consumer_stopping = asyncio.Event()

        async def consumer():
            consumer_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                consumer_stopping.set()
                await asyncio.Event().wait()

        router._process_queue = consumer
        await router.start()
        await asyncio.wait_for(consumer_started.wait(), 1)
        worker = router.router_task
        stopping = asyncio.create_task(router.stop())
        try:
            await asyncio.wait_for(consumer_stopping.wait(), 1)
            stopping.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await stopping
            self.assertTrue(worker.done())
            self.assertIsNone(router.router_task)
            self.assertFalse(router.running)
        finally:
            stopping.cancel()
            worker.cancel()
            await asyncio.gather(stopping, worker, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
