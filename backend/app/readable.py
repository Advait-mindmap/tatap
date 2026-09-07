"""Turn internal identifiers into the words a planner uses, for anything a person reads.

The reasoning trail exists so a civil or construction planner can satisfy themselves that the
logic holds without reading code. It stopped doing that: sentences arrived reading

    Instanced from frag.superstructure.steel (Superstructure - structural steel frame),
    selected because: ... formally confirmed via dp.dyn.low_confidence.frag.superstructure.steel
    ('Yes, it applies') and consistent with the resolved dp.delivery_mode.

Every fact in that is true and most of it is unreadable. `frag.superstructure.steel` and
`dp.delivery_mode` are variable names; a planner has no reason to know them, and a sentence that
demands them undercuts the one thing the panel is for. The human label was already sitting right
there in parentheses - it just was not the thing carrying the sentence.

So identifiers come OUT of the prose and go into a short technical reference list beside it. The
label leads, and the id stays available for whoever is debugging rather than reading.

DELIBERATELY UNTOUCHED: WBS codes, stage, department and duration labels, and activity names.
Those are real industry notation - a planner reads `05.03.001` fluently and would be worse off
without it. The distinction is not "codes are bad", it is "OUR internal names are not the
reader's vocabulary".
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple

#: Identifier shapes this module rewrites: our own internal namespaces, and nothing else.
#:
#: Allows dotted segments, so `dp.dyn.low_confidence.frag.x` is matched whole rather than
#: leaving `.frag.x` stranded mid-sentence.
_IDENTIFIER = re.compile(
    r'\b((?:dp|dyn|frag|gate|lead|path|safety|stat|zone)(?:\.[A-Za-z0-9_-]+)+)'
)

#: Namespaces described in words, for an id we hold no label for. Better a category than a
#: variable name: "a decision point" tells a planner what kind of thing was referred to.
_NAMESPACE_WORDS = {
    'dp': 'a decision point',
    'dyn': 'a decision point',
    'frag': 'a work package',
    'gate': 'a compliance gate',
    'lead': 'a long-lead item',
    'path': 'a statutory approval',
    'safety': 'a safety rule',
    'stat': 'a statutory approval',
    'zone': 'a zone',
}

#: A dynamic decision id wraps the thing it is about, and the wrapper carries meaning of its own.
#: Rewriting `dyn.low_confidence.frag.superstructure.steel` to just the package's name would say
#: the reasoning was confirmed "via Superstructure - structural steel frame", which reads as
#: nonsense: it was confirmed via the QUESTION ASKED ABOUT that package.
_DYNAMIC_SENSE = {
    'low_confidence': 'the low-confidence question about ',
    'no_coverage': 'the coverage question about ',
}

#: The words this module can leave behind in place of an id, for cleaning up dead parentheses.
_PLACEHOLDERS = (
    'work package', 'decision point', 'compliance gate', 'long-lead item',
    'statutory approval', 'safety rule', 'zone', 'internal reference',
)


def _namespace_of(identifier: str) -> str:
    return identifier.split('.', 1)[0]


def build_labels(*libraries: Iterable[Dict]) -> Dict[str, str]:
    """`{identifier: human label}` from library entries, so the map is never hand-kept.

    A label table maintained separately from the libraries drifts the moment an entry is added,
    and drifts silently - the id simply keeps appearing in prose.
    """
    labels: Dict[str, str] = {}
    for library in libraries:
        for entry in library or ():
            ident = str(entry.get('id') or '')
            # Each library names its entries in its own field - `equipment` for long-lead
            # plant, `activity_pattern` for a safety rule, `approval` for a statutory step - so
            # the lookup tries all of them. Missing one is not a crash; it is the id quietly
            # staying in the prose, which is the bug this module exists to remove.
            #
            # `question` is deliberately absent: a decision point's question is a whole sentence
            # - "For each discipline, is the work self-performed or subcontracted?" - and
            # dropping that into the middle of another sentence is less readable than the id it
            # replaced, not more. `title` is the short form and is what these entries carry.
            name = str(
                entry.get('title')
                or entry.get('name')
                or entry.get('label')
                or entry.get('equipment')
                or entry.get('activity_pattern')
                or entry.get('approval')
                or ''
            )
            if ident and name:
                labels[ident] = name
    return labels


def humanise(text: str, labels: Dict[str, str]) -> Tuple[str, List[str]]:
    """Rewrite identifiers in `text` as their labels. Returns (prose, identifiers removed)."""
    removed: List[str] = []

    def replace(match: 're.Match[str]') -> str:
        identifier = match.group(1)
        removed.append(identifier)

        if identifier in labels:
            return labels[identifier]

        # An id built around another id: use the longest inner id we hold a label for, and keep
        # the wrapper's sense in front of it.
        parts = identifier.split('.')
        for start in range(len(parts)):
            for end in range(len(parts), start, -1):
                inner = '.'.join(parts[start:end])
                if inner in labels and inner != identifier:
                    sense = next(
                        (phrase for key, phrase in _DYNAMIC_SENSE.items() if key in identifier),
                        '',
                    )
                    return f'{sense}{labels[inner]}'

        return _NAMESPACE_WORDS.get(_namespace_of(identifier), 'an internal reference')

    prose = _IDENTIFIER.sub(replace, text or '')

    # `X (X)` - the engine put the id in the sentence and the label in parentheses, so rewriting
    # the id leaves the label twice. Collapse it rather than making every caller remember.
    prose = re.sub(r'(.{4,80}?)\s*\(\1\)', r'\1', prose)

    # An id in parentheses was usually glossing the label that now carries the sentence, so
    # "(a work package)" is left saying nothing.
    placeholders = '|'.join(re.escape(word) for word in _PLACEHOLDERS)
    prose = re.sub(r'\s*\((?:a |an )?(?:' + placeholders + r')\)', '', prose)

    # Collapse the double spaces and orphaned punctuation a removal can leave.
    prose = re.sub(r'\s{2,}', ' ', prose)
    prose = re.sub(r'\s+([,.;:])', r'\1', prose).strip()

    # Stable order, de-duplicated: this is a reference list, not a transcript.
    return prose, sorted(set(removed))
