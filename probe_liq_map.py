"""
Probe Coinglass /api/futures/liquidation/map endpoint.
Run with: CG_API_KEY=your_key python probe_liq_map.py
"""
import os, json, requests

API_KEY = os.environ.get("CG_API_KEY", "")
BASE    = "https://open-api-v4.coinglass.com"

if not API_KEY:
    print("ERROR: set CG_API_KEY env var")
    exit(1)

headers = {"coinglassSecret": API_KEY}

# Try different range values
for rng in ["1d", "7d", "30d"]:
    print(f"\n{'='*60}")
    print(f"Testing range={rng}")
    params = {
        "exchange": "Binance",
        "symbol":   "BTCUSDT",
        "range":    rng,
    }
    resp = requests.get(BASE + "/api/futures/liquidation/map",
                        params=params, headers=headers, timeout=15)
    print(f"Status: {resp.status_code}")
    data = resp.json()
    print(f"Success: {data.get('success')}")
    print(f"Code: {data.get('code')}")
    print(f"Msg: {data.get('msg')}")

    if data.get("data"):
        d = data["data"]
        print(f"Data type: {type(d)}")
        if isinstance(d, dict):
            print(f"Keys: {list(d.keys())}")
            for k, v in d.items():
                if isinstance(v, list):
                    print(f"  {k}: list of {len(v)} items")
                    if v:
                        print(f"    First item: {v[0]}")
                elif isinstance(v, (int, float, str)):
                    print(f"  {k}: {v}")
        elif isinstance(d, list):
            print(f"List of {len(d)} items")
            if d:
                print(f"First item: {json.dumps(d[0], indent=2)}")
    else:
        print(f"Full response: {json.dumps(data, indent=2)[:500]}")
