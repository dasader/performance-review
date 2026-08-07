FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x docker-entrypoint.sh

# 비root로 돈다. 이 프로세스가 쓰는 곳은 DB와 /tmp(gemini_batch가 제출용 JSONL을
# tempfile로 만든다)뿐이라 /app에 쓰기 권한이 필요 없다 — 코드는 root 소유인 채로
# 읽기만 한다. 홈은 만들어 둔다: 라이브러리가 ~/.cache를 잡으려 할 때 HOME이 없으면
# 그때서야 터지는데, 디렉터리 하나 값이 그 진단 비용보다 싸다.
RUN useradd --system --create-home --uid 10001 app
USER app

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
