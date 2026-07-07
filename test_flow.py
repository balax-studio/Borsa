import sys
sys.path.append('.')
import config
from data_sources import get_crypto_data
from strategies.crypto import analyze_strategies_crypto

symbol = 'BTC/USDT'
print('Veriler çekiliyor...')
df_1d, df_4h = get_crypto_data(symbol)

if df_1d is not None and df_4h is not None and not df_1d.empty and not df_4h.empty:
    print(f'1D boyutu: {len(df_1d)}, 4H boyutu: {len(df_4h)}')
    try:
        signals = analyze_strategies_crypto(symbol, df_1d, df_4h, btc_ok=True, btc_sniper_bias=1)
        print(f'Analiz başarılı, toplam {len(signals)} sinyal bulundu.')
        for s in signals:
            print(f"- {s.get('strategy')} -> {s.get('signal')} (Puan: {s.get('conviction_score')})")
    except Exception as e:
        print(f'HATA OLUŞTU: {e}')
        import traceback
        traceback.print_exc()
else:
    print('Veri çekilemedi!')
