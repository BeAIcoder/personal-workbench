@echo off
rem Re-install the git sanitize filter for this repo (run once after each fresh clone).
rem Requires: python on PATH (project requirement anyway).
git config filter.sanitize.clean "python dev/git_sanitize_filter.py clean"
git config filter.sanitize.smudge "python dev/git_sanitize_filter.py smudge"
git config filter.sanitize.required false
git checkout-index -f -- dev/git_sanitize_filter.py
git rm -r --cached . -q
git read-tree HEAD
git checkout-index -f -a
echo [OK] sanitize filter installed and all files de-sanitized.
echo Note: two "external filter failed" lines about dev/git_sanitize_filter.py
echo itself are harmless (the file contains no sensitive strings).
