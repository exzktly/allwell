"""Batch/group data models extracted from well_viewer3."""

from __future__ import annotations



class ReplicateSet:
    def __init__(self, name: str, wells: list[str] | None = None) -> None:
        self.name: str = name
        self.wells: list[str] = list(wells or [])

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
