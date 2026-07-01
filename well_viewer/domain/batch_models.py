"""Batch/group data models extracted from well_viewer3."""

from __future__ import annotations

from typing import List, Optional


class ReplicateSet:
    def __init__(self, name: str, wells: Optional[List[str]] = None) -> None:
        self.name: str = name
        self.wells: List[str] = list(wells or [])

    def __repr__(self) -> str:
        return f"ReplicateSet({self.name!r}, {self.wells!r})"


class BarGroup:
    def __init__(
        self,
        name: str,
        members: Optional[List[ReplicateSet]] = None,
        solo_wells: Optional[List[str]] = None,
        hidden: bool = False,
    ) -> None:
        self.name: str = name
        self.members: List[ReplicateSet] = list(members or [])
        self.solo_wells: List[str] = list(solo_wells or [])
        self.hidden: bool = hidden

    @property
    def replicates(self) -> List[ReplicateSet]:
        return self.members

    @property
    def wells(self) -> List[str]:
        seen: set = set()
        out: List[str] = []
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

    def replicate_sets(self) -> List[List[str]]:
        result = [rset.wells for rset in self.members if rset.wells]
        for w in self.solo_wells:
            result.append([w])
        return result

    def __repr__(self) -> str:
        h = " [hidden]" if self.hidden else ""
        return f"BarGroup({self.name!r}, {len(self.members)} set(s){h})"
