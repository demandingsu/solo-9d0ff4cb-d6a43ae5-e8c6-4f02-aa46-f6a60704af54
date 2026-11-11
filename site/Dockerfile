FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv/site

COPY batches.py server.py ./

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/index.html', timeout=3).status==200 else 1)"

# 纯标准库示例站：可在线切换公告批次、详情故障、分页循环
CMD ["python", "-u", "server.py", "--host", "0.0.0.0", "--port", "8080", "--batch", "v1"]
