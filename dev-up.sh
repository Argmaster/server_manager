#!/bin/sh
python -m streamlit run main.py\
    --server.headless true\
    --server.port 8000\
    --server.runOnSave false\
    --server.fileWatcherType none
