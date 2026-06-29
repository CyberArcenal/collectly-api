#!/bin/bash

echo "BUILD START"

# Ito ang bagong command — ginagamit ang --break-system-packages para bypass ang uv error
python3 -m pip install --break-system-packages -r requirements.txt

# Collect static files (dagdag --clear para siguradong fresh)
python3 manage.py collectstatic --noinput --clear

echo "BUILD END"