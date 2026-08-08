#!/usr/bin/env sh
set -eu
pytest --cov=metasift --cov-report=term-missing
python -m compileall -q src tests
