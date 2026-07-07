import urllib.request, urllib.error

urls = ['fapi.binance.com', 'fapi1.binance.com', 'fapi2.binance.com', 'fapi3.binance.com', 'fapi.binance.me']
for url in urls:
    try:
        req = urllib.request.urlopen(f'https://{url}/fapi/v1/ping', timeout=3)
        print(f'{url}: {req.getcode()}')
    except Exception as e:
        print(f'{url}: Error -> {type(e).__name__} {e}')
