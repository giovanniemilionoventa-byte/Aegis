FROM python:3.11-slim

WORKDIR /agent

COPY infra/ai-agent/agent.py /agent/agent.py
COPY infra/ai-agent/tools.py /agent/tools.py
COPY infra/agent/probe.py /agent/probe.py

# httpx is the only dependency. No agent framework, no Google client library --
# this container must not even contain the means to talk to Gmail.
RUN pip install --no-cache-dir --break-system-packages httpx \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin aegis \
    && chown -R aegis:aegis /agent

USER 10001
ENV PYTHONUNBUFFERED=1

CMD ["sleep", "infinity"]
