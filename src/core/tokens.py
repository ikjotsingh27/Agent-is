"""A rough token estimate that works offline (about 4 characters per token).

Real counts always come from the model service's own usage numbers. This is only
for planning and for the offline check.
"""
from __future__ import annotations

import json
import math


def estimate_tokens(obj) -> int:
    if obj is None:
        return 0
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))
