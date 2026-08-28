"""Editable strategy and behavior layer of The Organism.

Everything under self/editable/ may be modified by the organism without
founder approval. The protected core imports nothing from here; the
main entry point imports these modules dynamically so changes take
effect on the next run.
"""

__version__ = "0.1.0"