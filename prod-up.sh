#!/bin/sh
python -m streamlit run main.py \
    --server.headless true\
    --server.port 8000\
    --client.toolbarMode minimal\
    --global.developmentMode false\
    --server.fileWatcherType none
