"""A *vertical* — one crawlable section of lalafo — loaded from YAML.

`constants.py` holds what is true of the *site* (endpoints, headers, geography).
Everything true of a *section* lives in a taxonomy file instead, so adding cars,
electronics or jobs is a new YAML file rather than a code change:

* ``categories`` — the leaf categories that become crawl streams, each classified
  on two axes (``type`` -> the ``property_type`` column, ``deal``) plus any number
  of free-form labels (cars carry ``brand``) that become extra listing columns;
* ``params``     — param-id -> English column.  lalafo reuses one concept under
  different ids per section (mileage is 56 for cars, 2063 for trucks), which is
  exactly why this map belongs to the section and not to the site;
* ``special_params`` — params that feed a dedicated column or dimension table
  (real estate's ЖК / developer / district).  Omitted where the concept does not
  exist.

A malformed file fails loudly at load time — same contract as ``config.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Category-entry keys with a fixed meaning.  Anything else is a free-form label.
_RESERVED = frozenset({"type", "deal", "name"})

#: Recognised keys in the ``special_params`` block.
_SPECIAL = ("complex", "developer", "district")


@dataclass(frozen=True, slots=True)
class Leaf:
    """One leaf category — a single crawl stream."""

    category_id: int
    property_type: str
    deal: str
    name: str
    #: extra per-leaf labels (e.g. ``{"brand": "Toyota"}``), flattened into the row
    labels: dict[str, Any] = field(default_factory=dict)

    def classification(self) -> dict[str, Any]:
        """The columns this leaf contributes to a listing row."""
        return {
            "property_type": self.property_type,
            "deal": self.deal,
            "category_name": self.name,
            **self.labels,
        }


@dataclass(slots=True)
class Taxonomy:
    """A loaded vertical.  Immutable in practice; passed wherever CATEGORIES was."""

    vertical: str
    title: str
    site_path: str
    root_category: int | None
    leaves: dict[int, Leaf]
    param_map: dict[int, str]
    special_params: dict[str, int]
    source: Path
    #: Optional long-form dataset guide, shipped as DATASET_GUIDE.md. Relative to
    #: the project root. A vertical without one simply ships no guide.
    guide: str | None = None

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> Taxonomy:
        path = Path(path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"categories file not found: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: expected a mapping at the top level")

        categories = raw.get("categories") or {}
        if not categories:
            raise ValueError(f"{path}: 'categories' is empty — a vertical needs at least one leaf")

        leaves: dict[int, Leaf] = {}
        for key, entry in categories.items():
            cid = _as_int(key, path, "category id")
            if not isinstance(entry, dict):
                raise ValueError(
                    f"{path}: category {cid} must be a mapping, "
                    f"got {type(entry).__name__}"
                )
            missing = {"type", "deal"} - entry.keys()
            if missing:
                raise ValueError(f"{path}: category {cid} is missing {sorted(missing)}")
            leaves[cid] = Leaf(
                category_id=cid,
                property_type=str(entry["type"]),
                deal=str(entry["deal"]),
                name=str(entry.get("name") or ""),
                labels={k: v for k, v in entry.items() if k not in _RESERVED},
            )

        param_map = {
            _as_int(k, path, "param id"): str(v)
            for k, v in (raw.get("params") or {}).items()
        }

        special_raw = raw.get("special_params") or {}
        unknown = special_raw.keys() - set(_SPECIAL)
        if unknown:
            raise ValueError(
                f"{path}: unknown special_params {sorted(unknown)}; known: {list(_SPECIAL)}"
            )
        special = {k: _as_int(v, path, f"special_params.{k}") for k, v in special_raw.items()}

        root = raw.get("root_category")
        return cls(
            vertical=str(raw.get("vertical") or path.stem),
            title=str(raw.get("title") or path.stem),
            site_path=str(raw.get("site_path") or "/"),
            root_category=int(root) if root is not None else None,
            leaves=leaves,
            param_map=param_map,
            special_params=special,
            source=path,
            guide=str(raw["guide"]) if raw.get("guide") else None,
        )

    # -- the scope vocabulary (what a run config may select) ---------------

    @property
    def property_types(self) -> tuple[str, ...]:
        return tuple(sorted({leaf.property_type for leaf in self.leaves.values()}))

    @property
    def deals(self) -> tuple[str, ...]:
        return tuple(sorted({leaf.deal for leaf in self.leaves.values()}))

    @property
    def label_names(self) -> tuple[str, ...]:
        """Extra label columns this vertical adds to the listings table."""
        names: set[str] = set()
        for leaf in self.leaves.values():
            names.update(leaf.labels)
        return tuple(sorted(names))

    # -- lookups -----------------------------------------------------------

    def categories_for(self, property_types: list[str], deals: list[str]) -> list[int]:
        """The leaf ids matching a scope of property types and deals."""
        wanted_types, wanted_deals = set(property_types), set(deals)
        return [
            cid
            for cid, leaf in self.leaves.items()
            if leaf.property_type in wanted_types and leaf.deal in wanted_deals
        ]

    def classify(self, category_id: Any) -> Leaf | None:
        try:
            return self.leaves.get(int(category_id))
        except (TypeError, ValueError):
            return None

    # -- convenience -------------------------------------------------------

    @property
    def complex_param(self) -> int | None:
        return self.special_params.get("complex")

    @property
    def developer_param(self) -> int | None:
        return self.special_params.get("developer")

    @property
    def district_param(self) -> int | None:
        return self.special_params.get("district")

    @property
    def site_url(self) -> str:
        from .constants import BASE_URL

        return f"{BASE_URL}{self.site_path}"

    def __len__(self) -> int:
        return len(self.leaves)


def _as_int(value: Any, path: Path, what: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{path}: {what} must be an integer, got {value!r}") from None
