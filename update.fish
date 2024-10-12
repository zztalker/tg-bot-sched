#!/opt/homebrew/bin/fish
scp migration.py ny:/root/bot-sked/
scp sched_bot.py ny:/root/bot-sked/
scp requirements.txt ny:/root/bot-sked/
scp start-bot.sh ny:/root/bot-sked/
ssh ny "systemctl restart bot-sked"