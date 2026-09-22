#!/bin/bash

echo "The quick setup for Edge AI has been started!"

venv/bin/pip install pandas scikit-learn joblib

echo "Setup the Edge AI Training...."
cp 3e-green-edge-ai-train.service.example 3e-green-edge-ai-train.service
cp 3e-green-edge-ai-train.timer.example 3e-green-edge-ai-train.timer

sudo cp 3e-green-edge-ai-train.service /etc/systemd/system/
sudo cp 3e-green-edge-ai-train.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now 3e-green-edge-ai-train.timer

echo "Setup the Edge AI Inference..."
cp 3e-green-edge-ai.service.example 3e-green-edge-ai.service

sudo cp 3e-green-edge-ai.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now 3e-green-edge-ai.service

echo "The quick setup for Edge AI has been done!"
