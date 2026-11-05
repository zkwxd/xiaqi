import json
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
raw = sys.stdin.readline()
data = json.loads(raw or "{}")
legal = data.get("legal_vertices") or []
allowed = data.get("allowed_actions") or []
if "END_GAME" in allowed:
    raise SystemExit("END_GAME must not be offered")
if legal:
    choice = random.choice(legal)
    out = {"action": "PLACE", "vertex": choice}
else:
    out = {"action": "PASS"}
sys.stdout.write(json.dumps(out) + "\n")
