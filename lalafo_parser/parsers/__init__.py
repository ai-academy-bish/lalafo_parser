"""JSON parsers — the only modules that know the shape of the API payload.

None of them make a request; each takes a decoded JSON object and returns records,
which makes them testable against a saved response.
"""

from .listing import ListingParser, ParsedAd

__all__ = ["ListingParser", "ParsedAd"]
