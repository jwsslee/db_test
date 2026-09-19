import streamlit as st

st.set_page_config(page_title="덧셈 계산기", page_icon="🧮")
st.title("🧮 덧셈 계산기")

a = 123
b = 234
result = a + b

st.success(f"{a} + {b} = {result}")
