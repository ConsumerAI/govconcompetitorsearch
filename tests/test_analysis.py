from __future__ import annotations

import unittest

import pandas as pd

from src.agency_components import build_agency_component_options, get_agency_component_config
from src.analysis import (
    analyze,
    award_table,
    build_location_options,
    competitor_leaderboard,
    filter_transactions,
    market_concentration,
    normalize_transactions,
)
from src.constants import ALL_COMPONENTS, ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES
from src.state import FilterSnapshot


def sample_transactions() -> pd.DataFrame:
    return normalize_transactions(
        [
            {
                "awarding_agency_name": "Department of State",
                "awarding_sub_agency_name": "Department of State",
                "funding_office_name": "BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS",
                "funding_office_code": "INL",
                "naics_code": "541611",
                "naics_description": "Administrative Management and General Management Consulting Services",
                "set_aside_type": "WOSB",
                "place_of_performance_country_code": "IRQ",
                "recipient_name": "Acme Global LLC",
                "recipient_uei": "UEI111",
                "award_id_piid": "S-AWARD-1",
                "contract_award_unique_key": "CONT_AWD_STATE_1",
                "federal_action_obligation": "1000",
                "current_total_value_of_award": "1500",
                "potential_total_value_of_award": "2000",
                "action_date": "2025-03-01",
                "transaction_description": "INL support services",
                "awarding_office_name": "State Awarding Office",
            },
            {
                "awarding_agency_name": "Department of State",
                "awarding_sub_agency_name": "Department of State",
                "funding_office_name": "BUREAU OF COUNTERTERRORISM",
                "funding_office_code": "CT",
                "naics_code": "541611",
                "place_of_performance_country_code": "USA",
                "place_of_performance_state_code": "VA",
                "recipient_name": "Beta Services Inc.",
                "recipient_uei": "UEI222",
                "award_id_piid": "S-AWARD-2",
                "contract_award_unique_key": "CONT_AWD_STATE_2",
                "federal_action_obligation": "500",
                "action_date": "2025-02-01",
            },
            {
                "awarding_agency_name": "Department of the Interior",
                "awarding_sub_agency_name": "Bureau of Reclamation",
                "awarding_sub_agency_code": "14R",
                "funding_office_name": "Reclamation Funding Office",
                "naics_code": "561210",
                "set_aside_type": "SBA",
                "place_of_performance_country_code": "USA",
                "place_of_performance_state_code": "CA",
                "recipient_name": "Water Works Corporation",
                "recipient_uei": "UEI333",
                "award_id_piid": "I-AWARD-1",
                "contract_award_unique_key": "CONT_AWD_INT_1",
                "federal_action_obligation": "2500",
                "action_date": "2025-04-01",
            },
            {
                "awarding_agency_name": "Department of Housing and Urban Development",
                "awarding_sub_agency_name": "Public and Indian Housing",
                "naics_code": "541611",
                "place_of_performance_country_code": "USA",
                "place_of_performance_state_code": "DC",
                "recipient_name": "Housing Analytics LLC",
                "award_id_piid": "H-AWARD-1",
                "contract_award_unique_key": "CONT_AWD_HUD_1",
                "federal_action_obligation": "700",
                "action_date": "2025-05-01",
            },
            {
                "awarding_agency_name": "Department of State",
                "awarding_sub_agency_name": "Department of State",
                "funding_office_name": "BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS",
                "funding_office_code": "INL",
                "naics_code": "541611",
                "recipient_name": "Acme Global, LLC",
                "award_id_piid": "S-AWARD-1-MOD",
                "contract_award_unique_key": "CONT_AWD_STATE_1",
                "federal_action_obligation": "-100",
                "action_date": "2025-03-05",
            },
        ]
    )


class AgencyComponentTests(unittest.TestCase):
    def test_state_uses_funding_office_component(self):
        config = get_agency_component_config("Department of State")
        self.assertEqual(config["label"], "Bureau / Funding Office")
        self.assertEqual(config["field_name"], "funding_office_name")
        options = build_agency_component_options(sample_transactions(), "Department of State")
        labels = [option["label"] for option in options]
        self.assertIn("BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS", labels)

    def test_state_component_filters_funding_office(self):
        df = sample_transactions()
        snapshot = FilterSnapshot(
            agency="Department of State",
            component="BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS",
            naics="541611 - Administrative Management and General Management Consulting Services",
        )
        scoped = filter_transactions(df, snapshot)
        self.assertEqual(set(scoped["funding_office_name"]), {"BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS"})
        self.assertEqual(set(scoped["awarding_sub_agency_name"]), {"Department of State"})

    def test_interior_uses_awarding_subagency(self):
        config = get_agency_component_config("Department of the Interior")
        self.assertEqual(config["label"], "Subagency / Bureau")
        self.assertEqual(config["field_name"], "awarding_sub_agency_name")
        options = build_agency_component_options(sample_transactions(), "Department of the Interior")
        self.assertIn("Bureau of Reclamation", [option["label"] for option in options])
        scoped = filter_transactions(
            sample_transactions(),
            FilterSnapshot(agency="Department of the Interior", component="Bureau of Reclamation"),
        )
        self.assertEqual(len(scoped), 1)


