import asyncio
import unittest

from xianyu_agent.browser_pool import BrowserPool


class BrowserPoolTests(unittest.TestCase):
    def test_can_construct_without_current_event_loop(self):
        try:
            old_loop = asyncio.get_event_loop()
        except RuntimeError:
            old_loop = None

        asyncio.set_event_loop(None)
        try:
            pool = BrowserPool()
        finally:
            if old_loop is not None and not old_loop.is_running():
                old_loop.close()
            asyncio.set_event_loop(None)

        self.assertEqual(pool.max_size, 3)


if __name__ == "__main__":
    unittest.main()
