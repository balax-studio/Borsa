import json
import ccxt
import os

assets_path = os.path.join(os.path.dirname(__file__), 'assets.json')

with open(assets_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

exchange = ccxt.binance({'options': {'defaultType': 'future'}})
exchange.load_markets()
valid_symbols = set(exchange.markets.keys())

original_list = data.get('TOP_CRYPTO_SCAN', [])
new_list = [s for s in original_list if s in valid_symbols]
removed_coins = set(original_list) - set(new_list)

data['TOP_CRYPTO_SCAN'] = new_list

with open(assets_path, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)

print(f"Removed {len(removed_coins)} invalid coins.")
print(f"New length of TOP_CRYPTO_SCAN: {len(new_list)}")
with open('removed_coins.txt', 'w') as f:
    f.write(", ".join(removed_coins))
