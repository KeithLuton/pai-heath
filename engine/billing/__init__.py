"""Billing and invoice extraction.

Reads billing documents (paper claim forms, EOBs, Medicare notices, patient
bills, FHIR financial resources) into source assertions with provenance, per
the contract in ingest/billing_profiles.py.

What this package never does:
* decide a profile for a paper document on its own (a person confirms it);
* map a money column without an explicit printed label;
* derive paid / allowed / responsibility by arithmetic;
* collapse repeated rows;
* turn a billed service into a clinical fact (billing assertions stay out of
  the record engine).
"""
from .api import ADAPTER_VERSION, PAPER, SUPPORTED, classify, classify_text, extract, profiles  # noqa: F401
