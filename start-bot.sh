#!/bin/bash
cd /root/bot-sked
mkdir -p /root/bot-sked/data
. .venv/bin/activate
pip install -r requirements.txt
. .env
python sched_bot.py