#!/bin/bash
echo "$(dirname "$0")"
source venv/bin/activate
python dictation.py
