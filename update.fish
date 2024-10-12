#!/opt/homebrew/bin/fish
scp sched_bot.py ny:/root/bot-sked/
ssh ny "systemctl restart bot-sked"