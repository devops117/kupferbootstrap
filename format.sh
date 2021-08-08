#!/bin/sh

yapf \
    --recursive \
    --in-place \
    --parallel \
    .
autoflake \
    --recursive \
    --in-place \
    --remove-unused-variables \
    --remove-all-unused-imports \
    --expand-star-imports \
    --remove-duplicate-keys \
    .
