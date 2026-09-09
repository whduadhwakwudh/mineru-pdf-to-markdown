"""Normalize MinerU/PDF extraction text: repair ligature and symbol artifacts.

PDF fonts frequently encode ligatures (fi, fl, ff, ffi, ffl, ft, st) as single
glyphs. When a converter cannot map them back to Unicode, it emits Private Use
Area (PUA) code points (U+E000-U+F8FF) or drops the glyph entirely. This module
repairs the common cases without touching legitimate text.

Design:
  - `repair_ligatures(text)` replaces known PUA ligature code points and the
    Unicode Ligature block (U+FB00-U+FB06) with their plain ASCII expansions.
  - Replacements are context-guarded for the ambiguous cases: U+E103 maps to
    "fi" or "fl" depending on the surrounding word, so a small word-list check
    picks the reading that forms a real English word.
  - Non-ligature symbol artifacts (NBSP, white-circle degree sign, figure dash
    mis-mapped into PUA) are normalized to their intended characters.
  - Anything still unresolved is reported, never guessed silently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ─── Ligature code points ───────────────────────────────────────────────────
# Unicode Ligature block: unambiguous, always expand.
UNICODE_LIGATURES: dict[str, str] = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}

# PUA ligature code points observed in real PDFs (Adobe Frutiger/Monotype
# subsets used by Elsevier, RSC, Wiley, Science Advances and others).
# Values are the *primary* reading; ambiguous ones are re-checked by context.
PUA_LIGATURES: dict[str, str] = {
    "\ue103": "fi",  # scienti-fi-c, specifi-c
    "\ue104": "fl",  # in-fl-uencing, -fl-ows, -fl-uoride
    "\ue09d": "ft",  # a-ft-er, o-ft-en
    "\ue096": "ff",  # Tro-ff-, o-ff-
}

# Alternative readings to try when the primary reading does not form a word.
AMBIGUOUS_READINGS: dict[str, tuple[str, ...]] = {
    "\ue103": ("fi", "fl", "ff"),
    "\ue104": ("fl", "ff", "fi"),
    "\ue09d": ("ft",),
    "\ue096": ("ff", "fi", "fl"),
}

# ─── Non-ligature artifacts ─────────────────────────────────────────────────
SYMBOL_FIXES: dict[str, str] = {
    "\u00a0": " ",   # NBSP -> space
    "\u25e6": "°",   # white bullet mis-used as degree sign
    "\uf02d": "-",   # PUA hyphen (Adobe)
    "\u00ad": "",    # soft hyphen
    "\ue0d5": " ",   # PUA separator glyph (CJK/space); collapsed later if CJK
}

# Degree-sign look-alikes that should stay as a degree sign
DEGREE_ALIKE = "\u25e6"

# MinerU sometimes renders an "ffi"/"ffl" ligature as plain "fi"/"fl",
# silently dropping one "f". The damage is unrecoverable from context alone,
# so a small explicit table repairs the frequent cases.
LIGATURE_DROP_FIXES: dict[str, str] = {
    "suficient": "sufficient",
    "insuficient": "insufficient",
    "suficiency": "sufficiency",
    "insuficiency": "insufficiency",
    "eficient": "efficient",
    "eficiency": "efficiency",
    "ineficient": "inefficient",
    "difcult": "difficult",
    "difculty": "difficulty",
    "dificult": "difficult",
    "dificulty": "difficulty",
    "dificulties": "difficulties",
    "ofce": "office",
    "ofcial": "official",
    "ofset": "offset",
    "diferent": "different",
    "diference": "difference",
    "diferences": "differences",
    "afect": "affect",
    "afected": "affected",
    "afects": "affects",
    "aford": "afford",
    "ofers": "offers",
    "ofer": "offer",
    "stuf": "stuff",
    "stif": "stiff",
    "trafic": "traffic",
    "efect": "effect",
    "efects": "effects",
    "efective": "effective",
    "efectively": "effectively",
}

_WORD_RE = re.compile(r"[A-Za-z]{4,}")
# Context fragments may be a single letter (e.g. "scienti" + ligature + "c"),
# so use an unrestricted letter run when reading the neighbourhood.
_LETTERS_RE = re.compile(r"[A-Za-z]+")
_TAG_RE = re.compile(r"<[^>]{0,80}>")

# A compact word list for disambiguation. Kept small and focused on the
# vocabulary where ligature artifacts actually occur (scientific English).
_WORDS: frozenset[str] = frozenset(
    """
    scientific specified specification specify specific significance significant
    efficient efficiency effectively effect effects
    influence influencing influenced influences
    flows flow flowing flat fluoride fluorine
    after often
    filling filled filling
    different differential diffusion
    difficult difficulty
    office officer offer offset
    staff stuff stiff
    transfer transferred transformation
    reflective refractive reflection
    identify identified identification
    modified modification modifier
    configuration configured
    coefficient coefficients
    buffer buffers
    field fields
    filter filters filtered filtration
    first finally final
    figure figures
    film films
    fiber fibers
    five
    fix fixed
    fire
    defined define defines defining
    classified classification classify
    beneficial benefit benefits
    sacrificing sacrifice
    verifying verified verification
    purified purification
    quantified quantification
    modified modification
    simplified simplification
    amplified amplification
    flexibility flexible
    fluctuation fluctuations
    """.split()
)


@dataclass
class RepairReport:
    """Outcome of one normalization pass."""

    ligature_fixes: dict[str, int] = field(default_factory=dict)
    symbol_fixes: dict[str, int] = field(default_factory=dict)
    word_fixes: dict[str, int] = field(default_factory=dict)
    unresolved: dict[str, int] = field(default_factory=dict)

    @property
    def total_fixes(self) -> int:
        return (
            sum(self.ligature_fixes.values())
            + sum(self.symbol_fixes.values())
            + sum(self.word_fixes.values())
        )

    def as_dict(self) -> dict:
        return {
            "ligature_fixes": dict(self.ligature_fixes),
            "symbol_fixes": dict(self.symbol_fixes),
            "word_fixes": dict(self.word_fixes),
            "unresolved": dict(self.unresolved),
            "total_fixes": self.total_fixes,
        }


def _pick_reading(char: str, left: str, right: str) -> str | None:
    """Choose the ligature expansion that forms a known word, else None."""
    readings = AMBIGUOUS_READINGS.get(char) or ((PUA_LIGATURES.get(char, "")),)
    for reading in readings:
        if not reading:
            continue
        candidate = re.sub(r"[^a-z]", "", (left + reading + right).lower())
        if len(candidate) >= 4 and candidate in _WORDS:
            return reading
    return None


def _context(text: str, start: int) -> tuple[str, str]:
    """Return the letter runs immediately left/right of position `start`.

    MinerU wraps ligature glyphs in markup such as ``<sup>\ue103</sup>``, so the
    character adjacent to the code point is often ``>`` or ``<``. Strip tags
    from the neighbourhood before taking the word fragments.
    """
    left_raw = text[max(0, start - 24):start]
    right_raw = text[start + 1:start + 25]
    left_raw = _TAG_RE.sub("", left_raw)
    right_raw = _TAG_RE.sub("", right_raw)
    left = _LETTERS_RE.findall(left_raw)
    right = _LETTERS_RE.findall(right_raw)
    return (left[-1] if left else ""), (right[0] if right else "")


def repair_text(text: str) -> tuple[str, RepairReport]:
    """Repair ligature and symbol artifacts, returning (fixed_text, report).

    Unambiguous code points are always expanded. Ambiguous PUA ligatures use the
    surrounding word to pick a reading; if no reading forms a known word the
    primary reading is used and the occurrence is recorded as unresolved.
    """
    fixed, report = _repair_code_points(text)
    # MinerU often wraps a ligature glyph in <sup>..</sup>; once the glyph is
    # expanded the wrapper is meaningless and breaks word continuity.
    fixed = _SUP_LIGATURE_RE.sub(r"\1", fixed)
    fixed = _fix_dropped_ligature_letters(fixed, report)
    fixed = _CJK_SPACE_RE.sub(r"\1\2", fixed)
    return fixed, report


def _fix_dropped_ligature_letters(text: str, report: RepairReport) -> str:
    """Repair words where a ligature lost one letter (suficient -> sufficient)."""
    def replace(match: re.Match) -> str:
        word = match.group(0)
        lower = word.lower()
        if lower not in LIGATURE_DROP_FIXES:
            return word
        fixed_word = LIGATURE_DROP_FIXES[lower]
        if word.isupper():
            fixed_word = fixed_word.upper()
        elif word[0].isupper():
            fixed_word = fixed_word[0].upper() + fixed_word[1:]
        report.word_fixes[word] = report.word_fixes.get(word, 0) + 1
        return fixed_word

    return _WORD_RE.sub(replace, text)


_SUP_LIGATURE_RE = re.compile(r"<sup>([a-z]{1,4})</sup>(?=[a-z])")
# A space introduced between two CJK characters is never intentional.
_CJK_SPACE_RE = re.compile(r"([\u4e00-\u9fff]) +([\u4e00-\u9fff])")


def _repair_code_points(text: str) -> tuple[str, RepairReport]:
    report = RepairReport()
    out: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        ch = text[index]

        if ch in UNICODE_LIGATURES:
            out.append(UNICODE_LIGATURES[ch])
            report.ligature_fixes[ch] = report.ligature_fixes.get(ch, 0) + 1
            index += 1
            continue

        if ch in PUA_LIGATURES:
            left, right = _context(text, index)
            chosen = _pick_reading(ch, left, right)
            if chosen is None:
                # No reading forms a known word: fall back to the primary
                # reading and record it for review rather than guessing.
                chosen = PUA_LIGATURES[ch]
                if chosen:
                    report.unresolved[ch] = report.unresolved.get(ch, 0) + 1
            if chosen:
                out.append(chosen)
                report.ligature_fixes[ch] = report.ligature_fixes.get(ch, 0) + 1
            # chosen == "" drops the glyph (CJK-only artifact)
            index += 1
            continue

        if ch in SYMBOL_FIXES:
            out.append(SYMBOL_FIXES[ch])
            report.symbol_fixes[ch] = report.symbol_fixes.get(ch, 0) + 1
            index += 1
            continue

        # Any other PUA code point is reported, left untouched.
        if 0xE000 <= ord(ch) <= 0xF8FF:
            report.unresolved[ch] = report.unresolved.get(ch, 0) + 1
            out.append(ch)
            index += 1
            continue

        out.append(ch)
        index += 1

    return "".join(out), report


def has_artifacts(text: str) -> bool:
    """True if the text contains any known or PUA artifact."""
    return any(
        ch in UNICODE_LIGATURES or ch in PUA_LIGATURES or ch in SYMBOL_FIXES
        or 0xE000 <= ord(ch) <= 0xF8FF
        for ch in text
    )
