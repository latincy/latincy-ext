"""latincy-ext: experimental spaCy components for LatinCy pipelines."""

from latincy_ext.lila_linker import LilaLinker
from latincy_ext.macron_morph import MacronMorphComponent
from latincy_ext.speaker import SpeakerComponent
from latincy_ext.syllabifier import SyllabifierComponent

__all__ = [
    "LilaLinker",
    "MacronMorphComponent",
    "SpeakerComponent",
    "SyllabifierComponent",
]
