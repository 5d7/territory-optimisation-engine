import unittest

import territory_optimisation as engine
from territory_optimisation import (
    Config,
    make_demo_data,
    prepare_data,
    run_territory_review,
)


class EngineTests(unittest.TestCase):
    def test_demo_generator_honours_requested_ae_count(self):
        accounts, aes = make_demo_data(
            seed=1, regions=("TEST",), aes_per_region=3, accounts_per_ae=2
        )

        self.assertEqual(len(aes), 3)
        self.assertEqual(len(accounts), 6)
        self.assertEqual(set(aes["Role"]), set(engine.engine.KNOWN_ROLES))
        self.assertIn("Spend Map", accounts.columns)
        self.assertNotIn("Money Map", accounts.columns)

    def test_invalid_solver_settings_fail_early(self):
        invalid = {
            "max_time_per_region_seconds": 0,
            "relative_gap_limit": 1.1,
            "stability_bonus": -1,
            "num_workers": -1,
            "allowed_workload_variance_pct": 101,
        }

        for name, value in invalid.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                Config(**{name: value}).validate()

    def test_blank_identifiers_are_blocking_data_errors(self):
        accounts, aes = make_demo_data(
            seed=2, regions=("TEST",), aes_per_region=3, accounts_per_ae=2
        )
        accounts.loc[0, "Account ID"] = ""
        aes.loc[0, "AE Name"] = ""

        _, _, quality = prepare_data(accounts, aes, Config())

        errors = set(quality.loc[quality["Severity"] == "Error", "Check"])
        self.assertIn("Empty Account IDs", errors)
        self.assertIn("AEs with empty names", errors)

    def test_small_review_assigns_every_account_within_its_region(self):
        accounts, aes = make_demo_data(
            seed=3, regions=("NORTH",), aes_per_region=3, accounts_per_ae=3
        )
        accounts.loc[0, "Current AE"] = "Former Rep"
        cfg = Config(
            ai_specialists_only=False,
            min_tier_3_per_ae=0,
            allowed_workload_variance_pct=100,
            require_full_coverage=True,
            max_time_per_region_seconds=5,
            num_workers=1,
        )

        result = run_territory_review(cfg, accounts, aes, quiet=True)
        ae_regions = dict(zip(aes["AE Name"], aes["Region Alignment"]))

        self.assertEqual(result.kpis["accounts_assigned"], len(accounts))
        self.assertTrue(
            all(
                ae_regions[owner] == region
                for owner, region in zip(
                    result.accounts["Proposed AE"], result.accounts["Region"]
                )
            )
        )
        self.assertEqual(result.kpis["moves"], len(result.moves))


if __name__ == "__main__":
    unittest.main()
