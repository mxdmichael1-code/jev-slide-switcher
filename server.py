"""Streamlit's documented ASGI entry point with headers for inline Moonshine."""
from pathlib import Path

import streamlit as st
from starlette.middleware import Middleware
from slide_switcher.web_server import IsolationHeaders, SessionRoutes


app = st.App(Path(__file__).with_name("app.py"), middleware=[Middleware(IsolationHeaders), Middleware(SessionRoutes)])
