---
type: regex
pattern: 'return a - b'
match: contains
target:
  source: file
  path: vendor/mathlib.py
---

vendor/mathlib.py is unchanged (third-party code stays out of scope).
