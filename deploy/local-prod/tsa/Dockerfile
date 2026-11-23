# A timestamp authority (RFC 3161) for the local rehearsal of the production deployment.
#
# It answers the same request the cell sends to a real authority (`application/timestamp-query`, DER) with a signed token, so the cell's timestamp client, its
# token verification and its trust file run the production path end to end, entirely on this machine: no evidence digest leaves it. It has its OWN root, made
# on first start and deleted once it has issued the signing certificate (an offline root, as a real authority keeps one); `/ca.pem` is that root, which the
# cell's `ZID_EVIDENCE_TSA_TRUST_FILE` points at. It is NOT a trusted third party: a real deployment sets `ZID_EVIDENCE_TSA_URL` to an authority the owners
# chose and contracted, and this one trusts nobody but this rehearsal. Python's standard library and the `openssl` command the base image already has; nothing is installed.
FROM python:3.13-slim
RUN useradd --system --uid 10001 --create-home tsa && mkdir /data && chown 10001:10001 /data
COPY --chown=10001:10001 server.py tsa.cnf /app/
USER 10001
VOLUME /data
EXPOSE 8318
HEALTHCHECK --interval=5s --timeout=3s --retries=12 CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8318/health', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["python", "/app/server.py"]
