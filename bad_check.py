import json, math, collections
from ingestion_fixed import event_minute

reasons, samples = collections.Counter(), {}
for line in open("server_telemetry.ndjson", encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line:
        continue
    try:
        ev = {k.lower(): v for k, v in json.loads(line).items()}
        shift = ev.get("epoch_shift")
        shift = int(shift) if shift is not None and not isinstance(shift, bool) else None
        event_minute(ev["timestamp"], shift)
        ev["service"].strip()
        if not math.isfinite(float(ev["latency_ms"])):
            raise ValueError("non-finite latency")
        str(ev["level"]); ev["event_id"]
    except Exception as e:
        r = f"{type(e).__name__}: {str(e)[:60]}"
        reasons[r] += 1
        samples.setdefault(r, line[:200])

for r, c in reasons.most_common():
    print(c, r, "\n   ", samples[r])