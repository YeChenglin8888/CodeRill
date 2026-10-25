import unittest

from users import active_names


class UserTests(unittest.TestCase):
    def test_active_names(self):
        users = [
            {"name": " Alice ", "active": True},
            {"name": "bob", "active": False},
            {"name": "ALAN", "active": True},
        ]
        self.assertEqual(active_names(users, "al"), ["ALAN", "Alice"])


if __name__ == "__main__":
    unittest.main()
