"""Keep health scheduling independent of slow durable-metrics reconstruction."""
import asyncio
from threading import Event
import unittest
from llm_observability.metrics import MetricsSnapshot


class MetricsConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Exercise real worker threads, event-loop progress and cancellation without a model or cloud."""
    async def test_scrape_burst_does_not_block_loop_or_duplicate_rebuilds(self):
        """One expensive rebuild serves simultaneous scrapes while ordinary coroutines keep running."""
        entered, release = Event(), Event()
        calls = []

        def load():
            """Hold an actual blocking reader until the coroutine proves the loop is responsive."""
            calls.append(1); entered.set(); release.wait(2); return b'counter 1\n'

        snapshot = MetricsSnapshot(load)
        tasks = [asyncio.create_task(snapshot.read()) for _ in range(20)]
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            await asyncio.sleep(0)
            self.assertTrue(all(not task.done() for task in tasks))
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
        self.assertEqual(await asyncio.gather(*tasks), [b'counter 1\n']*20)
        self.assertEqual(await snapshot.read(), b'counter 1\n')
        self.assertEqual(len(calls), 1)
        await snapshot.close()

    async def test_expiry_rebuilds_and_failed_rebuild_is_visible(self):
        """Publish a fresh snapshot after expiry; never pass an expired body off as a successful failed refresh."""
        clock = [0]; calls = []

        def load():
            """Return an independently counted body and one controlled storage failure."""
            calls.append(1)
            if len(calls) == 2: raise ValueError('synthetic storage failure')
            return ('counter '+str(len(calls))+'\n').encode()

        snapshot = MetricsSnapshot(load, ttl=5, clock=lambda: clock[0])
        self.assertEqual(await snapshot.read(), b'counter 1\n')
        clock[0] = 6
        with self.assertRaises(ValueError): await snapshot.read()
        self.assertEqual(await snapshot.read(), b'counter 3\n')
        await snapshot.close()

    async def test_cancelled_scrape_does_not_cancel_shared_refresh(self):
        """A disconnected scraper cannot start another expensive render while the first is still running."""
        entered, release = Event(), Event(); calls = []

        def load():
            """Represent a native SQLite/render operation that survives one HTTP caller disconnecting."""
            calls.append(1); entered.set(); release.wait(2); return b'counter 1\n'

        snapshot = MetricsSnapshot(load)
        first = asyncio.create_task(snapshot.read())
        await asyncio.to_thread(entered.wait, 1)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError): await first
        second = asyncio.create_task(snapshot.read())
        await asyncio.sleep(0); self.assertFalse(second.done()); release.set()
        self.assertEqual(await second, b'counter 1\n'); self.assertEqual(len(calls), 1)
        await snapshot.close()


if __name__ == '__main__': unittest.main()
