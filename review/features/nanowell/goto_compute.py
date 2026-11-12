"""What the Nanowells page's **Nanowell** box names: a well and an address in it, read from what was typed.

The box is on every view that draws nanowells, so "go to one nanowell" means one thing whichever view is open: the
text is read here, the controller selects the nanowell it names (its site detail opens, with its plots over time and
its crops), and each view then frames it in its own way. This module holds the reading, which needs no Qt and no
page: ``resolve`` takes the text, the wells the page draws, the well it shows, and a function giving a well's
lattice kind (a ring label names a different nanowell on a hexagonal and a square lattice).

The text is an address as the addresses are drawn — ``q,r`` (``-2,3``, ``(-2, 3)``, ``-2 3``) or a ring label
(``3.7``) — optionally led by a well: ``B04 2,-1``, ``B04: 2,-1``, ``b4/3.7``. Without a well the address is in the
page's well, the one the single-well views show and the Well control names.

Qt-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from review.features.nanowell.identity_focus_compute import parse_address

#: A well before the address: letters then digits (``B04``, ``b4``, ``AA12`` on a large plate), separated from the
#: address by a colon, a slash, a middle dot or a space. Digits are ``[0-9]`` rather than ``\d`` (``util/well_token``
#: explains why: ``\d`` matches every Unicode digit).
_WELL_LED = re.compile(r"\s*([A-Za-z]{1,2}[0-9]{1,3})\s*(?:[:/·]\s*|\s+)(.+)\Z")


@dataclass(frozen=True)
class Target:
    """The nanowell the box names: its well and its address, and the address written as the page writes it."""
    well: str
    q: int
    r: int

    @property
    def site(self) -> tuple[str, int, int]:
        return (self.well, self.q, self.r)

    @property
    def address(self) -> str:
        """``q,r``, as the addresses are drawn and the Identity view's focus is kept."""
        return f"{self.q},{self.r}"


def _same_well(typed: str, well: str) -> bool:
    """Whether *typed* names *well*: equal ignoring case and a column's leading zeros (``b4`` is ``B04``)."""
    def split(text):
        match = re.fullmatch(r"([A-Za-z]+)0*([0-9]+)", text.strip())
        return (match.group(1).upper(), match.group(2)) if match else (text.strip().upper(), "")
    return split(typed) == split(well)


def split_well(text: str, wells) -> tuple[str | None, str]:
    """``(well, the address text)``: the well *text* leads with, as the page names it, and the rest; ``(None, text)``
    when it leads with none. A leading token shaped like a well that names none of *wells* raises ``ValueError`` with
    the sentence the page shows, so a typo in the well is never read as an address in the page's well."""
    said = (text or "").strip()
    match = _WELL_LED.fullmatch(said)
    if not match:
        return None, said
    typed, rest = match.group(1), match.group(2).strip()
    for well in wells:
        if _same_well(typed, well):
            return well, rest
    shown = ", ".join(list(wells)[:8]) + (" …" if len(list(wells)) > 8 else "")
    raise ValueError(f"Well {typed.upper()} is not drawn on this page" + (f" (it draws {shown})" if shown else "")
                     + ": select it on the plate first.")


def resolve(text: str, wells, page_well: str, kind_of) -> Target:
    """The nanowell *text* names. *wells* are the wells the page draws, *page_well* the one it shows, and
    ``kind_of(well)`` that well's lattice kind (``"hex"`` / ``"square"``). Raises ``ValueError`` with the sentence
    the page shows when the text names no well the page draws or is not an address."""
    well, rest = split_well(text, wells)
    well = well or page_well
    if not well:
        raise ValueError("No well is drawn on this page: select a well with a nanowell table first.")
    q, r = parse_address(rest, kind_of(well))
    return Target(well, int(q), int(r))
