import time
import warnings
warnings.filterwarnings('ignore')

from strategies.crypto.ml_filter import evaluate_ml_fakeout

def run_benchmark():
    # Model'in bekledigi feature listesi
    dummy_features = {
        "OFI_Proxy": 5.2,
        "Vol_Z_Score": 1.5,
        "Upper_Wick_Ratio": 0.2,
        "Lower_Wick_Ratio": 0.8,
        "RSI_Diff_20_50": -12.4,
        "BTC_Corr_14": 0.85
    }

    print("--- ML Filtresi Benchmark Testi ---")
    
    # 1. Ilk yukleme / Isinma
    start_time = time.time()
    prob = evaluate_ml_fakeout(dummy_features)
    end_time = time.time()
    print(f"Ilk cagri (model yukleme dahil) suresi: {(end_time - start_time)*1000:.2f} ms")

    # 2. Dongusel test (1000 sinyal uzerinden)
    n_iterations = 1000
    start_time = time.time()
    for _ in range(n_iterations):
        _ = evaluate_ml_fakeout(dummy_features)
    end_time = time.time()
    
    total_time = end_time - start_time
    avg_time = (total_time / n_iterations) * 1000  # ms
    
    print(f"{n_iterations} adet ardisik cagri toplam suresi: {total_time:.4f} saniye")
    print(f"Sinyal basina ortalama islem suresi: {avg_time:.4f} ms")
    
    # Degerlendirme
    print("\n[Sonuc]")
    if avg_time < 5.0:
        print("[BASARILI] Performans Etkisi: COK DUSUK. ML filtresi ana donguyu yavaslatmayacak kadar hizli calisiyor.")
    elif avg_time < 50.0:
        print("[UYARI] Performans Etkisi: KABUL EDILEBILIR. ML filtresi hafif bir gecikme ekliyor ancak tolere edilebilir.")
    else:
        print("[HATA] Performans Etkisi: YUKSEK. Sinyal uretimi yavaslayabilir.")

if __name__ == "__main__":
    run_benchmark()