class FilterAndOutputTests(unittest.TestCase):
    def test_optional_location_supports_international_country(self):
        options = build_location_options(sample_transactions())
        self.assertIn("IRQ - Iraq", options)
        scoped = filter_transactions(sample_transactions(), FilterSnapshot(agency="Department of State", location="IRQ - Iraq"))
        self.assertEqual(len(scoped), 1)

    def test_set_aside_and_location_are_optional(self):
        broad = filter_transactions(
            sample_transactions(),
            FilterSnapshot(
                agency="Department of State",
                component=ALL_COMPONENTS,
                naics=ALL_NAICS,
                set_aside=ALL_SET_ASIDES,
                location=ALL_LOCATIONS,
            ),
        )
        self.assertEqual(len(broad), 3)

    def test_set_aside_filter_matches_option_code(self):
        scoped = filter_transactions(
            sample_transactions(),
            FilterSnapshot(agency="Department of State", set_aside="WOSB - Women-Owned Small Business"),
        )
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped["set_aside_type"].iloc[0], "WOSB")

    def test_encoded_naics_option_filters_by_code(self):
        scoped = filter_transactions(
            sample_transactions(),
            FilterSnapshot(agency="Department of the Interior", component="Bureau of Reclamation", naics="561210||Facilities Support Services"),
        )
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped["naics_code"].iloc[0], "561210")

    def test_market_concentration_uses_positive_obligations(self):
        df = sample_transactions()
        scoped = filter_transactions(df, FilterSnapshot(agency="Department of State"))
        concentration = market_concentration(scoped, top_n=1)
        self.assertAlmostEqual(concentration["positive_total"], 1500.0)
        self.assertLessEqual(concentration["top_share"], 1.0)

    def test_main_obligations_are_net_based(self):
        scoped = filter_transactions(sample_transactions(), FilterSnapshot(agency="Department of State"))
        result = analyze(scoped, FilterSnapshot())
        self.assertEqual(result["kpis"]["net_obligations"], 1400.0)

    def test_contractor_grouping_preserves_variants(self):
        scoped = filter_transactions(sample_transactions(), FilterSnapshot(agency="Department of State"))
        leaderboard = competitor_leaderboard(scoped)
        acme = leaderboard[leaderboard["Contractor Name"] == "Acme Global LLC"].iloc[0]
        self.assertEqual(acme["Obligations in Scope"], 900.0)

    def test_award_links_are_retained(self):
        awards = award_table(filter_transactions(sample_transactions(), FilterSnapshot(agency="Department of State")))
        self.assertTrue(awards["USAspending Award Link"].str.startswith("https://www.usaspending.gov/award/").any())

    def test_recipient_links_use_search_endpoint(self):
        from src.utils import usaspending_recipient_profile_url

        scoped = filter_transactions(sample_transactions(), FilterSnapshot(agency="Department of State"))
        leaderboard = competitor_leaderboard(scoped)
        self.assertIn("Recipient Profile Link", leaderboard.columns)
        self.assertTrue(leaderboard["Recipient Profile Link"].str.contains("hash=recipient").all())
        url = usaspending_recipient_profile_url("UEI111", "Acme Global LLC")
        self.assertIn("recipient_search_text=UEI111", url)
        self.assertNotIn("/recipient/UEI111/latest", url)

    def test_component_filter_matches_funding_subagency_when_awarding_differs(self):
        labor = normalize_transactions(
            [
                {
                    "awarding_agency_name": "Department of Labor",
                    "awarding_sub_agency_name": "Office of the Assistant Secretary for Administration and Management",
                    "funding_sub_agency_name": "Bureau of Labor Statistics",
                    "naics_code": "541720",
                    "federal_action_obligation": "500",
                    "action_date": "2025-01-01",
                    "recipient_name": "Example Vendor",
                    "award_id_piid": "LABOR-1",
                    "primary_place_of_performance_country_code": "USA",
                    "primary_place_of_performance_state_code": "DC",
                    "type_of_set_aside": "SBA",
                }
            ]
        )
        scoped = filter_transactions(
            labor,
            FilterSnapshot(agency="Department of Labor", component="Bureau of Labor Statistics", naics="541720"),
        )
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped["naics_code"].iloc[0], "541720")


if __name__ == "__main__":
    unittest.main()
