#!/bin/zsh
# Double-click this file in Finder to launch the Team Planner.
cd -- "$(dirname "$0")"
# Kill any old instance on port 5001
lsof -ti:5001 | xargs kill -9 2>/dev/null
sleep 0.5
python3 -m flask --app app run --port 5001 &
sleep 1
open http://127.0.0.1:5001
wait
