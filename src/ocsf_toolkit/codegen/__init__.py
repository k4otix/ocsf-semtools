"""The model generator: one OCSF IR projected into a typed package of msgspec Structs (D#5).

``generate`` writes a package; ``render`` returns its files without writing them. The modules beside
this one hold the pieces: ``layout`` (which module each item goes in), ``naming`` (Python names),
``types`` (annotations), ``emit`` (source text) and ``generate`` (the projection itself).
"""

from ocsf_toolkit.codegen.generate import IR_FILE, generate, render

__all__ = ["IR_FILE", "generate", "render"]
