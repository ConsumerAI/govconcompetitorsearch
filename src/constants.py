from __future__ import annotations

BASE_URL = "https://api.usaspending.gov"
AWARD_TYPE_CODES = ["A", "B", "C", "D"]
AWARD_OR_IDV_FLAG = "AWARD"

ALL_COMPONENTS = "All Components"
ALL_NAICS = "All NAICS"
ALL_SET_ASIDES = "All Set-Aside Types"
ALL_LOCATIONS = "All Locations"
OPTION_SEPARATOR = "||"

DEFAULT_START_DATE = "2020-10-01"
DEFAULT_END_DATE = "2026-09-30"

SET_ASIDE_TYPE_OPTIONS = {
    "NONE": "Unrestricted",
    "SBA": "Small Business Set-Aside",
    "SBP": "Small Business Partial Set-Aside",
    "8A": "8(a) Competed",
    "8AN": "8(a) Sole Source",
    "WOSB": "Women-Owned Small Business",
    "EDWOSB": "Economically Disadvantaged WOSB",
    "SDVOSBC": "Service-Disabled Veteran-Owned Small Business",
    "HZS": "HUBZone Sole Source",
    "HZC": "HUBZone Set-Aside",
}

# Labels returned by the USAspending transaction download CSV for type_of_set_aside.
SET_ASIDE_DOWNLOAD_LABELS = {
    "NO SET ASIDE USED.": "NONE",
    "SMALL BUSINESS SET ASIDE - TOTAL": "SBA",
    "SMALL BUSINESS SET ASIDE - PARTIAL": "SBP",
    "8A COMPETED": "8A",
    "8(A) SOLE SOURCE": "8AN",
    "WOMEN OWNED SMALL BUSINESS": "WOSB",
    "ECONOMICALLY DISADVANTAGED WOMEN OWNED SMALL BUSINESS": "EDWOSB",
    "SERVICE DISABLED VETERAN OWNED SMALL BUSINESS SET-ASIDE": "SDVOSBC",
    "HUBZONE SOLE SOURCE": "HZS",
    "HUBZONE SET-ASIDE": "HZC",
}

STATE_OPTIONS = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DC": "District of Columbia",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "IA": "Iowa",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "MA": "Massachusetts",
    "MD": "Maryland",
    "ME": "Maine",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MO": "Missouri",
    "MS": "Mississippi",
    "MT": "Montana",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "NE": "Nebraska",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NV": "Nevada",
    "NY": "New York",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VA": "Virginia",
    "VT": "Vermont",
    "WA": "Washington",
    "WI": "Wisconsin",
    "WV": "West Virginia",
    "WY": "Wyoming",
}

COUNTRY_NAMES = {
    "AFG": "Afghanistan",
    "CAN": "Canada",
    "DEU": "Germany",
    "GBR": "United Kingdom",
    "IRQ": "Iraq",
    "JPN": "Japan",
    "KOR": "South Korea",
    "USA": "United States",
}

