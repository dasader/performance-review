import logging

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="전략기술 논문성과 분석")


@app.get("/api/health")
def health():
    return {"status": "ok"}
