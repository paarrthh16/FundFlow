from django.test import SimpleTestCase

from reconciliation.services.normalization import name_found, normalize_name, normalize_reference


class ReferenceNormalizationTests(SimpleTestCase):
    def test_reference_normalization(self):
        cases = {
            "cc-1003": "CC1003",
            "CC-1003": "CC1003",
            "cc1001": "CC1001",
            " Cc 1003 ": "CC1003",
            "WIRE INCOMING RAHUL SHAH TRUST REF CC-1002": "WIREINCOMINGRAHULSHAHTRUSTREFCC1002",
            "FT20260403A": "FT20260403A",
            "--": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_reference(raw), expected)


class NameNormalizationTests(SimpleTestCase):
    def test_name_normalization_drops_trailing_suffix_keeps_trust(self):
        cases = {
            "Haller Capital LLC": ("HALLER", "CAPITAL"),
            "Meridian Partners LP": ("MERIDIAN", "PARTNERS"),
            "Cedar Grove Fund LP": ("CEDAR", "GROVE", "FUND"),
            "Patel Family Trust": ("PATEL", "FAMILY", "TRUST"),
            "Acme Ltd": ("ACME",),
            "Acme Inc.": ("ACME",),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_name(raw), expected)

    def test_punctuation_and_whitespace_become_single_spaces(self):
        self.assertEqual(normalize_name("  O'Brien  &  Sons,Holdings-LLC "), ("O", "BRIEN", "SONS", "HOLDINGS"))

    def test_only_one_trailing_suffix_is_dropped(self):
        self.assertEqual(normalize_name("LP Holdings LLC"), ("LP", "HOLDINGS"))
        self.assertEqual(normalize_name("Acme LLC LLC"), ("ACME", "LLC"))
        self.assertEqual(normalize_name("LLC"), ())

    def test_description_keeps_trailing_suffix(self):
        self.assertEqual(
            normalize_name("wire incoming OAKRIDGE HOLDINGS LLC", drop_suffix=False),
            ("WIRE", "INCOMING", "OAKRIDGE", "HOLDINGS", "LLC"),
        )


class NameFoundTests(SimpleTestCase):
    def found(self, name, description):
        return name_found(normalize_name(name), normalize_name(description, drop_suffix=False))

    def test_name_found_as_whole_words(self):
        self.assertTrue(self.found("Patel Family Trust", "WIRE INCOMING PATEL FAMILY TRUST"))
        self.assertTrue(self.found("Meridian Partners LP", "  wire incoming meridian partners  "))
        self.assertTrue(self.found("Haller Capital LLC", "ACH CREDIT HALLER-CAPITAL"))

    def test_name_match_whole_words_only(self):
        self.assertFalse(self.found("Shah Trust", "WIRE SHAHTRUST"))
        self.assertFalse(self.found("Patel", "WIRE PATELS FAMILY"))
        self.assertFalse(self.found("Rahul Trust", "RAHUL SHAH TRUST"))  # words must be contiguous

    def test_empty_name_never_matches(self):
        self.assertFalse(self.found("LLC", "ANY LLC PAYMENT"))
