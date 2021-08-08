#!/bin/bash

yapf_args=('--recursive' '--parallel')
autoflake_args=('--recursive' '--remove-unused-variables' '--remove-all-unused-imports' '--expand-star-imports' '--remove-duplicate-keys')

format() {
    yapf "${yapf_args[@]}"  .
    autoflake "${autoflake_args[@]}" .
}


if [[ "$1" == "--check" ]]; then
    yapf_args+=('--diff')
    [[ "$(format | tee /dev/stderr | wc -c)" == "0" ]]
else
    yapf_args+=('--in-place')
    autoflake_args+=('--in-place')
    format
fi
