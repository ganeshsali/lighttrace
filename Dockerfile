FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY lighttrace ./lighttrace
RUN pip install --no-cache-dir '.[postgres,grpc]' && useradd -r -u 1000 lighttrace && mkdir /data && chown lighttrace /data
USER lighttrace
ENV LIGHTTRACE_HOST=0.0.0.0 LIGHTTRACE_PORT=1984 LIGHTTRACE_DB=/data/lighttrace.db
VOLUME /data
# 4317: OTLP/gRPC, only when LIGHTTRACE_GRPC_PORT=4317 is set
EXPOSE 1984 4317
CMD ["lighttrace"]
