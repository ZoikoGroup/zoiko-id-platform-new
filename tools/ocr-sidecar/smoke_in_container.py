"""Smoke test for the photo-checking service, run INSIDE its container against its own code, real Pillow and real Tesseract:

    docker exec -i zoiko-id-ocr-1 python - < tools/ocr-sidecar/smoke_in_container.py

It proves the image clean-up (CR-125): metadata, location and device details removed, orientation applied, transparency made white, size capped, and anything that is not a photograph refused. It is not a unit test and needs the container running."""

import base64
import io
import json
import sys
import urllib.request

sys.path.insert(0, "/app")
import server  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

problems = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        problems.append(what)


def b64(data):
    return base64.b64encode(data).decode()


def jpeg_with_metadata(size=(800, 400), orientation=1):
    img = Image.new("RGB", size, (200, 200, 200))
    ImageDraw.Draw(img).rectangle([10, 10, size[0] - 10, size[1] - 10], outline=(0, 0, 0), width=4)
    exif = Image.Exif()
    exif[0x010F] = "ACME Phones"  # make
    exif[0x0110] = "Secret Model 9"  # model
    exif[0x0112] = orientation
    exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 0.0)}  # GPS
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def sanitize(raw):
    code, out = server.sanitize({"image_base64": b64(raw)})
    return code, out


# 1. metadata is gone and the image is a plain JPEG
raw = jpeg_with_metadata()
check(len(Image.open(io.BytesIO(raw)).getexif()) > 0, "the test image really carries metadata (make, model, GPS)")
code, out = sanitize(raw)
clean = base64.b64decode(out["image_base64"])
reread = Image.open(io.BytesIO(clean))
check(code == 200 and reread.format == "JPEG", "a JPEG comes back")
check(len(reread.getexif()) == 0 and b"ACME" not in clean and b"Secret Model" not in clean and b"Exif" not in clean[:64], "no metadata survives (make, model, GPS, exif header)")

# 2. orientation is applied before the metadata is dropped
code, out = sanitize(jpeg_with_metadata(size=(800, 400), orientation=6))
check(code == 200 and (out["width"], out["height"]) == (400, 800), "an image that was rotated by its metadata is stored upright")

# 3. a transparent PNG becomes opaque white, not hidden data
png = io.BytesIO()
Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(png, format="PNG")
code, out = sanitize(png.getvalue())
px = Image.open(io.BytesIO(base64.b64decode(out["image_base64"]))).getpixel((10, 10))
check(code == 200 and px == (255, 255, 255), "a transparent area becomes white")

# 4. the longest side is capped
code, out = sanitize(jpeg_with_metadata(size=(5000, 1000)))
check(code == 200 and max(out["width"], out["height"]) <= server.MAX_SIDE, "an oversized image is brought down to the cap")

# 5. what is not a photograph is refused
check(server.sanitize({"image_base64": b64(b"GIF89a....")})[0] == 422, "a GIF is refused")
check(server.sanitize({"image_base64": b64(b"%PDF-1.7 hello")})[0] == 422, "a PDF is refused")
check(server.sanitize({"image_base64": b64(b"\xff\xd8\xff" + b"not really a jpeg")})[0] == 422, "a damaged JPEG is refused")
check(server.sanitize({"image_base64": "!!!"})[0] == 422, "bad base64 is refused")

# 6. end to end over HTTP, the way the backend calls it
req = urllib.request.Request(
    "http://127.0.0.1:8090/sanitize", data=json.dumps({"image_base64": b64(raw)}).encode(), headers={"Content-Type": "application/json"}
)
http = json.loads(urllib.request.urlopen(req, timeout=20).read())
check(base64.b64decode(http["image_base64"]).startswith(b"\xff\xd8\xff") and http["format"] == "JPEG", "the HTTP route answers the same")

# 7. the inspection and the reading, on a clean image: what the service says about a plain synthetic page
req = urllib.request.Request(
    "http://127.0.0.1:8090/inspect", data=json.dumps({"image_base64": http["image_base64"]}).encode(), headers={"Content-Type": "application/json"}
)
metrics = json.loads(urllib.request.urlopen(req, timeout=20).read())
check({"width", "height", "sharpness", "glare_fraction", "dark_fraction"} <= set(metrics), "inspect returns the measurements the platform reads")
print("INFO inspect:", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics.items()})

print()
print("RESULT:", "ALL PASSED" if not problems else f"{len(problems)} FAILED: {problems}")
sys.exit(1 if problems else 0)
