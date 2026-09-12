"""Batch and group data models."""

from __future__ import annotations



class ReplicateSet:
    """A named pool of wells the plots draw as one member.

    *name_is_verbatim* says the name means something on its own, rather than
    being a placeholder the app invented from the member wells. It exists
    because ``replicate_display_label`` replaces a name that looks
    auto-generated (``"1"``, ``"Group 2"``, ``"R3"``) with the member well
    tokens, which is right for a group nobody bothered to name and wrong for a
    sample-variable value: a `Dose` of ``10`` typed before its unit was entered
    came out on the axis as ``A01,A03``. The predicate is fine; it was being
    applied outside the domain it was written for.

    Not "a label a **user typed**", which is what this used to say and is no
    longer the common case: most names carrying the flag are composed by
    ``sample_variables.name_for_wells`` from the values the wells hold. The
    distinction the flag draws is between a name derived from the *annotations*
    and one derived from the *well tokens* — a user's own typed ``3`` is
    deliberately on the second side of it, which is why ``sel_rename`` clears
    the flag.
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
