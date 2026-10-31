---
type: regex
pattern: 'return a \+ b'
match: contains
target:
  source: file
  path: calc.py
---

calc.py now returns the sum.
