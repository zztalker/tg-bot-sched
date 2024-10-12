#!/bin/bash
cd /root/bot-sked
. .venv/bin/activate
pip install -r requirements.txt
. .env
python sched_bot.py