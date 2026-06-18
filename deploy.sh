#!/bin/bash
# Запуск: ./deploy.sh
# Делает: git pull + пересобирает и рестартует контейнер

set -e  # остановить при любой ошибке

echo "📥 Получаю обновления..."
git pull origin main

echo "🐳 Пересобираю и перезапускаю контейнер..."
docker compose up --build -d

echo "✅ Готово! Логи:"
docker compose logs --tail=20
