#!/bin/bash
git ls-files \*.py | sort -u | xargs mypy --pretty --install-types --ignore-missing-imports "$@"
