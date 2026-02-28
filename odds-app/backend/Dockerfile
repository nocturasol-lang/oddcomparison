FROM python:3.12-slim
WORKDIR /app
RUN pip install playwright && playwright install chromium --with-deps
COPY requirements.txt .
RUN pip install -r requirements.txt
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
