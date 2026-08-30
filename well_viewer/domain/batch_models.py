"""Batch/group data models extracted from well_viewer3."""

from __future__ import annotations



class ReplicateSet:
    """A named pool of wells the plots draw as one member.

    *name_is_verbatim* says the name is a label a **user typed**, not one the
    app generated. It exists because ``replicate_display_label`` replaces a name
    that looks auto-generated (``"1"``, ``"Group 2"``, ``"R3"``) with the
    member well tokens, which is right for a group nobody bothered to name and
    wrong for a sample-variable value: a `Dose` of ``10`` typed before its unit
    was entered came out on the axis as ``A01,A03``. The predicate is fine; it
    was being applied outside the domain it was written for.
    """

    def __init__(self, name: str, wells: list[str] | None = None, *,
                 name_is_verbatim: bool = False) -> None:
        self.name: str = name
        self.wells: list[str] = list(wells or [])
        self.name_is_verbatim: bool = bool(name_is_verbatim)

    def __repr__(self) -> str:
        return f"ReplicateSet({self.name!r}, {self.wells!r})"


class BarGroup:
    def __init__(
        self,
        name: str,
        members: list[ReplicateSet] | None = None,
        solo_wells: list[str] | None = None,
        hidden: bool = False,
    ) -> None:
        self.name: str = name
        self.members: list[ReplicateSet] = list(members or [])
        self.solo_wells: list[str] = list(solo_wells or [])
        self.hidden: bool = hidden

    @property
    def replicates(self) -> list[ReplicateSet]:
        return self.members

    @property
    def wells(self) -> list[str]:
        seen: set = set()
        out: list[str] = []
        for rset in self.members:
            for w in rset.wells:
                if w not in seen:
                    out.append(w)
                    seen.add(w)
        for w in self.solo_wells:
            if w not in seen:
                out.append(w)
                seen.add(w)
        return out

    def __repr__(self) -> str:
        h = " [hidden]" if self.hidden else ""
        return f"BarGroup({self.name!r}, {len(self.members)} set(s){h})"
