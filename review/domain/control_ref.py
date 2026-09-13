"""What a fold-change control points at, said unambiguously.

A control used to be a plain string, and a string cannot say whether ``A01``
means the well ``A01`` or a replicate set someone named ``A01``. The UI tried
to carry that distinction in the text itself, appending ``" (well)"`` to the
well entry, and the resolvers tried the suffixed spelling first. Both halves
were reasonable and the scheme still cannot work, because replicate-set names
are unrestricted free text — ``domain.selections_format._unique_name`` strips
whitespace and de-duplicates, and reserves no character at all. A user may name
a set ``A01 (well)``, and then the combo offers that set under a label the
resolvers read as "the well ``A01``": picking the set silently normalizes
against the well, and that set can never be chosen as a control.

No in-band encoding survives that, a ``well:`` / ``set:`` prefix included — the
namespace an encoding would need is one the user is free to write into. So the
kind travels *beside* the name rather than inside it, and is persisted as a
JSON object rather than a string.

``None`` means "no control". There is deliberately no empty ``ControlRef``, so
"is a control set?" has exactly one spelling everywhere.

Qt-free (``tests/test_qt_free_layers.py``) and free of ``features/`` imports
(``tests/test_layering.py``). Resolution needs ``ctx`` and therefore lives in
``domain.fold_change``; this module only describes the reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: A Sample-Definitions selection, addressed by its name.
KIND_SET = "set"
#: A loaded well, addressed by its token.
KIND_WELL = "well"
#: A reference read from a document written before the kind was recorded. Its
#: meaning is whatever the old precedence said — set name first, then well
#: token — and it is upgraded to a concrete kind the first time it resolves.
KIND_LEGACY = "legacy"

_KINDS = (KIND_SET, KIND_WELL, KIND_LEGACY)

#: Appended to a well's combo entry when a replicate set shares its token. This
#: is now **display only**: nothing parses it back, because the reference
#: carries its own kind. Keeping it is what lets the user tell the two entries
#: apart on screen.
WELL_DISAMBIG_SUFFIX = " (well)"


@dataclass(frozen=True)
class ControlRef:
    """A reference to the thing a fold-change axis divides by.

    Frozen, so it has value equality and a real hash and is safe as a dict key
    — which the per-redraw denominator cache relies on.
    """

    kind: str
    name: str

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"ControlRef.kind must be one of {_KINDS}, got {self.kind!r}")
        if not self.name:
            raise ValueError("ControlRef.name must not be empty; use None for 'no control'")

    # ── construction ─────────────────────────────────────────────────────────

    @classmethod
    def for_well(cls, token: Any) -> "ControlRef | None":
        token = str(token or "").strip()
        return cls(KIND_WELL, token) if token else None

    @classmethod
    def for_replicate_set(cls, name: Any) -> "ControlRef | None":
        name = str(name or "").strip()
        return cls(KIND_SET, name) if name else None

    @classmethod
    def legacy(cls, text: Any) -> "ControlRef | None":
        """A reference whose kind was never recorded.

        Also the reading for a bare string handed to an API that now expects a
        reference — a great many tests spell a control that way, and "resolve
        it the way it has always been resolved" is the only honest meaning.
        The display suffix is stripped here, since for a legacy value it was
        the only hint of kind and the old precedence handles the rest.
        """
        text = str(text or "").strip()
        if not text:
            return None
        if text.endswith(WELL_DISAMBIG_SUFFIX):
            bare = text[: -len(WELL_DISAMBIG_SUFFIX)].strip()
            return cls(KIND_WELL, bare) if bare else None
        return cls(KIND_LEGACY, text)

    @classmethod
    def coerce(cls, value: Any) -> "ControlRef | None":
        """Accept a reference, a bare string (legacy), or ``None``."""
        if value is None or isinstance(value, ControlRef):
            return value
        if isinstance(value, str):
            return cls.legacy(value)
        if isinstance(value, dict):
            return cls.from_storage(value)
        raise TypeError(f"cannot read a control reference from {type(value)!r}")

    @classmethod
    def from_storage(cls, value: Any) -> "ControlRef | None":
        """Read the persisted form: an object, or a legacy bare string."""
        if isinstance(value, dict):
            kind = str(value.get("kind") or "").strip()
            name = str(value.get("name") or "").strip()
            if not name:
                return None
            # An unknown kind is read as legacy rather than dropped: the name
            # is still meaningful and the old precedence can resolve it.
            return cls(kind if kind in _KINDS else KIND_LEGACY, name)
        return cls.legacy(value)

    # ── output ───────────────────────────────────────────────────────────────

    def to_storage(self) -> dict:
        return {"kind": self.kind, "name": self.name}

    @property
    def display_name(self) -> str:
        """The plain token, for an axis title, a CSV cell or a summary line.

        Never carries the disambiguation suffix — that is combo chrome, and it
        leaked into figures and exports while it was part of the stored value.
        """
        return self.name

    def combo_text(self, *, disambiguate: bool = False) -> str:
        """The label to show in a picker.

        *disambiguate* is set by the caller when a replicate set in the same
        list shares this well's token; it changes only what is drawn, since
        the reference itself already says which kind this is.
        """
        if disambiguate and self.kind == KIND_WELL:
            return f"{self.name}{WELL_DISAMBIG_SUFFIX}"
        return self.name

    @property
    def is_legacy(self) -> bool:
        return self.kind == KIND_LEGACY
