import os
import pandas as pd
import joblib

MODEL_PATH = "models/crypto_fakeout_xgb.pkl"
_ml_model = None

def get_ml_model():
    global _ml_model
    if _ml_model is None:
        if os.path.exists(MODEL_PATH):
            try:
                _ml_model = joblib.load(MODEL_PATH)
            except Exception as e:
                print(f"ML Modeli yüklenirken hata oluştu: {e}")
                _ml_model = False # Don't try again
        else:
            _ml_model = False
    return _ml_model if _ml_model is not False else None

def evaluate_ml_fakeout(ml_features: dict) -> float:
    """
    Girilen özellikler için Fakeout (1.0 sınıfı) olasılığını döndürür.
    """
    model = get_ml_model()
    if model is None:
        return 0.0
    
    try:
        df_feat = pd.DataFrame([ml_features])
        df_feat = df_feat[['OFI_Proxy', 'Vol_Z_Score', 'Upper_Wick_Ratio', 'Lower_Wick_Ratio']]
        # predict_proba returns [[prob_0, prob_1]]
        prob = model.predict_proba(df_feat)[0][1]
        return float(prob)
    except Exception as e:
        print(f"ML Tahmin hatası: {e}")
        return 0.0
