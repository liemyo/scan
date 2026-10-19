#!/bin/bash
cd "$(dirname "$0")"
pip3 install -q playwright 2>/dev/null
python3 fb_scan.py
