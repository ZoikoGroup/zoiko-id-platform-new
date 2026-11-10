# A stand-in agent for trying workload identity locally (development only): it does nothing but stay alive and log a heartbeat.
# Replace it with real agent software; what matters to Zoiko iD is the image's digest, which a person registers under Builds and approves.
#   docker compose build invoice-agent        # builds acme/invoice-agent:1.0.0
#   docker inspect --format '{{.Id}}' acme/invoice-agent:1.0.0     # the digest to register
FROM alpine:3.20
RUN adduser -D -u 10001 agent
USER 10001
COPY --chown=10001 heartbeat.sh /home/agent/heartbeat.sh
CMD ["sh", "/home/agent/heartbeat.sh"]
