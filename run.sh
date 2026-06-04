#!/usr/bin/env bash
# Start the FEC Email Marketing Portal
export FLASK_APP=app.py
export FLASK_ENV=production
python -c "from app import app, init_db; app.app_context().__enter__(); init_db()"
python app.py
