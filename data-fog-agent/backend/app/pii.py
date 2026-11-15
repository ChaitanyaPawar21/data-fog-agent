"""Personal data detection and masking.

Regex rules for Indian identifiers, phones and emails, plus Presidio for names and places.
`column_pii_types` decides whether a column holds personal data from a sample of its values.
"""

import logging
import re
from collections import Counter
from functools import cache

VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 7, 6, 8, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check_digit(digits: str) -> int:
    check = 0
    # Position 0 is where the check digit will go, so the payload starts at 1.
    for position, digit in enumerate(reversed(digits), start=1):
        check = VERHOEFF_D[check][VERHOEFF_P[position % 8][int(digit)]]
    return VERHOEFF_INV[check]


def verhoeff_is_valid(digits: str) -> bool:
    check = 0
    for position, digit in enumerate(reversed(digits)):
        check = VERHOEFF_D[check][VERHOEFF_P[position % 8][int(digit)]]
    return check == 0


# The lookarounds stop a match inside a longer number, like an order id.
AADHAAR = re.compile(r"(?<!\d)[2-9]\d{3}\s?\d{4}\s?\d{4}(?!\d)")
PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
PHONE = re.compile(r"(?:\+91[\-\s]?|(?<!\d))[6-9]\d{9}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def is_aadhaar(text: str) -> bool:
    # 12 random digits pass the pattern; the checksum is what makes it an Aadhaar number.
    return verhoeff_is_valid(re.sub(r"\s", "", text))


def mask_value(value: str) -> str:
    keep = 4 if len(value) >= 8 else 2
    return "XXXX" + value[-keep:]


def mask_pii_in_text(text: str) -> str:
    """Masks Aadhaar, PAN, phone and email values inside free text."""
    text = AADHAAR.sub(lambda m: mask_value(m[0]) if is_aadhaar(m[0]) else m[0], text)
    for pattern in [PAN, PHONE, EMAIL]:
        text = pattern.sub(lambda m: mask_value(m[0]), text)
    return text


# ---------------------------------------------------------------- column scanning

# A column is tagged when at least this share of its non-empty samples match.
MATCH_SHARE = 0.2
# Free-text columns are tagged on a single match: one Aadhaar number in one note is enough.
FREE_TEXT_WORDS = {"note", "notes", "comment", "comments", "message", "messages", "log", "logs"}
FREE_TEXT_WORDS |= {"remark", "remarks", "description", "text", "body"}
# Postgres and pandas names for text columns. Names and places are only looked for in these.
TEXT_TYPES = {"text", "character varying", "character", "str", "object"}


def regex_types(value: str) -> set[str]:
    found = set()
    if any(is_aadhaar(match) for match in AADHAAR.findall(value)):
        found.add("AADHAAR")
    for name, pattern in [("PAN", PAN), ("PHONE", PHONE), ("EMAIL", EMAIL)]:
        if pattern.search(value):
            found.add(name)
    return found


@cache
def name_and_place_analyzer():
    # Imported here so only the processes that scan pay for loading spaCy.
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    # Presidio warns about every recognizer for a language we don't load.
    logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
    model = {"lang_code": "en", "model_name": "en_core_web_sm"}
    nlp = NlpEngineProvider(
        nlp_configuration={"nlp_engine_name": "spacy", "models": [model]}
    ).create_engine()
    return AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])


def name_and_place_types(values: list[str]) -> list[set[str]]:
    """PERSON / LOCATION found in each value. One batch, so spaCy can process them together."""
    from presidio_analyzer import BatchAnalyzerEngine

    batch = BatchAnalyzerEngine(analyzer_engine=name_and_place_analyzer())
    results = batch.analyze_iterator(
        values, language="en", batch_size=50, entities=["PERSON", "LOCATION"]
    )
    return [{result.entity_type for result in found} for found in results]


def is_free_text(column_name: str) -> bool:
    return bool(set(re.split(r"[^a-z]+", column_name.lower())) & FREE_TEXT_WORDS)


def column_pii_types(column_name: str, data_type: str, samples: list[str]) -> set[str]:
    """The kinds of personal data a column holds, judged from a sample of its values."""
    values = [value for value in samples if value and value.strip()]
    if not values:
        return set()

    free_text = is_free_text(column_name)
    # Presidio would find a "name" in almost any column of prose, and free text is tagged on
    # a single match, so prose is checked with the regex rules only.
    use_presidio = data_type in TEXT_TYPES and not free_text

    counts = Counter()
    for value in values:
        counts.update(regex_types(value))
    if use_presidio:
        for found in name_and_place_types(values):
            counts.update(found)

    needed = 1 if free_text else MATCH_SHARE * len(values)
    tagged = {kind for kind, count in counts.items() if count >= needed}

    # The small spaCy model often reads an Indian city as a person and the other way round,
    # so a column of names or places can split its hits below the line for each kind.
    # Together they still show it holds one or the other; tag the more common one. Only for
    # columns nothing else matched: a PAN column with a few "names" in it is still a PAN column.
    names_and_places = counts["PERSON"] + counts["LOCATION"]
    if use_presidio and not tagged and names_and_places >= needed:
        tagged.add(max(["PERSON", "LOCATION"], key=lambda kind: counts[kind]))
    return tagged
