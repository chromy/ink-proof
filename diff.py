#!/usr/bin/env python3

import sys
import codecs
from difflib import unified_diff

if len(sys.argv) != 3:
  exit(1)

_, a, b = sys.argv

a_lines = codecs.open(a, encoding='utf-8-sig').read().splitlines()
b_lines = codecs.open(b, encoding='utf-8-sig').read().splitlines()


out_lines = list(unified_diff(a_lines, b_lines, fromfile=a, tofile=b, lineterm=""))

if out_lines:
  sys.stdout.writelines(out_lines)
  exit(1)

