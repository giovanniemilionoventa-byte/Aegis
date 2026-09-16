FROM python:3.11-slim

WORKDIR /proxy

COPY infra/egress-proxy/proxy.py /proxy/proxy.py

# Standard library only. Nothing to install, nothing to compromise.
RUN useradd --uid 10002 --create-home --shell /usr/sbin/nologin egress \
    && chown -R egress:egress /proxy

USER 10002
ENV PYTHONUNBUFFERED=1
EXPOSE 8888

CMD ["python", "proxy.py"]
