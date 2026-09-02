"""Lead Intelligence System — auto-qualify inbound leads against a rubric.

The package is organised as one module per pipeline stage (see
`pipeline_architecture_spec.md` §2), each independently testable:

    ingest  -> normalize -> rubric -> decision -> postprocess -> report

`pipeline.run` wires them together; `main.py` is the CLI entry point.
"""

__version__ = "0.1.0"
