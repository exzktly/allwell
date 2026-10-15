"""The names of the two image-storage options, with no dependencies.

``--phase_output`` and ``--tophat_dtype`` are parsed by ``pipeline.cli_args``,
offered by the Analyze form, forwarded by the remote runner and recorded in
``pipeline_info.json``; :mod:`pipeline.output_images` is what acts on them, and
its docstring says what each value does. The constants live here, apart from
that module, so the argument parser and the GUI can name the choices without
importing the image writers (numpy, tifffile, scikit-image) to do it.
"""

from __future__ import annotations

#: ``--phase_output`` values. ``reference`` is the default: the phase frame is
#: read back from the input archive rather than archived a second time.
PHASE_OUTPUT_REFERENCE = "reference"
PHASE_OUTPUT_COPY = "copy"
PHASE_OUTPUT_CHOICES = (PHASE_OUTPUT_REFERENCE, PHASE_OUTPUT_COPY)
DEFAULT_PHASE_OUTPUT = PHASE_OUTPUT_REFERENCE

#: ``--tophat_dtype`` values. ``source`` is the default: a corrected frame is
#: stored in its input's integer type when that loses nothing but rounding.
TOPHAT_DTYPE_SOURCE = "source"
TOPHAT_DTYPE_FLOAT32 = "float32"
TOPHAT_DTYPE_CHOICES = (TOPHAT_DTYPE_SOURCE, TOPHAT_DTYPE_FLOAT32)
DEFAULT_TOPHAT_DTYPE = TOPHAT_DTYPE_SOURCE

#: What a ``pipeline_info.json`` written before these options existed means:
#: every channel's ``_tophat`` was written, and written as float32.
LEGACY_PHASE_OUTPUT = PHASE_OUTPUT_COPY
LEGACY_TOPHAT_DTYPE = TOPHAT_DTYPE_FLOAT32
