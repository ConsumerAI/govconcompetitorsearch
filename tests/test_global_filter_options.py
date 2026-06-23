import unittest

from src.constants import ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES
from src.global_filter_options import (
    global_location_option_values,
    global_naics_option_values,
    global_set_aside_option_values,
)


class GlobalFilterOptionTests(unittest.TestCase):
    def test_global_naics_includes_common_codes(self):
        options, diag = global_naics_option_values()
        self.assertEqual(options[0], ALL_NAICS)
        self.assertGreater(len(options), 1000)
        joined = "\n".join(options)
        self.assertIn("541611", joined)
        self.assertIn("541990", joined)
        self.assertEqual(diag["source"], "static_reference")

    def test_global_set_aside_includes_known_codes(self):
        options, diag = global_set_aside_option_values()
        self.assertEqual(options[0], ALL_SET_ASIDES)
        self.assertTrue(any(option.startswith("WOSB") for option in options))
        self.assertEqual(diag["source"], "static_reference")

    def test_global_location_includes_states_and_countries(self):
        options, diag = global_location_option_values()
        self.assertEqual(options[0], ALL_LOCATIONS)
        self.assertIn("VA - Virginia", options)
        self.assertTrue(any("IRQ" in option for option in options))
        self.assertEqual(diag["source"], "static_reference")


if __name__ == "__main__":
    unittest.main()
