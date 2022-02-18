#!/bin/bash
git ls-files \*.py | xargs mypy --pretty --install-types --ignore-missing-imports "$@"
