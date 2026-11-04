FROM python:3.11-slim
WORKDIR /app
RUN apt-get update
RUN apt-get install -y --no-install-recommends nodejs ffmpeg fonts-dejavu-core
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --no-cache-dir '.[render,video,test]'
RUN python -m playwright install --with-deps chromium
ENTRYPOINT ["mirror-maze-bench"]
