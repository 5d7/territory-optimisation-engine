import unittest

import pandas as pd

from territory_optimisation import (
    Config,
    DataValidationError,
    build_next_cycle_frames,
    make_demo_data,
    result_tables,
    run_territory_review,
)


class PackageTests(unittest.TestCase):
    def test_result_tables_expose_destination_neutral_outputs(self):
        accounts, aes = make_demo_data(
            seed=11, regions=("TEST",), aes_per_region=3, accounts_per_ae=3
        )
        result = run_territory_review(
            Config(
                ai_specialists_only=False,
                min_tier_3_per_ae=0,
                allowed_workload_variance_pct=100,
                require_full_coverage=True,
                max_time_per_region_seconds=5,
                num_workers=1,
            ),
            accounts,
            aes,
            quiet=True,
        )
        tables = result_tables(result)
        self.assertEqual(
            set(tables),
            {
                "Run Summary", "Region Status", "Moves", "Rule Scorecard",
                "AE Summary", "Assignments", "Unassigned", "Rule Detail",
                "Pre-flight", "Data Quality",
            },
        )
        self.assertEqual(len(tables["Assignments"]), len(accounts))

    def test_next_cycle_requires_explicit_voluntary_decisions(self):
        assignments = pd.DataFrame([
            {
                "Account ID": "A1", "Current AE": "Old", "Proposed AE": "New",
                "Action": "Move", "Locked": "No",
            }
        ])
        reviews = pd.DataFrame([
            {
                "Proposal ID": "P1", "Account ID": "A1", "Current AE": "Old",
                "Proposed AE": "New", "Action": "Move", "Manager Decision": "",
                "Lock next cycle": "",
            }
        ])
        with self.assertRaises(DataValidationError):
            build_next_cycle_frames(assignments, reviews, pd.DataFrame())

        reviews.loc[0, "Manager Decision"] = "Reject"
        next_accounts, _ = build_next_cycle_frames(
            assignments, reviews, pd.DataFrame()
        )
        self.assertEqual(next_accounts.loc[0, "Current AE"], "Old")
        self.assertEqual(next_accounts.loc[0, "Locked"], "Yes")


if __name__ == "__main__":
    unittest.main()
