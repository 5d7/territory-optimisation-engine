#!/usr/bin/env python3
"""Generate coherent synthetic Salesforce-style data with no paid API required."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import string
import sys
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


PRESETS: dict[str, list[str]] = {
    "account": [
        "Id", "Name", "AccountNumber", "Type", "Industry", "AnnualRevenue", "CurrencyIsoCode",
        "NumberOfEmployees", "Phone", "Website", "BillingStreet", "BillingCity",
        "BillingState", "BillingPostalCode", "BillingCountry", "OwnerId",
        "Sales_Region__c", "Sales_District__c", "CreatedDate", "LastModifiedDate",
    ],
    "opportunity": [
        "Id", "Name", "AccountId", "OwnerId", "StageName", "Amount", "CurrencyIsoCode", "Probability",
        "CloseDate", "IsClosed", "IsWon", "ForecastCategoryName", "Type", "LeadSource",
        "NextStep", "Description", "Sales_Region__c", "Sales_District__c",
        "Closed_Won_Amount__c", "Closed_Lost_Amount__c", "Open_Pipeline_Amount__c",
        "CreatedDate", "LastModifiedDate",
    ],
    "contact": [
        "Id", "AccountId", "FirstName", "LastName", "Title", "Department", "Email",
        "Phone", "MobilePhone", "MailingStreet", "MailingCity", "MailingState",
        "MailingPostalCode", "MailingCountry", "LeadSource", "OwnerId",
        "Sales_Region__c", "Sales_District__c", "CreatedDate", "LastModifiedDate",
    ],
    "lead": [
        "Id", "FirstName", "LastName", "Company", "Title", "Email", "Phone", "Status",
        "LeadSource", "Industry", "AnnualRevenue", "NumberOfEmployees", "Street", "City",
        "State", "PostalCode", "Country", "OwnerId", "IsConverted", "ConvertedDate",
        "Sales_Region__c", "Sales_District__c", "CreatedDate", "LastModifiedDate",
    ],
    "case": [
        "Id", "CaseNumber", "AccountId", "ContactId", "OwnerId", "Subject", "Description",
        "Status", "Priority", "Origin", "Type", "Reason", "IsClosed", "CreatedDate",
        "ClosedDate", "Sales_Region__c", "Sales_District__c", "LastModifiedDate",
    ],
    "user": [
        "Id", "Username", "FirstName", "LastName", "Name", "Email", "Title", "Department",
        "IsActive", "UserRoleId", "ManagerId", "Sales_Region__c", "Sales_District__c",
        "CreatedDate", "LastModifiedDate",
    ],
}


DEFAULT_CONFIG: dict[str, Any] = {
    "regions": {
        "AMER": ["West", "Central", "East", "Canada", "LATAM"],
        "EMEA": ["UKI", "DACH", "France", "Benelux", "Nordics", "Southern Europe", "MEA"],
        "APAC": ["ANZ", "Japan", "India", "Southeast Asia", "Greater China"],
    },
    "opportunity_stages": [
        {"name": "Prospecting", "probability": 10, "weight": 14, "is_closed": False, "is_won": False, "forecast": "Pipeline"},
        {"name": "Qualification", "probability": 25, "weight": 18, "is_closed": False, "is_won": False, "forecast": "Pipeline"},
        {"name": "Needs Analysis", "probability": 40, "weight": 15, "is_closed": False, "is_won": False, "forecast": "Best Case"},
        {"name": "Proposal/Price Quote", "probability": 60, "weight": 14, "is_closed": False, "is_won": False, "forecast": "Best Case"},
        {"name": "Negotiation/Review", "probability": 80, "weight": 13, "is_closed": False, "is_won": False, "forecast": "Commit"},
        {"name": "Closed Won", "probability": 100, "weight": 16, "is_closed": True, "is_won": True, "forecast": "Closed"},
        {"name": "Closed Lost", "probability": 0, "weight": 10, "is_closed": True, "is_won": False, "forecast": "Omitted"},
    ],
    "lead_statuses": ["New", "Working", "Nurturing", "Qualified", "Unqualified"],
    "case_statuses": ["New", "Working", "Escalated", "Closed"],
    "industries": [
        "Financial Services", "Technology", "Healthcare", "Retail", "Manufacturing",
        "Telecommunications", "Energy", "Media", "Transportation", "Public Sector",
    ],
    "account_types": ["Prospect", "Customer - Direct", "Customer - Channel", "Partner", "Competitor"],
    "lead_sources": ["Web", "Partner Referral", "Trade Show", "Outbound", "Advertisement", "Employee Referral"],
    "opportunity_types": ["New Business", "Existing Business", "Renewal", "Upsell", "Cross-sell"],
    "case_types": ["Problem", "Question", "Feature Request", "Incident"],
    "currencies": ["USD", "EUR", "GBP"],
}


FIRST_NAMES = [
    "Alex", "Amara", "Amina", "Ana", "Ben", "Camila", "Chen", "Daniel", "Diego", "Elena",
    "Emma", "Fatima", "Grace", "Hana", "Haruto", "Isabella", "Jack", "James", "Javier", "Jordan",
    "Kai", "Leila", "Liam", "Lucia", "Maya", "Mei", "Mia", "Mohammed", "Noah", "Oliver",
    "Priya", "Ravi", "Sofia", "Sora", "Theo", "Yuki", "Zara",
]
LAST_NAMES = [
    "Adams", "Ahmed", "Andersson", "Brown", "Campbell", "Chen", "Costa", "Dubois", "Evans",
    "Garcia", "Gupta", "Hernandez", "Ito", "Johnson", "Khan", "Kim", "Kowalski", "Lee",
    "Martin", "Miller", "Murphy", "Nakamura", "Nguyen", "Patel", "Rossi", "Schmidt", "Silva",
    "Singh", "Smith", "Taylor", "Thomas", "Wang", "Wilson", "Yamamoto",
]
COMPANY_WORDS = [
    "Apex", "Atlas", "Aurora", "Blue", "Bright", "Cedar", "Cloud", "Core", "Crown", "Delta",
    "Evergreen", "Global", "Harbor", "Horizon", "Iron", "Juniper", "Keystone", "Lighthouse",
    "Meridian", "Metro", "Nimbus", "Northstar", "Nova", "Oak", "Orion", "Peak", "Pioneer",
    "Quantum", "River", "Silver", "Summit", "Terra", "Unity", "Vertex", "Westbridge",
]
COMPANY_NOUNS = [
    "Analytics", "Capital", "Digital", "Dynamics", "Foods", "Health", "Industries", "Labs",
    "Logistics", "Media", "Networks", "Partners", "Retail", "Robotics", "Security", "Services",
    "Solutions", "Systems", "Technologies", "Telecom", "Ventures", "Works",
]
TITLES = [
    "Chief Executive Officer", "Chief Financial Officer", "Chief Information Officer",
    "Chief Revenue Officer", "VP Sales", "VP Operations", "Sales Director", "IT Director",
    "Head of Procurement", "Finance Director", "Operations Manager", "Product Manager",
    "Account Executive", "Business Development Manager", "Data Architect",
]
DEPARTMENTS = ["Executive", "Sales", "Finance", "Operations", "IT", "Procurement", "Marketing", "Product"]


@dataclass(frozen=True)
class Stage:
    name: str
    probability: int
    weight: float
    is_closed: bool
    is_won: bool
    forecast: str


ID_PREFIX = {
    "account": "001", "contact": "003", "user": "005", "opportunity": "006",
    "lead": "00Q", "case": "500", "campaign": "701", "userrole": "00E",
}


ALIASES = {
    "accountname": "Name", "opportunityname": "Name", "fullname": "Name",
    "accountid": "AccountId", "owner": "OwnerId", "ownerid": "OwnerId",
    "stage": "StageName", "opportunitystage": "StageName", "stagename": "StageName",
    "closed": "IsClosed", "isclosed": "IsClosed", "won": "IsWon", "iswon": "IsWon",
    "closedwonamount": "Closed_Won_Amount__c", "closedwonamountc": "Closed_Won_Amount__c",
    "closedlostamount": "Closed_Lost_Amount__c", "closedlostamountc": "Closed_Lost_Amount__c",
    "openpipelineamount": "Open_Pipeline_Amount__c", "openpipelineamountc": "Open_Pipeline_Amount__c",
    "salesregion": "Sales_Region__c", "salesregionc": "Sales_Region__c", "region": "Sales_Region__c",
    "salesdistrict": "Sales_District__c", "salesdistrictc": "Sales_District__c", "district": "Sales_District__c",
    "employees": "NumberOfEmployees", "numberemployees": "NumberOfEmployees",
    "created": "CreatedDate", "modified": "LastModifiedDate", "lastmodified": "LastModifiedDate",
}


LOCATION_BY_REGION = {
    "AMER": [
        ("United States", "California", "San Francisco"), ("United States", "New York", "New York"),
        ("United States", "Illinois", "Chicago"), ("Canada", "Ontario", "Toronto"),
        ("Brazil", "São Paulo", "São Paulo"), ("Mexico", "CDMX", "Mexico City"),
    ],
    "EMEA": [
        ("United Kingdom", "England", "London"), ("Germany", "Bavaria", "Munich"),
        ("France", "Île-de-France", "Paris"), ("Netherlands", "North Holland", "Amsterdam"),
        ("Sweden", "Stockholm", "Stockholm"), ("Spain", "Madrid", "Madrid"),
        ("United Arab Emirates", "Dubai", "Dubai"),
    ],
    "APAC": [
        ("Australia", "New South Wales", "Sydney"), ("Japan", "Tokyo", "Tokyo"),
        ("India", "Karnataka", "Bengaluru"), ("Singapore", "Singapore", "Singapore"),
        ("South Korea", "Seoul", "Seoul"), ("New Zealand", "Auckland", "Auckland"),
    ],
}


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def canonical_field(column: str) -> str:
    key = normalize(column)
    if key in ALIASES:
        return ALIASES[key]
    for fields in PRESETS.values():
        for field in fields:
            if normalize(field) == key:
                return field
    return column.strip()


def parse_columns(text: str) -> list[str]:
    columns = [part.strip() for part in re.split(r"[,;\t\n]+", text) if part.strip()]
    if not columns:
        raise ValueError("No columns were supplied.")
    duplicates = sorted({c for c in columns if columns.count(c) > 1})
    if duplicates:
        raise ValueError(f"Duplicate columns: {', '.join(duplicates)}")
    return columns


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def base36(number: int, width: int = 15) -> str:
    alphabet = string.digits + string.ascii_uppercase
    chars = []
    while number:
        number, rem = divmod(number, 36)
        chars.append(alphabet[rem])
    return ("".join(reversed(chars)) or "0").rjust(width, "0")[-width:]


def sf_id(object_name: str, number: int) -> str:
    """Return a Salesforce-style 18-character ID with the standard case checksum."""
    id15 = ID_PREFIX.get(object_name, "a00") + base36(number, width=12)
    checksum_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    suffix = ""
    for start in range(0, 15, 5):
        flags = sum(1 << offset for offset, char in enumerate(id15[start : start + 5]) if char.isupper())
        suffix += checksum_alphabet[flags]
    return id15 + suffix


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower()) or "example"


def iso_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def weighted_choice(rng: random.Random, values: list[Any], weights: list[float]) -> Any:
    return rng.choices(values, weights=weights, k=1)[0]


class SalesforceGenerator:
    def __init__(
        self,
        object_name: str,
        rows: int,
        config: dict[str, Any],
        seed: int,
        region_filter: set[str] | None = None,
        district_filter: set[str] | None = None,
    ) -> None:
        if object_name not in PRESETS:
            raise ValueError(f"Unknown object '{object_name}'. Choose from: {', '.join(PRESETS)}")
        if rows < 1:
            raise ValueError("rows must be at least 1")
        self.object_name = object_name
        self.rows = rows
        self.config = config
        self.rng = random.Random(seed)
        self.today = date.today()
        regions = config["regions"]
        self.region_map = {
            region: [district for district in districts if not district_filter or district in district_filter]
            for region, districts in regions.items()
            if (not region_filter or region in region_filter)
        }
        self.region_map = {region: districts for region, districts in self.region_map.items() if districts}
        if not self.region_map:
            raise ValueError("The selected region/district filters produced no valid combinations.")
        self.stages = [Stage(**stage) for stage in config["opportunity_stages"]]
        if not self.stages or not any(stage.weight > 0 for stage in self.stages):
            raise ValueError("opportunity_stages must contain at least one stage with a positive weight")
        self.owner_pool = [sf_id("user", i + 1) for i in range(max(12, min(200, math.ceil(rows / 150))))]
        self.account_pool = [sf_id("account", i + 1) for i in range(max(100, math.ceil(rows / 4)))]
        self.contact_pool = [sf_id("contact", i + 1) for i in range(max(100, math.ceil(rows / 3)))]

    def _person(self) -> tuple[str, str]:
        return self.rng.choice(FIRST_NAMES), self.rng.choice(LAST_NAMES)

    def _company(self, index: int) -> str:
        return f"{self.rng.choice(COMPANY_WORDS)} {self.rng.choice(COMPANY_NOUNS)} {index:05d}"

    def _region(self) -> tuple[str, str]:
        region = self.rng.choice(list(self.region_map))
        return region, self.rng.choice(self.region_map[region])

    def _location(self, region: str) -> tuple[str, str, str, str, str]:
        country, state, city = self.rng.choice(LOCATION_BY_REGION.get(region, LOCATION_BY_REGION["AMER"]))
        number = self.rng.randint(1, 999)
        street = f"{number} {self.rng.choice(COMPANY_WORDS)} Street"
        postal = f"{self.rng.randint(10000, 99999)}"
        return street, city, state, postal, country

    def _amount(self) -> float:
        return round(min(10_000_000, max(1_000, self.rng.lognormvariate(11.4, 1.05))), 2)

    def _context(self, index: int) -> dict[str, Any]:
        first, last = self._person()
        company = self._company(index)
        region, district = self._region()
        street, city, state, postal, country = self._location(region)
        created_date = self.today - timedelta(days=self.rng.randint(1, 730))
        created_dt = datetime.combine(created_date, time(self.rng.randint(7, 19), self.rng.randint(0, 59)), tzinfo=timezone.utc)
        modified_dt = min(datetime.now(timezone.utc), created_dt + timedelta(days=self.rng.randint(0, 180)))
        stage = weighted_choice(self.rng, self.stages, [s.weight for s in self.stages])
        if stage.is_closed:
            close_date = max(created_date, self.today - timedelta(days=self.rng.randint(0, 365)))
        else:
            close_date = max(created_date, self.today + timedelta(days=self.rng.randint(-30, 365)))
        amount = self._amount()
        status = self.rng.choice(self.config["lead_statuses"])
        is_converted = status == "Qualified" and self.rng.random() < 0.72
        case_status = self.rng.choice(self.config["case_statuses"])
        return {
            "index": index,
            "first": first,
            "last": last,
            "company": company,
            "domain": slug(company) + ".example",
            "region": region,
            "district": district,
            "street": street,
            "city": city,
            "state": state,
            "postal": postal,
            "country": country,
            "created_date": created_date,
            "created_dt": created_dt,
            "modified_dt": modified_dt,
            "stage": stage,
            "close_date": close_date,
            "amount": amount,
            "lead_status": status,
            "is_converted": is_converted,
            "case_status": case_status,
            "owner_id": self.rng.choice(self.owner_pool),
            "account_id": self.rng.choice(self.account_pool),
            "contact_id": self.rng.choice(self.contact_pool),
        }

    def _name(self, ctx: dict[str, Any]) -> str:
        if self.object_name == "account":
            return ctx["company"]
        if self.object_name == "opportunity":
            offer = self.rng.choice(["Platform", "Analytics", "Cloud", "Security", "Expansion", "Renewal"])
            return f"{ctx['company']} - {offer}"
        return f"{ctx['first']} {ctx['last']}"

    def _fallback(self, field: str, ctx: dict[str, Any]) -> Any:
        key = normalize(field)
        if key.startswith(("is", "has")):
            return self.rng.random() < 0.5
        if key.endswith("date"):
            return (self.today + timedelta(days=self.rng.randint(-365, 365))).isoformat()
        if "amount" in key or "revenue" in key or "value" in key:
            return self._amount()
        if "percent" in key or "probability" in key:
            return self.rng.randint(0, 100)
        if "count" in key or "number" in key or "quantity" in key:
            return self.rng.randint(0, 5000)
        if "email" in key:
            return f"{ctx['first']}.{ctx['last']}{ctx['index']}@{ctx['domain']}".lower()
        if "phone" in key:
            return f"+1-555-{self.rng.randint(100, 999)}-{self.rng.randint(1000, 9999)}"
        if key.endswith("id"):
            return sf_id("custom", ctx["index"])
        return f"{field.replace('_', ' ').strip()} {ctx['index']:05d}"

    def _value(self, field: str, ctx: dict[str, Any]) -> Any:
        stage: Stage = ctx["stage"]
        amount = ctx["amount"]
        values: dict[str, Any] = {
            "Id": sf_id(self.object_name, ctx["index"]),
            "Name": self._name(ctx),
            "AccountId": ctx["account_id"],
            "ContactId": ctx["contact_id"],
            "OwnerId": ctx["owner_id"],
            "ManagerId": self.rng.choice(self.owner_pool),
            "UserRoleId": sf_id("userrole", self.rng.randint(1, 25)),
            "AccountNumber": f"ACCT-{ctx['index']:08d}",
            "CaseNumber": f"{ctx['index']:08d}",
            "FirstName": ctx["first"],
            "LastName": ctx["last"],
            "Company": ctx["company"],
            "Title": self.rng.choice(TITLES),
            "Department": self.rng.choice(DEPARTMENTS),
            "Email": f"{ctx['first']}.{ctx['last']}{ctx['index']}@{ctx['domain']}".lower(),
            "Username": f"{ctx['first']}.{ctx['last']}.{ctx['index']}@synthetic.example".lower(),
            "Phone": f"+1-555-{self.rng.randint(100, 999)}-{self.rng.randint(1000, 9999)}",
            "MobilePhone": f"+1-555-{self.rng.randint(100, 999)}-{self.rng.randint(1000, 9999)}",
            "Website": f"https://www.{ctx['domain']}",
            "Type": self.rng.choice(
                self.config["opportunity_types"] if self.object_name == "opportunity"
                else self.config["case_types"] if self.object_name == "case"
                else self.config["account_types"]
            ),
            "Industry": self.rng.choice(self.config["industries"]),
            "AnnualRevenue": round(amount * self.rng.uniform(8, 80), 2),
            "NumberOfEmployees": max(2, int(self.rng.lognormvariate(5.0, 1.3))),
            "LeadSource": self.rng.choice(self.config["lead_sources"]),
            "Status": ctx["case_status"] if self.object_name == "case" else ctx["lead_status"],
            "StageName": stage.name,
            "Amount": amount,
            "CurrencyIsoCode": self.rng.choice(self.config["currencies"]),
            "Probability": stage.probability,
            "CloseDate": ctx["close_date"].isoformat(),
            "IsClosed": stage.is_closed if self.object_name == "opportunity" else ctx["case_status"] == "Closed",
            "IsWon": stage.is_won,
            "ForecastCategoryName": stage.forecast,
            "Closed_Won_Amount__c": amount if stage.is_won else 0.0,
            "Closed_Lost_Amount__c": amount if stage.is_closed and not stage.is_won else 0.0,
            "Open_Pipeline_Amount__c": amount if not stage.is_closed else 0.0,
            "NextStep": "Finalize commercial terms" if stage.probability >= 60 else "Confirm discovery and stakeholders",
            "Subject": self.rng.choice(["Login issue", "Billing question", "Feature request", "Data import", "Performance issue", "Integration help"]),
            "Description": f"Synthetic {self.object_name} record for testing. Region: {ctx['region']}; district: {ctx['district']}.",
            "Priority": self.rng.choices(["Low", "Medium", "High"], weights=[35, 50, 15], k=1)[0],
            "Origin": self.rng.choice(["Web", "Email", "Phone", "Chat"]),
            "Reason": self.rng.choice(["User education", "Configuration", "Bug", "Integration", "Billing"]),
            "IsActive": self.rng.random() >= 0.06,
            "IsConverted": ctx["is_converted"],
            "ConvertedDate": min(self.today, ctx["created_date"] + timedelta(days=self.rng.randint(1, 90))).isoformat() if ctx["is_converted"] else "",
            "CreatedDate": iso_datetime(ctx["created_dt"]),
            "LastModifiedDate": iso_datetime(ctx["modified_dt"]),
            "ClosedDate": ctx["close_date"].isoformat() if (stage.is_closed if self.object_name == "opportunity" else ctx["case_status"] == "Closed") else "",
            "Sales_Region__c": ctx["region"],
            "Sales_District__c": ctx["district"],
            "BillingStreet": ctx["street"], "BillingCity": ctx["city"], "BillingState": ctx["state"],
            "BillingPostalCode": ctx["postal"], "BillingCountry": ctx["country"],
            "MailingStreet": ctx["street"], "MailingCity": ctx["city"], "MailingState": ctx["state"],
            "MailingPostalCode": ctx["postal"], "MailingCountry": ctx["country"],
            "Street": ctx["street"], "City": ctx["city"], "State": ctx["state"],
            "PostalCode": ctx["postal"], "Country": ctx["country"],
        }
        return values.get(field, self._fallback(field, ctx))

    def generate(self, columns: list[str]) -> Iterable[dict[str, Any]]:
        canonical = [(column, canonical_field(column)) for column in columns]
        for index in range(1, self.rows + 1):
            ctx = self._context(index)
            yield {output: self._value(field, ctx) for output, field in canonical}


def apply_stage_overrides(config: dict[str, Any], names: list[str] | None, won_rate: float | None, lost_rate: float | None) -> None:
    if names:
        stages = []
        open_names = [name for name in names if "closed" not in name.lower()]
        for index, name in enumerate(names):
            lower = name.lower()
            is_won = "won" in lower
            is_closed = "closed" in lower or is_won or "lost" in lower
            probability = 100 if is_won else 0 if is_closed else round(10 + 80 * (index / max(1, len(open_names))))
            forecast = "Closed" if is_won else "Omitted" if is_closed else "Pipeline" if probability < 40 else "Best Case" if probability < 70 else "Commit"
            stages.append({"name": name, "probability": probability, "weight": 1, "is_closed": is_closed, "is_won": is_won, "forecast": forecast})
        config["opportunity_stages"] = stages
    if won_rate is None and lost_rate is None:
        return
    won = 0.16 if won_rate is None else won_rate
    lost = 0.10 if lost_rate is None else lost_rate
    if not 0 <= won <= 1 or not 0 <= lost <= 1 or won + lost > 1:
        raise ValueError("won-rate and lost-rate must be between 0 and 1 and sum to at most 1")
    stages = config["opportunity_stages"]
    if won > 0 and not any(stage["is_won"] for stage in stages):
        raise ValueError("won-rate requires a Closed Won stage")
    if lost > 0 and not any(stage["is_closed"] and not stage["is_won"] for stage in stages):
        raise ValueError("lost-rate requires a Closed Lost stage")
    open_stages = [stage for stage in stages if not stage["is_closed"]]
    for stage in stages:
        if stage["is_won"]:
            stage["weight"] = won
        elif stage["is_closed"]:
            stage["weight"] = lost
        else:
            stage["weight"] = (1 - won - lost) / max(1, len(open_stages))


def groq_enrich(
    records: list[dict[str, Any]],
    fields: list[str],
    object_name: str,
    model: str,
    limit: int,
    api_key_env: str,
) -> None:
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(f"Set {api_key_env} before using --groq-fields")
    count = min(limit, len(records))
    for start in range(0, count, 20):
        batch = records[start : min(count, start + 20)]
        context_rows = [{"row": start + i, **{k: v for k, v in row.items() if k in {"Name", "StageName", "Industry", "Sales_Region__c", "Sales_District__c"}}} for i, row in enumerate(batch)]
        prompt = (
            f"Create realistic but fictional values for these synthetic Salesforce {object_name} records. "
            f"Return JSON only as {{\"records\":[{{\"row\": integer, {', '.join(repr(f) + ': string' for f in fields)}}}]}}. "
            "Do not use real people or companies. Keep each value under 160 characters. Rows: " + json.dumps(context_rows)
        )
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "system", "content": "You generate fictional CRM test data and return valid JSON only."}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.8,
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
            result = json.loads(body["choices"][0]["message"]["content"])
            for item in result.get("records", []):
                row_index = int(item["row"])
                if start <= row_index < start + len(batch):
                    for field in fields:
                        if field in records[row_index] and field in item:
                            records[row_index][field] = item[field]
        except (urllib.error.URLError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Groq enrichment failed near row {start + 1}: {exc}") from exc


def write_records(records: Iterable[dict[str, Any]], columns: list[str], path: Path, output_format: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        if output_format == "csv":
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                writer.writerow(record)
                count += 1
        else:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate 2,000-15,000+ coherent synthetic Salesforce-style records.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python salesforce_synthetic_data.py --object opportunity --rows 5000
  python salesforce_synthetic_data.py --object account --rows 15000 --regions EMEA,APAC
  python salesforce_synthetic_data.py --object opportunity --rows 2000 \\
    --columns 'Id,Name,StageName,Amount,CloseDate,IsClosed,IsWon,Sales Region,Sales District'
  python salesforce_synthetic_data.py --list-fields opportunity
""",
    )
    parser.add_argument("--object", choices=sorted(PRESETS), default="opportunity", help="Salesforce object preset (default: opportunity)")
    parser.add_argument("--rows", type=int, default=2000, help="Number of rows (default: 2000)")
    parser.add_argument("--columns", help="Comma, tab, semicolon, or newline-separated column list")
    parser.add_argument("--columns-file", type=Path, help="Text file containing pasted column names")
    parser.add_argument("--output", type=Path, help="Output path; defaults to synthetic_<object>_<rows>.csv")
    parser.add_argument("--format", choices=["csv", "jsonl"], help="Output format; inferred from extension when omitted")
    parser.add_argument("--seed", type=int, default=42, help="Repeatable random seed")
    parser.add_argument("--config", type=Path, help="JSON file overriding regions, stages, and picklists")
    parser.add_argument("--regions", help="Comma-separated region names to include")
    parser.add_argument("--districts", help="Comma-separated district names to include")
    parser.add_argument("--stages", help="Comma-separated opportunity stage names")
    parser.add_argument("--won-rate", type=float, help="Target Closed Won share from 0 to 1")
    parser.add_argument("--lost-rate", type=float, help="Target Closed Lost share from 0 to 1")
    parser.add_argument("--list-presets", action="store_true", help="List available object presets")
    parser.add_argument("--list-fields", choices=sorted(PRESETS), help="Print preset fields for one object")
    parser.add_argument("--write-example-config", type=Path, help="Write a configurable JSON example and exit")
    parser.add_argument("--groq-fields", help="Optional comma-separated text fields to enrich using Groq")
    parser.add_argument("--groq-limit", type=int, default=100, help="Maximum rows sent to Groq (default: 100)")
    parser.add_argument("--groq-model", default="openai/gpt-oss-20b", help="Groq model ID")
    parser.add_argument("--groq-api-key-env", default="GROQ_API_KEY", help="Environment variable containing the Groq API key")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_presets:
        for name in sorted(PRESETS):
            print(f"{name}: {len(PRESETS[name])} fields")
        return 0
    if args.list_fields:
        print("\n".join(PRESETS[args.list_fields]))
        return 0
    if args.write_example_config:
        args.write_example_config.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {args.write_example_config}")
        return 0

    config = deepcopy(DEFAULT_CONFIG)
    if args.config:
        config = deep_merge(config, json.loads(args.config.read_text(encoding="utf-8")))
    stage_names = parse_columns(args.stages) if args.stages else None
    apply_stage_overrides(config, stage_names, args.won_rate, args.lost_rate)
    if args.columns and args.columns_file:
        raise ValueError("Use either --columns or --columns-file, not both")
    if args.columns_file:
        columns = parse_columns(args.columns_file.read_text(encoding="utf-8"))
    elif args.columns:
        columns = parse_columns(args.columns)
    else:
        columns = PRESETS[args.object]

    region_filter = set(parse_columns(args.regions)) if args.regions else None
    district_filter = set(parse_columns(args.districts)) if args.districts else None
    generator = SalesforceGenerator(args.object, args.rows, config, args.seed, region_filter, district_filter)
    records: Iterable[dict[str, Any]] = generator.generate(columns)
    if args.groq_fields:
        materialized = list(records)
        fields = parse_columns(args.groq_fields)
        missing = [field for field in fields if field not in columns]
        if missing:
            raise ValueError(f"Groq fields must also be output columns: {', '.join(missing)}")
        groq_enrich(materialized, fields, args.object, args.groq_model, args.groq_limit, args.groq_api_key_env)
        records = materialized

    output = args.output or Path(f"synthetic_{args.object}_{args.rows}.csv")
    output_format = args.format or ("jsonl" if output.suffix.lower() in {".jsonl", ".ndjson"} else "csv")
    count = write_records(records, columns, output, output_format)
    print(f"Generated {count:,} {args.object} rows -> {output}")
    print(f"Columns: {', '.join(columns)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
