#!/bin/bash
cd /home/ubuntu/quant_bot

echo "Stopping quant_bot service to free up RAM..."
sudo systemctl stop quant_bot

echo "Starting AI training..."
/home/ubuntu/quant_bot/venv/bin/python train_model.py

echo "Restarting quant_bot service..."
sudo systemctl start quant_bot

echo "Retraining process completed."
