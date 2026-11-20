# Face-match sidecar (lab): OpenCV's YuNet face detector and SFace face recogniser behind a tiny HTTP service. The backend never links an imaging or model library.
# Build:  docker build -t zid/face:prodlocal tools/face-sidecar        Run:  docker compose up -d face
#
# LAB ONLY. This service measures how alike two faces are. It does not liveness-test, it is not an approved engine, and nothing it returns approves a person
# (see docs/signoff/face-engine-owner-pack.md). Both models are open source (YuNet: MIT; SFace: Apache-2.0 code) but SFace was trained on a public face dataset whose
# terms for commercial use have not been confirmed: engine-register.yaml records it as LAB_ONLY, licence unverified, on purpose.
FROM python:3.13-slim

# numpy and the headless OpenCV wheel (no GUI libraries) are the only Python dependencies.
RUN pip install --no-cache-dir "numpy==2.2.6" "opencv-python-headless==4.11.0.86"

# The two model files, fetched once at build time and refused unless they are exactly the files this was written against.
RUN mkdir -p /app/models && python - <<'PY'
import hashlib, urllib.request
FILES = {
    "face_detection_yunet_2023mar.onnx": ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
                                          "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "face_recognition_sface_2021dec.onnx": ("https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
                                            "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
for name, (url, digest) in FILES.items():
    data = urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit(f"{name}: unexpected content (a changed or pointer file)")
    open(f"/app/models/{name}", "wb").write(data)
print("models verified")
PY

COPY server.py /app/server.py

# no root, no shell login, nothing writable but /tmp (compose mounts it as a tmpfs)
RUN useradd --system --no-create-home --shell /usr/sbin/nologin face
USER face
WORKDIR /app
EXPOSE 8091
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8091/healthz',timeout=4).status==200 else 1)"
CMD ["python", "/app/server.py"]
