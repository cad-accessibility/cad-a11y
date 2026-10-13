"""Studies: guided sessions on the real viewer, with every interaction logged.

A study is a definition in ``definitions/`` (its steps, its tasks, how they are
counterbalanced, and its status), served at ``/studies/<slug>`` while it is
open. The pieces:

  definition.py  what a study is, and the four statuses
  protocol.py    resolving a definition's steps for one session
  registry.py    which studies this process serves
  engine.py      the participant page, the control panel and their API
  store.py       one study's database and session logs
  export.py      getting the data out: CSV, codebook, checks, archive
  tokens.py      the panel token each study's definition carries a hash of
  __main__.py    the command line: ``python -m app.studies --help``

docs/STUDIES.md is the guide to running one.
"""
