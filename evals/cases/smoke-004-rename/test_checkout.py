import unittest

from checkout import checkout


class CheckoutTest(unittest.TestCase):
    def test_shipping(self):
        self.assertEqual(checkout([10, 20]), 35)
        self.assertEqual(checkout([], shipping=0), 0)


if __name__ == "__main__":
    unittest.main()
