import pandas as pd
import numpy as np
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import joblib
import os

def train_fakeout_model(dataset_path="fakeout_dataset.csv", model_save_path="models/crypto_fakeout_xgb.pkl"):
    print(f"[{dataset_path}] okunuyor...")
    if not os.path.exists(dataset_path):
        print(f"Hata: Veri seti {dataset_path} bulunamadı!")
        return
        
    df = pd.read_csv(dataset_path)
    
    features = ['OFI_Proxy', 'Vol_Z_Score', 'Upper_Wick_Ratio', 'Lower_Wick_Ratio']
    target = 'Target_Label'
    
    # Veri kontrolü
    missing_cols = [c for c in features + [target] if c not in df.columns]
    if missing_cols:
        print(f"Eksik sütunlar: {missing_cols}")
        return
        
    df = df.dropna(subset=features + [target])
    print(f"Toplam geçerli satır: {len(df)}")
    
    if len(df) < 50:
        print("Model eğitimi için yeterli veri yok!")
        return
        
    X = df[features]
    y = df[target]
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    
    neg_count = sum(y_train == 0)
    pos_count = sum(y_train == 1)
    scale_pos_weight = neg_count / pos_count if pos_count > 0 else 1.0
    print(f"Eğitim Sınıf Dağılımı - Fakeout Yok: {neg_count}, Fakeout Var: {pos_count}")
    
    print("XGBoost modeli eğitiliyor...")
    model = XGBClassifier(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=4,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        eval_metric='logloss'
    )
    
    model.fit(X_train, y_train)
    
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    
    print("Test Seti Sınıflandırma Raporu:")
    print(classification_report(y_test, y_pred))
    print(f"Doğruluk (Accuracy): {accuracy_score(y_test, y_pred):.4f}")
    
    # Modeli kaydet
    os.makedirs(os.path.dirname(model_save_path), exist_ok=True)
    joblib.dump(model, model_save_path)
    print(f"Model başarıyla '{model_save_path}' yoluna kaydedildi.")

if __name__ == "__main__":
    train_fakeout_model()
